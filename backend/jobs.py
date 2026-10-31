"""Durable preparation jobs. Prepared windows are not completed extraction."""
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field

from .models import StrictModel
from .privacy import preview
from .store import connect

MAX_WINDOW_CHARS = 12000


def context_warnings(db, window, limited, *, has_nontext=None):
    reasons = ['bounded_or_selected_history'] if limited else []
    if has_nontext is None:
        has_nontext = bool(db.execute("SELECT 1 FROM message WHERE type!='text' LIMIT 1").fetchone())
    if has_nontext:
        reasons.append('conversation_has_unprocessed_nontext')
    dates = [r['event_utc'] for r in window]
    if any(date is None for date in dates):
        reasons.append('unknown_event_time')
    known = [datetime.fromisoformat(date) for date in dates if date]
    if any((b-a).total_seconds() > 1800 for a,b in zip(known, known[1:])):
        reasons.append('time_gap_over_30_minutes')
    return reasons


class Initialization(StrictModel):
    mode: Literal['full', 'batch']
    message_ids: list[int] = Field(default_factory=list, max_length=10000)


class Control(StrictModel):
    action: Literal['pause', 'resume', 'cancel', 'retry']


def claim(path, owner, now=None):
    now = time.time() if now is None else now
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE job SET state='pending',owner=NULL,lease_until=NULL WHERE state='running' AND lease_until<=?", (now,))
        row = db.execute("SELECT j.* FROM job j JOIN initialization i ON i.id=j.initialization_id WHERE j.state='pending' AND i.state='active' ORDER BY CASE WHEN j.kind='auto_extract' THEN 0 ELSE 1 END,j.id LIMIT 1").fetchone()
        if row is None:
            return None
        db.execute("UPDATE job SET state='running',owner=?,lease_until=?,attempt=attempt+1 WHERE id=?", (owner, now + 60, row['id']))
        return dict(row)


def heartbeat(path, job_id, owner):
    with connect(path) as db:
        now = time.time()
        return bool(db.execute("UPDATE job SET lease_until=? WHERE id=? AND owner=? AND state='running' AND lease_until>? AND initialization_id IN (SELECT id FROM initialization WHERE state='active')", (now + 60, job_id, owner, now)).rowcount)


def prepare_one(path):
    owner = secrets.token_hex(16)
    job = claim(path, owner)
    if not job:
        return {'processed': False}
    with connect(path) as db:
        # The transaction validates versions and commits result + state together.
        db.execute('BEGIN IMMEDIATE')
        current = db.execute("SELECT j.* FROM job j JOIN initialization i ON i.id=j.initialization_id WHERE j.id=? AND j.owner=? AND j.state='running' AND j.lease_until>? AND i.state='active'", (job['id'], owner, time.time())).fetchone()
        if not current:
            return {'processed': False}
        inputs = json.loads(job['input_json'])
        messages = []
        total_chars = 0
        state, error = 'prepared', None
        for entry in inputs['messages']:
            row = db.execute('SELECT * FROM message WHERE id=?', (entry['id'],)).fetchone()
            if not row or row['revision'] != entry['revision']:
                state, error = 'failed', 'source_revision_changed'
                break
            if row['event_utc'] is None:
                state, error = 'failed', 'unknown_event_time'
                break
            total_chars += len(row['text'])
            if total_chars > MAX_WINDOW_CHARS:
                state, error = 'failed', 'context_character_limit'
                break
            if db.execute('SELECT 1 FROM suppressed_evidence WHERE message_id=? AND message_revision=?', (row['id'], row['revision'])).fetchone():
                state, error = 'failed', 'corrected_evidence'
                break
            checked = preview(db, row['text'])
            if checked['status'] == 'blocked':
                state, error = 'blocked_privacy', 'unresolved_sensitive_context'
                break
            # Local preparation only; real dates and roles still need an approved outbound policy.
            messages.append({'id': row['id'], 'revision': row['revision'], 'role': row['sender_role'],
                             'event_time': row['event_time'], 'text': checked['text'], 'allowed_tokens': checked['allowed_tokens']})
        result = {'messages': messages, 'context_limited': inputs['context_limited'],
                  'context_warnings': inputs.get('context_warnings', []),
                  'external_enabled': False, 'extracted': False} if state == 'prepared' else None
        db.execute('UPDATE job SET state=?,result_json=?,error=?,owner=NULL,lease_until=NULL WHERE id=?',
                   (state, json.dumps(result, ensure_ascii=False) if result else None, error, job['id']))
    return {'processed': True, 'job_id': job['id'], 'state': state}


def invalidate_jobs(db, message_id):
    # Uses stored version membership, not text matching. Applies to prepared and pending windows.
    for row in db.execute("SELECT id,input_json FROM job WHERE state NOT IN ('cancelled','failed')").fetchall():
        if any(m['id'] == message_id for m in json.loads(row['input_json'])['messages']):
            db.execute("UPDATE job SET state='failed',error='source_or_correction_changed',result_json=NULL,owner=NULL,lease_until=NULL WHERE id=?", (row['id'],))


