from backend.store import initialize,connect
from schema_helpers import remove_pricing
from tools.migration_rehearsal import run


def test_migration_uses_only_copy_and_cleans_temporary_files(tmp_path):
    source=tmp_path/'memory.sqlite3';initialize(source)
    with connect(source) as db:
        remove_pricing(db);db.execute('PRAGMA user_version=22')
        db.execute("INSERT INTO conversation VALUES('synthetic','synthetic','synthetic')")
    report=run(source,tmp_path/'private')
    assert report['passed'] and report['source_version_after']==22 and report['migrated_version']==24
    assert report['temporary_copy_removed'] and report['old_columns_equal'] and report['restore_old_columns_equal']
    assert report['integrity_check']=='ok' and report['automatic_backups']==1
    assert list((tmp_path/'private').iterdir())==[]
