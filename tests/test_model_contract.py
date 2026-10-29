import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.jobs import prepare_one
from backend.model_contract import commit_response, issue_request
from backend.store import connect
from test_core import batch, client


def prepared(client, text='我喜欢咖啡'):
    client.post('/api/import', json=batch(text))
    client.post('/api/initializations', json={'mode':'full'})
    prepare_one(client.app.state.db_path)
    return issue_request(client.app.state.db_path, 1)


def result(summary='对方喜欢咖啡', refs=None):
    return {'summary':summary, 'subject':'peer', 'polarity':'positive', 'assertion':'direct',
            'temporal_kind':'preference', 'source_refs': refs or ['s1']}


def test_request_scope_and_atomic_commit(client):
    request = prepared(client)
    serialized = json.dumps(request)
    assert 'synthetic-account' not in serialized and 'manual-json' not in serialized
    assert '2026-10-18' not in serialized
    path = client.app.state.db_path
    raw = json.dumps({'memories':[result()]})
    assert commit_response(path, request['request_id'], raw)['memory_ids'] == [1]
    assert commit_response(path, request['request_id'], raw)['duplicate']
    assert client.get('/api/memories').json()[0]['origin'] == 'synthetic'
    assert not client.get('/api/initializations').json()[0]['extraction_complete']


@pytest.mark.parametrize('bad', [result(refs=['s999']), result('[PERSON_deadbeef]'), result('[PHONE_broken'),
                               {**result(), 'quote':'伪造原话'}, {**result(), 'subject':'invalid'}])
def test_bad_result_rolls_back_whole_batch(client, bad):
    request = prepared(client)
    with pytest.raises(HTTPException):
        commit_response(client.app.state.db_path, request['request_id'], json.dumps({'memories':[result(), bad]}))
    assert client.get('/api/memories').json() == []
    with connect(client.app.state.db_path) as db:
        assert db.execute('SELECT state FROM job').fetchone()[0] == 'prepared'
        assert db.execute('SELECT state FROM model_request').fetchone()[0] == 'issued'


def test_mapping_restore_and_stale_mapping_rejection(client):
    token = client.post('/api/mappings', json={'kind':'PERSON','value':'小王'}).json()['token']
    request = prepared(client, '小王喜欢咖啡')
    assert '小王' not in json.dumps(request, ensure_ascii=False)
    raw = json.dumps({'memories':[result(token+'喜欢咖啡')]})
    client.post('/api/mappings', json={'kind':'PERSON','value':'小李'})
    with pytest.raises(HTTPException, match='409'):
        commit_response(client.app.state.db_path, request['request_id'], raw)
    new_request = issue_request(client.app.state.db_path, 1)
    commit_response(client.app.state.db_path, new_request['request_id'], raw)
    assert client.get('/api/memories').json()[0]['summary'] == '小王喜欢咖啡'


def test_cancelled_or_revised_request_cannot_commit(client):
    request = prepared(client)
    client.post('/api/import', json=batch('现在不喜欢咖啡', revision=2))
    with pytest.raises(HTTPException):
        commit_response(client.app.state.db_path, request['request_id'], json.dumps({'memories':[result()]}))
    assert client.get('/api/memories').json() == []


def test_background_worker(tmp_path):
    app = create_app(tmp_path, 't', enable_worker=True)
    with TestClient(app, base_url='http://127.0.0.1:8765', headers={'Authorization':'Bearer t'}) as c:
        c.post('/api/import', json=batch())
        c.post('/api/initializations', json={'mode':'full'})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if c.get('/api/initializations').json()[0]['counts'] == {'prepared':1}:
                break
            time.sleep(.05)
        assert c.get('/api/initializations').json()[0]['counts'] == {'prepared':1}
        assert c.get('/api/worker').json()['running']
    assert not app.state.preparation_worker.thread.is_alive()
