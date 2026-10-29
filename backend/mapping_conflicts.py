"""Choose future replacement tokens; never rewrite existing mapping values."""
import json
from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictBool, field_validator
from .models import Mapping
from .privacy import normalized_value
from .store import connect


class Resolution(Mapping):
    expected_tokens:list[str]=Field(min_length=2,max_length=100)
    selected_token:str=Field(min_length=1,max_length=100)
    confirmed:StrictBool
    note:str=Field(min_length=1,max_length=500)

    @field_validator('note')
    @classmethod
    def nonblank(cls,value):
        if not value.strip():
            raise ValueError('请填写核对依据')
        return value.strip()


def router(path):
    routes=APIRouter(prefix='/api/mapping-conflicts')

    @routes.get('')
    def listing():
        with connect(path) as db:
            grouped={}
            for row in db.execute('SELECT kind,value,token FROM entity_value ORDER BY id'):
                grouped.setdefault((row['kind'],normalized_value(row['kind'],row['value'])),[]).append(dict(row))
            result=[]
            for (kind,value),rows in grouped.items():
                if len(rows)<2:
                    continue
                group=json.dumps(sorted(r['token'] for r in rows))
                chosen=db.execute('SELECT selected_token,note FROM mapping_resolution WHERE kind=? AND normalized_value=? AND token_group=?',(kind,value,group)).fetchone()
                result.append({'kind':kind,'value':value,'mappings':rows,'resolution':dict(chosen) if chosen else None})
            return result

    @routes.post('')
    def resolve(body:Resolution):
        if not body.confirmed:
            raise HTTPException(422,'需明确确认这些值代表同一项信息')
        key=normalized_value(body.kind,body.value)
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            tokens=sorted(r['token'] for r in db.execute('SELECT value,token FROM entity_value WHERE kind=?',(body.kind,)) if normalized_value(body.kind,r['value'])==key)
            if len(tokens)<2 or tokens!=sorted(body.expected_tokens) or body.selected_token not in tokens:
                raise HTTPException(409,'冲突组已变化或所选代号无效，请刷新核对')
            db.execute('''INSERT INTO mapping_resolution(kind,normalized_value,token_group,selected_token,note) VALUES(?,?,?,?,?)
ON CONFLICT(kind,normalized_value) DO UPDATE SET token_group=excluded.token_group,selected_token=excluded.selected_token,note=excluded.note,updated_at=CURRENT_TIMESTAMP''',
                       (body.kind,key,json.dumps(tokens),body.selected_token,body.note))
        return {'ok':True}

    return routes
