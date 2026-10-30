"""Local evidence-backed memory editing; no external inference is performed here."""
import hashlib
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, field_validator

from .models import StrictModel
from .store import connect, tokens
from .jobs import invalidate_jobs


class Evidence(StrictModel):
    message_id: int = Field(gt=0)
    message_revision: int = Field(ge=1)
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)


class MemoryDraft(StrictModel):
    summary: str = Field(min_length=1, max_length=2000)
    subject: Literal['self', 'peer', 'third_party', 'unknown']
    polarity: Literal['positive', 'negative', 'uncertain']
    assertion: Literal['direct', 'reported', 'inferred']
    temporal_kind: Literal['preference', 'plan', 'past_event', 'fact', 'unknown']
    evidence: list[Evidence] = Field(min_length=1, max_length=20)

    @field_validator('summary')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('记忆内容不能为空')
        return value.strip()


class Correction(StrictModel):
    reason: str = Field(default='用户标错', min_length=1, max_length=1000)


class Review(StrictModel):
    expected_version: int = Field(ge=1)


class Replacement(StrictModel):
    expected_version: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=2000)
    subject: Literal['self', 'peer', 'third_party', 'unknown']
    polarity: Literal['positive', 'negative', 'uncertain']
    assertion: Literal['direct', 'reported', 'inferred']
    temporal_kind: Literal['preference', 'plan', 'past_event', 'fact', 'unknown']

    @field_validator('summary')
    @classmethod
    def nonblank(cls, value):
        return MemoryDraft.nonblank(value)


def invalidate_source(db, message_id):
    ids = [r[0] for r in db.execute('SELECT memory_id FROM memory_evidence WHERE message_id=?', (message_id,))]
    for memory_id in ids:
        db.execute("UPDATE memory SET status='stale',version=version+1 WHERE id=? AND status='active'", (memory_id,))
        db.execute('DELETE FROM memory_fts WHERE rowid=?', (memory_id,))
        db.execute("UPDATE reminder SET status='withdrawn' WHERE watch_id IN (SELECT id FROM watch_item WHERE source_memory_id=?)", (memory_id,))


def create_memory(db, draft, origin='manual', permitted_sources=frozenset()):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    unique = {(e.message_id, e.message_revision, e.span_start, e.span_end) for e in draft.evidence}
    if len(unique) != len(draft.evidence):
        raise HTTPException(422, '证据片段重复')
    for evidence in draft.evidence:
        row = db.execute('SELECT * FROM message WHERE id=?', (evidence.message_id,)).fetchone()
        if not row or row['type'] != 'text':
            raise HTTPException(422, '证据必须指向本地文字消息')
        if row['revision'] != evidence.message_revision:
            raise HTTPException(409, '证据版本已变化，请重新选择原文')
        if not 0 <= evidence.span_start < evidence.span_end <= len(row['text']):
            raise HTTPException(422, '证据引用范围无效')
        if draft.assertion == 'direct' and draft.subject in ('peer', 'self') and row['sender_role'] != draft.subject:
            raise HTTPException(422, '本人陈述必须由对应角色的原文支持；转述请单独标记')
        if (evidence.message_id,evidence.message_revision) not in permitted_sources and db.execute('SELECT 1 FROM suppressed_evidence WHERE message_id=? AND message_revision=?',
                      (evidence.message_id, evidence.message_revision)).fetchone():
            raise HTTPException(409, '该版本证据已被用户标错停用，不可重复提炼；可使用新消息或修订后的证据')
    payload = draft.model_dump()
    payload['evidence'] = sorted(payload['evidence'], key=lambda e: (e['message_id'], e['span_start'], e['span_end']))
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    existing = db.execute('SELECT id,status FROM memory WHERE fingerprint=?', (fingerprint,)).fetchone()
    if existing:
        if existing['status'] != 'active':
            raise HTTPException(409, '记忆已停用，不能自动复活')
        return {'id': existing['id'], 'duplicate': True}
    cursor = db.execute('INSERT INTO memory(summary,subject,polarity,assertion,temporal_kind,fingerprint) VALUES(?,?,?,?,?,?)',
                        (draft.summary, draft.subject, draft.polarity, draft.assertion, draft.temporal_kind, fingerprint))
    memory_id = cursor.lastrowid
    db.execute('UPDATE memory SET origin=? WHERE id=?', (origin, memory_id))
    db.executemany('INSERT INTO memory_evidence VALUES(?,?,?,?,?)',
                   [(memory_id, e.message_id, e.message_revision, e.span_start, e.span_end) for e in draft.evidence])
    db.execute('INSERT INTO memory_fts(rowid,summary) VALUES(?,?)', (memory_id, tokens(draft.summary)))
    return {'id': memory_id, 'duplicate': False}


