import json
from unittest.mock import Mock

from backend import ai_settings
from backend.automatic import process_next
from backend.jobs import prepare_one
from backend.notifications import pending_associations, deliver_association
from backend.store import connect, initialize
from test_core import client, batch
from test_memories import draft
from test_ai_settings import CONFIG


def setup(client, limit=200):
    client.post('/api/import',json=batch())
    client.post('/api/memories',json=draft())
    client.put('/api/ai/settings',json=CONFIG)
    assert client.put('/api/automatic',json={'enabled':True,'daily_limit':limit}).status_code==200
    body=batch('今天想喝咖啡',source_id='2',mode='live')
    body['messages'][0]['event_time']='2026-10-19T19:00:00+08:00'
    return client.app.state.db_path,body


def ready(path):
    prepare_one(path)
    with connect(path) as db:
        db.execute('UPDATE auto_message SET ready_at=0')


def responder(calls, confirmed=True):
    def complete(config, system, data):
        calls.append(data)
        if 'response_schema' in data:
            raise AssertionError('unexpected wrapper')
        if 'messages' in data:
            return json.dumps({'memories':[{'summary':'对方今天想喝咖啡','subject':'peer','polarity':'positive','assertion':'direct','temporal_kind':'plan','source_refs':['s1']}]})
        return json.dumps({'confirmed':confirmed})
    return complete


def test_live_extract_judge_notify_dedup_restart(client, monkeypatch):
    path,body=setup(client)
    calls=[]
    monkeypatch.setattr(ai_settings,'complete',responder(calls))
    client.post('/api/import',json=body)
    ready(path)
    assert process_next(path)['state']=='done'
    assert len(calls)==2
    cards=client.get('/api/ordinary-reminders').json()
    assert len(cards)==1 and cards[0]['origin']=='model'
    assert client.get('/api/automatic').json()['used']==2
    assert client.get('/api/initializations').json()==[]
    sender=Mock(return_value='accepted')
    aid=pending_associations(path,'wechat-bot')[0]
    assert deliver_association(path,aid,'wechat-bot',sender)=='accepted'
    initialize(path)
    assert not pending_associations(path,'wechat-bot')
    assert deliver_association(path,aid,'wechat-bot',sender)=='skipped_accepted'
    assert sender.call_count==1 and '咖啡' not in str(sender.call_args)
    client.post('/api/import',json=body)
    assert not process_next(path)['processed']
    assert len(calls)==2


def test_history_disabled_and_burst_only_latest(client, monkeypatch):
    path,body=setup(client)
    client.post('/api/import',json={**body,'mode':'history'})
    assert client.get('/api/automatic').json()['counts']=={}
    for number in ('3','4'):
        body['messages'][0]['source']['id']=number
        client.post('/api/import',json=body)
    assert client.get('/api/automatic').json()['counts']=={'pending':1,'superseded':1}
    client.put('/api/automatic',json={'enabled':False})
    calls=[]
    monkeypatch.setattr(ai_settings,'complete',responder(calls))
    ready(path)
    assert not process_next(path)['processed']
    assert calls==[]
    client.put('/api/automatic',json={'enabled':True})
    assert not process_next(path)['processed']


def test_privacy_blocks_without_api_call(client, monkeypatch):
    path,body=setup(client)
    body['messages'][0]['text']='咖啡 13800138000'
    client.post('/api/import',json=body)
    ready(path)
    send=Mock(side_effect=AssertionError('must not send'))
    monkeypatch.setattr(ai_settings,'complete',send)
    process_next(path)
    assert client.get('/api/automatic').json()['counts']=={'blocked_privacy':1}
    send.assert_not_called()


def test_rejected_judgment_never_pushes(client, monkeypatch):
    path,body=setup(client)
    client.post('/api/import',json=body)
    ready(path)
    monkeypatch.setattr(ai_settings,'complete',responder([],False))
    process_next(path)
    assert not pending_associations(path,'wechat-bot')
    assert not client.get('/api/ordinary-reminders').json()


