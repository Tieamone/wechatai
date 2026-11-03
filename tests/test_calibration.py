import json
from backend.store import connect
from test_core import client,batch
from test_memories import draft
from tools.calibration_page import generate
from tools.calibration_agreement import evaluate


def test_readonly_page_has_no_default_labels_or_unsafe_markup(client,tmp_path):
    client.post('/api/import',json=batch()).raise_for_status()
    client.post('/api/memories',json=draft(summary='合成 </script><img src=x onerror=alert(1)>')).raise_for_status()
    path=client.app.state.db_path;output=tmp_path/'page.html'
    with connect(path) as db:before=db.execute('SELECT count(*) FROM feedback').fetchone()[0]
    report=generate(path,output);text=output.read_text(encoding='utf-8')
    assert report['cards']==1 and report['human_labels_filled']==0
    assert 'connect-src \'none\'' in text and 'script-src \'nonce-' in text
    assert '</script><img' not in text and 'health_finance:false' in text
    assert 'conclusion:null' in text and 'localStorage' in text and '导入进度' in text
    with connect(path) as db:assert db.execute('SELECT count(*) FROM feedback').fetchone()[0]==before


def test_dimension_agreement_does_not_infer_accuracy():
    human={'snapshot_sha256':'synthetic','labels':[
        {'memory_id':1,'memory_version':1,'conclusion':'long','evidence_support':None,'validity_bin':'long_term','sensitive_type':'health'},
        {'memory_id':2,'memory_version':1,'conclusion':'incorrect','evidence_support':'unsupported','validity_bin':None,'sensitive_type':None}]}
    reviewer={'snapshot_sha256':'synthetic','reviews':[
        {'memory_id':1,'memory_version':1,'category':'long','evidence_support':'supported','validity_bin':'long_term','sensitive_type':'health'},
        {'memory_id':2,'memory_version':1,'category':'trivial','evidence_support':'supported'}]}
    result=evaluate(human,reviewer)
    assert result['dimensions']['category']['comparable']==1
    assert result['dimensions']['evidence_support']['comparable']==1
    assert result['dimensions']['evidence_support']['agreement']==0
    assert result['dimensions']['validity_bin']['agreement']==1
    assert not result['automatic_decision_allowed']
    reviewer['reviews'][0]['memory_version']=2
    assert evaluate(human,reviewer)['dimensions']['validity_bin']['agreement'] is None


def test_disagreeing_snapshot_is_rejected():
    import pytest
    with pytest.raises(ValueError):evaluate({'snapshot_sha256':'old','labels':[]},{'snapshot_sha256':'new','reviews':[]})


def test_workload_distinguishes_unassigned_messages_from_frozen_windows(client):
    from tools.workload_report import report
    client.post('/api/import',json=batch()).raise_for_status()
    initial=report(client.app.state.db_path)
    assert initial['remaining_history_windows']==0 and initial['known_text_not_yet_assigned']==1
    assert initial['additional_windows_estimate']==1
    client.post('/api/initializations',json={'mode':'full'}).raise_for_status()
    frozen=report(client.app.state.db_path)
    assert frozen['remaining_history_windows']==1 and frozen['known_text_not_yet_assigned']==0
    assert frozen['estimated_total_tokens']==2600


def test_new_semantics_derive_support_with_explicit_override():
    human={'format':2,'label_semantics':'accuracy_from_conclusion_v2','snapshot_sha256':'synthetic','labels':[
        {'memory_id':1,'memory_version':1,'conclusion':'long'},
        {'memory_id':2,'memory_version':1,'conclusion':'incorrect'},
        {'memory_id':3,'memory_version':1,'conclusion':'short','evidence_support':'unsupported'},
        {'memory_id':4,'memory_version':1,'conclusion':'uncertain'}]}
    reviewer={'snapshot_sha256':'synthetic','reviews':[{'memory_id':i,'memory_version':1,'evidence_support':v} for i,v in [(1,'supported'),(2,'unsupported'),(3,'unsupported'),(4,'supported')]]}
    result=evaluate(human,reviewer)
    assert result['derived_support_count']==2 and result['explicit_support_count']==1
    assert result['support_conflicts']==1 and result['dimensions']['evidence_support']['comparable']==3
    assert result['dimensions']['evidence_support']['agreement']==1
    assert result['evidence_support_sources'][0]['source']=='derived_v2'
    human['format']=1
    assert evaluate(human,reviewer)['derived_support_count']==0


def test_review_sensitive_boolean_adapter():
    human={'snapshot_sha256':'synthetic','labels':[{'memory_id':1,'memory_version':1,'sensitive_type':'health_financial'}]}
    reviewer={'snapshot_sha256':'synthetic','reviews':[{'memory_id':1,'memory_version':1,'sensitive_type':{'health':True,'financial':True,'privacy_token':False}}]}
    assert evaluate(human,reviewer)['dimensions']['sensitive_type']['agreement']==1
    reviewer['reviews'][0]['sensitive_type']['health']=1
    assert evaluate(human,reviewer)['dimensions']['sensitive_type']['comparable']==0
