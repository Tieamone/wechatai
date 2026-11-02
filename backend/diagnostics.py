"""Bounded local rejection artifacts. Never expose response text over HTTP."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import threading
import uuid
import sqlite3
from contextlib import closing
from fastapi import APIRouter, HTTPException

CURRENT=ContextVar('model_diagnostic',default=None)
LOCK=threading.RLock()
MAX_RAW=256*1024
MAX_TOTAL=5*1024*1024


RESPONSE_CODES={'D101','D102','D103','D104','D105','D106','D110','D111','D112','D113','D114','D115','D301','D302','D303','D304','D305','D306','D308'}


def details(error,values=None):
    result=dict(getattr(error,'diagnostic',{'code':'D999','location':[]}))
    if not result['location']:result['location']=(values or {}).get('location',[])
    return result


def failure(status,detail,code,location=None):
    error=HTTPException(status,detail)
    error.diagnostic={'code':code,'location':location or []}
    return error


def observe(**values):
    current=CURRENT.get()
    if current is not None:current.update(values)


def directory(path):return path.parent/'model-rejections'


def files(path):return sorted(directory(path).glob('rejection-*.json'))


def save(path,request_id,error,values):
    diagnostic=details(error,values)
    raw=values.get('raw')
    if isinstance(raw,str):raw=raw.encode('utf-8')
    size=len(raw) if isinstance(raw,bytes) else values.get('response_bytes')
    body={'id':uuid.uuid4().hex,'request_id':request_id,'at':datetime.now(timezone.utc).isoformat(),
          **diagnostic,'response_bytes':size,'completion_tokens':values.get('completion_tokens'),
          'memory_count':values.get('memory_count'),'raw_kind':values.get('raw_kind','extraction'),
          'raw':raw.decode('utf-8',errors='replace') if isinstance(raw,bytes) and len(raw)<=MAX_RAW else None}
    with LOCK:
        target=directory(path);target.mkdir(exist_ok=True)
        name=target/('rejection-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+body['id']+'.json')
        name.write_text(json.dumps(body,ensure_ascii=False),encoding='utf-8')
        records=files(path);total=sum(p.stat().st_size for p in records)
        while records and (len(records)>20 or total>MAX_TOTAL):
            old=records.pop(0);total-=old.stat().st_size;old.unlink()


@contextmanager
def capture(path,request_id):
    if CURRENT.get() is not None:
        yield
        return
    values={};token=CURRENT.set(values)
    try:yield
    except Exception as error:
        # Admission failures are not rejected model responses.
        code=details(error,values)['code']
        if (values.get('raw') is not None or values.get('response_bytes') is not None) and code in RESPONSE_CODES:
            try:save(path,request_id,error,values)
            except OSError:pass
        raise
    finally:CURRENT.reset(token)


def listing(path):
    with LOCK:
        rows=[]
        for file in reversed(files(path)):
            try:
                item=json.loads(file.read_text(encoding='utf-8'))
                rows.append({k:v for k,v in item.items() if k!='raw'})
                rows[-1]['has_raw']=item.get('raw') is not None
            except (OSError,ValueError):continue
        return {'records':rows,'count_limit':20,'raw_byte_limit':MAX_RAW,'total_byte_limit':MAX_TOTAL}


@contextmanager
def connection(path,readonly):
    from .store import connect
    if not readonly:
        with connect(path) as db:yield db
    else:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            yield db


def router(path):
    routes=APIRouter(prefix='/api/model-diagnostics')
    @routes.get('')
    def status():return listing(path)
    @routes.delete('')
    def clear():
        with LOCK:
            for file in files(path):file.unlink()
        return {'ok':True}
    return routes
