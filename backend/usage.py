"""Local usage accounting. Unknown outcomes retain the pre-call reservation."""
import json
import time
from contextvars import ContextVar
from fastapi import HTTPException
from .store import connect
from . import budgets

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
        batch_id,a,b,estimated,lease=budgets.reserve(db,config,estimate,4096)
        record=db.execute('''INSERT INTO usage_ledger(kind,model,at,state,reserved_input,reserved_output,
                            batch_id,input_rate,output_rate,reserved_cost,lease_until,price_snapshot)
                            VALUES(?,?,?,'reserved',?,4096,?,?,?,?,?,?)''',
                          (kind,config.get('model','unknown'),time.time(),estimate,batch_id,a,b,estimated,lease,budgets.price_snapshot(budgets.policy(db),config))).lastrowid
    token=CURRENT.set((path,record))
    try:
        result=sender(config,system,data)
        mark_sent()  # Also accounts for injected senders which returned a response.
        return result
    except Exception:
        with connect(path) as db:
            db.execute("UPDATE usage_ledger SET state='released',reserved_input=0,reserved_output=0,reserved_cost=0,lease_until=NULL WHERE id=? AND state='reserved'",(record,))
        raise
    finally:CURRENT.reset(token)


def mark_sent():
    context=CURRENT.get()
    if context:
        with connect(context[0]) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM usage_ledger WHERE id=?',(context[1],)).fetchone()
            if row['state']=='reserved':
                if row['lease_until'] is not None and row['lease_until']<time.time():raise HTTPException(429,'请求预留已过期，未发送')
                if row['batch_id'] is not None:
                    run=db.execute('SELECT state FROM batch_run WHERE id=?',(row['batch_id'],)).fetchone()
                    if not run or run['state']!='active':raise HTTPException(429,'批量任务已暂停或取消，未发送')
                sent_at=time.time();snapshot=json.loads(row['price_snapshot']) if row['price_snapshot'] else None
                a,b=row['input_rate'],row['output_rate'];period=None
                if snapshot:
                    period=('idle' if budgets.idle(sent_at) else 'busy') if snapshot['time_pricing'] else 'fixed'
                    prefix='idle_' if period=='idle' else ''
                    a,b=snapshot[prefix+'input_rate'],snapshot[prefix+'output_rate']
                db.execute("UPDATE usage_ledger SET state='unknown',lease_until=NULL,sent_at=?,price_period=?,input_rate=?,output_rate=? WHERE id=? AND state='reserved'",(sent_at,period,a,b,context[1]))


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
        row=db.execute('SELECT input_rate,output_rate FROM usage_ledger WHERE id=?',(context[1],)).fetchone()
        amount=budgets.cost(a,b,row['input_rate'],row['output_rate']) if row['input_rate'] is not None else None
        details=token_details(usage)
        db.execute("UPDATE usage_ledger SET state='settled',input_tokens=?,output_tokens=?,actual_cost=?,reasoning_tokens=?,cached_input_tokens=?,reported_text_tokens=?,lease_until=NULL WHERE id=?",
                   (a,b,amount,details['reasoning_tokens'],details['cached_input_tokens'],details['reported_text_tokens'],context[1]))


def token_details(usage):
    """Optional provider counters, never extra tokens or inferred visible output."""
    result={}
    for target,parent,key,bound in (
        ('reasoning_tokens','completion_tokens_details','reasoning_tokens','completion_tokens'),
        ('cached_input_tokens','prompt_tokens_details','cached_tokens','prompt_tokens'),
        ('reported_text_tokens','completion_tokens_details','text_tokens','completion_tokens')):
        group=usage.get(parent) if isinstance(usage,dict) else None
        value=group.get(key) if isinstance(group,dict) else None
        maximum=usage.get(bound) if isinstance(usage,dict) else None
        result[target]=value if type(value) is int and type(maximum) is int and 0<=value<=maximum else None
    return result


def detail_totals(db,since):
    row=db.execute('''SELECT count(*) calls,count(reasoning_tokens) reasoning_known,
        sum(reasoning_tokens) reasoning_tokens,
        sum(CASE WHEN reasoning_tokens IS NOT NULL THEN output_tokens ELSE 0 END) covered_output_tokens,
        count(cached_input_tokens) cached_known,sum(cached_input_tokens) cached_input_tokens,
        count(reported_text_tokens) text_known,sum(reported_text_tokens) reported_text_tokens
        FROM usage_ledger WHERE at>? AND state!='released' ''',(since,)).fetchone()
    return dict(row)
