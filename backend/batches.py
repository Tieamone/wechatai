"""Persistent, explicitly requested historical extraction with per-run limits."""
import hashlib
import json
import time
from fastapi import APIRouter,HTTPException
from pydantic import Field
from .models import StrictModel
from .store import connect
from . import ai_settings,budgets


class Start(StrictModel):
    initialization_id:int=Field(gt=0)
    request_key:str=Field(min_length=16,max_length=100)
    max_windows:int=Field(default=5,ge=1,le=100)
    token_limit:int=Field(default=100000,ge=4096,le=1000000000)
    amount_limit:int|None=Field(default=None,ge=1,le=1000000000000)


class Control(StrictModel):
    action:str=Field(pattern='^(pause|resume|cancel)$')


def process_next(path,*,should_stop=lambda:False):
    with connect(path) as db:
        expired=db.execute("SELECT s.*,j.state AS job_state FROM batch_step s JOIN job j ON j.id=s.job_id WHERE s.state='processing' AND s.lease_until<? LIMIT 1",(time.time(),)).fetchone()
        row=db.execute("""SELECT s.*,j.state AS job_state FROM batch_step s JOIN batch_run r ON r.id=s.run_id
            JOIN job j ON j.id=s.job_id JOIN initialization i ON i.id=r.initialization_id
            WHERE r.state='active' AND s.state='pending' AND i.state='active'
            AND j.state NOT IN ('pending','running','extracting') ORDER BY r.id,j.id LIMIT 1""").fetchone()
    if expired:
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            state='done' if expired['job_state']=='validated_model' else 'unknown'
            changed=db.execute("UPDATE batch_step SET state=?,lease_until=NULL WHERE run_id=? AND job_id=? AND state='processing' AND lease_until<?",(state,expired['run_id'],expired['job_id'],time.time())).rowcount
            if changed:db.execute("UPDATE batch_run SET state='paused',reason='interrupted' WHERE id=? AND state='active'",(expired['run_id'],))
        return {'processed':bool(changed)}
    if not row:return {'processed':False}
    config=ai_settings.load(path.parent)
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        run=db.execute('SELECT * FROM batch_run WHERE id=?',(row['run_id'],)).fetchone()
        if run['state']!='active':return {'processed':False}
        if not config.get('enabled') or not config.get('key_encrypted'):
            db.execute("UPDATE batch_run SET state='paused',reason='model_disabled' WHERE id=?",(run['id'],))
            return {'processed':True}
        if run['model']!=config.get('model') or run['base_url']!=config.get('base_url'):
            db.execute("UPDATE batch_run SET state='paused',reason='model_changed' WHERE id=?",(run['id'],))
            return {'processed':True}
        changed=db.execute("UPDATE batch_step SET state='processing',lease_until=? WHERE run_id=? AND job_id=? AND state='pending'",(time.time()+120,run['id'],row['job_id'])).rowcount
        if not changed:return {'processed':False}
    state,reason='done',None
    context=budgets.BATCH.set(row['run_id'])
    try:
        if should_stop():raise HTTPException(429,'工作台正在退出')
        if row['job_state']=='validated_model':pass
        elif row['job_state']!='prepared':state,reason='blocked','window_blocked'
        else:ai_settings.extract_job(path,row['job_id'],config)
    except HTTPException as error:
        state,reason=('pending','budget_limit') if error.status_code==429 else ('failed','model_failed')
    except Exception:state,reason='unknown','model_failed'
    finally:budgets.BATCH.reset(context)
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT state FROM batch_run WHERE id=?',(row['run_id'],)).fetchone()
        if current['state']=='cancelled' and state=='pending':state='cancelled'
        db.execute("UPDATE batch_step SET state=?,lease_until=NULL WHERE run_id=? AND job_id=? AND state='processing'",(state,row['run_id'],row['job_id']))
        if reason:db.execute("UPDATE batch_run SET state='paused',reason=? WHERE id=? AND state='active'",(reason,row['run_id']))
        elif not db.execute("SELECT 1 FROM batch_step WHERE run_id=? AND state IN ('pending','processing')",(row['run_id'],)).fetchone():
            db.execute("UPDATE batch_run SET state='completed',reason=NULL WHERE id=? AND state='active'",(row['run_id'],))
    return {'processed':True,'state':state}


