"""Explicit local identity records. Names and shared phones never merge people."""
import secrets
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, StrictBool, field_validator, model_validator

from .models import StrictModel
from .memories import Evidence
from .privacy import add_mapping, normalized_value, PHONE_VALUE
from .store import connect


class Person(StrictModel):
    label: str = Field(min_length=1, max_length=200)

    @field_validator('label')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('名称不能为空')
        return value.strip()


class Property(StrictModel):
    kind: Literal['alias', 'phone']
    value: str = Field(min_length=1, max_length=200)
    valid_from: str | None = None
    valid_to: str | None = None
    confirmed: StrictBool
    confirmation_note: str = Field(min_length=1, max_length=1000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)

    @field_validator('value', 'confirmation_note')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('值和确认依据不能为空')
        return value.strip()

    @field_validator('valid_from', 'valid_to')
    @classmethod
    def timestamp(cls, value):
        if value is None:
            return None
        date=datetime.fromisoformat(value.replace('Z','+00:00'))
        if date.tzinfo is None:
            raise ValueError('时间须带时区')
        return date.astimezone(timezone.utc).isoformat()

    @model_validator(mode='after')
    def valid(self):
        if not self.confirmed:
            raise ValueError('关系须由用户明确确认')
        if self.kind=='phone' and not re.fullmatch(r'\+?[0-9][0-9 -]{5,24}[0-9]',self.value):
            raise ValueError('号码需使用数字及可选的区号、空格或连字符')
        if self.valid_from and self.valid_to and self.valid_from >= self.valid_to:
            raise ValueError('结束时间必须晚于开始时间')
        return self


def evidence_rows(db, property_id):
    return [dict(r) for r in db.execute('SELECT message_id,message_revision,span_start,span_end FROM property_evidence WHERE property_id=? ORDER BY message_id,span_start,span_end',(property_id,))]


def property_card(db, row):
    evidence=evidence_rows(db,row['id'])
    for item in evidence:
        message=db.execute('SELECT text,revision,event_time FROM message WHERE id=?',(item['message_id'],)).fetchone()
        item['quote']=message['text'][item['span_start']:item['span_end']] if message['revision']==item['message_revision'] else None
        item['event_time']=message['event_time'] if item['quote'] is not None else None
    return {**dict(row),'evidence':evidence}


def invalidate_property_source(db, message_id):
    db.execute("UPDATE person_property SET status='stale' WHERE status='active' AND id IN (SELECT property_id FROM property_evidence WHERE message_id=?)",(message_id,))


def within_period(row, instant):
    return not instant or not ((row['valid_from'] and instant<row['valid_from']) or (row['valid_to'] and instant>=row['valid_to']))


