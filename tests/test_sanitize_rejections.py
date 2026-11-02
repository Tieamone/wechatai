import json
import pytest
from tools.sanitize_rejections import run


def test_dry_run_and_explicit_snapshot_approval(tmp_path):
    file=tmp_path/'rejection-fixture.json'
    original={'raw_kind':'http_json','raw':json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'visible','reasoning_content':'private thought'}}]})}
    file.write_text(json.dumps(original),encoding='utf-8');before=file.read_bytes()
    report=run(tmp_path)
    assert report['dry_run'] and report['count']==1 and file.read_bytes()==before
    assert 'private thought' not in json.dumps(report)
    with pytest.raises(ValueError):run(tmp_path,approved='wrong')
    assert file.read_bytes()==before
    result=run(tmp_path,approved=report['manifest_sha256'])
    assert not result['dry_run'] and 'private thought' not in file.read_text()
    assert json.loads(json.loads(file.read_text())['raw'])['choices'][0]['message']['content']=='visible'
    assert run(tmp_path)['count']==0