def suppress(db, memory_id, reason, keep_id=None):
    db.execute('INSERT INTO correction(memory_id,reason) VALUES(?,?)', (memory_id,reason))
    sources=db.execute('SELECT message_id,message_revision FROM memory_evidence WHERE memory_id=?',(memory_id,)).fetchall()
    for source in sources:
        invalidate_jobs(db,source['message_id'])
        db.execute('INSERT OR IGNORE INTO suppressed_evidence VALUES(?,?)',tuple(source))
        for other in db.execute('SELECT memory_id FROM memory_evidence WHERE message_id=? AND message_revision=?',tuple(source)).fetchall():
            if other[0]==keep_id:
                continue
            db.execute("UPDATE memory SET status='incorrect',version=version+1 WHERE id=? AND status NOT IN ('incorrect','superseded')",(other[0],))
            db.execute('DELETE FROM memory_fts WHERE rowid=?',(other[0],))
            db.execute("UPDATE reminder SET status='withdrawn' WHERE watch_id IN (SELECT id FROM watch_item WHERE source_memory_id=?)",(other[0],))


def memory_card(db, row):
    card = dict(row)
    review=db.execute('SELECT confirmed_version,confirmed_at FROM memory_review WHERE memory_id=?',(row['id'],)).fetchone()
    card['confirmed']=bool(review and review['confirmed_version']==row['version'] and row['status']=='active')
    card['confirmed_at']=review['confirmed_at'] if card['confirmed'] else None
    card['evidence'] = []
    for evidence in db.execute('SELECT e.*,m.text,m.sender_role,m.event_time,m.revision AS current_revision FROM memory_evidence e JOIN message m ON m.id=e.message_id WHERE memory_id=? ORDER BY m.id,e.span_start', (row['id'],)):
        item = dict(evidence)
        # Never show a changed message as if it were the original cited text.
        item['quote'] = item.pop('text')[item['span_start']:item['span_end']] if item['current_revision'] == item['message_revision'] else None
        item.pop('text', None)
        card['evidence'].append(item)
    return card


def record_feedback(db,memory_id,action):
    import json
    row=db.execute('SELECT version FROM memory WHERE id=?',(memory_id,)).fetchone()
    evidence=[dict(r) for r in db.execute('SELECT message_id,message_revision,span_start,span_end FROM memory_evidence WHERE memory_id=?',(memory_id,))]
    db.execute('INSERT INTO feedback(memory_id,memory_version,action,evidence_json) VALUES(?,?,?,?)',(memory_id,row['version'],action,json.dumps(evidence)))


