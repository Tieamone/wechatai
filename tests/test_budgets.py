import json
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from backend import budgets,usage,batches,ai_settings
from backend.store import initialize,connect
from backend.jobs import prepare_one
from test_core import client,batch
from test_ai_settings import CONFIG


def configured(tmp_path,*,money=False):
    path=tmp_path/'memory.sqlite3';initialize(path)
    config={'model':'fixture','base_url':'https://fixture.invalid'}
    with connect(path) as db:
        db.execute("UPDATE budget_policy SET enabled=1,token_limit=5000,model=?,base_url=?,input_rate=1000000,output_rate=1000000,amount_limit=? WHERE id=1",(config['model'],config['base_url'],10000 if money else None))
    return path,config


@pytest.mark.parametrize('money',[False,True])
def test_two_entrances_reserve_atomically(tmp_path,money):
    path,config=configured(tmp_path,money=money)
    with connect(path) as db:
        db.execute('UPDATE auto_control SET enabled=1')
        if money:db.execute('UPDATE budget_policy SET token_limit=1000000,amount_limit=5000')
    def call(kind):
        try:return usage.invoke(path,config,'test',{},kind=kind,sender=lambda *a:'ok')
        except HTTPException as error:return error.status_code
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(call,('manual','automatic')))
    assert results.count('ok')==1 and results.count(429)==1
    with connect(path) as db:
        assert budgets.totals(db)['amount' if money else 'tokens']<=5000


def test_settled_uses_actual_tokens_and_snapshot_rates(tmp_path):
    path,config=configured(tmp_path,money=True)
    def send(*args):
        usage.mark_sent()
        # Later price edits must not change this call's settlement.
        with connect(path) as db:db.execute('UPDATE budget_policy SET input_rate=9000000,output_rate=9000000')
        usage.settle({'prompt_tokens':5,'completion_tokens':7,'total_tokens':12})
        return 'ok'
    usage.invoke(path,config,'test',{},kind='manual',sender=send)
    with connect(path) as db:
        assert budgets.totals(db)['tokens']==12
        assert budgets.totals(db)['amount']==12
        row=db.execute('SELECT * FROM usage_ledger').fetchone()
        assert row['reserved_input']+row['reserved_output']>12


def test_money_missing_usage_and_unpriced_are_not_free(tmp_path):
    path,config=configured(tmp_path,money=True)
    with connect(path) as db:db.execute('UPDATE budget_policy SET amount_limit=5000')
    usage.invoke(path,config,'test',{},kind='manual',sender=lambda *a:'ok')
    sender=Mock()
    with pytest.raises(HTTPException) as error:usage.invoke(path,config,'test',{},kind='manual',sender=sender)
    assert error.value.status_code==429;sender.assert_not_called()
    with connect(path) as db:
        assert budgets.totals(db)['amount']>0
        db.execute('UPDATE usage_ledger SET reserved_cost=NULL')
        db.execute('UPDATE budget_policy SET amount_limit=1000000,token_limit=1000000')
    with pytest.raises(HTTPException):usage.invoke(path,config,'test',{},kind='manual',sender=sender)
    sender.assert_not_called()


def test_release_and_recovery_and_rolling_window(tmp_path):
    path,config=configured(tmp_path)
    def fail(*args):raise ValueError('before transport')
    with pytest.raises(ValueError):usage.invoke(path,config,'test',{},kind='manual',sender=fail)
    with connect(path) as db:assert budgets.totals(db)['tokens']==0
    # Simulate termination after reserve, before a transport outcome can be recorded.
    with connect(path) as db:
        db.execute("INSERT INTO usage_ledger(kind,model,at,state,reserved_input,reserved_output,lease_until) VALUES('manual','fixture',?,'reserved',100,4096,?)",(time.time(),time.time()-1))
    budgets.recover_expired(path)
    with connect(path) as db:
        assert db.execute('SELECT state FROM usage_ledger ORDER BY id DESC LIMIT 1').fetchone()[0]=='unknown'
        assert budgets.totals(db)['tokens']==4196
        db.execute('UPDATE usage_ledger SET at=?',(time.time()-86401,))
        assert budgets.totals(db)['tokens']==0
        db.execute("INSERT INTO usage_ledger(kind,model,at,state,reserved_input,reserved_output) VALUES('manual','fixture',?,'reserved',100,4096)",(time.time(),))
    budgets.recover_expired(path)
    with connect(path) as db:
        assert db.execute('SELECT state FROM usage_ledger ORDER BY id DESC LIMIT 1').fetchone()[0]=='unknown'


