import json
from unittest.mock import Mock
import httpx,pytest
from fastapi import HTTPException
from backend import ai_settings,diagnostics
from backend.model_contract import issue_request
from backend.store import connect
from test_core import client
from test_role_normalization import setup


def test_default_wire_payload_unchanged():
    config={'model':'fixture','thinking_mode':'off'}
    default=ai_settings.request_body(config,'system',{})
    assert default['max_tokens']==4096 and 'enable_thinking' not in default
    off=ai_settings.request_body({**config,'_extraction_thinking_mode':'off'},'system',{})
    assert off=={**default,'enable_thinking':False}
    assert 'extra_body' not in off and 'thinking_budget' not in off and 'reasoning_effort' not in off


def test_request_hash_freezes_mode(client):
    path,_=setup(client)
    a=issue_request(path,1,normalize_d304=False,thinking_mode='default')
    with connect(path) as db:first=db.execute('SELECT input_hash FROM model_request WHERE id=?',(a['request_id'],)).fetchone()[0]
    b=issue_request(path,1,normalize_d304=False,thinking_mode='off')
    with connect(path) as db:
        row=db.execute('SELECT * FROM model_request WHERE id=?',(b['request_id'],)).fetchone()
        assert first!=row['input_hash'] and json.loads(row['payload_json'])['policy']['thinking_mode']=='off'


@pytest.mark.parametrize('reasoning,status',[(None,'unknown'),(0,'zero_reported'),(7,'reasoning_reported')])
def test_fake_sender_observation_is_not_assumed_success(client,reasoning,status):
    path,_=setup(client);config={'thinking_mode':'off'}
    def sender(c,*args):
        config['thinking_mode']='default'
        assert c['_extraction_thinking_mode']=='off'
        diagnostics.observe(reasoning_tokens=reasoning)
        return '{"memories":[]}'
    ai_settings.extract_job(path,1,config,sender=sender)
    with connect(path) as db:
        saved=json.loads(db.execute('SELECT result_json FROM job WHERE id=1').fetchone()[0])
        assert saved['extraction_policy']['thinking_mode']=='off'
        assert saved['thinking_observation']['status']==status


def test_parameter_rejection_is_distinct_and_never_retries(monkeypatch):
    monkeypatch.setattr(ai_settings,'protect',lambda *a,**k:b'synthetic')
    requests=[]
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(400,json={'error':{'message':'enable_thinking unsupported'}})
    factory=httpx.Client
    monkeypatch.setattr(ai_settings.httpx,'Client',lambda **kw:factory(transport=httpx.MockTransport(handle),**kw))
    with pytest.raises(HTTPException) as raised:
        ai_settings.complete({'key_encrypted':'eA==','base_url':'https://synthetic.invalid','model':'fixture','_extraction_thinking_mode':'off'},'system',{})
    assert raised.value.diagnostic['code']=='D401' and len(requests)==1
    assert requests[0]['enable_thinking'] is False
