import json

from backend.associations import judge_next
from backend.store import connect
from test_core import batch, client
from test_memories import draft


def setup(client,when='2026-10-19T19:00:00+08:00',mode='live'):
    client.post('/api/import',json=batch())
    client.post('/api/memories',json=draft())
    body=batch('今天喝咖啡吗',source_id='2',mode=mode)
    body['messages'][0]['event_time']=when
    client.post('/api/import',json=body)
    return client.app.state.db_path,body


def test_unconfirmed_hidden_confirmed_cites_evidence_and_dismiss_sticks(client):
    path,body=setup(client)
    assert client.get('/api/ordinary-reminders').json()==[]
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM association').fetchone()[0]==1
    assert judge_next(path,lambda _:json.dumps({'confirmed':True}))['state']=='confirmed_synthetic'
    cards=client.get('/api/ordinary-reminders').json()
    assert len(cards)==1
    assert cards[0]['memory']['evidence'][0]['quote']=='我喜欢咖啡'
    client.post('/api/ordinary-reminders/1/dismiss')
    client.post('/api/import',json=body)
    assert not judge_next(path,lambda _:'{"confirmed":true}')['processed']
    assert client.get('/api/ordinary-reminders').json()==[]


def test_timeout_no_ordinary_alert(client):
    path,_=setup(client)
    def timeout(_):
        raise TimeoutError()
    assert judge_next(path,timeout)['state']=='failed'
    assert client.get('/api/ordinary-reminders').json()==[]


def test_correction_during_judgment_rejects_result(client):
    path,_=setup(client)
    def judge(_):
        client.post('/api/memories/1/correct',json={})
        return '{"confirmed":true}'
    assert judge_next(path,judge)['state']=='stale'
    assert client.get('/api/ordinary-reminders').json()==[]


def test_same_time_not_history(client):
    path,_=setup(client,when='2026-10-18T19:00:00+08:00')
    assert not judge_next(path,lambda _:'{"confirmed":true}')['processed']


def test_history_replay_does_not_enqueue(client):
    path,_=setup(client,mode='history')
    assert not judge_next(path,lambda _:'{"confirmed":true}')['processed']


def test_rejection_and_false_boolean_type_not_confirmed(client):
    path,_=setup(client)
    assert judge_next(path,lambda _:'{"confirmed":"false"}')['state']=='failed'
    assert client.get('/api/ordinary-reminders').json()==[]


def test_privacy_blocks_judge(client):
    path,_=setup(client)
    # Make a fresh live trigger with a sensitive phone number.
    body=batch('咖啡 13800138000',source_id='3',mode='live')
    body['messages'][0]['event_time']='2026-10-20T19:00:00+08:00'
    client.post('/api/import',json=body)
    judge_next(path,lambda _:'{"confirmed":false}')
    def must_not_call(_):
        raise AssertionError('privacy-blocked data reached judge')
    judge_next(path,must_not_call)
    with connect(path) as db:
        assert db.execute('SELECT state FROM association WHERE trigger_id=3').fetchone()[0]=='blocked_privacy'