def router(path: Path):
    routes = APIRouter(prefix='/api/memories')

    @routes.post('')
    def add(body: MemoryDraft):
        with connect(path) as db:
            return create_memory(db, body)

    @routes.get('')
    def listing(q: str = '', review: Literal['all','pending','confirmed']='all',
                limit: int = Query(default=100,ge=1,le=100), before: int = Query(default=0,ge=0)):
        if len(q) > 200:
            raise HTTPException(422, '搜索词过长')
        with connect(path) as db:
            confirmed="EXISTS(SELECT 1 FROM memory_review r WHERE r.memory_id=m.id AND r.confirmed_version=m.version)"
            clause="m.status='active'" + (f' AND {confirmed}' if review=='confirmed' else f' AND NOT {confirmed}' if review=='pending' else '')
            params=[]
            if before:
                clause+=' AND m.id<?'
                params.append(before)
            if q.strip():
                phrase = '"' + tokens(q.strip()).replace('"', '""') + '"'
                rows = db.execute(f'SELECT m.* FROM memory m JOIN memory_fts f ON m.id=f.rowid WHERE memory_fts MATCH ? AND {clause} ORDER BY m.id DESC LIMIT ?', [phrase,*params,limit]).fetchall()
            else:
                rows = db.execute(f'SELECT m.* FROM memory m WHERE {clause} ORDER BY m.id DESC LIMIT ?', [*params,limit]).fetchall()
            return [memory_card(db, row) for row in rows]

    @routes.get('/review-counts')
    def review_counts():
        with connect(path) as db:
            total=db.execute("SELECT count(*) FROM memory WHERE status='active'").fetchone()[0]
            confirmed=db.execute("SELECT count(*) FROM memory m JOIN memory_review r ON r.memory_id=m.id AND r.confirmed_version=m.version WHERE m.status='active'").fetchone()[0]
            return {'pending':total-confirmed,'confirmed':confirmed}

    @routes.post('/{memory_id}/review/{action}')
    def review_memory(memory_id:int, action:Literal['confirm','reopen'], body:Review):
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM memory WHERE id=?',(memory_id,)).fetchone()
            if not row or row['status']!='active' or row['version']!=body.expected_version:
                raise HTTPException(409,'记忆已变化，请刷新后再核对')
            if db.execute('SELECT 1 FROM memory_evidence e JOIN message m ON m.id=e.message_id WHERE e.memory_id=? AND e.message_revision!=m.revision',(memory_id,)).fetchone():
                raise HTTPException(409,'原文证据已变化，请先核对新版本')
            record_feedback(db,memory_id,action)
            if action=='confirm':
                db.execute('INSERT INTO memory_review(memory_id,confirmed_version) VALUES(?,?) ON CONFLICT(memory_id) DO UPDATE SET confirmed_version=excluded.confirmed_version,confirmed_at=CURRENT_TIMESTAMP',(memory_id,row['version']))
            else:
                db.execute('DELETE FROM memory_review WHERE memory_id=?',(memory_id,))
            return {'ok':True,'confirmed':action=='confirm'}

    @routes.post('/{memory_id}/correct')
    def correct(memory_id: int, body: Correction):
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            memory = db.execute('SELECT * FROM memory WHERE id=?', (memory_id,)).fetchone()
            if not memory:
                raise HTTPException(404, '记忆不存在')
            if memory['status'] == 'incorrect':
                return {'ok': True}
            record_feedback(db,memory_id,'incorrect')
            suppress(db,memory_id,body.reason)
        return {'ok': True}

    @routes.post('/{memory_id}/replace')
    def replace(memory_id:int,body:Replacement):
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM memory WHERE id=?',(memory_id,)).fetchone()
            if not old or old['status']!='active' or old['version']!=body.expected_version:
                raise HTTPException(409,'记忆已变化，请刷新后再更正')
            fields=body.model_dump(exclude={'expected_version'})
            evidence=[Evidence(**dict(r)) for r in db.execute('SELECT message_id,message_revision,span_start,span_end FROM memory_evidence WHERE memory_id=?',(memory_id,))]
            draft=MemoryDraft(**fields,evidence=evidence)
            if all(old[key]==getattr(draft,key) for key in fields):
                return {'id':memory_id,'duplicate':True}
            permitted={(e.message_id,e.message_revision) for e in evidence}
            new=create_memory(db,draft,origin='manual_correction',permitted_sources=permitted)
            if new['duplicate']:
                raise HTTPException(409,'已有相同记忆，请检查现有记录')
            suppress(db,memory_id,'用户明确更正',keep_id=new['id'])
            db.execute("UPDATE memory SET status='superseded' WHERE id=?",(memory_id,))
            db.execute('UPDATE memory SET supersedes_id=? WHERE id=?',(memory_id,new['id']))
            return new

    @routes.get('/{memory_id}/history')
    def history(memory_id:int):
        with connect(path) as db:
            cards=[]
            current=memory_id
            while current is not None and len(cards)<100:
                row=db.execute('SELECT * FROM memory WHERE id=?',(current,)).fetchone()
                if row is None:
                    raise HTTPException(404,'记忆不存在')
                cards.append(memory_card(db,row))
                current=row['supersedes_id']
            return cards

    return routes
