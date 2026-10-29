import sqlite3

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.store import SCHEMA
from test_core import batch, client


def draft(message_id=1, revision=1, summary='对方喜欢咖啡'):
    return {'summary': summary, 'subject': 'peer', 'polarity': 'positive', 'assertion': 'direct',
            'temporal_kind': 'preference', 'evidence': [
                {'message_id': message_id, 'message_revision': revision, 'span_start': 0, 'span_end': 5}]}


def test_evidence_and_dedup(client):
    client.post('/api/import', json=batch())
    response = client.post('/api/memories', json=draft())
    assert response.status_code == 200
    assert client.post('/api/memories', json=draft()).json()['duplicate']
    cards = client.get('/api/memories', params={'q': '咖啡'}).json()
    assert len(cards) == 1
    assert cards[0]['evidence'][0]['quote'] == '我喜欢咖啡'
    assert client.get('/api/status').json()['memories'] == 1


def test_bad_evidence_and_role_rejected(client):
    client.post('/api/import', json=batch())
    assert client.post('/api/memories', json=draft(999)).status_code == 422
    assert client.post('/api/memories', json=draft(revision=2)).status_code == 409
    bad = draft()
    bad['evidence'][0]['span_end'] = 999
    assert client.post('/api/memories', json=bad).status_code == 422
    bad = draft()
    bad['subject'] = 'self'
    assert client.post('/api/memories', json=bad).status_code == 422
    bad['assertion'] = 'reported'
    assert client.post('/api/memories', json=bad).status_code == 200


def test_correction_blocks_paraphrases_but_not_new_evidence(client):
    client.post('/api/import', json=batch())
    mid = client.post('/api/memories', json=draft()).json()['id']
    client.post('/api/memories', json=draft(summary='另一个措辞'))
    assert client.post(f'/api/memories/{mid}/correct', json={}).status_code == 200
    assert client.get('/api/memories').json() == []
    assert client.post('/api/memories', json=draft(summary='试图重新提炼')).status_code == 409
    assert len(client.get('/api/search', params={'q': '咖啡'}).json()) == 1
    client.post('/api/import', json=batch(source_id='new'))
    assert client.post('/api/memories', json=draft(2)).status_code == 200


def test_source_update_invalidates_memory(client):
    client.post('/api/import', json=batch())
    client.post('/api/memories', json=draft())
    client.post('/api/import', json=batch('不再喜欢咖啡', revision=2))
    assert client.get('/api/memories').json() == []
    assert client.get('/api/memories', params={'q': '咖啡'}).json() == []
    assert client.post('/api/memories', json=draft()).status_code == 409


def test_migrate_v1_and_correction_survives_restart(tmp_path):
    db = sqlite3.connect(tmp_path / 'memory.sqlite3')
    db.executescript(SCHEMA + 'PRAGMA user_version=1;')
    db.close()
    for phase in range(2):
        with TestClient(create_app(tmp_path, 'test'), base_url='http://127.0.0.1:8765', headers={'Authorization':'Bearer test'}) as c:
            if phase == 0:
                c.post('/api/import', json=batch())
                mid = c.post('/api/memories', json=draft()).json()['id']
                c.post(f'/api/memories/{mid}/correct', json={})
            else:
                assert c.get('/api/memories').json() == []
                assert c.post('/api/memories', json=draft()).status_code == 409
    db = sqlite3.connect(tmp_path / 'memory.sqlite3')
    assert db.execute('PRAGMA user_version').fetchone()[0] == 19
    db.close()
