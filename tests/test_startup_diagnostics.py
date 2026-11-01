from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend import __main__ as startup
from backend.backups import clear_failure, record_failure, status
from backend.runtime import locked


@pytest.mark.parametrize('error',[OSError('runtime I/O failure'),RuntimeError('runtime failure')])
def test_runtime_error_is_not_reported_as_migration_failure(tmp_path,monkeypatch,error,capsys):
    monkeypatch.setattr(startup,'ROOT',tmp_path)
    monkeypatch.setattr(startup,'create_app',Mock(return_value=SimpleNamespace(state=SimpleNamespace(token='synthetic'))))
    monkeypatch.setattr(startup.uvicorn,'Config',Mock())
    server=SimpleNamespace(run=Mock(side_effect=error))
    monkeypatch.setattr(startup.uvicorn,'Server',Mock(return_value=server))
    monkeypatch.setattr(startup.threading,'Thread',Mock())
    with pytest.raises(type(error),match='runtime'):
        startup.main()
    assert status(tmp_path/'data/memory.sqlite3')['last_failure'] is None
    assert '升级' not in capsys.readouterr().out
    assert not locked(tmp_path/'data/runtime','workbench')


@pytest.mark.parametrize('started',[False,True])
def test_clear_failure_only_after_server_started(tmp_path,monkeypatch,started):
    path=tmp_path/'data/memory.sqlite3';path.parent.mkdir()
    record_failure(path,'合成旧启动失败')
    monkeypatch.setattr(startup,'ROOT',tmp_path)
    browser=Mock();monkeypatch.setattr(startup.webbrowser,'open',browser)
    codes=SimpleNamespace(issue=Mock(return_value='synthetic-once'))
    startup.open_console(SimpleNamespace(started=started,should_exit=True),SimpleNamespace(state=SimpleNamespace(login_codes=codes)))
    assert bool(status(path)['last_failure']) is not started
    assert browser.call_count==int(started)
    assert codes.issue.call_count==int(started)


def test_failure_cleanup_permission_error_is_nonfatal(tmp_path,monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path,'unlink',Mock(side_effect=PermissionError('synthetic')))
    clear_failure(tmp_path/'memory.sqlite3')
