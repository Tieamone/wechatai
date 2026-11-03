"""Local, read-only evidence pack. Existing accuracy actions are not value labels."""
from collections import Counter
from contextlib import closing
import argparse
import html
import json
from pathlib import Path
import secrets
import sqlite3
from backend.usage import token_details


def build(root,output):
    path=root/'data/memory.sqlite3'
    frozen=[json.loads(x) for x in (root/'data/extraction-eval/frozen-windows-v1.jsonl').read_text(encoding='utf-8').splitlines()][1:]
    comparison=json.loads((root/'private/v3-comparison-20261102/receipt.json').read_text(encoding='utf-8'))
    empty={r['job_id'] for r in comparison['results'] if r.get('validation',{}).get('audit',{}).get('returned_memories')==0}
    report={'value_labels':0,'valuable_memories_per_10000_tokens':None,'v3_recall_of_valuable_v2':None,
            'v3_reasoning_coverage':{'known':0,'calls':len(comparison['results'])},'windows':[],'retained_usage':[]}
    sections=[]
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        report['database_version']=db.execute('PRAGMA user_version').fetchone()[0]
        report['all_current_reviews']=db.execute("SELECT count(*) FROM memory_review r JOIN memory m ON m.id=r.memory_id WHERE m.version=r.confirmed_version AND m.status='active'").fetchone()[0]
        report['feedback_actions']=dict(Counter(r[0] for r in db.execute('SELECT action FROM feedback')))
        report['ledger_columns']=[r[1] for r in db.execute('PRAGMA table_info(usage_ledger)')]
        for artifact in sorted((root/'data/model-rejections').glob('rejection-*.json')):
            item=json.loads(artifact.read_text(encoding='utf-8'))
            if item.get('raw_kind')!='http_json' or not item.get('raw'):continue
            payload=json.loads(item['raw']);u=payload.get('usage',{})
            report['retained_usage'].append({'artifact_id':item['id'],'request_id':item['request_id'],
                'input_tokens':u.get('prompt_tokens') if type(u.get('prompt_tokens')) is int else None,
                'output_tokens':u.get('completion_tokens') if type(u.get('completion_tokens')) is int else None,**token_details(u)})
        for entry in frozen:
            window={'job_id':entry['job_id'],'v3_empty':entry['job_id'] in empty,'memories':[]}
            message_html=[]
            for ref in entry['messages']:
                row=db.execute('SELECT * FROM message WHERE id=?',(ref['id'],)).fetchone()
                if not row or row['revision']!=ref['revision']:raise ValueError('冻结原文已修订，停止生成，不能冒充原版本')
                role={'peer':'对方','self':'本人','system':'系统'}.get(row['sender_role'],'未知')
                message_html.append(f"<blockquote><small>#{row['id']} · v{row['revision']} · {role}</small><p>{html.escape(row['text'])}</p></blockquote>")
            memory_html=[]
            for mid in entry['model_memory_ids']:
                memory=db.execute('SELECT * FROM memory WHERE id=?',(mid,)).fetchone()
                review=db.execute('SELECT * FROM memory_review WHERE memory_id=?',(mid,)).fetchone()
                evidence=[dict(r) for r in db.execute('SELECT message_id,message_revision,span_start,span_end FROM memory_evidence WHERE memory_id=? ORDER BY message_id,span_start',(mid,))]
                fresh=all(db.execute('SELECT revision FROM message WHERE id=?',(e['message_id'],)).fetchone()[0]==e['message_revision'] for e in evidence)
                fresh=fresh and not db.execute('SELECT 1 FROM memory_evidence e JOIN suppressed_evidence s ON s.message_id=e.message_id AND s.message_revision=e.message_revision WHERE e.memory_id=?',(mid,)).fetchone()
                confirmed=bool(review and memory['status']=='active' and review['confirmed_version']==memory['version'] and fresh)
                actions=[]
                for f in db.execute('SELECT * FROM feedback WHERE memory_id=? ORDER BY id',(mid,)):
                    snapshot=json.loads(f['evidence_json'])
                    valid=bool(f['valid'] and f['memory_version']==memory['version'] and fresh and sorted(snapshot,key=lambda e:json.dumps(e,sort_keys=True))==sorted(evidence,key=lambda e:json.dumps(e,sort_keys=True)))
                    actions.append({'id':f['id'],'memory_version':f['memory_version'],'action':f['action'],'valid':valid})
                legacy=db.execute('SELECT count(*) FROM correction WHERE memory_id=?',(mid,)).fetchone()[0]
                latest=next((f['action'] for f in reversed(actions) if f['valid'] and f['action'] in ('low_value','clear_low_value')),None)
                value='low_value' if latest=='low_value' else None
                report['value_labels']+=int(value is not None)
                window['memories'].append({'memory_id':mid,'memory_version':memory['version'],'status':memory['status'],'confirmed_current':confirmed,
                    'evidence_fresh':bool(fresh),'evidence':evidence,'feedback':actions,'unversioned_corrections':legacy,'value_label':value})
                label='当前版本已确认准确性（不等于有价值）' if confirmed else '无当前版本确认，未标价值'
                if value:label+='；用户已标记：准确但不值得保留'
                if legacy:label+='；有无版本绑定的旧更正记录，不能直接作金标准'
                memory_html.append(f"<li>记忆 #{mid} v{memory['version']}：{html.escape(memory['summary'])}<br><small>{label}；引用 "+', '.join('#'+str(e['message_id'])+' v'+str(e['message_revision']) for e in evidence)+'</small></li>')
            report['windows'].append(window)
            if window['v3_empty']:
                sections.append(f"<details id='w{entry['job_id']}'><summary>窗口 #{entry['job_id']} · v3 返回空 · v2 入库候选 {len(memory_html)} 条</summary><h3>完整冻结原文</h3>"+''.join(message_html)+'<h3>对应 v2 候选与已有记录</h3><ul>'+''.join(memory_html)+'</ul></details>')
    valid_usage=[r for r in report['retained_usage'] if r['reasoning_tokens'] is not None and r['output_tokens'] is not None]
    reasoning=sum(r['reasoning_tokens'] for r in valid_usage);out=sum(r['output_tokens'] for r in valid_usage)
    memories=[m for w in report['windows'] for m in w['memories']]
    report['summary']={'empty_windows':len(empty),'v2_memory_count':len(memories),'v2_current_confirmed':sum(m['confirmed_current'] for m in memories),
        'v2_valid_feedback':sum(f['valid'] for m in memories for f in m['feedback']),'unversioned_corrections':sum(m['unversioned_corrections'] for m in memories),
        'usage_samples':len(valid_usage),'known_reasoning_tokens':reasoning,'covered_output_tokens':out,'sample_reasoning_fraction':reasoning/out if out else None}
    nonce=secrets.token_hex(16)
    document=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-{nonce}'; connect-src 'none'; base-uri 'none'; form-action 'none'"><title>空窗口只读核查材料</title><style nonce="{nonce}">body{{max-width:1000px;margin:30px auto;padding:0 18px;font:16px/1.7 system-ui;background:#f1f6f3;color:#193c31}}details{{background:white;border:1px solid #b8cdc2;border-radius:8px;padding:18px;margin:20px 0}}summary{{cursor:pointer;font-weight:bold}}blockquote{{margin:12px 0;border-left:4px solid #6b9984;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere}}small{{color:#52665d}}li{{margin:18px 0;overflow-wrap:anywhere}}</style><h1>11 个空窗口 · 本机只读核查材料</h1><p>这里只整理完整原文、对应 v2 候选和已有确认／更正，不新增价值标注、不代判漏提。确认准确性不等于长期价值。现有记录不足时，质量指标保持未知。本页不调用模型、不写数据库，含明文聊天，只留本机。</p><p>展开窗口后逐条看原文。材料中的摘要仍为模型候选，不是标准答案。旧更正缺少版本绑定时不作有效标签。</p>'''+''.join(sections)+'</html>'
    output.mkdir(parents=True,exist_ok=True)
    (output/'空窗口核查.html').write_text(document,encoding='utf-8')
    (output/'evidence-index.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report['summary']


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    print(json.dumps(build(root,root/'private/usage-quality-audit'),ensure_ascii=True))
