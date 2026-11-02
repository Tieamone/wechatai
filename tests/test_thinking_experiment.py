from collections import Counter
import json
import pytest
from backend import ai_settings,diagnostics,usage
from backend.model_contract import issue_request
from backend.store import connect
from test_core import client
from test_model_contract import prepared
from tools.thinking_experiment import MODEL,prepare,execute


def fixture_root(client,tmp_path):
    path=client.app.state.db_path;request=prepared(client)
    entries=[]
    for i in range(1,5):
        if i>1:
            with connect(path) as db:db.execute("INSERT INTO job(initialization_id,idempotency_key,state,input_json,result_json) SELECT initialization_id,?,'prepared',input_json,result_json FROM job WHERE id=1",('synthetic-'+str(i),))
            request=issue_request(path,i)
        entries.append({'job_id':i,'request_id':request['request_id'],'messages':[{'id':1,'revision':1}]})
    diagnostics.save(path,entries[0]['request_id'],diagnostics.failure(422,'synthetic','D304'),{'raw':'{"memories":[]}'})
    root=tmp_path/'fixture';data=root/'data';data.mkdir(parents=True)
    # Online backup keeps the test copy complete without copying an open WAL.
    import sqlite3,shutil
    with connect(path) as db:
        db.execute('UPDATE budget_policy SET enabled=1,token_limit=1000000')
    with connect(path) as db,sqlite3.connect(data/'memory.sqlite3') as destination:db.backup(destination)
    shutil.copytree(path.parent/'model-rejections',data/'model-rejections')
    evaluation=data/'extraction-eval';evaluation.mkdir()
    (evaluation/'frozen-windows-v1.jsonl').write_text('\n'.join(json.dumps(x) for x in [{'type':'header'},*entries]),encoding='utf-8')
    previous=root/'private/v3-comparison-20261102';previous.mkdir(parents=True)
    (previous/'receipt.json').write_text(json.dumps({'results':[{'job_id':i,'validation':{'audit':{'returned_memories':0 if i==2 else 1}}} for i in range(1,5)]}),encoding='utf-8')
    (data/'ai-settings.json').write_text(json.dumps({'model':MODEL,'base_url':'https://synthetic.invalid','enabled':True,'key_encrypted':'synthetic-not-decryptable'}),encoding='utf-8')
    return root


def test_plan_is_readonly_and_covers_repetitions(client,tmp_path):
    root=fixture_root(client,tmp_path);path=root/'data/memory.sqlite3'
    before=path.read_bytes();config=(path.parent/'ai-settings.json').read_bytes()
    plan=prepare(root)
    assert path.read_bytes()==before and (path.parent/'ai-settings.json').read_bytes()==config
    assert len(plan['windows'])==4 and len(plan['sequence'])==12 and plan['token_limit']==40000
    assert {w['selection'] for w in plan['windows']}=={'v2_d304','v3_empty','v3_nonempty','remaining_median_message_count'}
    counts=Counter((s['job_id'],s['mode']) for s in plan['sequence'])
    assert sum(n==2 for n in counts.values())==4
    assert '咖啡' not in json.dumps(plan,ensure_ascii=False)
    with pytest.raises(ValueError):execute(root,plan,'wrong',root/'private/never-created')
    assert not (root/'private/never-created').exists()


def test_fake_sender_full_experiment_does_not_commit_memories(client,tmp_path):
    root=fixture_root(client,tmp_path);path=root/'data/memory.sqlite3';plan=prepare(root)
    with connect(path) as db:before={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('memory','memory_evidence','model_request','feedback')}
    calls=[]
    def sender(config,system,data):
        body=ai_settings.request_body(config,system,data);calls.append(body)
        usage.mark_sent();usage.settle({'prompt_tokens':100,'completion_tokens':100,'total_tokens':200,'completion_tokens_details':{'reasoning_tokens':0}})
        return '{"memories":[]}'
    report=execute(root,plan,plan['manifest_sha256'],root/'private/synthetic-run',sender=sender)
    assert report['state']=='completed' and len(calls)==12 and report['usage']['tokens']==2400
    assert sum('enable_thinking' in b for b in calls)==6
    assert report['disk_model_config_unchanged']
    with connect(path) as db:assert {t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in before}==before
    with pytest.raises(FileExistsError):execute(root,plan,plan['manifest_sha256'],root/'private/synthetic-run',sender=sender)
    assert len(calls)==12
