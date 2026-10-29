from test_core import client, batch


def person(client,label='小王'):
    return client.post('/api/entities',json={'label':label}).json()


def property_body(value='13800138000',**extra):
    return {'kind':'phone','value':value,'confirmed':True,'confirmation_note':'合成样本人工确认',**extra}


def test_same_name_shared_phone_and_number_change_preserve_identity(client):
    first,second=person(client),person(client)
    assert first['id']!=second['id'] and first['token']!=second['token']
    for p in (first,second):
        assert client.post(f"/api/entities/{p['id']}/properties",json=property_body()).status_code==200
    newer=client.post(f"/api/entities/{first['id']}/properties",json=property_body('13900139000',valid_from='2026-10-26T12:00:00+08:00')).json()
    people=client.get('/api/entities').json()
    old,new=people[0]['properties']
    assert old['value_token']==people[1]['properties'][0]['value_token']
    assert old['value_token']!=new['value_token']
    assert new['valid_from']=='2026-10-26T04:00:00+00:00'
    client.post(f"/api/entities/{first['id']}/properties/{newer['id']}/retract")
    after=client.get('/api/entities').json()
    assert after[0]['properties'][1]['status']=='retracted'
    assert after[0]['properties'][0]==old


def test_alias_requires_confirmation_and_does_not_global_replace_name(client):
    p=person(client)
    url=f"/api/entities/{p['id']}/properties"
    body=property_body('阿王',kind='alias')
    assert client.post(url,json={**body,'confirmed':False}).status_code==422
    assert client.post(url,json={**body,'confirmed':'true'}).status_code==422
    assert client.post(url,json=body).status_code==200
    assert client.post(url,json=body).json()['duplicate']
    assert client.get('/api/entities').json()[0]['properties'][0]['value_token'] is None


def test_invalid_relationship_is_atomic(client):
    p=person(client)
    url=f"/api/entities/{p['id']}/properties"
    for fields in ({'value':' '},{'confirmation_note':' '},{'valid_from':'2026-10-26'},
                   {'valid_from':'2026-10-27T00:00:00Z','valid_to':'2026-10-26T00:00:00Z'}):
        assert client.post(url,json=property_body(**fields)).status_code==422
    assert client.get('/api/entities').json()[0]['properties']==[]


def test_property_evidence_revision_and_replay(client):
    client.post('/api/import',json=batch('大家叫我阿王'))
    p=person(client)
    url=f"/api/entities/{p['id']}/properties"
    body=property_body('阿王',kind='alias',evidence=[{'message_id':1,'message_revision':1,'span_start':0,'span_end':6}])
    first=client.post(url,json=body)
    assert first.status_code==200
    assert client.post(url,json=body).json()['duplicate']
    stored=client.get('/api/entities').json()[0]['properties'][0]
    assert stored['evidence'][0]['quote']=='大家叫我阿王'
    client.post('/api/import',json=batch('他才是阿王',revision=2))
    stored=client.get('/api/entities').json()[0]['properties'][0]
    assert stored['status']=='stale' and stored['evidence'][0]['quote'] is None
    assert client.post(url,json=body).status_code==409


def test_bad_property_evidence_does_not_create_mapping(client):
    client.post('/api/import',json=batch())
    p=person(client)
    body=property_body(evidence=[{'message_id':1,'message_revision':1,'span_start':0,'span_end':999}])
    assert client.post(f"/api/entities/{p['id']}/properties",json=body).status_code==422
    assert client.get('/api/entities').json()[0]['properties']==[]
    assert client.post('/api/privacy/preview',json={'text':'13800138000'}).json()['status']=='blocked'
