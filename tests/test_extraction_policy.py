import json
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from backend import ai_settings
from backend.model_contract import issue_request,commit_response,Extraction,role_audit
from backend.store import connect
from test_core import client
from test_role_normalization import setup
from test_model_contract import result


def test_preflight_counts_identical_with_switch_off_on():
    facts=Extraction(memories=[result(refs=['s1','s2']),result(refs=['s2']),result(refs=['s3'])])
    roles={'s1':'peer','s2':'self','s3':'system'};refs={r:{'id':i,'revision':1} for i,r in enumerate(roles,1)}
    off=role_audit(facts,roles,refs,False);on=role_audit(facts,roles,refs,True)
    assert off['d304_event_count']==on['d304_event_count']==3
    assert off['dropped_memories']==0 and on['dropped_memories']==2
    assert {r['category'] for r in off['items']}=={'mixed_roles','other_role_only','system_role_reference'}


def test_request_policy_frozen_and_changed_setting_does_not_change_commit(client):
    path,_=setup(client)
    request=issue_request(path,1,normalize_d304=True)
    raw=json.dumps({'memories':[result(refs=['s1','s2'])]})
    with pytest.raises(HTTPException) as error:commit_response(path,request['request_id'],raw,normalize_d304=False)
    assert error.value.diagnostic['code']=='D209'
    committed=commit_response(path,request['request_id'],raw)
    assert committed['audit']['enabled'] and len(committed['memory_ids'])==1


@pytest.mark.parametrize('enabled',[False,True])
def test_extract_job_wired_and_failed_audit_survives_rollback(client,enabled):
    path,_=setup(client)
    config={'normalize_d304':enabled}
    raw=json.dumps({'memories':[result(refs=['s1','s2']),result(refs=['s2'])]})
    sender=Mock(return_value=raw)
    if enabled:
        output=ai_settings.extract_job(path,1,config,sender=sender)
        assert len(output['memory_ids'])==1
    else:
        with pytest.raises(HTTPException):ai_settings.extract_job(path,1,config,sender=sender)
    sender.assert_called_once()
    with connect(path) as db:
        job=db.execute('SELECT * FROM job WHERE id=1').fetchone()
        audit=json.loads(job['result_json'])['extraction_audit']
        assert audit['d304_event_count']==2 and audit['enabled']==enabled
        assert db.execute('SELECT count(*) FROM memory').fetchone()[0]==int(enabled)
    events=client.get('/api/initializations/1/extraction-audits?events_only=true').json()
    assert len(events['items'])==1


def test_settings_default_off_and_event_only_filter(client):
    assert client.get('/api/ai/settings').json()['normalize_d304'] is False
    path,request=setup(client)
    commit_response(path,request['request_id'],json.dumps({'memories':[]}))
    assert len(client.get('/api/initializations/1/extraction-audits').json()['items'])==1
    assert client.get('/api/initializations/1/extraction-audits?events_only=true').json()['items']==[]


def test_new_invalid_response_does_not_reuse_previous_audit(client):
    path,_=setup(client)
    with connect(path) as db:db.execute("UPDATE job SET result_json=? WHERE id=1",(json.dumps({'extraction_audit':{'d304_event_count':99}}),))
    with pytest.raises(HTTPException):ai_settings.extract_job(path,1,{},sender=lambda *args:'{"memories":[{}]}')
    with connect(path) as db:
        saved=json.loads(db.execute('SELECT result_json FROM job WHERE id=1').fetchone()[0])
        assert 'extraction_audit' not in saved and saved['extraction_policy']['normalize_d304'] is False
