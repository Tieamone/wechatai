from test_core import batch, client
from test_memories import draft


def setup_watch(client):
    client.post('/api/import', json=batch())
    mid = client.post('/api/memories', json=draft()).json()['id']
    watch = client.post('/api/watches', json={'user_text':'咖啡','source_memory_id':mid})
    assert watch.status_code == 200
    return mid, watch.json()['id']


def incoming(client, source_id='2', when='2026-10-19T19:00:00+08:00'):
    body = batch('去咖啡店吗', source_id=source_id, mode='live')
    body['messages'][0]['event_time'] = when
    return client.post('/api/import', json=body)


def test_memory_watch_cites_prior_evidence_and_close_preserves_memory(client):
    mid, wid = setup_watch(client)
    incoming(client)
    cards = client.get('/api/reminders').json()
    assert len(cards) == 1
    assert cards[0]['source_memory']['id'] == mid
    assert cards[0]['source_memory']['evidence'][0]['quote'] == '我喜欢咖啡'
    client.patch(f'/api/watches/{wid}', json={'enabled':False})
    assert client.get('/api/reminders').json() == []
    assert len(client.get('/api/memories').json()) == 1


def test_current_or_future_message_cannot_be_historical_evidence(client):
    setup_watch(client)
    incoming(client, when='2026-10-18T19:00:00+08:00')
    incoming(client, source_id='3', when='2026-10-17T19:00:00+08:00')
    incoming(client, source_id='4', when=None)
    assert client.get('/api/reminders').json() == []


def test_corrected_memory_disables_effect_but_keeps_user_watch(client):
    mid, wid = setup_watch(client)
    incoming(client)
    client.post(f'/api/memories/{mid}/correct', json={})
    incoming(client, source_id='3')
    assert client.get('/api/reminders').json() == []
    watch = client.get('/api/watches').json()[0]
    assert watch['enabled'] == 1
    assert watch['source_status'] == 'incorrect'
    assert client.post('/api/watches', json={'user_text':'咖啡','source_memory_id':mid}).status_code == 409


def test_revised_evidence_withdraws_reminder(client):
    setup_watch(client)
    incoming(client)
    client.post('/api/import', json=batch('我不喜欢咖啡', revision=2))
    assert client.get('/api/reminders').json() == []
    assert client.get('/api/watches').json()[0]['source_status'] == 'stale'
