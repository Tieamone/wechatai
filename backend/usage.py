"""Local usage accounting. Unknown outcomes retain the pre-call reservation."""
import json
import time
from contextvars import ContextVar
from fastapi import HTTPException
from .store import connect

CURRENT = ContextVar('usage_record',default=None)


def invoke(path, config, system, data, *, kind, sender):
    # UTF-8 byte count is deliberately conservative without a model tokenizer.
    estimate=len((system+json.dumps(data,ensure_ascii=False)).encode('utf-8'))+128
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if kind=='automatic':
            control=db.execute('SELECT * FROM auto_control WHERE id=1').fetchone()
            used=db.execute("SELECT count(*) FROM usage_ledger WHERE kind='automatic' AND state!='released' AND at>?",(time.time()-86400,)).fetchone()[0]
            if not control['enabled'] or used>=control['daily_limit']:
                raise HTTPException(409,'自动处理已暂停或达到调用上限')
        record=db.execute("INSERT INTO usage_ledger(kind,model,at,state,reserved_input,reserved_output) VALUES(?,?,?,'reserved',?,4096)",
                          (kind,config.get('model','unknown'),time.time(),estimate)).lastrowid
    token=CURRENT.set((path,record))
    try:
        result=sender(config,system,data)
        mark_sent()  # Also accounts for injected senders which returned a response.
        return result
    except Exception:
        with connect(path) as db:
            db.execute("UPDATE usage_ledger SET state='released',reserved_input=0,reserved_output=0 WHERE id=? AND state='reserved'",(record,))
        raise
    finally:CURRENT.reset(token)


def mark_sent():
    context=CURRENT.get()
    if context:
        with connect(context[0]) as db:
            db.execute("UPDATE usage_ledger SET state='unknown' WHERE id=? AND state='reserved'",(context[1],))


def trace(event, info):
    # httpcore invokes this immediately before it attempts to write request headers.
    # Connection/pool/TLS failures occur earlier and can release the reservation.
    if event.endswith('send_request_headers.started'):mark_sent()


def settle(usage):
    context=CURRENT.get()
    if not context or not isinstance(usage,dict):return
    a,b,total=(usage.get(k) for k in ('prompt_tokens','completion_tokens','total_tokens'))
    if any(type(v) is not int or v<0 for v in (a,b,total)) or total!=a+b or total==0:return
    with connect(context[0]) as db:
        db.execute("UPDATE usage_ledger SET state='settled',input_tokens=?,output_tokens=? WHERE id=?",(a,b,context[1]))