def history(client):
    client.put('/api/ai/settings',json=CONFIG).raise_for_status()
    body=batch();body['messages']=[{**body['messages'][0],'source':{**body['messages'][0]['source'],'id':str(i)},'text':'合成闲聊'+str(i)} for i in range(30)]
    client.post('/api/import',json=body).raise_for_status()
    init=client.post('/api/initializations',json={'mode':'full'}).json()['id']
    path=client.app.state.db_path
    for _ in range(4):prepare_one(path)
    return path,init


def start(client,init,**changes):
    return client.post('/api/batches',json={'initialization_id':init,'request_key':'synthetic-request-key-01','max_windows':2,'token_limit':100000,**changes})


def test_batch_idempotency_window_limit_and_unknown_usage(client,monkeypatch):
    path,init=history(client)
    first=start(client,init).json()['id']
    assert start(client,init).json()['id']==first
    assert start(client,init,request_key='synthetic-request-key-02').status_code==409
    sender=Mock(return_value='{"memories":[]}');monkeypatch.setattr(ai_settings,'complete',sender)
    for _ in range(5):batches.process_next(path)
    assert sender.call_count==2
    row=client.get('/api/batches').json()[0]
    assert row['state']=='completed' and row['counts']=={'done':2}
    assert row['usage']['tokens']>0


def test_batch_exhaustion_stops_before_sender_and_can_be_cancelled(client,monkeypatch):
    path,init=history(client)
    run=start(client,init,token_limit=4096).json()['id']
    sender=Mock();monkeypatch.setattr(ai_settings,'complete',sender)
    assert batches.process_next(path)['state']=='pending'
    sender.assert_not_called()
    row=client.get('/api/batches').json()[0]
    assert row['state']=='paused' and row['reason']=='budget_limit'
    assert row['usage']['calls']==0
    assert client.post(f'/api/batches/{run}/control',json={'action':'cancel'}).status_code==200


def test_batch_money_requires_model_binding(client):
    _,init=history(client)
    assert start(client,init,amount_limit=1000000).status_code==409
    settings=budgets.Settings(model=CONFIG['model'],base_url=CONFIG['base_url'],input_rate=1000000,output_rate=1000000).model_dump()
    client.put('/api/budgets',json=settings).raise_for_status()
    assert start(client,init,amount_limit=1).status_code==200


def test_model_switch_blocks_money_but_allows_disabling_policy(client):
    history(client)
    settings=budgets.Settings(enabled=True,model=CONFIG['model'],base_url=CONFIG['base_url'],input_rate=1000000,output_rate=1000000,amount_limit=1000000).model_dump()
    client.put('/api/budgets',json=settings).raise_for_status()
    client.put('/api/ai/settings',json={**CONFIG,'model':'different-model','api_key':''}).raise_for_status()
    assert not client.get('/api/budgets').json()['price_matches']
    assert client.put('/api/budgets',json=settings).status_code==409
    settings['enabled']=False
    client.put('/api/budgets',json=settings).raise_for_status()


def test_restart_does_not_retry_claimed_window(client,monkeypatch):
    path,init=history(client);start(client,init).raise_for_status()
    with connect(path) as db:
        db.execute("UPDATE batch_step SET state='processing',lease_until=0 WHERE job_id=(SELECT min(job_id) FROM batch_step)")
    sender=Mock();monkeypatch.setattr(ai_settings,'complete',sender)
    batches.process_next(path);batches.process_next(path)
    sender.assert_not_called()
    row=client.get('/api/batches').json()[0]
    assert row['state']=='paused' and row['counts']['unknown']==1


