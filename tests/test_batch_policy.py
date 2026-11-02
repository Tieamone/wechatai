from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from backend import ai_settings,batches,budgets,usage
from backend.store import connect,initialize,SCHEMA_VERSION
from test_core import client
from test_budgets import history,start


@pytest.mark.parametrize('money',[False,True])
def test_all_batches_share_atomic_rolling_fraction(client,money):
    path,init=history(client);run=start(client,init).json()['id']
    config=ai_settings.load(path.parent)
    with connect(path) as db:
        second=db.execute("INSERT INTO initialization(mode,cutoff) VALUES('full','2026-01-01')").lastrowid
        other=db.execute("INSERT INTO batch_run(initialization_id,request_key,request_hash,max_windows,token_limit,model,base_url,created_at) VALUES(?,'other-key','hash',2,100000,?,?,0)",(second,config['model'],config['base_url'])).lastrowid
        db.execute('UPDATE budget_policy SET enabled=1,token_limit=?,amount_limit=?,model=?,base_url=?,input_rate=1000000,output_rate=1000000',(100000 if money else 12000,12000 if money else None,config['model'],config['base_url']))
    def call(run_id):
        token=budgets.BATCH.set(run_id)
        try:
            try:return usage.invoke(path,config,'test',{},kind='manual',sender=lambda *a:'ok')
            except HTTPException as e:return e.status_code
        finally:budgets.BATCH.reset(token)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(call,(run,other)))
    assert results.count('ok')==1 and results.count(429)==1
    # A non-batch call can use the reserved remainder.
    assert usage.invoke(path,config,'test',{},kind='manual',sender=lambda *a:'ok')=='ok'
    with connect(path) as db:
        assert budgets.totals(db,batch_only=True)['amount' if money else 'tokens']<=8400


def test_privacy_skips_without_pausing_following_window(client,monkeypatch):
    path,init=history(client);start(client,init).raise_for_status()
    with connect(path) as db:db.execute("UPDATE job SET state='blocked_privacy' WHERE id=(SELECT min(job_id) FROM batch_step)")
    sender=Mock(return_value='{"memories":[]}');monkeypatch.setattr(ai_settings,'complete',sender)
    assert batches.process_next(path)['state']=='skipped_privacy';sender.assert_not_called()
    assert batches.process_next(path)['state']=='done';sender.assert_called_once()
    row=client.get('/api/batches').json()[0]
    assert row['state']=='completed' and row['skipped'][0]['reason']=='privacy_blocked'


def test_idle_only_waits_and_rechecks_before_headers(client,monkeypatch):
    path,init=history(client);run=start(client,init,idle_only=True).json()['id']
    monkeypatch.setattr(budgets,'idle',lambda *a:False)
    sender=Mock();monkeypatch.setattr(ai_settings,'complete',sender)
    assert not batches.process_next(path)['processed'];sender.assert_not_called()
    assert client.get('/api/batches').json()[0]['waiting_idle']
    monkeypatch.setattr(budgets,'idle',lambda *a:True)
    def cross_boundary(*args):
        monkeypatch.setattr(budgets,'idle',lambda *a:False)
        usage.mark_sent()
        raise AssertionError('must not send')
    monkeypatch.setattr(ai_settings,'complete',cross_boundary)
    assert batches.process_next(path)['state']=='pending'
    with connect(path) as db:
        assert db.execute('SELECT state FROM usage_ledger').fetchone()[0]=='released'
        assert db.execute('SELECT state FROM batch_run WHERE id=?',(run,)).fetchone()[0]=='active'


def test_v23_migration_backs_up_and_preserves_rows(tmp_path):
    import shutil,sqlite3
    path=tmp_path/'memory.sqlite3';initialize(path)
    with connect(path) as db:
        db.execute('ALTER TABLE budget_policy DROP COLUMN batch_percent')
        db.execute('ALTER TABLE batch_run DROP COLUMN idle_only')
        db.execute('ALTER TABLE batch_step DROP COLUMN reason')
        db.execute('PRAGMA user_version=23')
        db.execute('UPDATE budget_policy SET token_limit=54321')
    initialize(path)
    backup=next((tmp_path/'backups').glob('migration-auto-*.sqlite3'))
    with sqlite3.connect(backup) as db:assert db.execute('PRAGMA user_version').fetchone()[0]==23
    restored=tmp_path/'restored/memory.sqlite3';restored.parent.mkdir();shutil.copy2(backup,restored);initialize(restored)
    with connect(restored) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert tuple(db.execute('SELECT token_limit,batch_percent FROM budget_policy').fetchone())==(54321,70)
