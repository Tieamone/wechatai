from schema_helpers import remove_v21
import sqlite3
import shutil
from unittest.mock import patch
import pytest
from backend.store import initialize, connect
from backend.backups import PREFIX, snapshot, prune
from backend.runtime import worker_lock


def old_database(tmp_path):
    path=tmp_path/'memory.sqlite3'
    initialize(path)
    with connect(path) as db:
        remove_v21(db)
        db.executescript('DROP TRIGGER job_message_insert; DROP TRIGGER deferred_cancel; DROP TRIGGER deferred_revision; DROP TRIGGER feedback_revision; DROP TABLE job_message; DROP TABLE usage_ledger; DROP TABLE feedback; DROP INDEX job_initialization; ')
        for name in ('notification_record','notification_policy','deferred_message','memory_review'):
            db.execute('DROP TABLE '+name)
        db.execute('PRAGMA user_version=17')
        db.execute("INSERT INTO conversation VALUES('fixture','fixture','fixture')")
    return path


def test_upgrade_and_restore(tmp_path):
    path=old_database(tmp_path)
    initialize(path)
    files=list((tmp_path/'backups').glob(PREFIX+'*.sqlite3'))
    assert len(files)==1
    with sqlite3.connect(files[0]) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('PRAGMA user_version').fetchone()[0]==17
        assert db.execute('SELECT count(*) FROM conversation').fetchone()[0]==1
    initialize(path)
    assert len(list((tmp_path/'backups').glob(PREFIX+'*.sqlite3')))==1
    restored=tmp_path/'restored'/'memory.sqlite3'
    restored.parent.mkdir()
    shutil.copy2(files[0],restored)
    initialize(restored)
    with connect(restored) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==21
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('SELECT count(*) FROM conversation').fetchone()[0]==1


def test_failure_prevents_migration(tmp_path):
    path=old_database(tmp_path)
    with patch('backend.backups.shutil.disk_usage') as disk:
        disk.return_value.free=0
        with pytest.raises(RuntimeError):initialize(path)
    with connect(path) as db:assert db.execute('PRAGMA user_version').fetchone()[0]==17


@pytest.mark.parametrize('name',['collector','bot','workbench'])
def test_writer_lock_blocks_upgrade(tmp_path,name):
    path=old_database(tmp_path)
    directory=tmp_path/('runtime' if name=='workbench' else name)
    with worker_lock(directory,name),pytest.raises(OSError):initialize(path)
    with connect(path) as db:assert db.execute('PRAGMA user_version').fetchone()[0]==17


def test_retention_and_wal(tmp_path):
    path=old_database(tmp_path)
    with connect(path) as db:
        for _ in range(7):snapshot(db,path,17,18)
    manual=tmp_path/'backups'/'manual.sqlite3';manual.write_bytes(b'keep')
    prune(path)
    assert manual.read_bytes()==b'keep'
    assert len(list(manual.parent.glob(PREFIX+'*.sqlite3')))==5