def create_initialization(db, body):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    if db.execute("SELECT 1 FROM initialization WHERE state IN ('active','paused') AND mode!='auto'").fetchone():
        raise HTTPException(409, '已有初始化范围，请先处理或取消')
    cutoff = datetime.now(timezone.utc).isoformat()
    if body.mode == 'full':
        if body.message_ids:
            raise HTTPException(422, '全量模式无需消息 ID')
        rows = db.execute("SELECT * FROM message WHERE type='text' AND (event_utc<=? OR event_utc IS NULL) ORDER BY event_utc,id", (cutoff,)).fetchall()
    else:
        if not body.message_ids:
            raise HTTPException(422, '分批模式需要选定消息')
        ids = sorted(set(body.message_ids))
        rows = db.execute('SELECT * FROM message WHERE id IN (' + ','.join('?' for _ in ids) + ') ORDER BY event_utc,id', ids).fetchall()
        if len(rows) != len(ids) or any(r['type'] != 'text' or (r['event_utc'] and r['event_utc'] > cutoff) for r in rows):
            raise HTTPException(422, '选择包含不存在、非文字或截止时间之后的消息')
    if not rows:
        raise HTTPException(422, '没有可处理的历史文字')
    init_id = db.execute('INSERT INTO initialization(mode,cutoff) VALUES(?,?)', (body.mode, cutoff)).lastrowid
    # Bounded 12-message windows with 2-message overlap, always preserving both roles.
    # This is a configurable-later context baseline, not a topic segmentation claim.
    offset = 0
    has_nontext = bool(db.execute("SELECT 1 FROM message WHERE type!='text' LIMIT 1").fetchone())
    while offset < len(rows):
        window = rows[offset:offset + 12]
        warnings = context_warnings(db, window, offset > 0 or offset + 12 < len(rows) or body.mode == 'batch', has_nontext=has_nontext)
        payload = {'messages': [{'id': r['id'], 'revision': r['revision']} for r in window],
                   'context_limited': bool(warnings), 'context_warnings': warnings}
        serialized = json.dumps(payload, sort_keys=True)
        key = hashlib.sha256(f'{init_id}:prepare-v1:{serialized}'.encode()).hexdigest()
        db.execute('INSERT INTO job(initialization_id,idempotency_key,input_json) VALUES(?,?,?)', (init_id, key, serialized))
        if offset + 12 >= len(rows):
            break
        offset += 10
    return {'id': init_id, 'cutoff': cutoff, 'message_count': len(rows)}


def router(path: Path):
    routes = APIRouter(prefix='/api/initializations')

    @routes.post('')
    def start(body: Initialization):
        with connect(path) as db:
            return create_initialization(db, body)

    @routes.get('')
    def listing():
        with connect(path) as db:
            items = []
            for row in db.execute("SELECT * FROM initialization WHERE mode!='auto' ORDER BY id DESC LIMIT 20").fetchall():
                item = dict(row)
                item['counts'] = {r['state']: r['n'] for r in db.execute('SELECT state,count(*) AS n FROM job WHERE initialization_id=? GROUP BY state', (row['id'],))}
                item['extraction_complete'] = False
                coverage={}
                warnings=set()
                for job in db.execute('SELECT input_json,state FROM job WHERE initialization_id=?',(row['id'],)):
                    inputs=json.loads(job['input_json'])
                    warnings.update(inputs.get('context_warnings',[]))
                    for message in inputs['messages']:
                        coverage.setdefault((message['id'],message['revision']),set()).add(job['state'])
                current={r['id']:r['revision'] for r in db.execute('SELECT id,revision FROM message')}
                item['context_warnings']=sorted(warnings)
                valid=[states for (mid,version),states in coverage.items() if current.get(mid)==version]
                item['message_coverage']={
                    'scope':len(coverage),
                    'prepared':sum(states<={'prepared','extracting','validated_synthetic','validated_model'} for states in valid),
                    'validated_synthetic':sum(states=={'validated_synthetic'} for states in valid),
                    'failed_or_blocked':sum(bool(states & {'failed','blocked_privacy'}) for states in valid),
                    'stale':len(coverage)-len(valid),
                    'real_extracted':sum(states=={'validated_model'} for states in valid),
                }
                item['extraction_complete'] = bool(coverage) and len(valid)==len(coverage) and all(states=={'validated_model'} for states in valid)
                item['jobs'] = [dict(r) for r in db.execute('SELECT id,state,attempt,error FROM job WHERE initialization_id=? ORDER BY id LIMIT 200', (row['id'],))]
                items.append(item)
            return items

    @routes.post('/process-next')
    def process_next():
        return prepare_one(path)

    @routes.post('/{init_id}/control')
    def control(init_id: int, body: Control):
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM initialization WHERE id=?', (init_id,)).fetchone()
            if not row:
                raise HTTPException(404, '初始化不存在')
            if row['state'] == 'cancelled':
                raise HTTPException(409, '已取消的范围不能恢复，请重新创建')
            if body.action == 'retry':
                db.execute("UPDATE job SET state='pending',error=NULL,result_json=NULL WHERE initialization_id=? AND state IN ('failed','blocked_privacy')", (init_id,))
            else:
                state = {'pause': 'paused', 'resume': 'active', 'cancel': 'cancelled'}[body.action]
                db.execute('UPDATE initialization SET state=? WHERE id=?', (state, init_id))
                db.execute("UPDATE job SET state=?,owner=NULL,lease_until=NULL,result_json=NULL WHERE initialization_id=? AND state IN ('running','extracting')", ('cancelled' if state == 'cancelled' else 'pending', init_id))
                if state == 'cancelled':
                    db.execute("UPDATE job SET state='cancelled',result_json=NULL WHERE initialization_id=? AND state NOT IN ('cancelled','validated_model')", (init_id,))
        return {'ok': True}

    return routes
