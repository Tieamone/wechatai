from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend import policy
from backend.store import connect, initialize
from backend.notifications import deliver, pending
from backend.login import LoginCode


def stamp(age=0):
    return datetime.fromtimestamp(time.time()-age,timezone.utc).isoformat()


def message(number, age):
    return {'conversation_id':'fixture','source':{'adapter':'test','id':str(number),'partition':'test'},
            'sender_role':'peer','event_time':stamp(age),'text':'测试咖啡','revision':1}


@pytest.fixture
def api(tmp_path):
    app=create_app(tmp_path,'fixture-token')
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer fixture-token'}) as client:
        yield client


def ingest(api,messages,mode='live'):
    return api.post('/api/import',json={'account_ref':'fixture','contact_ref':'fixture','mode':mode,'messages':messages})


def test_mixed_import_and_manual_backlog(api):
    api.post('/api/watches',json={'user_text':'咖啡'})
    unknown=message(5,0);unknown['event_time']=None
    assert ingest(api,[message(1,400),message(2,0),message(3,-60),message(4,-180),unknown]).status_code==200
    path=api.app.state.db_path
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM message').fetchone()[0]==5
        assert db.execute('SELECT count(*) FROM deferred_message').fetchone()[0]==3
        assert not db.execute('SELECT 1 FROM reminder WHERE trigger_id IN (1,4,5)').fetchone()
    assert api.get('/api/automatic').json()['deferred']['pending']==3
    created=api.post('/api/automatic/deferred-range')
    assert created.status_code==200
    assert created.json()['message_count']==1
    stats=api.get('/api/automatic').json()['deferred']
    assert stats=={'pending':2,'assigned':1,'completed':0}
    api.post(f"/api/initializations/{created.json()['id']}/control",json={'action':'cancel'})
    assert api.get('/api/automatic').json()['deferred']['pending']==3


def test_age_boundaries():
    cfg=policy.Settings().model_dump();now=float(int(time.time()))
    def at(age):return datetime.fromtimestamp(now-age,timezone.utc).isoformat()
    assert policy.age_reason(at(300),now,cfg) is None
    assert policy.age_reason(at(301),now,cfg)=='expired'
    assert policy.age_reason(at(-120),now,cfg) is None
    assert policy.age_reason(at(-121),now,cfg)=='future_time'
    assert policy.age_reason(at(600),now,cfg,sending=True) is None
    assert policy.age_reason(at(901),now,cfg,sending=True)=='expired'


def test_expired_queue_without_enabled_model(api):
    from backend.automatic import process_next
    path=api.app.state.db_path
    with connect(path) as db:
        init=db.execute("INSERT INTO initialization(mode,cutoff) VALUES('auto',?)",(stamp(),)).lastrowid
        db.execute('UPDATE auto_control SET enabled=1,initialization_id=? WHERE id=1',(init,))
    assert ingest(api,[message(1,0)]).status_code==200
    with connect(path) as db:
        db.execute('UPDATE message SET event_utc=?',(stamp(1000),))
    assert process_next(path)=={'processed':False}
    with connect(path) as db:
        assert db.execute('SELECT state FROM auto_message').fetchone()[0]=='expired'
        assert db.execute('SELECT count(*) FROM deferred_message').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM auto_call').fetchone()[0]==0


def seed(tmp_path):
    path=tmp_path/'memory.sqlite3';initialize(path)
    with connect(path) as db:
        db.execute('UPDATE notification_policy SET quiet_enabled=0')
        db.execute("INSERT INTO conversation VALUES('fixture','fixture','fixture')")
        db.execute("INSERT INTO memory(id,summary,subject,polarity,assertion,temporal_kind,fingerprint) VALUES(1,'合成记忆','peer','positive','direct','fact','fixture')")
        for mid in (1,2,3):
            db.execute("INSERT INTO message(id,conversation_id,adapter,source_id,partition,sender_role,event_utc,text,type,revision) VALUES(?,'fixture','test',?,'test','peer',?,'合成文字','text',1)",(mid,str(mid),stamp()))
            db.execute('INSERT INTO watch_item(id,user_text,source_memory_id) VALUES(?,?,1)',(mid,'fixture'+str(mid)))
            db.execute("INSERT INTO reminder(id,trigger_id,watch_id,watch_revision,reason) VALUES(?,?,?,1,'fixture')",(mid,mid,mid))
        db.execute('INSERT INTO association(id,trigger_id,trigger_revision,memory_id,memory_version) VALUES(1,3,1,1,1)')
    return path


