"""Fixed local worker controls; no client-provided commands or process IDs."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from contextvars import ContextVar

ROOT = Path(__file__).resolve().parents[1]
MODULES = {'collector': 'tools.collect_contact', 'bot': 'tools.bot_dispatch'}
CONTROL_LOCK = threading.Lock()
OWNED_WORKBENCH = ContextVar('owned_workbench', default=None)


def owned_workbench(data_dir):
    return OWNED_WORKBENCH.get() == data_dir.resolve()


@contextmanager
def workbench_session(data_dir):
    """OS releases this lock even when the console closes or the backend crashes."""
    directory=data_dir/'runtime'
    with worker_lock(directory,'workbench'):
        owned = OWNED_WORKBENCH.set(data_dir.resolve())
        session=uuid.uuid4().hex
        (directory/'session').write_text(session,encoding='ascii')
        try:
            yield session
        finally:
            OWNED_WORKBENCH.reset(owned)
            for name in MODULES:
                (data_dir/name).mkdir(parents=True,exist_ok=True)
                (data_dir/name/'stop.request').touch()


def current_session(data_dir):
    directory=data_dir/'runtime'
    try:
        session=(directory/'session').read_text(encoding='ascii')
        return session if locked(directory,'workbench') else None
    except OSError:
        return None


def session_alive(data_dir, session):
    return bool(session and current_session(data_dir)==session)


@contextmanager
def worker_lock(directory, name):
    import msvcrt
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / (name + '.lock')).open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def locked(directory, name):
    try:
        with worker_lock(directory, name):
            return False
    except OSError:
        return True


def replace_status(temporary, path):
    # Windows readers can briefly deny replacement of an open status file.
    for attempt in range(6):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt==5:raise
            time.sleep(.02)


def write_status(directory, state):
    path = directory / 'status.json'
    temporary = directory / f'status-{os.getpid()}.tmp'
    temporary.write_text(json.dumps({'state': state, 'updated_at': datetime.now(timezone.utc).isoformat()}), encoding='utf-8')
    replace_status(temporary,path)


def status(data_dir, name):
    directory = data_dir / name
    value = {}
    try:
        value = json.loads((directory / 'status.json').read_text(encoding='utf-8'))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(value['updated_at'])).total_seconds()
    except (OSError, ValueError, KeyError, TypeError):
        age = float('inf')
    running = locked(directory, name)
    starting = value.get('state') == 'starting' and 0 <= age < 15
    if running:
        state = 'stopping' if (directory / 'stop.request').exists() else ('running' if age < 90 else 'unresponsive')
    else:
        state = ('stopping' if (directory / 'stop.request').exists() else 'starting') if starting else ('error' if value.get('state') in {'starting', 'stopped_error', 'uncertain'} else 'stopped')
    return {'state': state, 'active': running or starting, 'updated_at': value.get('updated_at')}


def control(data_dir, name, action):
    directory = data_dir / name
    with CONTROL_LOCK:
        current = status(data_dir, name)
        stop = directory / 'stop.request'
        if action == 'stop':
            stop.touch()
            return status(data_dir, name)
        if current['active']:
            return current
        if not current_session(data_dir):
            raise ValueError('请先启动工作台；采集和机器人随工作台退出')
        if name == 'collector' and not all((directory / f).exists() for f in ('binding.json', 'initialized.json')):
            raise ValueError('请先完成指定联系人会话的绑定和首次导入')
        stop.unlink(missing_ok=True)
        write_status(directory, 'starting')
        try:
            with (directory / 'worker.log').open('ab') as log:
                subprocess.Popen([sys.executable, '-X', 'utf8', '-m', MODULES[name], '--watch', '--managed'],
                                 cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError:
            write_status(directory, 'stopped_error')
            raise ValueError('后台程序启动失败，请检查本地运行环境') from None
        return status(data_dir, name)
