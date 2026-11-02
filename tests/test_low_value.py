import json
from backend.store import connect
from test_core import client,batch
from test_memories import draft


def test_value_label_is_versioned_and_independent(client):
    client.post('/api/import',json=batch()).raise_for_status()
    client.post('/api/memories',json=draft()).raise_for_status()
    endpoint='/api/memories/1/review/'
    assert client.post(endpoint+'low_value',json={'expected_version':99}).status_code==409
    client.post(endpoint+'confirm',json={'expected_version':1}).raise_for_status()
    client.post(endpoint+'low_value',json={'expected_version':1}).raise_for_status()
    card=client.get('/api/memories').json()[0]
    assert card['confirmed'] and card['low_value'] and card['status']=='active'
    client.post(endpoint+'reopen',json={'expected_version':1}).raise_for_status()
    assert client.get('/api/memories').json()[0]['low_value']
    with connect(client.app.state.db_path) as db:
        row=db.execute("SELECT * FROM feedback WHERE action='low_value'").fetchone()
        assert row['memory_version']==1 and json.loads(row['evidence_json'])[0]['message_revision']==1
        assert db.execute('SELECT count(*) FROM suppressed_evidence').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM correction').fetchone()[0]==0
    client.post('/api/import',json=batch('我不喜欢咖啡',revision=2)).raise_for_status()
    assert client.post(endpoint+'low_value',json={'expected_version':1}).status_code==409
    with connect(client.app.state.db_path) as db:
        assert db.execute("SELECT valid FROM feedback WHERE action='low_value'").fetchone()[0]==0
