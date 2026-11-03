"""Prepare a local 12-call comparison. No requests without explicit manifest approval."""
import argparse
from contextlib import ExitStack
import html,json,time
from pathlib import Path
from fastapi import HTTPException
from backend import ai_settings,budgets,diagnostics,usage
from backend.model_contract import (current_window,mapping_hash,digest,Extraction,role_audit,
    _commit_response,SYSTEM_PROMPT,PROMPT_VERSION)

MODEL='deepseek-v4.1-flash-1017'


def prepare(root):
    path=root/'data/memory.sqlite3';config=ai_settings.load(path.parent)
    if config.get('model')!=MODEL:raise ValueError('仅允许当前精确模型快照，不接受别名')
    frozen=[json.loads(x) for x in (root/'data/extraction-eval/frozen-windows-v1.jsonl').read_text(encoding='utf-8').splitlines()][1:]
    previous=json.loads((root/'private/v3-comparison-20261102/receipt.json').read_text(encoding='utf-8'))
    v3={r['job_id']:r.get('validation',{}).get('audit',{}).get('returned_memories') for r in previous['results']}
    errors={json.loads(f.read_text(encoding='utf-8'))['request_id'] for f in diagnostics.files(path)
            if json.loads(f.read_text(encoding='utf-8')).get('code')=='D304'}
    selected=[]
    for label,predicate in (
        ('v2_d304',lambda w:w['request_id'] in errors),
        ('v3_empty',lambda w:v3.get(w['job_id'])==0),
        ('v3_nonempty',lambda w:(v3.get(w['job_id']) or 0)>0)):
        eligible=sorted((w for w in frozen if predicate(w) and w['job_id'] not in [x['job_id'] for x in selected]),key=lambda w:w['job_id'])
        if not eligible:raise ValueError('缺少独立的窗口选择层：'+label)
        selected.append({**eligible[0],'selection':label})
    remaining=sorted((w for w in frozen if w['job_id'] not in [x['job_id'] for x in selected]),key=lambda w:(len(w['messages']),w['job_id']))
    selected.append({**remaining[len(remaining)//2],'selection':'remaining_median_message_count'})
    windows=[]
    with diagnostics.connection(path,True) as db:
        db.execute('BEGIN')
        for w in selected:
            job=db.execute('SELECT * FROM job WHERE id=?',(w['job_id'],)).fetchone()
            request=db.execute('SELECT * FROM model_request WHERE id=?',(w['request_id'],)).fetchone()
            payload,refs=current_window(db,job);stored=json.loads(request['payload_json'])
            if request['prompt_version']!=PROMPT_VERSION or digest(stored)!=request['input_hash'] or request['mapping_hash']!=mapping_hash(db):raise ValueError('请求契约或隐私映射已变化')
            if payload!=stored['payload'] or refs!=stored['refs'] or json.loads(job['input_json'])['messages']!=w['messages']:raise ValueError('冻结窗口已变化')
            windows.append({k:w[k] for k in ('job_id','request_id','selection','messages')})
            windows[-1].update(payload_hash=digest({'payload':payload,'refs':refs}),mapping_hash=request['mapping_hash'])
    sequence=[]
    for i,w in enumerate(windows):
        for mode in (('default','off') if i%2==0 else ('off','default')):sequence.append({'job_id':w['job_id'],'mode':mode,'repeat':1})
    for i,w in enumerate(windows[:2]):
        for mode in (('off','default') if i%2==0 else ('default','off')):sequence.append({'job_id':w['job_id'],'mode':mode,'repeat':2})
    plan={'format':1,'model':MODEL,'prompt_version':PROMPT_VERSION,'system_hash':digest(SYSTEM_PROMPT),
          'schema_hash':digest(Extraction.model_json_schema()),'max_tokens':4096,'max_calls':12,'token_limit':40000,
          'config_hash':digest({k:config.get(k) for k in ('model','base_url')}),'windows':windows,'sequence':sequence}
    return {**plan,'manifest_sha256':digest(plan)}


def verify_window(path,w):
    with diagnostics.connection(path,True) as db:
        db.execute('BEGIN')
        job=db.execute('SELECT * FROM job WHERE id=?',(w['job_id'],)).fetchone()
        payload,refs=current_window(db,job)
        if digest({'payload':payload,'refs':refs})!=w['payload_hash'] or mapping_hash(db)!=w['mapping_hash']:raise ValueError('窗口或映射变化，停止')
        return payload,refs,job['initialization_id']


def preflight_budget(db,config,system,payload,limit):
    """Read-only estimate of the next call; invoke still reserves atomically later."""
    settings=budgets.policy(db)
    estimate=len((system+json.dumps(payload,ensure_ascii=False)).encode('utf-8'))+128
    priced=settings['model']==config.get('model') and settings['base_url']==config.get('base_url') and settings['input_rate'] is not None and settings['output_rate'] is not None
    a=max(settings['input_rate'],settings['cached_rate'] or 0) if priced else None
    b=max(settings['output_rate'],settings['reasoning_rate'] or 0) if priced else None
    cost=budgets.cost(estimate,4096,a,b) if priced else None
    if not settings['enabled']:raise ValueError('请先明确开启全局预算，实验不修改预算')
    budgets.check_limit(settings,budgets.totals(db),estimate,4096,cost)
    share={**settings,'token_limit':settings['token_limit']*settings['batch_percent']//100,
           'amount_limit':settings['amount_limit']*settings['batch_percent']//100 if settings['amount_limit'] is not None else None}
    budgets.check_limit(share,budgets.totals(db,batch_only=True),estimate,4096,cost)
    if estimate+4096>limit:raise ValueError('单轮 Token 上限不足以预留首个窗口，零调用退出')


def execute(root,plan,approved,output,*,sender=None):
    """Future authorized run: only usage/batch accounting is persisted, never memories."""
    if approved!=plan['manifest_sha256'] or prepare(root)!=plan:raise ValueError('清单不匹配，请重新生成并审阅')
    from backend.runtime import worker_lock
    from backend.store import connect,SCHEMA_VERSION
    path=root/'data/memory.sqlite3';settings_file=path.parent/'ai-settings.json'
    original=settings_file.read_bytes();config=ai_settings.load(path.parent)
    if not config.get('enabled') or not config.get('key_encrypted'):raise ValueError('模型未启用')
    with ExitStack() as stack:
        for name in ('workbench','collector','bot'):stack.enter_context(worker_lock(path.parent/('runtime' if name=='workbench' else name),name))
        payload,refs,initialization=verify_window(path,plan['windows'][0])
        system=SYSTEM_PROMPT+'\nJSON schema:\n'+json.dumps(Extraction.model_json_schema())
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('PRAGMA user_version').fetchone()[0]!=SCHEMA_VERSION:raise ValueError('需先按备份流程升级；实验不自动迁移')
            prior=db.execute('SELECT * FROM batch_run WHERE request_key=?',('thinking-'+approved,)).fetchone()
            if prior and db.execute('SELECT 1 FROM usage_ledger WHERE batch_id=? LIMIT 1',(prior['id'],)).fetchone():
                raise ValueError('本清单已有调用账本记录，禁止同清单重试；不能凭 receipt 推断零花费')
            prior_id=prior['id'] if prior else -1
            if db.execute("SELECT 1 FROM batch_run WHERE id!=? AND (state='active' OR (initialization_id=? AND state='paused'))",(prior_id,initialization)).fetchone():raise ValueError('仍有占用中的批量运行')
            try:preflight_budget(db,config,system,payload,plan['token_limit'])
            except HTTPException as error:raise ValueError('预算预检未通过，零调用退出；全局、批量比例或单轮额度不足：'+str(error.detail)) from None
            # Files alone never prohibit a retry. Only this manifest's ledger does.
            output.mkdir(parents=True,exist_ok=True);receipt=output/'receipt.json'
            receipt.write_text(json.dumps({'state':'starting','manifest_sha256':approved}),encoding='utf-8')
            if prior:
                run=prior['id'];db.execute("UPDATE batch_run SET state='active',reason=NULL WHERE id=?",(run,))
            else:
                run=db.execute('''INSERT INTO batch_run(initialization_id,request_key,request_hash,max_windows,token_limit,model,base_url,created_at)
                    VALUES(?,?,?,12,40000,?,?,?)''',(initialization,'thinking-'+approved,approved,MODEL,config['base_url'],time.time())).lastrowid
        report={'manifest_sha256':approved,'model':MODEL,'max_calls':12,'token_limit':40000,'state':'running','results':[]}
        period=budgets.idle();token=budgets.BATCH.set(run)
        try:
            for index,step in enumerate(plan['sequence']):
                if budgets.idle()!=period:report['state']='stopped_period_change';break
                w=next(w for w in plan['windows'] if w['job_id']==step['job_id'])
                payload,refs,_=verify_window(path,w)
                frozen={**config,'_extraction_thinking_mode':step['mode']}
                result={**step,'input_hash':digest({'payload':payload,'refs':refs,'thinking_mode':step['mode'],'normalize_d304':False,
                    'model':MODEL,'system':SYSTEM_PROMPT,'schema':Extraction.model_json_schema(),'max_tokens':4096}),
                    'quality_label':None,'visible_output_tokens':None}
                with connect(path) as db:last=db.execute('SELECT COALESCE(max(id),0) FROM usage_ledger').fetchone()[0]
                started=time.perf_counter();raw=None
                try:
                    raw=usage.invoke(path,frozen,SYSTEM_PROMPT+'\nJSON schema:\n'+json.dumps(Extraction.model_json_schema()),payload,kind='manual',sender=sender or ai_settings.complete)
                    result.update(state='returned',visible_characters=len(raw),visible_bytes=len(raw.encode('utf-8')))
                    parsed=Extraction.model_validate_json(raw)
                    audit=role_audit(parsed,{r['source_ref']:r['role'] for r in payload['messages']},refs,False)
                    result.update(candidate_count=len(parsed.memories),d304_events=audit['d304_event_count'])
                    # The same read-only contract checks identifiers, evidence and privacy.
                    try:result['validation']=_commit_response(path,w['request_id'],raw,replay=True,normalize_d304=False)
                    except HTTPException as e:result['validation']=diagnostics.details(e)
                except HTTPException as e:result.update(state='stopped',failure=diagnostics.details(e),http_status=e.status_code)
                except Exception:result.update(state='stopped',failure={'code':'experiment_invalid_output'})
                result['elapsed_seconds']=time.perf_counter()-started
                if raw is not None:(output/f'response-{index+1:02d}.json').write_text(raw,encoding='utf-8')
                with connect(path) as db:
                    rows=[dict(r) for r in db.execute('SELECT id,state,input_tokens,output_tokens,reasoning_tokens,reported_text_tokens,reserved_input,reserved_output FROM usage_ledger WHERE batch_id=? AND id>?',(run,last))]
                    result['ledger']=rows[0] if rows else None
                    report['usage']=budgets.totals(db,batch_id=run)
                report['results'].append(result)
                receipt.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                if result['state']=='stopped' or not rows or rows[0]['state']!='settled':report['state']='stopped';break
            else:report['state']='completed'
        finally:
            budgets.BATCH.reset(token)
            with connect(path) as db:db.execute("UPDATE batch_run SET state=?,reason='thinking_experiment' WHERE id=?",('completed' if report['state']=='completed' else 'paused',run))
            report['disk_model_config_unchanged']=settings_file.read_bytes()==original
            receipt.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            panels=[]
            for i,r in enumerate(report['results']):
                file=output/f'response-{i+1:02d}.json'
                panels.append('<article><h2>'+html.escape(f"窗口 {r['job_id']} / {r['mode']} / 第 {r['repeat']} 次")+'</h2><pre>'+html.escape(file.read_text(encoding='utf-8') if file.exists() else '无可见输出')+'</pre></article>')
            (output/'comparison.html').write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; base-uri \'none\'; form-action \'none\'"><title>思考模式并排材料</title><h1>仅供人工审阅，不含质量结论</h1><table><tr>'+''.join('<td>'+x+'</td>' for x in panels)+'</tr></table>',encoding='utf-8')
        return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--execute-approved-manifest')
    args=parser.parse_args();plan=prepare(args.root)
    if args.execute_approved_manifest:
        report=execute(args.root,plan,args.execute_approved_manifest,args.root/'private/thinking-experiment'/plan['manifest_sha256'])
    else:report=plan
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
