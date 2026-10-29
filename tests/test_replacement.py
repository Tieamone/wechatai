from test_core import batch,client
from test_memories import draft


def replacement(summary='对方不喜欢咖啡'):
    fields=draft(summary=summary)
    fields.pop('evidence')
    return {**fields,'polarity':'negative','expected_version':1}


def setup(client):
    client.post('/api/import',json=batch())
    return client.post('/api/memories',json=draft()).json()['id']


def test_replace_preserves_history_and_blocks_reextraction(client):
    mid=setup(client)
    client.post('/api/watches',json={'user_text':'咖啡','source_memory_id':mid})
    response=client.post(f'/api/memories/{mid}/replace',json=replacement())
    assert response.status_code==200
    new_id=response.json()['id']
    cards=client.get('/api/memories').json()
    assert len(cards)==1 and cards[0]['id']==new_id
    assert cards[0]['origin']=='manual_correction'
    history=client.get(f'/api/memories/{new_id}/history').json()
    assert [m['status'] for m in history]==['active','superseded']
    assert client.post('/api/memories',json=draft()).status_code==409
    assert client.get('/api/watches').json()[0]['source_status']=='superseded'
    assert client.post(f'/api/memories/{mid}/replace',json=replacement()).status_code==409
    assert client.post(f'/api/memories/{new_id}/replace',json=replacement('对方晚上不喝咖啡')).status_code==200


def test_invalid_correction_does_not_disable_original(client):
    mid=setup(client)
    assert client.post(f'/api/memories/{mid}/replace',json=replacement('   ')).status_code==422
    invalid=replacement()
    invalid['subject']='self'
    assert client.post(f'/api/memories/{mid}/replace',json=invalid).status_code==422
    assert client.get('/api/memories').json()[0]['id']==mid
    wrong=replacement()
    wrong['expected_version']=999
    assert client.post(f'/api/memories/{mid}/replace',json=wrong).status_code==409
