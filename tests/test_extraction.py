import json

from backend.extraction import extract_next
from backend.jobs import prepare_one
from backend.store import connect
from test_core import batch, client
from test_model_contract import result


def ready(client):
    client.post('/api/import', json=batch())
    client.post('/api/initializations', json={'mode':'full'})
    prepare_one(client.app.state.db_path)
    return client.app.state.db_path


def test_provider_pipeline_and_single_claim(client):
    path=ready(client)
    def provider(request):
        assert request['data']['messages'][0]['source_ref']=='s1'
        assert not extract_next(path, lambda _: 'invalid')['processed']
        return json.dumps({'memories':[result()]})
    assert extract_next(path,provider)['state']=='validated_synthetic'
    assert len(client.get('/api/memories').json())==1
    assert not extract_next(path,provider)['processed']


def test_timeout_does_not_retry_or_make_memory(client):
    path=ready(client)
    def timeout(_):
        raise TimeoutError('sensitive payload must not be logged')
    assert extract_next(path,timeout)['error']=='model_timeout'
    assert not extract_next(path,timeout)['processed']
    assert client.get('/api/memories').json()==[]
    with connect(path) as db:
        assert db.execute('SELECT error FROM job').fetchone()[0]=='model_timeout'


def test_pause_during_provider_rejects_late_result(client):
    path=ready(client)
    def provider(_):
        client.post('/api/initializations/1/control',json={'action':'pause'})
        return json.dumps({'memories':[result()]})
    assert extract_next(path,provider)['error']=='model_result_rejected'
    assert client.get('/api/memories').json()==[]
    with connect(path) as db:
        assert db.execute('SELECT state FROM job').fetchone()[0]=='pending'


def test_expired_extraction_does_not_commit(client):
    path=ready(client)
    def provider(_):
        with connect(path) as db:
            db.execute('UPDATE job SET lease_until=0')
        return json.dumps({'memories':[result()]})
    assert extract_next(path,provider)['error']=='model_result_rejected'
    assert client.get('/api/memories').json()==[]
