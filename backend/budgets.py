"""Atomic reservation limits; money is integer micro-yuan, rates per million tokens."""
from contextvars import ContextVar
import time
from fastapi import APIRouter,HTTPException
from pydantic import Field,model_validator
from .models import StrictModel
from .store import connect

BATCH = ContextVar('budget_batch',default=None)
MILLION=1000000


class Settings(StrictModel):
    enabled:bool=False
    token_limit:int=Field(default=1000000,ge=4096,le=1000000000)
    amount_limit:int|None=Field(default=None,ge=1,le=1000000000000)
    model:str=Field(default='',max_length=200)
    base_url:str=Field(default='',max_length=500)
    input_rate:int|None=Field(default=None,ge=0,le=1000000000000)
    output_rate:int|None=Field(default=None,ge=0,le=1000000000000)
    cached_rate:int|None=Field(default=None,ge=0,le=1000000000000)
    reasoning_rate:int|None=Field(default=None,ge=0,le=1000000000000)

    @model_validator(mode='after')
    def pricing(self):
        if (self.input_rate is None)!=(self.output_rate is None):raise ValueError('输入和输出单价需同时填写')
        if self.input_rate is not None and (not self.model or not self.base_url):raise ValueError('单价必须绑定模型和接口')
        if (self.cached_rate is not None or self.reasoning_rate is not None) and self.input_rate is None:raise ValueError('请先填写输入和输出单价')
        if self.amount_limit is not None and (self.input_rate is None or not self.model or not self.base_url):
            raise ValueError('金额预算必须先配置并确认当前模型单价')
        return self


def policy(db):return dict(db.execute('SELECT * FROM budget_policy WHERE id=1').fetchone())


def cost(input_tokens,output_tokens,input_rate,output_rate):
    return (input_tokens*input_rate+output_tokens*output_rate+MILLION-1)//MILLION


def totals(db,*,batch_id=None):
    clause='batch_id=?' if batch_id is not None else 'at>?'
    value=batch_id if batch_id is not None else time.time()-86400
    row=db.execute(f'''SELECT COALESCE(SUM(CASE WHEN state='settled' THEN input_tokens+output_tokens
                ELSE reserved_input+reserved_output END),0) AS tokens,
            COALESCE(SUM(CASE WHEN state='settled' THEN actual_cost ELSE reserved_cost END),0) AS amount,
            COALESCE(SUM(CASE WHEN state='settled' THEN actual_cost IS NULL ELSE reserved_cost IS NULL END),0) AS unpriced,
            COALESCE(SUM(state!='settled' AND model='legacy' AND reserved_input=0),0) AS legacy_unknown,
            count(*) AS calls
        FROM usage_ledger WHERE state!='released' AND {clause}''',(value,)).fetchone()
    return dict(row)


def check_limit(limit,spent,input_tokens,output_tokens,estimated_cost):
    if spent['legacy_unknown']:raise HTTPException(429,'旧调用缺少输入估算，无法安全核定预算；请等待滚动窗口移出')
    if spent['tokens']+input_tokens+output_tokens>limit['token_limit']:
        raise HTTPException(429,'Token 预算不足，已停止启动后续调用')
    if limit['amount_limit'] is not None:
        if estimated_cost is None or spent['unpriced']:
            raise HTTPException(429,'单价未确认或窗口内存在无法计价的调用，金额预算阻止发送')
        if spent['amount']+estimated_cost>limit['amount_limit']:
            raise HTTPException(429,'金额预算不足，已停止启动后续调用')


def reserve(db,config,input_tokens,output_tokens):
    assert db.in_transaction
    settings=policy(db)
    now=time.time()
    db.execute("UPDATE usage_ledger SET state='unknown',lease_until=NULL WHERE state='reserved' AND lease_until<?",(now,))
    priced=(settings['model']==config.get('model') and settings['base_url']==config.get('base_url') and settings['input_rate'] is not None and settings['output_rate'] is not None)
    a=max(settings['input_rate'],settings['cached_rate'] or 0) if priced else None
    b=max(settings['output_rate'],settings['reasoning_rate'] or 0) if priced else None
    estimated=cost(input_tokens,output_tokens,a,b) if priced else None
    if settings['enabled']:check_limit(settings,totals(db),input_tokens,output_tokens,estimated)
    batch_id=BATCH.get()
    if batch_id is not None:
        run=db.execute('SELECT * FROM batch_run WHERE id=?',(batch_id,)).fetchone()
        if not run or run['state']!='active':raise HTTPException(429,'批量任务已暂停或取消')
        if run['model']!=config.get('model') or run['base_url']!=config.get('base_url'):
            raise HTTPException(429,'批量模型已变化，请核对单价后创建新运行')
        if totals(db,batch_id=batch_id)['calls']>=run['max_windows']:
            raise HTTPException(429,'批量窗口调用上限已达到')
        check_limit(run,totals(db,batch_id=batch_id),input_tokens,output_tokens,estimated)
    return batch_id,a,b,estimated,now+120


def recover_expired(path):
    with connect(path) as db:
        if not db.execute("SELECT 1 FROM usage_ledger WHERE state='reserved' AND lease_until<? LIMIT 1",(time.time(),)).fetchone():return
        db.execute("UPDATE usage_ledger SET state='unknown',lease_until=NULL WHERE state='reserved' AND lease_until<?",(time.time(),))


def router(path):
    routes=APIRouter(prefix='/api/budgets')

    @routes.get('')
    def get():
        from .ai_settings import load
        config=load(path.parent)
        with connect(path) as db:
            settings=policy(db)
            return {'settings':settings,'rolling':totals(db),
                    'current_model':config.get('model',''),'current_base_url':config.get('base_url',''),
                    'price_matches':settings['model']==config.get('model') and settings['base_url']==config.get('base_url') and settings['input_rate'] is not None,
                    'states':{r[0]:r[1] for r in db.execute('SELECT state,count(*) FROM usage_ledger WHERE at>? GROUP BY state',(time.time()-86400,))}}

    @routes.put('')
    def put(body:Settings):
        from .ai_settings import load
        data=body.model_dump();config=load(path.parent)
        if body.input_rate is not None and (body.model!=config.get('model') or body.base_url!=config.get('base_url')):
            # A model switch must not prevent disabling an old policy.
            with connect(path) as db:previous=policy(db)
            fields=('model','base_url','input_rate','output_rate','cached_rate','reasoning_rate')
            if body.enabled or any(data[k]!=previous[k] for k in fields):
                raise HTTPException(409,'单价必须绑定当前已保存的模型和接口')
        with connect(path) as db:
            db.execute('UPDATE budget_policy SET '+','.join(k+'=?' for k in data)+' WHERE id=1',list(data.values()))
        return get()

    return routes
