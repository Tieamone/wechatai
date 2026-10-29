import json

from backend.jobs import claim, heartbeat, prepare_one
from backend.store import connect
from test_core import batch, client


def test_fixed_range_overlap_and_no_false_completion(client):
    data = batch()
    data['messages'] = [batch(source_id=str(i))['messages'][0] for i in range(15)]
    client.post('/api/import', json=data)
    initial = client.post('/api/initializations', json={'mode':'full'}).json()
    assert initial['message_count'] == 15
    client.post('/api/import', json=batch(source_id='late'))
    path = client.app.state.db_path
    with connect(path) as db:
        windows = [json.loads(r[0]) for r in db.execute('SELECT input_json FROM job')]
    assert len(windows) == 2
    assert len(windows[0]['messages']) == 12
    assert len(windows[1]['messages']) == 5
    assert windows[0]['messages'][-2:] == windows[1]['messages'][:2]
    assert all(m['id'] != 16 for w in windows for m in w['messages'])
    assert prepare_one(path)['state'] == 'prepared'
    assert client.get('/api/initializations').json()[0]['message_coverage']['prepared']==10
    assert prepare_one(path)['state'] == 'prepared'
    assert not prepare_one(path)['processed']
    info = client.get('/api/initializations').json()[0]
    assert info['counts'] == {'prepared': 2}
    assert info['extraction_complete'] is False
    assert info['message_coverage']=={'scope':15,'prepared':15,'validated_synthetic':0,'failed_or_blocked':0,'stale':0,'real_extracted':0}


def test_pause_resume_cancel_and_lease_recovery(client):
    client.post('/api/import', json=batch())
    client.post('/api/initializations', json={'mode':'full'})
    path = client.app.state.db_path
    job = claim(path, 'dead-worker', now=0)
    assert job
    assert heartbeat(path, job['id'], 'dead-worker') is False
    recovered = claim(path, 'new-worker')
    assert recovered['id'] == job['id']
    assert heartbeat(path, job['id'], 'new-worker')
    assert claim(path, 'other') is None
    client.post('/api/initializations/1/control', json={'action':'pause'})
    assert not heartbeat(path, job['id'], 'new-worker')
    assert not prepare_one(path)['processed']
    client.post('/api/initializations/1/control', json={'action':'resume'})
    assert prepare_one(path)['state'] == 'prepared'
    client.post('/api/initializations/1/control', json={'action':'cancel'})
    assert client.post('/api/initializations/1/control', json={'action':'resume'}).status_code == 409


def test_privacy_whole_window_block_and_retry(client):
    data = batch('正常文字')
    data['messages'] += batch('手机号13800138000', source_id='2')['messages']
    client.post('/api/import', json=data)
    client.post('/api/initializations', json={'mode':'full'})
    assert prepare_one(client.app.state.db_path)['state'] == 'blocked_privacy'
    with connect(client.app.state.db_path) as db:
        assert db.execute('SELECT result_json FROM job').fetchone()[0] is None
    client.post('/api/mappings', json={'kind':'PHONE','value':'13800138000'})
    client.post('/api/initializations/1/control', json={'action':'retry'})
    assert prepare_one(client.app.state.db_path)['state'] == 'prepared'
    with connect(client.app.state.db_path) as db:
        result = db.execute('SELECT result_json FROM job').fetchone()[0]
        assert '13800138000' not in result
        assert len(json.loads(result)['messages']) == 2


def test_stale_input_and_batch_validation(client):
    client.post('/api/import', json=batch())
    assert client.post('/api/initializations', json={'mode':'batch','message_ids':[99]}).status_code == 422
    client.post('/api/initializations', json={'mode':'batch','message_ids':[1]})
    assert prepare_one(client.app.state.db_path)['state'] == 'prepared'
    client.post('/api/import', json=batch('改了内容', revision=2))
    info = client.get('/api/initializations').json()[0]
    assert info['counts'] == {'failed':1}
    client.post('/api/initializations/1/control', json={'action':'retry'})
    assert prepare_one(client.app.state.db_path)['state'] == 'failed'


def test_nontext_and_time_gap_are_visible(client):
    data = batch()
    later = batch('这是后一天的消息', source_id='2')['messages'][0]
    later['event_time'] = '2026-10-19T19:00:00+08:00'
    media = batch(source_id='3')['messages'][0]
    media['type'] = 'image'
    data['messages'] += [later, media]
    client.post('/api/import', json=data)
    client.post('/api/initializations', json={'mode':'full'})
    prepare_one(client.app.state.db_path)
    with connect(client.app.state.db_path) as db:
        result = json.loads(db.execute('SELECT result_json FROM job').fetchone()[0])
    assert result['context_limited']
    assert 'conversation_has_unprocessed_nontext' in result['context_warnings']
    assert 'time_gap_over_30_minutes' in result['context_warnings']
    assert len(result['messages']) == 2


def test_oversized_context_is_not_silently_truncated(client):
    client.post('/api/import', json=batch('长' * 12001))
    client.post('/api/initializations', json={'mode':'full'})
    assert prepare_one(client.app.state.db_path)['state'] == 'failed'
    with connect(client.app.state.db_path) as db:
        row = db.execute('SELECT result_json,error FROM job').fetchone()
    assert row['result_json'] is None
    assert row['error'] == 'context_character_limit'
