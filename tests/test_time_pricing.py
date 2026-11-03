from datetime import datetime
import shutil,sqlite3
import pytest
from backend import budgets,usage
from backend.store import initialize,connect,SCHEMA_VERSION
from test_budgets import configured
from schema_helpers import remove_pricing


@pytest.mark.parametrize('hour,expected',[(7,True),(8,False),(21,False),(22,True),(0,True)])
def test_shanghai_boundaries(hour,expected):
    at=datetime.fromisoformat(f'2026-11-02T{hour:02}:00:00+08:00').timestamp()
    assert budgets.idle(at)==expected


def test_reservation_busy_settlement_send_time_frozen(tmp_path,monkeypatch):
    path,config=configured(tmp_path,money=True)
    with connect(path) as db:
        db.execute('UPDATE budget_policy SET time_pricing=1,idle_input_rate=500000,idle_output_rate=500000')
    monkeypatch.setattr(budgets,'idle',lambda at=None:True)
    def send(*args):
        # Prices freeze at reserve, not when the caller edits settings mid-flight.
        with connect(path) as db:db.execute('UPDATE budget_policy SET idle_output_rate=900000')
        usage.mark_sent()
        usage.settle({'prompt_tokens':5,'completion_tokens':7,'total_tokens':12})
        return 'ok'
    usage.invoke(path,config,'test',{},kind='manual',sender=send)
    with connect(path) as db:
        r=db.execute('SELECT * FROM usage_ledger').fetchone()
        assert r['reserved_cost']>4000 and r['actual_cost']==6
        assert r['price_period']=='idle' and r['input_rate']==r['output_rate']==500000 and r['sent_at']
        assert '500000' in r['price_snapshot']


def test_empty_defaults_and_busy_upper_bound():
    default=budgets.Settings()
    assert not default.time_pricing and default.idle_input_rate is None
    with pytest.raises(ValueError):budgets.Settings(time_pricing=True)
    with pytest.raises(ValueError):budgets.Settings(time_pricing=True,model='fixture',base_url='fixture',input_rate=2,output_rate=8,idle_input_rate=3,idle_output_rate=4)


def test_v22_backup_and_restore(tmp_path):
    path=tmp_path/'memory.sqlite3';initialize(path)
    with connect(path) as db:
        remove_pricing(db);db.execute('PRAGMA user_version=22')
        db.execute("INSERT INTO usage_ledger(kind,model,at,state,reserved_input,reserved_output) VALUES('manual','fixture',123,'unknown',10,4096)")
    initialize(path)
    backup=next((tmp_path/'backups').glob('migration-auto-*.sqlite3'))
    with sqlite3.connect(backup) as db:assert db.execute('PRAGMA user_version').fetchone()[0]==22
    restored=tmp_path/'restored/memory.sqlite3';restored.parent.mkdir();shutil.copy2(backup,restored);initialize(restored)
    with connect(restored) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert tuple(db.execute('SELECT at,reserved_input,price_snapshot FROM usage_ledger').fetchone())==(123,10,None)
