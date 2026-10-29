import copy
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.extraction import extract_next
from backend.jobs import prepare_one
from backend.model_contract import issue_request
from tools.synthetic_demo import seed
from tools.context_fixture import fixture_provider, EXPECTED, TEXTS


def test_complete_negation_and_reported_context(tmp_path):
    seed(tmp_path,'context')
    app=create_app(tmp_path,'context-test')
    path=app.state.db_path
    assert prepare_one(path)['state']=='prepared'
    request=issue_request(path,1)
    assert request['data']['context_limited']
    assert 'conversation_has_unprocessed_nontext' in request['data']['context_warnings']
    assert [r['text'] for r in request['data']['messages']]==TEXTS
    partial=copy.deepcopy(request)
    partial['data']['messages'].pop()
    with pytest.raises(ValueError):
        fixture_provider(partial)
    assert extract_next(path,fixture_provider)['state']=='validated_synthetic'
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer context-test'}) as client:
        cards=sorted(client.get('/api/memories').json(),key=lambda m:m['id'])
        assert len(cards)==2
        for i,(card,expected) in enumerate(zip(cards,EXPECTED)):
            assert all(card[k]==v for k,v in expected.items() if k!='source_refs')
            assert [e['quote'] for e in card['evidence']]==TEXTS[i*2:i*2+2]
            assert card['origin']=='synthetic'
        assert client.get('/api/reminders').json()==[]
        assert not client.get('/api/initializations').json()[0]['extraction_complete']
