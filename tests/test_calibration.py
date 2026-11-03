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
