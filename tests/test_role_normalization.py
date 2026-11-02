import json
from copy import deepcopy
import pytest
from fastapi import HTTPException
from backend import diagnostics
from backend.memories import create_memory,MemoryDraft,Evidence
from backend.model_contract import commit_response,issue_request
from backend.jobs import prepare_one
from backend.store import connect
from test_core import client,batch
from test_model_contract import result


def setup(client):
    body=batch('合成对方陈述')
    other=deepcopy(body['messages'][0]);other.update(sender_role='self',text='合成本人提问');other['source']['id']='2'
    body['messages'].append(other)
    client.post('/api/import',json=body).raise_for_status()
    client.post('/api/initializations',json={'mode':'full'}).raise_for_status()
    prepare_one(client.app.state.db_path)
    return client.app.state.db_path,issue_request(client.app.state.db_path,1)


def test_mixed_preserved_opposite_dropped_and_audit_is_atomic(client):
    path,request=setup(client)
    raw=json.dumps({'memories':[result(refs=['s1','s2']),result('应被丢弃',refs=['s2'])]})
    with pytest.raises(HTTPException):commit_response(path,request['request_id'],raw)
    output=commit_response(path,request['request_id'],raw,normalize_d304=True)
    assert len(output['memory_ids'])==1
    assert output['audit']['removed_references']==0 and output['audit']['dropped_memories']==1
    assert len(output['audit']['items'][0]['kept'])==2
    assert output['audit']['mixed_role_memories']==1
    assert '应被丢弃' not in json.dumps(output['audit'])
    with connect(path) as db:
        assert [r[0] for r in db.execute('SELECT message_id FROM memory_evidence ORDER BY message_id')]==[1,2]
    card=client.get('/api/memories').json()[0]
    assert card['mixed_role_evidence'] and not card['confirmed']
    assert len(card['evidence'])==2
    assert card['assertion']=='direct' and card['summary']=='对方喜欢咖啡'
    detail=client.get('/api/initializations/1/extraction-audits').json()
    assert detail['items'][0]['audit']==output['audit']
    assert commit_response(path,request['request_id'],raw,normalize_d304=True)['duplicate']
    with pytest.raises(HTTPException) as error:commit_response(path,request['request_id'],raw)
    assert error.value.diagnostic['code']=='D209'


@pytest.mark.parametrize('bad',[result(refs=['s99']),result('[PERSON_missing]',refs=['s2']),{**result(),'extra':'invalid'}])
def test_other_errors_rollback_even_after_normalized_memory(client,bad):
    path,request=setup(client)
    with pytest.raises(HTTPException):
        commit_response(path,request['request_id'],json.dumps({'memories':[result(refs=['s1','s2']),bad]}),normalize_d304=True)
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM memory').fetchone()[0]==0
        assert 'extraction_audit' not in (db.execute('SELECT result_json FROM job').fetchone()[0] or '')


def test_all_dropped_window_has_visible_audit(client):
    path,request=setup(client)
    output=commit_response(path,request['request_id'],json.dumps({'memories':[result(refs=['s2'])]}),normalize_d304=True)
    assert output['memory_ids']==[] and output['audit']['dropped_memories']==1
    assert client.get('/api/initializations/1/extraction-audits').json()['items'][0]['audit']['dropped_memories']==1


def test_manual_memory_remains_strict(client):
    path,_=setup(client)
    with connect(path) as db:
        draft=MemoryDraft(summary='合成',subject='peer',polarity='positive',assertion='direct',temporal_kind='fact',evidence=[Evidence(message_id=2,message_revision=1,span_start=0,span_end=2)])
        with pytest.raises(HTTPException) as error:create_memory(db,draft,allow_mixed_roles=True)
        assert error.value.diagnostic['code']=='D304'


def test_lifecycle_errors_do_not_evict_output_rejections(tmp_path):
    path=tmp_path/'memory.sqlite3'
    for _ in range(25):
        with pytest.raises(HTTPException):
            with diagnostics.capture(path,'synthetic'):
                diagnostics.observe(raw='sensitive')
                raise diagnostics.failure(409,'changed display text','D203')
    assert diagnostics.files(path)==[]
    with pytest.raises(HTTPException):
        with diagnostics.capture(path,'synthetic'):
            diagnostics.observe(raw='synthetic')
            raise diagnostics.failure(422,'changed display text','D304')
    assert diagnostics.listing(path)['records'][0]['code']=='D304'


def test_preview_preserves_quotes_escapes_content_and_does_not_write(client,tmp_path):
    from tools.mixed_role_preview import generate
    path,request=setup(client)
    raw=json.dumps({'memories':[result('</script><img src=x onerror=alert(1)>',refs=['s1','s2'])]})
    with pytest.raises(HTTPException):commit_response(path,request['request_id'],raw)
    preview=tmp_path/'preview.html'
    output=generate(path,preview)
    assert output['cards']==1
    html=preview.read_text(encoding='utf-8')
    assert '<img src=x' not in html and "connect-src 'none'" in html
    assert 'innerHTML' not in html
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM memory').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM memory_review').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM usage_ledger').fetchone()[0]==0
