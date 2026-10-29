from backend.outbox import changes
from backend.store import connect
from test_core import batch, client
from test_memories import draft


def test_create_dedup_and_withdraw_events(client):
    client.post('/api/watches', json={'user_text':'咖啡'})
    body = batch(mode='live')
    client.post('/api/import', json=body)
    client.post('/api/import', json=body)
    path = client.app.state.db_path
    with connect(path) as db:
        first = changes(db)
    assert first['items'] == [{'id':1,'revision':1,'status':'active'}]
    client.post('/api/reminders/1/dismiss')
    client.post('/api/reminders/1/dismiss')
    with connect(path) as db:
        second = changes(db, first['cursor'])
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 2
    assert second['items'] == [{'id':1,'revision':2,'status':'incorrect'}]


def test_pagination_replay_and_transaction_rollback(client):
    client.post('/api/watches', json={'user_text':'咖啡'})
    for i in range(3):
        body=batch(source_id=str(i), mode='live')
        body['messages'][0]['event_time']=f'2026-10-{18+i}T19:00:00+08:00'
        client.post('/api/import', json=body)
    path = client.app.state.db_path
    with connect(path) as db:
        page1 = changes(db, limit=2)
        assert page1['has_more']
        page2 = changes(db, page1['cursor'], limit=2)
        assert not page2['has_more']
        assert [i['id'] for i in page1['items']+page2['items']] == [1,2,3]
    try:
        with connect(path) as db:
            db.execute("UPDATE reminder SET status='withdrawn' WHERE id=1")
            raise RuntimeError('simulated crash before commit')
    except RuntimeError:
        pass
    with connect(path) as db:
        assert changes(db, limit=2) == page1
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 3


def test_memory_correction_is_queued(client):
    client.post('/api/import', json=batch())
    mid = client.post('/api/memories', json=draft()).json()['id']
    client.post('/api/watches', json={'user_text':'咖啡','source_memory_id':mid})
    body = batch(source_id='2', mode='live')
    body['messages'][0]['event_time'] = '2026-10-19T19:00:00+08:00'
    client.post('/api/import', json=body)
    client.post(f'/api/memories/{mid}/correct', json={})
    with connect(client.app.state.db_path) as db:
        assert changes(db)['items'] == [{'id':1,'revision':2,'status':'withdrawn'}]