def test_global_and_batch_limits_both_apply(client,monkeypatch):
    path,init=history(client);start(client,init).raise_for_status()
    client.put('/api/budgets',json=budgets.Settings(enabled=True,token_limit=4096).model_dump()).raise_for_status()
    sender=Mock();monkeypatch.setattr(ai_settings,'complete',sender)
    batches.process_next(path);sender.assert_not_called()


def test_batch_reservation_concurrency_and_pause_before_send(client):
    path,init=history(client);run=start(client,init,token_limit=5000).json()['id']
    config=ai_settings.load(path.parent)
    def call(_):
        context=budgets.BATCH.set(run)
        try:
            try:return usage.invoke(path,config,'test',{},kind='manual',sender=lambda *args:'ok')
            except HTTPException:return 'blocked'
        finally:budgets.BATCH.reset(context)
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(call,range(2)))==['blocked','ok']
    with connect(path) as db:
        assert budgets.totals(db,batch_id=run)['tokens']<=5000
        db.execute('UPDATE batch_run SET token_limit=100000 WHERE id=?',(run,))
    def paused(*args):
        client.post(f'/api/batches/{run}/control',json={'action':'pause'}).raise_for_status()
        usage.mark_sent()
    context=budgets.BATCH.set(run)
    try:
        with pytest.raises(HTTPException):usage.invoke(path,config,'test',{},kind='manual',sender=paused)
    finally:budgets.BATCH.reset(context)
    with connect(path) as db:assert db.execute('SELECT state FROM usage_ledger ORDER BY id DESC LIMIT 1').fetchone()[0]=='released'


def test_cancellation_preserves_completed_deferred_messages(client):
    path,init=history(client)
    with connect(path) as db:
        db.execute("INSERT INTO deferred_message SELECT id,revision,'expired',? FROM message",(init,))
        db.execute("UPDATE job SET state='validated_model' WHERE initialization_id=? AND id=(SELECT min(id) FROM job)",(init,))
        complete=db.execute("SELECT count(*) FROM deferred_message d WHERE EXISTS(SELECT 1 FROM job_message x JOIN job j ON j.id=x.job_id WHERE j.initialization_id=d.initialization_id AND x.message_id=d.message_id) AND NOT EXISTS(SELECT 1 FROM job_message x JOIN job j ON j.id=x.job_id WHERE j.initialization_id=d.initialization_id AND x.message_id=d.message_id AND j.state!='validated_model')").fetchone()[0]
        assert complete>0
    client.post(f'/api/initializations/{init}/control',json={'action':'cancel'}).raise_for_status()
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM deferred_message WHERE initialization_id=?',(init,)).fetchone()[0]==complete
        assert db.execute("SELECT count(*) FROM job WHERE state='validated_model'").fetchone()[0]==1


def test_v20_restore_and_context_index(tmp_path):
    import sqlite3,shutil
    from contextlib import closing
    from schema_helpers import remove_v21
    path=tmp_path/'memory.sqlite3';initialize(path)
    with connect(path) as db:
        remove_v21(db);db.execute('PRAGMA user_version=20')
        db.execute("INSERT INTO conversation VALUES('fixture','fixture','fixture')")
        db.execute("INSERT INTO usage_ledger(kind,model,at,state,reserved_input,reserved_output) VALUES('manual','fixture',123456,'unknown',100,4096)")
    initialize(path)
    backup=next((tmp_path/'backups').glob('migration-auto-*.sqlite3'))
    with closing(sqlite3.connect(backup)) as db:assert db.execute('PRAGMA user_version').fetchone()[0]==20
    restored=tmp_path/'restored/memory.sqlite3';restored.parent.mkdir();shutil.copy2(backup,restored)
    initialize(restored)
    with connect(restored) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==21
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert tuple(db.execute('SELECT at,reserved_input,reserved_output FROM usage_ledger').fetchone())==(123456,100,4096)
        plan=' '.join(r[-1] for r in db.execute("EXPLAIN QUERY PLAN SELECT * FROM message WHERE conversation_id=? AND event_utc>=? AND (event_utc<? OR (event_utc=? AND id<=?)) ORDER BY event_utc DESC,id DESC LIMIT 8",('fixture','2026-01-01','2026-02-01','2026-02-01',10)))
        assert 'message_conversation_time' in plan and 'TEMP B-TREE' not in plan