def test_concurrent_cross_watch_cooldown(tmp_path):
    path=seed(tmp_path);sender=Mock(return_value='accepted')
    with ThreadPoolExecutor(2) as pool:
        result=list(pool.map(lambda mid:deliver(path,mid,'test',sender),(1,2)))
    assert sorted(result)==['accepted','skipped_cooldown']
    assert sender.call_count==1
    assert len(pending(path,'test'))==1
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        assert policy.reserve(db,'ordinary',1,'test')=='skipped_cooldown'


def test_legacy_cooldown_survives_upgrade(tmp_path):
    path=seed(tmp_path)
    with connect(path) as db:
        db.execute("INSERT INTO notification_attempt(reminder_id,channel,state) VALUES(1,'test','uncertain')")
    sender=Mock()
    assert deliver(path,2,'test',sender)=='skipped_cooldown'
    sender.assert_not_called()


def test_old_ordinary_cooldown_blocks_watch(tmp_path):
    path=seed(tmp_path)
    with connect(path) as db:db.execute("INSERT INTO association_notification VALUES(1,'test','accepted',?)",(time.time(),))
    assert deliver(path,1,'test',Mock())=='skipped_cooldown'


def test_expiry_and_synthetic_do_not_claim_real_cooldown(tmp_path):
    path=seed(tmp_path);sender=Mock(return_value='accepted')
    with connect(path) as db:db.execute('UPDATE message SET event_utc=? WHERE id=1',(stamp(1000),))
    assert deliver(path,1,'test',sender)=='skipped_expired'
    assert deliver(path,1,'test',sender,synthetic=True)=='accepted'
    assert deliver(path,2,'test',sender)=='accepted'
    assert sender.call_count==2
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM notification_attempt WHERE reminder_id=1').fetchone()[0]==0


def test_quiet_skips_permanently_at_send_time(tmp_path,monkeypatch):
    path=seed(tmp_path);sender=Mock(return_value='accepted')
    with connect(path) as db:db.execute("UPDATE notification_policy SET quiet_enabled=1,quiet_start='23:00',quiet_end='08:00'")
    now=datetime.now().replace(hour=23,minute=30,second=0).timestamp()
    with connect(path) as db:db.execute('UPDATE message SET event_utc=?',(datetime.fromtimestamp(now,timezone.utc).isoformat(),))
    monkeypatch.setattr(policy.time,'time',lambda:now)
    assert deliver(path,1,'test',sender)=='skipped_quiet'
    assert deliver(path,2,'test',sender,synthetic=True)=='accepted'
    with connect(path) as db:db.execute('UPDATE notification_policy SET quiet_enabled=0')
    assert 1 not in pending(path,'test')
    assert sender.call_count==1


def test_policy_validation(api):
    body=policy.Settings().model_dump();body.update(quiet_start='08:00',quiet_end='08:00')
    assert api.put('/api/notification-policy',json=body).json()['settings']['quiet_enabled']==0
    body['validity_seconds']=60
    assert api.put('/api/notification-policy',json=body).status_code==422


def test_login_single_use_origin_and_expiry(api,monkeypatch):
    code=api.app.state.login_codes.issue()
    assert api.post('/api/session/exchange',json={'code':code},headers={'Origin':'https://example.invalid'}).status_code==403
    api.headers.pop('Authorization')
    assert api.post('/api/session/exchange',json={'code':code}).json()['token']=='fixture-token'
    assert api.post('/api/session/exchange',json={'code':code}).status_code==401
    assert api.get('/api/status').status_code==401
    code=api.app.state.login_codes.issue();api.app.state.login_codes.expires=0
    assert api.post('/api/session/exchange',json={'code':code}).status_code==401


def test_login_concurrent_consume():
    codes=LoginCode();code=codes.issue()
    def take(_):
        try:codes.consume(code);return True
        except Exception:return False
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(take,range(2)))==1
