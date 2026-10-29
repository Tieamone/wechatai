from datetime import datetime, timezone, timedelta
import json
from unittest.mock import Mock
import pytest

from fastapi.testclient import TestClient

from backend.app import create_app
from backend import runtime


def test_control_start_once_and_stop_during_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime,'current_session',lambda _: 'test-session')
    spawn = Mock()
    monkeypatch.setattr(runtime.subprocess, 'Popen', spawn)
    assert runtime.control(tmp_path, 'bot', 'start')['state'] == 'starting'
    assert runtime.control(tmp_path, 'bot', 'start')['active']
    assert spawn.call_count == 1
    assert spawn.call_args.args[0][-3:] == ['tools.bot_dispatch', '--watch', '--managed']
    assert runtime.control(tmp_path, 'bot', 'stop')['state'] == 'stopping'
    runtime.control(tmp_path, 'bot', 'start')
    assert (tmp_path/'bot/stop.request').exists()
    assert spawn.call_count == 1


def test_lock_is_authority_not_old_heartbeat(tmp_path):
    directory = tmp_path/'bot'
    with runtime.worker_lock(directory, 'bot'):
        runtime.write_status(directory, 'running')
        assert runtime.status(tmp_path, 'bot')['state'] == 'running'
        assert runtime.control(tmp_path, 'bot', 'stop')['state'] == 'stopping'
    assert runtime.status(tmp_path, 'bot')['state'] == 'stopped'


def test_start_failure_and_missing_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime,'current_session',lambda _: 'test-session')
    spawn = Mock(side_effect=OSError())
    monkeypatch.setattr(runtime.subprocess, 'Popen', spawn)
    import pytest
    with pytest.raises(ValueError, match='首次导入'):
        runtime.control(tmp_path, 'collector', 'start')
    spawn.assert_not_called()
    with pytest.raises(ValueError, match='启动失败'):
        runtime.control(tmp_path, 'bot', 'start')
    assert runtime.status(tmp_path, 'bot')['state'] == 'error'


def test_crashed_start_does_not_stay_active(tmp_path):
    directory = tmp_path/'bot'
    directory.mkdir()
    (directory/'status.json').write_text(json.dumps({'state':'starting','updated_at':(datetime.now(timezone.utc)-timedelta(seconds=20)).isoformat()}))
    assert runtime.status(tmp_path,'bot') == {'state':'error','active':False,'updated_at':json.loads((directory/'status.json').read_text())['updated_at']}


def test_routes_require_token_and_isolate_test_data(tmp_path, monkeypatch):
    control = Mock()
    monkeypatch.setattr(runtime, 'control', control)
    with TestClient(create_app(tmp_path, 'test'), base_url='http://127.0.0.1:8765') as client:
        assert client.post('/api/runtime/bot/start').status_code == 401
        client.headers['Authorization'] = 'Bearer test'
        assert client.get('/api/runtime').json() == {'enabled':False}
        assert client.post('/api/runtime/bot/start').status_code == 409
        assert client.post('/api/runtime/arbitrary/start').status_code == 422
    control.assert_not_called()


@pytest.mark.parametrize('stop_before_start', [True, False])
def test_bot_managed_stop_and_uncertain_exit(tmp_path, monkeypatch, stop_before_start):
    from tools import bot_dispatch
    from backend.store import initialize
    monkeypatch.setattr(bot_dispatch, 'ROOT', tmp_path)
    monkeypatch.setattr('sys.argv', ['bot_dispatch', '--watch', '--managed'])
    initialize(tmp_path/'data/memory.sqlite3')
    directory = tmp_path/'data/bot'
    directory.mkdir()
    if stop_before_start:
        (directory/'stop.request').touch()
    dispatch = Mock(return_value='uncertain')
    monkeypatch.setattr(bot_dispatch, 'dispatch_once', dispatch)
    with runtime.workbench_session(tmp_path/'data'):
        assert bot_dispatch.main() == (0 if stop_before_start else 2)
    assert runtime.status(tmp_path/'data', 'bot')['state'] == ('stopped' if stop_before_start else 'error')
    assert dispatch.call_count == (0 if stop_before_start else 1)
