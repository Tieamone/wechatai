import json
from unittest.mock import Mock

import httpx
import pytest

from backend import ai_settings as ai
from backend.jobs import prepare_one
from backend.store import connect
from test_core import client, batch
from test_model_contract import result

CONFIG = {'base_url':'https://model.example/v1','model':'example','api_key':'test-secret','enabled':True}


def test_settings_encrypt_keep_and_delete(client):
    assert client.post('/api/ai/test').status_code == 409
    response = client.put('/api/ai/settings', json=CONFIG)
    assert response.status_code == 200
    assert response.json()['has_key'] and 'test-secret' not in response.text
    directory = client.app.state.db_path.parent
    assert 'test-secret' not in (directory/'ai-settings.json').read_text()
    assert ai.protect(__import__('base64').b64decode(ai.load(directory)['key_encrypted']), decrypt=True) == b'test-secret'
    assert client.put('/api/ai/settings', json={**CONFIG,'api_key':''}).status_code == 200
    assert client.put('/api/ai/settings', json={**CONFIG,'api_key':'','base_url':'https://other.example'}).status_code == 409
    assert client.delete('/api/ai/settings').json()['has_key'] is False
    assert client.post('/api/ai/test').status_code == 409


@pytest.mark.parametrize('url',['http://remote.example','https://user:secret@example.com','https://example.com?a=secret','https://example.com/chat/completions'])
def test_invalid_addresses(client, url):
    assert client.put('/api/ai/settings',json={**CONFIG,'base_url':url}).status_code == 422


def test_connection_test_never_sends_chat(client, monkeypatch):
    client.put('/api/ai/settings',json=CONFIG)
    send = Mock(return_value='{"ok":true}')
    monkeypatch.setattr(ai,'complete',send)
    assert client.post('/api/ai/test').json()['ok']
    assert send.call_args.args[2] == {'test':'connection only, no chat data'}
    client.put('/api/ai/settings',json={**CONFIG,'enabled':False,'api_key':''})
    assert client.post('/api/ai/test').status_code == 409
    assert send.call_count == 1


def test_real_provider_commit_and_duplicate_click(client, monkeypatch):
    client.put('/api/ai/settings',json=CONFIG)
    client.post('/api/import',json=batch())
    client.post('/api/initializations',json={'mode':'full'})
    prepare_one(client.app.state.db_path)
    send = Mock(return_value=json.dumps({'memories':[result()]}))
    monkeypatch.setattr(ai,'complete',send)
    assert client.post('/api/ai/extract/1').status_code == 200
    assert client.get('/api/memories').json()[0]['origin'] == 'model'
    item=client.get('/api/initializations').json()[0]
    assert item['extraction_complete'] and item['message_coverage']['real_extracted'] == 1
    assert client.post('/api/ai/extract/1').status_code == 409
    assert send.call_count == 1
    assert 'synthetic-account' not in json.dumps(send.call_args.args[2])


def test_invalid_model_response_does_not_store_memory(client, monkeypatch):
    client.put('/api/ai/settings',json=CONFIG)
    client.post('/api/import',json=batch())
    client.post('/api/initializations',json={'mode':'full'})
    prepare_one(client.app.state.db_path)
    monkeypatch.setattr(ai,'complete',lambda *a:json.dumps({'memories':[result(refs=['invented'])]}))
    assert client.post('/api/ai/extract/1').status_code == 422
    assert client.get('/api/memories').json() == []
    with connect(client.app.state.db_path) as db:
        assert db.execute('SELECT state FROM job').fetchone()[0] == 'failed'


def test_english_summary_rejected_before_commit(client, monkeypatch):
    client.put('/api/ai/settings',json=CONFIG)
    client.post('/api/import',json=batch())
    client.post('/api/initializations',json={'mode':'full'})
    prepare_one(client.app.state.db_path)
    def response(config, system, data):
        assert '简体中文' in system
        return json.dumps({'memories':[result('Peer likes coffee.')]})
    monkeypatch.setattr(ai,'complete',response)
    reply=client.post('/api/ai/extract/1')
    assert reply.status_code == 422
    assert '中文' in reply.json()['detail']
    assert client.get('/api/memories').json() == []


def test_transport_payload_and_redirection_denied(monkeypatch):
    from base64 import b64encode
    config={**CONFIG,'key_encrypted':b64encode(ai.protect(b'test-secret')).decode()}
    def handle(request):
        assert str(request.url) == 'https://model.example/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer test-secret'
        body=json.loads(request.content)
        assert body['response_format'] == {'type':'json_object'}
        return httpx.Response(302,headers={'location':'https://other.example'})
    factory=httpx.Client
    monkeypatch.setattr(ai.httpx,'Client',lambda **kwargs:factory(**kwargs,transport=httpx.MockTransport(handle)))
    with pytest.raises(ai.HTTPException) as failure:
        ai.complete(config,'json',{'test':True})
    assert failure.value.status_code == 502
    assert 'test-secret' not in failure.value.detail
