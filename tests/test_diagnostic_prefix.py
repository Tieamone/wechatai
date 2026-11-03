import json
from unittest.mock import Mock
import httpx
import pytest
from fastapi import HTTPException
from backend import ai_settings,diagnostics


@pytest.mark.parametrize('raw,code,has_prefix',[
    (b'{"reasoning_content":"private thought', 'D102',False),
    (json.dumps({'reasoning_content':'private thought','choices':[]}).encode(),'D106',True),
    (b'[]','D106',False),
])
def test_prefix_is_bounded_filtered_and_not_exposed(tmp_path,monkeypatch,raw,code,has_prefix):
    monkeypatch.setattr(ai_settings,'protect',lambda *a,**k:b'synthetic')
    transport=httpx.MockTransport(lambda request:httpx.Response(200,content=raw,headers={'content-type':'application/json'}))
    factory=httpx.Client
    monkeypatch.setattr(ai_settings.httpx,'Client',lambda **kw:factory(transport=transport,**kw))
    path=tmp_path/'memory.sqlite3'
    with pytest.raises(HTTPException):
        with diagnostics.capture(path,'synthetic'):
            ai_settings.complete({'key_encrypted':'eA==','base_url':'https://synthetic.invalid','model':'fixture'},'system',{})
    record=json.loads(diagnostics.files(path)[0].read_text(encoding='utf-8'))
    assert record['code']==code and record['http_status']==200
    assert record['content_type']=='application/json' and record['raw'] is None
    assert bool(record['raw_prefix'])==has_prefix
    assert len((record['raw_prefix'] or '').encode())<=2048
    assert 'private thought' not in json.dumps(record)
    metadata=diagnostics.listing(path)['records'][0]
    assert 'raw_prefix' not in metadata and 'raw' not in metadata


def test_other_codes_do_not_keep_prefix(tmp_path):
    path=tmp_path/'memory.sqlite3'
    diagnostics.save(path,'synthetic',diagnostics.failure(422,'synthetic','D304'),{'raw':b'visible','raw_prefix':'should not save'})
    assert 'raw_prefix' not in json.loads(diagnostics.files(path)[0].read_text())


@pytest.mark.parametrize('content_type,keep',[('text/plain; charset=utf-8',True),('text/html',True),('application/json',False)])
def test_d102_text_prefix_and_json_omission(tmp_path,monkeypatch,content_type,keep):
    raw=('网关响应'*1000).encode('utf-8')
    monkeypatch.setattr(ai_settings,'protect',lambda *a,**k:b'synthetic')
    transport=httpx.MockTransport(lambda request:httpx.Response(200,content=raw,headers={'content-type':content_type}))
    factory=httpx.Client;monkeypatch.setattr(ai_settings.httpx,'Client',lambda **kw:factory(transport=transport,**kw))
    path=tmp_path/'memory.sqlite3'
    with pytest.raises(HTTPException):
        with diagnostics.capture(path,'synthetic'):
            ai_settings.complete({'key_encrypted':'eA==','base_url':'https://synthetic.invalid','model':'fixture'},'system',{})
    record=json.loads(diagnostics.files(path)[0].read_text(encoding='utf-8'))
    assert record['code']=='D102' and bool(record['raw_prefix'])==keep
    assert len((record['raw_prefix'] or '').encode('utf-8'))<=2048 and record['raw'] is None