def router(path,*,allowed=True):
    routes=APIRouter(prefix='/api/batches')

    @routes.get('')
    def listing():
        with connect(path) as db:
            rows=[]
            for run in db.execute('SELECT * FROM batch_run ORDER BY id DESC LIMIT 30').fetchall():
                rows.append({**dict(run),'usage':budgets.totals(db,batch_id=run['id']),
                             'counts':{r[0]:r[1] for r in db.execute('SELECT state,count(*) FROM batch_step WHERE run_id=? GROUP BY state',(run['id'],))}})
            return rows

    @routes.post('')
    def start(body:Start):
        if not allowed:raise HTTPException(409,'合成演示不能启用真实批量调用')
        config=ai_settings.load(path.parent)
        if not config.get('enabled') or not config.get('key_encrypted'):raise HTTPException(409,'请先保存并允许模型调用')
        fingerprint=hashlib.sha256(json.dumps(body.model_dump(exclude={'request_key'}),sort_keys=True).encode()).hexdigest()
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT * FROM batch_run WHERE request_key=?',(body.request_key,)).fetchone()
            if previous:
                if previous['request_hash']!=fingerprint:raise HTTPException(409,'重复提交的参数不一致')
                return {'id':previous['id']}
            if db.execute("SELECT 1 FROM batch_run WHERE initialization_id=? AND state IN ('active','paused')",(body.initialization_id,)).fetchone():
                raise HTTPException(409,'此范围已有批量运行，请先继续或取消')
            if not db.execute("SELECT 1 FROM initialization WHERE id=? AND state='active' AND mode!='auto'",(body.initialization_id,)).fetchone():
                raise HTTPException(409,'请选择启用中的历史范围')
            settings=budgets.policy(db)
            if body.amount_limit is not None and (settings['model']!=config.get('model') or settings['base_url']!=config.get('base_url') or settings['input_rate'] is None):
                raise HTTPException(409,'金额预算需先确认当前模型单价')
            jobs=db.execute("""SELECT id FROM job j WHERE initialization_id=? AND state IN ('pending','running','prepared')
                 AND NOT EXISTS(SELECT 1 FROM batch_step s WHERE s.job_id=j.id AND s.state IN ('pending','processing')) ORDER BY id LIMIT ?""",(body.initialization_id,body.max_windows)).fetchall()
            if not jobs:raise HTTPException(409,'没有可分配窗口；已完成、失败或其他运行占用的窗口不会重复加入')
            run=db.execute('''INSERT INTO batch_run(initialization_id,request_key,request_hash,max_windows,token_limit,amount_limit,model,base_url,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)''',(body.initialization_id,body.request_key,fingerprint,body.max_windows,body.token_limit,body.amount_limit,config['model'],config['base_url'],time.time())).lastrowid
            db.executemany('INSERT INTO batch_step(run_id,job_id) VALUES(?,?)',[(run,j[0]) for j in jobs])
            return {'id':run,'selected_windows':len(jobs)}

    @routes.post('/{run_id}/control')
    def control(run_id:int,body:Control):
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM batch_run WHERE id=?',(run_id,)).fetchone()
            if not row or row['state'] in ('completed','cancelled'):raise HTTPException(409,'运行不存在或已结束')
            if body.action=='resume' and not db.execute("SELECT 1 FROM batch_step WHERE run_id=? AND state IN ('pending','processing')",(run_id,)).fetchone():
                raise HTTPException(409,'没有可继续窗口；失败或未知结果不会自动重复调用')
            state={'pause':'paused','resume':'active','cancel':'cancelled'}[body.action]
            db.execute('UPDATE batch_run SET state=?,reason=NULL WHERE id=?',(state,run_id))
            if body.action=='cancel':db.execute("UPDATE batch_step SET state='cancelled' WHERE run_id=? AND state='pending'",(run_id,))
        return {'ok':True}

    return routes
