from test_core import client,batch
from test_entities import person
from test_entity_lookup import add


def messages(client,p,**params):
    response=client.get(f"/api/entities/{p['id']}/messages",params=params)
    assert response.status_code==200
    return response.json()


def test_number_recall_periods_and_shared_ambiguity(client):
    a,b=person(client),person(client)
    add(client,a,valid_from='2026-10-01T00:00:00Z',valid_to='2026-10-20T00:00:00Z')
    add(client,b)
    client.post('/api/import',json=batch('打给 +86 138-0013-8000'))
    later=batch('13800138000',source_id='2')
    later['messages'][0]['event_time']='2026-10-21T00:00:00Z'
    client.post('/api/import',json=later)
    result=messages(client,a)['items']
    assert len(result)==1 and result[0]['id']==1
    assert result[0]['matches'][0]['ambiguous'] and not result[0]['matches'][0]['time_uncertain']
    assert not result[0]['identity_confirmed']
    assert len(messages(client,b)['items'])==2


def test_alias_pagination_and_retract(client):
    p=person(client)
    rid=add(client,p,kind='alias',value='阿王')
    body=batch('阿王来了')
    body['messages']=[batch('阿王来了',source_id=str(i))['messages'][0] for i in range(51)]
    client.post('/api/import',json=body)
    first=messages(client,p)
    assert len(first['items'])==50 and first['has_more']
    second=messages(client,p,before=first['next_before'])
    assert len(second['items'])==1 and not second['has_more']
    assert len({m['id'] for m in first['items']+second['items']})==51
    client.post(f"/api/entities/{p['id']}/properties/{rid}/retract")
    assert messages(client,p)['items']==[]
