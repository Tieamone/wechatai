from backend.associations import judge_next
from backend.store import connect
from backend.outbox import ordinary_changes
from test_core import client,batch
from test_entities import person
from test_entity_lookup import add
from test_memories import draft


def setup(client,evidence_time='2026-10-17T00:00:00Z',extra=''):
    client.post('/api/import',json=batch('阿王花生过敏'))
    client.post('/api/memories',json=draft(summary='阿王花生过敏'))
    evidence=batch('阿王也叫小李'+extra,source_id='aliases')
    evidence['messages'][0]['event_time']=evidence_time
    client.post('/api/import',json=evidence)
    p=person(client,'不会发给判断器的备注')
    refs=[{'message_id':2,'message_revision':1,'span_start':0,'span_end':len(evidence['messages'][0]['text'])}]
    a=add(client,p,kind='alias',value='阿王',evidence=refs)
    b=add(client,p,kind='alias',value='小李',evidence=refs)
    live=batch('小李搬家了',source_id='live',mode='live')
    live['messages'][0]['event_time']='2026-10-19T00:00:00Z'
    client.post('/api/import',json=live)
    return client.app.state.db_path,p,a,b


def test_alias_recall_still_waits_for_judgment(client):
    path,p,_,_=setup(client)
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM association').fetchone()[0]==1
    assert client.get('/api/ordinary-reminders').json()==[]
    payloads=[]
    def judge(payload):
        payloads.append(payload)
        return '{"confirmed":false}'
    assert judge_next(path,judge)['state']=='rejected'
    mentions=payloads[0]['entity_mentions']
    assert {m['mention'] for m in mentions}=={'阿王','小李'}
    assert {m['person_token'] for m in mentions}=={p['token']}
    assert all(m['time_uncertain'] for m in mentions)
    assert p['label'] not in str(payloads)
    assert client.get('/api/ordinary-reminders').json()==[]


def test_future_relationship_evidence_does_not_recall(client):
    path,_,_,_=setup(client,evidence_time='2026-10-20T00:00:00Z')
    assert not judge_next(path,lambda _:'{"confirmed":true}')['processed']


def test_relation_retraction_during_judgment_rejects_late_result(client):
    path,p,_,b=setup(client)
    def judge(_):
        client.post(f"/api/entities/{p['id']}/properties/{b}/retract")
        return '{"confirmed":true}'
    assert judge_next(path,judge)['state']=='stale'
    assert client.get('/api/ordinary-reminders').json()==[]


def test_sensitive_relationship_evidence_blocks_judge(client):
    path,_,_,_=setup(client,extra=' 联系13800138000')
    calls=[]
    judge_next(path,lambda payload:calls.append(payload))
    assert calls==[]
    with connect(path) as db:
        assert db.execute('SELECT state FROM association').fetchone()[0]=='blocked_privacy'


def test_shared_alias_is_marked_ambiguous(client):
    path,_,_,_=setup(client)
    other=person(client)
    add(client,other,kind='alias',value='小李',evidence=[{'message_id':2,'message_revision':1,'span_start':0,'span_end':6}])
    seen=[]
    def judge(payload):
        seen.extend(m for m in payload['entity_mentions'] if m['mention']=='小李')
        return '{"confirmed":false}'
    judge_next(path,judge)
    assert len(seen)==2 and all(m['ambiguous'] for m in seen)


def test_retraction_withdraws_previously_confirmed_relation(client):
    path,p,_,b=setup(client)
    judge_next(path,lambda _:'{"confirmed":true}')
    with connect(path) as db:
        first=ordinary_changes(db)
    client.post(f"/api/entities/{p['id']}/properties/{b}/retract")
    with connect(path) as db:
        assert ordinary_changes(db,after=first['cursor'])['items']==[{'id':1,'status':'stale','revision':2}]
