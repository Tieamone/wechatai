import json
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from backend import diagnostics
from backend.ai_settings import parse_response
from backend.model_contract import commit_response
from backend.store import connect
from test_model_contract import prepared,result
from test_core import client
from tools.revalidate_model import revalidate


@pytest.mark.parametrize('raw,code',[
 ('{','D102'),('{"memories":[{}]}','D111'),
 (json.dumps({'memories':[{**result(),'secret-personal-field':'private'}]}),'D112'),
 (json.dumps({'memories':[result(refs=['s999'])]}),'D302'),
 (json.dumps({'memories':[result('[PERSON_unknown]')]}),'D303'),
 (json.dumps({'memories':[{**result(),'subject':'self'}]}),'D304'),
 (json.dumps({'memories':[result()]*31}),'D114'),
 (json.dumps({'memories':[result(refs=['s1']*13)]}),'D115'),
])
def test_rejection_metadata_and_readonly_replay(client,raw,code):
    path=client.app.state.db_path;request=prepared(client)
    with pytest.raises(HTTPException):commit_response(path,request['request_id'],raw)
    metadata=diagnostics.listing(path)['records'][0]
    assert metadata['code']==code and 'private' not in json.dumps(metadata)
    assert 'secret-personal-field' not in json.dumps(metadata)
    artifact=diagnostics.files(path)[0]
    with connect(path) as db:before=db.execute('SELECT count(*) FROM memory').fetchone()[0]
    replay=revalidate(path,artifact)
    assert not replay['valid'] and replay['code']==code
    with connect(path) as db:assert db.execute('SELECT count(*) FROM memory').fetchone()[0]==before
    assert len(diagnostics.files(path))==1
    assert client.delete('/api/model-diagnostics').status_code==200
    assert diagnostics.files(path)==[]


def test_success_has_no_artifact(client):
    path=client.app.state.db_path;request=prepared(client)
    commit_response(path,request['request_id'],json.dumps({'memories':[result()]}))
    assert diagnostics.files(path)==[]


def test_caps_and_oversize(tmp_path):
    path=tmp_path/'memory.sqlite3'
    error=HTTPException(422,'untrusted text')
    for _ in range(25):diagnostics.save(path,'synthetic',error,{'raw':'x'*200000})
    assert len(diagnostics.files(path))<=20
    assert sum(f.stat().st_size for f in diagnostics.files(path))<=5*1024*1024
    diagnostics.save(path,'synthetic',error,{'raw':'x'*(diagnostics.MAX_RAW+1)})
    record=json.loads(diagnostics.files(path)[-1].read_text(encoding='utf-8'))
    assert record['raw'] is None and record['response_bytes']==diagnostics.MAX_RAW+1


@pytest.mark.parametrize('body,code',[
 ('bad','D102'),('[]','D106'),
 (json.dumps({'choices':[{'finish_reason':'length'}]}),'D104'),
 (json.dumps({'choices':[{'finish_reason':'stop','message':{'content':None}}]}),'D105'),
])
def test_transport_codes(body,code):
    with pytest.raises(HTTPException) as raised:parse_response(body)
    assert raised.value.diagnostic['code']==code


def test_replay_does_not_call_transport_or_usage(client,monkeypatch):
    from backend import usage,ai_settings
    path=client.app.state.db_path;request=prepared(client)
    raw=json.dumps({'memories':[result()]})
    diagnostics.save(path,request['request_id'],HTTPException(422,'synthetic'),{'raw':raw})
    sender=Mock(side_effect=AssertionError('network'));settle=Mock(side_effect=AssertionError('write'))
    monkeypatch.setattr(ai_settings,'complete',sender);monkeypatch.setattr(usage,'settle',settle)
    assert revalidate(path,diagnostics.files(path)[0])['valid']
    sender.assert_not_called();settle.assert_not_called()
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM memory').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM usage_ledger').fetchone()[0]==0
