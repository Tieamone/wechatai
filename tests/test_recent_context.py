from backend.associations import judge_next
from test_associations import setup
from test_core import batch, client


def followup(client,text,role='peer'):
    body=batch(text,source_id='3',mode='live')
    body['messages'][0]['sender_role']=role
    body['messages'][0]['event_time']='2026-10-19T19:00:30+08:00'
    client.post('/api/import',json=body)


def test_followup_negation_withdraws_old_result_and_is_in_context(client):
    path,_=setup(client)
    judge_next(path,lambda _:'{"confirmed":true}')
    assert len(client.get('/api/ordinary-reminders').json())==1
    followup(client,'不去了，刚才开玩笑的')
    assert client.get('/api/ordinary-reminders').json()==[]
    def judge(payload):
        assert [r['text'] for r in payload['recent']]==['今天喝咖啡吗','不去了，刚才开玩笑的']
        assert payload['current']=='不去了，刚才开玩笑的'
        return '{"confirmed":false}'
    assert judge_next(path,judge)['state']=='rejected'


def test_self_followup_also_withdraws_previous_alert(client):
    path,_=setup(client)
    judge_next(path,lambda _:'{"confirmed":true}')
    followup(client,'刚才说的是别人','self')
    assert client.get('/api/ordinary-reminders').json()==[]


def test_sensitive_fragment_blocks_entire_recent_window(client):
    path,_=setup(client)
    followup(client,'手机号13800138000')
    called=[]
    judge_next(path,lambda payload:called.append(payload) or '{"confirmed":true}')
    assert called==[]
    assert client.get('/api/ordinary-reminders').json()==[]


def test_new_fragment_during_judgment_rejects_late_result(client):
    path,_=setup(client)
    def judge(_):
        followup(client,'不去了')
        return '{"confirmed":true}'
    judge_next(path,judge)
    assert client.get('/api/ordinary-reminders').json()==[]