def test_budget_and_failed_call_are_not_retried(client, monkeypatch):
    path,body=setup(client,1)
    client.post('/api/import',json=body)
    ready(path)
    calls=[]
    monkeypatch.setattr(ai_settings,'complete',responder(calls))
    assert process_next(path)['state']=='partial'
    assert len(calls)==1 and not pending_associations(path,'wechat-bot')
    assert not process_next(path)['processed']


def test_pause_inflight_rejects_memory_and_notification(client, monkeypatch):
    path,body=setup(client)
    client.post('/api/import',json=body)
    ready(path)
    def complete(*args):
        client.put('/api/automatic',json={'enabled':False})
        return responder([])(*args)
    monkeypatch.setattr(ai_settings,'complete',complete)
    process_next(path)
    assert len(client.get('/api/memories').json())==1
    assert not pending_associations(path,'wechat-bot')


def test_evidence_change_before_delivery_cancels(client, monkeypatch):
    path,body=setup(client)
    client.post('/api/import',json=body)
    ready(path)
    monkeypatch.setattr(ai_settings,'complete',responder([]))
    process_next(path)
    aid=pending_associations(path,'wechat-bot')[0]
    client.post('/api/memories/1/correct',json={})
    sender=Mock(return_value='accepted')
    assert deliver_association(path,aid,'wechat-bot',sender)=='skipped_inactive'
    sender.assert_not_called()


def test_uncertain_notification_survives_restart(client, monkeypatch):
    path,body=setup(client)
    client.post('/api/import',json=body)
    ready(path)
    monkeypatch.setattr(ai_settings,'complete',responder([]))
    process_next(path)
    aid=pending_associations(path,'wechat-bot')[0]
    sender=Mock(side_effect=TimeoutError())
    assert deliver_association(path,aid,'wechat-bot',sender)=='uncertain'
    initialize(path)
    assert deliver_association(path,aid,'wechat-bot',sender)=='skipped_uncertain'
    assert sender.call_count==1


def test_model_timeout_no_automatic_retry(client, monkeypatch):
    path,body=setup(client)
    client.post('/api/import',json=body)
    ready(path)
    send=Mock(side_effect=ai_settings.HTTPException(504,'timeout'))
    monkeypatch.setattr(ai_settings,'complete',send)
    assert process_next(path)['state']=='failed'
    initialize(path)
    assert not process_next(path)['processed']
    assert send.call_count==1
    assert not pending_associations(path,'wechat-bot')


def test_new_context_during_model_call_rejects_late_result(client, monkeypatch):
    path,body=setup(client)
    client.post('/api/import',json=body)
    ready(path)
    def complete(*args):
        body['messages'][0]['source']['id']='3'
        body['messages'][0]['text']='刚才只是开玩笑'
        client.post('/api/import',json=body)
        return responder([])(*args)
    monkeypatch.setattr(ai_settings,'complete',complete)
    process_next(path)
    assert len(client.get('/api/memories').json())==1
    assert not pending_associations(path,'wechat-bot')


def test_watch_notification_prevents_duplicate_model_alert(client, monkeypatch):
    from backend.notifications import pending,deliver
    path,body=setup(client)
    client.post('/api/watches',json={'user_text':'咖啡'})
    client.post('/api/import',json=body)
    sender=Mock(return_value='accepted')
    deliver(path,pending(path,'wechat-bot')[0],'wechat-bot',sender)
    ready(path)
    monkeypatch.setattr(ai_settings,'complete',responder([]))
    process_next(path)
    assert deliver_association(path,pending_associations(path,'wechat-bot')[0],'wechat-bot',sender)=='skipped_duplicate'
    assert sender.call_count==1


def test_pause_during_judgment_prevents_push(client, monkeypatch):
    path,body=setup(client)
    client.post('/api/import',json=body)
    ready(path)
    def complete(*args):
        if 'messages' not in args[2]:
            client.put('/api/automatic',json={'enabled':False})
        return responder([])(*args)
    monkeypatch.setattr(ai_settings,'complete',complete)
    process_next(path)
    assert not pending_associations(path,'wechat-bot')
