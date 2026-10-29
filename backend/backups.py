"""Version-upgrade snapshots only; never delete user-created backups."""
import os
import shutil
import sqlite3
import uuid
from contextlib import ExitStack, contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path

PREFIX = 'migration-auto-'


@contextmanager
def migration_guard(path):
    from .runtime import worker_lock, owned_workbench
    with ExitStack() as stack:
        if not owned_workbench(path.parent):
            stack.enter_context(worker_lock(path.parent/'runtime', 'workbench'))
        for name in ('collector', 'bot'):
            stack.enter_context(worker_lock(path.parent/name, name))
        yield


def snapshot(db, path, version, target):
    directory = path.parent/'backups'
    directory.mkdir(exist_ok=True)
    resolved = directory.resolve()
    for key in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial'):
        if os.environ.get(key) and resolved.is_relative_to(Path(os.environ[key]).resolve()):
            raise RuntimeError('备份目录位于已知云同步目录，停止升级')
    size = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
    if shutil.disk_usage(directory).free < size * 2 + 64*1024*1024:
        raise RuntimeError('备份磁盘空间不足，停止升级')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    final = directory/f'{PREFIX}{stamp}-v{version}-to-v{target}-{uuid.uuid4().hex[:8]}.sqlite3'
    temporary = final.with_suffix('.tmp')
    try:
        with closing(sqlite3.connect(temporary)) as dest:
            db.backup(dest)
            if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('备份完整性校验失败，停止升级')
            if dest.execute('PRAGMA user_version').fetchone()[0] != version:
                raise RuntimeError('备份版本不一致，停止升级')
        temporary.replace(final)
    finally:
        temporary.unlink(missing_ok=True)
    return final


def prune(path):
    for old in sorted((path.parent/'backups').glob(PREFIX+'*.sqlite3'), reverse=True)[5:]:
        old.unlink()


def status(path):
    files = sorted((path.parent/'backups').glob(PREFIX+'*.sqlite3'), reverse=True)
    return {'count':len(files), 'latest':files[0].name if files else None,
            'retention':5, 'plaintext':True}