def router(path):
    routes=APIRouter(prefix='/api/entities')

    @routes.post('')
    def create(body:Person):
        with connect(path) as db:
            token=f'[PERSON_{secrets.token_hex(16)}]'
            cursor=db.execute('INSERT INTO person(label,token) VALUES(?,?)',(body.label,token))
            return {'id':cursor.lastrowid,'label':body.label,'token':token}

    @routes.get('')
    def listing():
        with connect(path) as db:
            people=[]
            for row in db.execute('SELECT * FROM person ORDER BY id'):
                people.append({**dict(row),'properties':[property_card(db,p) for p in db.execute('SELECT * FROM person_property WHERE person_id=? ORDER BY id',(row['id'],))]})
            return people

    @routes.get('/lookup')
    def lookup(q:str,at:str|None=None):
        q=q.strip()
        if not q or len(q)>200:
            raise HTTPException(422,'请输入不超过 200 字的别名或号码')
        try:
            instant=Property.timestamp(at)
        except ValueError:
            raise HTTPException(422,'查询时间须为带时区的有效时间')
        with connect(path) as db:
            candidates={}
            for row in db.execute("SELECT * FROM person_property WHERE status='active' ORDER BY person_id,id"):
                matches=(normalized_value('PHONE',row['value'])==normalized_value('PHONE',q) if row['kind']=='phone' else row['value']==q)
                if not matches:
                    continue
                if instant and ((row['valid_from'] and instant<row['valid_from']) or (row['valid_to'] and instant>=row['valid_to'])):
                    continue
                card=property_card(db,row)
                if any(e['quote'] is None for e in card['evidence']):
                    continue
                card['time_uncertain']=bool(instant and (not row['valid_from'] or not row['valid_to']))
                if row['person_id'] not in candidates:
                    person=db.execute('SELECT * FROM person WHERE id=?',(row['person_id'],)).fetchone()
                    candidates[row['person_id']]={**dict(person),'matches':[]}
                candidates[row['person_id']]['matches'].append(card)
            items=list(candidates.values())
            return {'candidates':items,'ambiguous':len(items)>1,'at':instant,
                    'mode':'retrospective_current_records','identity_confirmed':False}

    @routes.post('/{person_id}/properties')
    def add(person_id:int,body:Property):
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM person WHERE id=?',(person_id,)).fetchone():
                raise HTTPException(404,'人物不存在')
            refs=sorted([e.model_dump() for e in body.evidence],key=lambda e:(e['message_id'],e['span_start'],e['span_end']))
            if len({(e.message_id,e.span_start,e.span_end) for e in body.evidence})!=len(refs):
                raise HTTPException(422,'证据片段重复')
            for e in body.evidence:
                message=db.execute('SELECT * FROM message WHERE id=?',(e.message_id,)).fetchone()
                if not message or message['type']!='text':
                    raise HTTPException(422,'关系证据须指向文字消息')
                if message['revision']!=e.message_revision:
                    raise HTTPException(409,'关系证据已修订，请重新选择')
                if not 0<=e.span_start<e.span_end<=len(message['text']):
                    raise HTTPException(422,'关系证据范围无效')
            # Explicit time ranges describe history; neither a new phone nor an
            # identical alias silently replaces an existing relationship.
            values=(person_id,body.kind,body.value,body.valid_from,body.valid_to,body.confirmation_note)
            existing=db.execute("SELECT id FROM person_property WHERE person_id=? AND kind=? AND value=? AND valid_from IS ? AND valid_to IS ? AND confirmation_note=? AND status='active'",values).fetchall()
            for row in existing:
                if evidence_rows(db,row['id'])==refs:
                    return {'id':row['id'],'duplicate':True}
            token=add_mapping(db,'PHONE',body.value)['token'] if body.kind=='phone' else None
            cursor=db.execute('INSERT INTO person_property(person_id,kind,value,valid_from,valid_to,confirmation_note,value_token) VALUES(?,?,?,?,?,?,?)',(*values,token))
            db.executemany('INSERT INTO property_evidence VALUES(?,?,?,?,?)',[(cursor.lastrowid,e.message_id,e.message_revision,e.span_start,e.span_end) for e in body.evidence])
            return {'id':cursor.lastrowid,'duplicate':False}

    @routes.get('/{person_id}/messages')
    def messages(person_id:int,before:int|None=Query(default=None,gt=0)):
        with connect(path) as db:
            if not db.execute('SELECT 1 FROM person WHERE id=?',(person_id,)).fetchone():
                raise HTTPException(404,'人物不存在')
            properties=db.execute("SELECT * FROM person_property WHERE status='active' ORDER BY id").fetchall()
            own=[r for r in properties if r['person_id']==person_id]
            items=[]
            if own:
                for message in db.execute("SELECT * FROM message WHERE type='text' AND (? IS NULL OR id<?) ORDER BY id DESC",(before,before)):
                    phones={normalized_value('PHONE',m[0]) for m in re.finditer(PHONE_VALUE,message['text'])}
                    matches=[]
                    for prop in own:
                        if not within_period(prop,message['event_utc']):
                            continue
                        key=normalized_value('PHONE',prop['value']) if prop['kind']=='phone' else prop['value']
                        if not (key in phones if prop['kind']=='phone' else key in message['text']):
                            continue
                        others={r['person_id'] for r in properties if r['kind']==prop['kind'] and within_period(r,message['event_utc']) and (normalized_value('PHONE',r['value']) if r['kind']=='phone' else r['value'])==key}
                        matches.append({'property_id':prop['id'],'kind':prop['kind'],'value':prop['value'],
                                        'ambiguous':len(others)>1,'candidate_person_ids':sorted(others),
                                        'time_uncertain':not message['event_utc'] or not prop['valid_from'] or not prop['valid_to']})
                    if matches:
                        items.append({**dict(message),'matches':matches,'identity_confirmed':False})
                        if len(items)>50:
                            break
            return {'items':items[:50],'has_more':len(items)>50,
                    'next_before':items[49]['id'] if len(items)>50 else None,
                    'mode':'retrospective_current_records'}

    @routes.post('/{person_id}/properties/{property_id}/retract')
    def retract(person_id:int,property_id:int):
        with connect(path) as db:
            cursor=db.execute("UPDATE person_property SET status='retracted' WHERE id=? AND person_id=?",(property_id,person_id))
            if not cursor.rowcount:
                raise HTTPException(404,'人物关系不存在')
            # The value token is retained: old evidence must still mean the old value.
        return {'ok':True}

    return routes
