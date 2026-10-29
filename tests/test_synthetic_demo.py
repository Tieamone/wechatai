import time
import json

from fastapi.testclient import TestClient

from backend.app import create_app
from tools.synthetic_demo import seed, fixture_provider, fixture_judge, ROOT


def test_background_extraction_demo(tmp_path):
    seed(tmp_path)
    app=create_app(tmp_path,'demo-test',enable_worker=True,synthetic_provider=fixture_provider,synthetic_judge=fixture_judge)
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer demo-test'}) as client:
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            memories=client.get('/api/memories').json()
            if len(memories)==2:
                break
            time.sleep(.05)
        assert len(memories)==2
        assert all(m['origin']=='synthetic' for m in memories)
        assert {m['summary'] for m in memories}=={'晚上不喝咖啡，避免睡不着','计划周末去公园散步'}
        assert client.get('/api/status').json()['synthetic_demo']
        assert client.get('/api/initializations').json()[0]['counts']=={'validated_synthetic':1}
        assert client.get('/api/initializations').json()[0]['extraction_complete'] is False
        assert client.get('/api/reminders').json()==[]
        live=json.loads((ROOT/'samples'/'live.json').read_text(encoding='utf-8'))
        client.post('/api/import',json=live).raise_for_status()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            ordinary=client.get('/api/ordinary-reminders').json()
            if ordinary:
                break
            time.sleep(.05)
        assert len(ordinary)==1
        assert ordinary[0]['memory']['summary']=='晚上不喝咖啡，避免睡不着'
        assert ordinary[0]['origin']=='synthetic'
        assert client.get('/api/reminders').json()==[]  # No watch was required.
    assert not app.state.preparation_worker.thread.is_alive()
