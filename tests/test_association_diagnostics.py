from backend.associations import judge_next
from test_associations import setup
from test_core import client


def test_pending_and_timeout_visible_without_candidate_text(client):
    path,_=setup(client)
    response=client.get('/api/ordinary-reminders/diagnostics')
    assert response.status_code==200
    data=response.json()
    assert data['counts']=={'pending':1} and data['scope']=='candidate_diagnostics'
    assert set(data['items'][0])=={'id','trigger_id','memory_id','state','error'}
    def timeout(_):
        raise TimeoutError('private exception must not appear')
    judge_next(path,timeout)
    data=client.get('/api/ordinary-reminders/diagnostics').json()
    assert data['counts']=={'failed':1}
    assert data['items'][0]['error']=='model_timeout'
    assert client.get('/api/ordinary-reminders').json()==[]
    assert client.get('/api/search',params={'q':'咖啡'}).json()


def test_diagnostics_reconciles_confirmed_stale_evidence(client):
    path,_=setup(client)
    judge_next(path,lambda _:'{"confirmed":true}')
    client.post('/api/memories/1/correct',json={})
    data=client.get('/api/ordinary-reminders/diagnostics').json()
    assert data['counts']=={'stale':1}
    assert data['items'][0]['error']=='evidence_or_policy_changed'
