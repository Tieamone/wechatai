from test_core import client,batch
from test_entities import person,property_body


def add(client,p,**fields):
    return client.post(f"/api/entities/{p['id']}/properties",json=property_body(**fields)).json()['id']


def lookup(client,q,**extra):
    response=client.get('/api/entities/lookup',params={'q':q,**extra})
    assert response.status_code==200
    return response.json()


def test_shared_phone_candidates_and_retraction(client):
    a,b=person(client),person(client)
    add(client,a)
    rid=add(client,b)
    result=lookup(client,'+86 138-0013-8000')
    assert result['ambiguous'] and not result['identity_confirmed']
    assert [p['id'] for p in result['candidates']]==[a['id'],b['id']]
    client.post(f"/api/entities/{b['id']}/properties/{rid}/retract")
    assert len(lookup(client,'13800138000')['candidates'])==1


def test_alias_time_boundaries_and_unknown_time(client):
    a,b,c=person(client),person(client),person(client)
    start,end='2026-10-01T00:00:00Z','2026-10-20T00:00:00Z'
    add(client,a,value='阿王',kind='alias',valid_from=start,valid_to=end)
    add(client,b,value='阿王',kind='alias',valid_from=end,valid_to='2026-11-01T00:00:00Z')
    add(client,c,value='阿王',kind='alias')
    result=lookup(client,'阿王',at=end)
    assert [p['id'] for p in result['candidates']]==[b['id'],c['id']]
    assert result['candidates'][1]['matches'][0]['time_uncertain']
    assert not result['candidates'][0]['matches'][0]['time_uncertain']
    assert lookup(client,'阿')['candidates']==[]
    assert client.get('/api/entities/lookup',params={'q':'阿王','at':'2026-10-01'}).status_code==422


def test_lookup_cites_original_and_hides_revised_relationship(client):
    client.post('/api/import',json=batch('叫我阿王'))
    p=person(client)
    add(client,p,value='阿王',kind='alias',evidence=[{'message_id':1,'message_revision':1,'span_start':0,'span_end':4}])
    assert lookup(client,'阿王')['candidates'][0]['matches'][0]['evidence'][0]['quote']=='叫我阿王'
    client.post('/api/import',json=batch('那是别人',revision=2))
    assert lookup(client,'阿王')['candidates']==[]
