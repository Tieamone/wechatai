from backend.store import connect, initialize
from backend.associations import enqueue_candidates
from test_core import batch, client
from test_memories import draft


def test_confirm_archives_without_deleting_or_breaking_recall(client):
    client.post('/api/import',json=batch())
    mid=client.post('/api/memories',json=draft()).json()['id']
    path=client.app.state.db_path
    before=client.get('/api/memories').json()[0]
    for _ in range(2):
        assert client.post(f'/api/memories/{mid}/review/confirm',json={'expected_version':1}).status_code==200
    initialize(path)
    assert client.get('/api/memories?review=pending').json()==[]
    confirmed=client.get('/api/memories?review=confirmed').json()[0]
    assert confirmed['confirmed'] and confirmed['version']==before['version']
    assert confirmed['evidence']==before['evidence']
    assert client.get('/api/memories/review-counts').json()=={'pending':0,'confirmed':1}
    assert len(client.get('/api/memories?q=咖啡').json())==1
    assert client.post('/api/memories',json=draft()).json()['duplicate']
    assert client.get('/api/memories?review=pending').json()==[]
    body=batch('今天喝咖啡吗',source_id='2',mode='live')
    body['messages'][0]['event_time']='2026-10-19T19:00:00+08:00'
    client.post('/api/import',json=body)
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM association WHERE memory_id=?',(mid,)).fetchone()[0]==1
    client.post(f'/api/memories/{mid}/review/reopen',json={'expected_version':1})
    assert len(client.get('/api/memories?review=pending').json())==1


def test_revision_or_source_change_cannot_inherit_confirmation(client):
    client.post('/api/import',json=batch())
    mid=client.post('/api/memories',json=draft()).json()['id']
    assert client.post(f'/api/memories/{mid}/review/confirm',json={'expected_version':99}).status_code==409
    client.post(f'/api/memories/{mid}/review/confirm',json={'expected_version':1})
    replacement={k:v for k,v in draft().items() if k!='evidence'}
    replacement.update(summary='对方表达喜欢喝咖啡',expected_version=1)
    assert client.post(f'/api/memories/{mid}/replace',json=replacement).status_code==200
    assert client.get('/api/memories/review-counts').json()=={'pending':1,'confirmed':0}
    client.post('/api/import',json=batch('我不喜欢咖啡',revision=2))
    assert client.post(f'/api/memories/{mid}/review/confirm',json={'expected_version':1}).status_code==409
    assert client.get('/api/memories/review-counts').json()=={'pending':0,'confirmed':0}


def test_review_filter_and_pagination_beyond_default_page(client):
    client.post('/api/import',json=batch())
    for number in range(105):
        client.post('/api/memories',json=draft(summary=f'咖啡偏好记录{number}'))
    client.post('/api/memories/1/review/confirm',json={'expected_version':1})
    assert [r['id'] for r in client.get('/api/memories?review=confirmed').json()]==[1]
    first=client.get('/api/memories?review=pending&limit=10').json()
    second=client.get(f"/api/memories?review=pending&limit=10&before={first[-1]['id']}").json()
    assert len(first)==len(second)==10
    assert not {r['id'] for r in first}&{r['id'] for r in second}
    assert client.get('/api/memories/review-counts').json()=={'pending':104,'confirmed':1}
