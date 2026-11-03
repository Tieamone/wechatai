"""Offline calibration; business database is read-only and no labels are inferred."""
import argparse,hashlib,json,secrets
from pathlib import Path
from backend import diagnostics


def generate(path,output):
    cards=[]
    with diagnostics.connection(path,True) as db:
        db.execute('BEGIN')
        for row in db.execute("SELECT * FROM memory m WHERE status='active' AND NOT EXISTS(SELECT 1 FROM memory_review r WHERE r.memory_id=m.id AND r.confirmed_version=m.version) ORDER BY m.id").fetchall():
            evidence=[dict(r) for r in db.execute('''SELECT e.message_id,e.message_revision,e.span_start,e.span_end,m.revision current_revision,m.sender_role,m.text
                FROM memory_evidence e JOIN message m ON m.id=e.message_id WHERE memory_id=? ORDER BY m.id,e.span_start''',(row['id'],))]
            if any(e['current_revision']!=e['message_revision'] for e in evidence):raise ValueError('存在失效证据，请先核对来源；未生成冒充旧版本的原文')
            cards.append({'memory_id':row['id'],'memory_version':row['version'],'summary':row['summary'],'evidence':evidence})
    snapshot=[{'memory_id':r['memory_id'],'memory_version':r['memory_version'],'evidence':[{k:e[k] for k in ('message_id','message_revision','span_start','span_end')} for e in r['evidence']]} for r in cards]
    fingerprint=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()
    payload=json.dumps({'snapshot_sha256':fingerprint,'cards':cards},ensure_ascii=True).replace('<','\\u003c')
    nonce=secrets.token_hex(16)
    template=(Path(__file__).with_name('calibration_template.html')).read_text(encoding='utf-8')
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(template.replace('NONCE_VALUE',nonce).replace('SNAPSHOT_DATA',payload),encoding='utf-8')
    return {'cards':len(cards),'snapshot_sha256':fingerprint,'human_labels_filled':0}


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1];p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=root/'private/calibration/校准标注.html');a=p.parse_args()
    if not a.output.resolve().is_relative_to(root/'private'):p.error('含原文页面仅保存到本机 private/ 下')
    result=generate(root/'data/memory.sqlite3',a.output)
    (a.output.parent/'snapshot.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True))
