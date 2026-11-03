from backend.store import SCHEMA_VERSION
import shutil
import sqlite3
import json
from unittest.mock import Mock
import pytest
from backend import usage,budgets,ai_settings
from backend.store import initialize,connect
from backend.associations import rank_candidates
from backend.automatic import process_next
from backend.notifications import pending_associations,deliver_association
from test_core import client
from test_automatic import setup,ready,responder


def test_retention_never_saves_provider_reasoning_text():
    raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"memories":[]}','reasoning_content':'secret thought','reasoning_details':[{'text':'secret thought'}]}}],
                    'usage':{'prompt_tokens':5,'completion_tokens':7,'total_tokens':12,'completion_tokens_details':{'reasoning_tokens':6}}})
    retained=ai_settings.retained_response(raw)
    assert b'secret thought' not in retained and b'reasoning_content' not in retained
    assert json.loads(retained)['usage']['completion_tokens_details']['reasoning_tokens']==6
    assert ai_settings.parse_response(retained)=='{"memories":[]}'
    assert ai_settings.retained_response('broken json') is None


@pytest.mark.parametrize('details,expected',[
 ({},(None,None,None)),
 ({'completion_tokens_details':{'reasoning_tokens':0,'text_tokens':0},'prompt_tokens_details':{'cached_tokens':0}},(0,0,0)),
 ({'completion_tokens_details':{'reasoning_tokens':6,'text_tokens':7},'prompt_tokens_details':{'cached_tokens':3}},(6,3,7)),
 ({'completion_tokens_details':{'reasoning_tokens':True,'text_tokens':8},'prompt_tokens_details':{'cached_tokens':-1}},(None,None,None)),
 ({'completion_tokens_details':'malformed'},(None,None,None)),
])
def test_optional_counters_never_add_to_total(tmp_path,details,expected):
    path=tmp_path/'memory.sqlite3';initialize(path)
    def sender(*args):
        usage.mark_sent();usage.settle({'prompt_tokens':5,'completion_tokens':7,'total_tokens':12,**details});return 'ok'
    usage.invoke(path,{'model':'synthetic'},'test',{},kind='manual',sender=sender)
    with connect(path) as db:
        assert tuple(db.execute('SELECT reasoning_tokens,cached_input_tokens,reported_text_tokens FROM usage_ledger').fetchone())==expected
        assert budgets.totals(db)['tokens']==12
        summary=usage.detail_totals(db,0)
        assert summary['reasoning_known']==int(expected[0] is not None)


def test_v21_backup_restore_preserves_ledger_and_unknown_details(tmp_path):
    path=tmp_path/'memory.sqlite3';initialize(path)
    with connect(path) as db:
        from schema_helpers import remove_pricing
        remove_pricing(db)
        for name in ('reasoning_tokens','cached_input_tokens','reported_text_tokens'):db.execute('ALTER TABLE usage_ledger DROP COLUMN '+name)
        db.execute('PRAGMA user_version=21')
        db.execute("INSERT INTO usage_ledger(kind,model,at,state,reserved_input,reserved_output,input_tokens,output_tokens) VALUES('manual','synthetic',123,'settled',100,4096,5,7)")
    initialize(path)
    backup=next((tmp_path/'backups').glob('migration-auto-*.sqlite3'))
    with sqlite3.connect(backup) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==21
        assert 'reasoning_tokens' not in {r[1] for r in db.execute('PRAGMA table_info(usage_ledger)')}
    restored=tmp_path/'restore/memory.sqlite3';restored.parent.mkdir();shutil.copy2(backup,restored)
    initialize(restored)
    with connect(restored) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
        assert tuple(db.execute('SELECT at,input_tokens,output_tokens,reasoning_tokens FROM usage_ledger').fetchone())==(123,5,7,None)


def test_reported_is_recalled_judged_and_delivered(client,monkeypatch):
    path,body=setup(client)
    with connect(path) as db:db.execute("UPDATE memory SET assertion='reported' WHERE id=1")
    client.post('/api/import',json=body).raise_for_status();ready(path)
    with connect(path) as db:
        trigger=db.execute("SELECT id FROM message WHERE source_id='2'").fetchone()[0]
        before=rank_candidates(db,trigger)
        db.execute("UPDATE memory SET assertion='direct' WHERE id=1")
        assert rank_candidates(db,trigger)==before
        db.execute("UPDATE memory SET assertion='reported' WHERE id=1")
    calls=[];monkeypatch.setattr(ai_settings,'complete',responder(calls))
    assert process_next(path)['state']=='done'
    assert any(call.get('assertion')=='reported' for call in calls)
    candidate=pending_associations(path,'synthetic-channel')[0]
    sender=Mock(return_value='accepted')
    assert deliver_association(path,candidate,'synthetic-channel',sender)=='accepted'
    sender.assert_called_once()


def test_existing_accuracy_review_is_not_a_value_label(client,tmp_path):
    from test_core import batch
    from test_memories import draft
    from tools.extraction_review_materials import build
    client.post('/api/import',json=batch()).raise_for_status()
    client.post('/api/memories',json=draft()).raise_for_status()
    client.post('/api/memories/1/review/confirm',json={'expected_version':1}).raise_for_status()
    root=tmp_path
    # The client database already lives at root; supply a read-only data entry point.
    data=root/'data';data.mkdir()
    shutil.copy2(client.app.state.db_path,data/'memory.sqlite3')
    evaluation=data/'extraction-eval';evaluation.mkdir()
    (evaluation/'frozen-windows-v1.jsonl').write_text(json.dumps({'type':'header'})+'\n'+json.dumps({'job_id':1,'messages':[{'id':1,'revision':1}],'model_memory_ids':[1]})+'\n',encoding='utf-8')
    private=root/'private/v3-comparison-20261102';private.mkdir(parents=True)
    (private/'receipt.json').write_text(json.dumps({'results':[{'job_id':1,'validation':{'audit':{'returned_memories':0}}}]}),encoding='utf-8')
    summary=build(root,root/'private/materials')
    report=json.loads((root/'private/materials/evidence-index.json').read_text(encoding='utf-8'))
    assert summary['v2_current_confirmed']==1 and summary['v2_valid_feedback']==1
    assert report['value_labels']==0 and report['valuable_memories_per_10000_tokens'] is None
    assert report['v3_recall_of_valuable_v2'] is None
