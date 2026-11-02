"""Generate an offline, human-only review page; never write the business DB."""
import argparse
import json
from pathlib import Path
import secrets
from .revalidate_model import revalidate
from backend import diagnostics
from backend.privacy import TOKEN


def generate(path,output):
    cards=[];reports=[]
    with diagnostics.connection(path,True) as db:
        mapping={r['token']:r['value'] for r in db.execute('SELECT token,value FROM entity_value')}
        for artifact in diagnostics.files(path):
            record=json.loads(artifact.read_text(encoding='utf-8'))
            if record['code']!='D304':continue
            report=revalidate(path,artifact,normalize_d304=True)
            reports.append({'artifact_id':record['id'],'result':report})
            if not report['valid']:continue
            from backend.ai_settings import parse_response
            raw=parse_response(record['raw']) if record['raw_kind']=='http_json' else record['raw']
            facts=json.loads(raw)['memories']
            for item in report['audit']['items']:
                if item['action']!='mixed_roles':continue
                fact=facts[item['memory_index']]
                evidence=[]
                for ref in item['references']:
                    row=db.execute('SELECT text,revision,sender_role FROM message WHERE id=?',(ref['id'],)).fetchone()
                    if row['revision']!=ref['revision']:raise ValueError('证据版本已变化，请重新生成')
                    evidence.append({'id':ref['id'],'revision':ref['revision'],'role':row['sender_role'],'text':row['text']})
                cards.append({'artifact_id':record['id'],'request_id':record['request_id'],'memory_index':item['memory_index'],
                              'summary':TOKEN.sub(lambda m:mapping.get(m[0],m[0]),fact['summary']),
                              'subject':fact['subject'],'assertion':fact['assertion'],'evidence':evidence})
    nonce=secrets.token_hex(16)
    data=json.dumps(cards,ensure_ascii=True).replace('<','\\u003c')
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-NONCE'; style-src 'nonce-NONCE'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>混合角色证据 · 本机人工审阅</title>
<style nonce="NONCE">body{font:16px/1.7 system-ui;margin:32px auto;padding:0 20px;max-width:1000px;background:#f2f6f4;color:#19332d}article{background:white;padding:24px;border-radius:12px;margin:24px 0;border:1px solid #cbdcd4}blockquote{margin:12px 0;padding:12px 18px;border-left:4px solid #548875;background:#f4f8f6;white-space:pre-wrap;overflow-wrap:anywhere}select,button{font:inherit;padding:10px;border:1px solid #719386;border-radius:6px}small{color:#526c62}h2{font-size:20px;white-space:pre-wrap;overflow-wrap:anywhere}.tag{color:#874e00;font-weight:bold}</style>
<h1>混合角色证据 · 本机人工审阅</h1>
<p>本页只读业务数据，保留完整摘要、原断言类型及全部引用。请你亲自判断，不代填、不调用模型。原始候选未入库，在线开关未开启。本页含聊天信息，只留本机。</p>
<p>每项默认未标注。导出只包含你已选择的结论、ID 与版本，不含摘要和原文。关闭页面会丢失尚未导出的选择。</p>
<button id="export">导出已选择的人工结论</button><p id="status" role="status"></p><main id="cards"></main>
<script id="data" type="application/json">DATA</script>
<script nonce="NONCE">
const rows=JSON.parse(document.getElementById('data').textContent),fields=[];
const roles={self:'本人',peer:'对方',system:'系统',unknown:'未知'},answers={'支持':'supported','部分支持':'partial','不支持':'unsupported','不确定':'uncertain'};
function add(parent,tag,text){const el=document.createElement(tag);el.textContent=text;parent.append(el);return el}
rows.forEach((r,i)=>{const card=add(document.getElementById('cards'),'article','');add(card,'p','混合角色证据 · 待人工核对').className='tag';add(card,'h2',r.summary);add(card,'small',`候选 ${i+1} · 主体：${roles[r.subject]||'第三方'} · 断言：直接陈述 · 全部 ${r.evidence.length} 条证据`);
r.evidence.forEach(e=>{add(card,'small',`消息 #${e.id} · 版本 ${e.revision} · ${roles[e.role]||'未知'}`);add(card,'blockquote',e.text)});
const label=add(card,'label','完整证据是否支持摘要：'),select=document.createElement('select');select.setAttribute('aria-label',`候选 ${i+1} 人工结论`);label.append(select);const unset=add(select,'option','请选择，不默认给结论');unset.value='';Object.entries(answers).forEach(([title,value])=>{add(select,'option',title).value=value});fields.push(select)});
document.getElementById('status').textContent=`共 ${rows.length} 条候选，尚无人工结论。`;
document.getElementById('export').onclick=()=>{const labels=rows.flatMap((r,i)=>fields[i].value?[{artifact_id:r.artifact_id,request_id:r.request_id,memory_index:r.memory_index,evidence:r.evidence.map(e=>({id:e.id,revision:e.revision,role:e.role})),label:fields[i].value,annotation_source:'human_local_preview',at:new Date().toISOString()}]:[]);
if(!labels.length){document.getElementById('status').textContent='尚未选择结论，没有导出文件。';return}
const url=URL.createObjectURL(new Blob([JSON.stringify({version:1,labels},null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='mixed-role-human-labels.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);document.getElementById('status').textContent=`已导出 ${labels.length} 条你选择的结论；没有写入业务数据库。`};
</script></html>'''.replace('NONCE',nonce).replace('DATA',data)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(html,encoding='utf-8')
    return {'cards':len(cards),'reports':reports}


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=root/'private/mixed-role-review/审阅页面.html')
    args=parser.parse_args()
    if not args.output.resolve().is_relative_to(root/'private'):parser.error('真实审阅页面只保存到本机 private/ 下')
    result=generate(root/'data/memory.sqlite3',args.output)
    (args.output.parent/'replay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'cards':result['cards'],'windows':len(result['reports']),'output':str(args.output)},ensure_ascii=True))
