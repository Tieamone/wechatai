"""Read the live database; migrate and restore only a disposable private snapshot."""
import argparse,hashlib,json,sqlite3,tempfile
from contextlib import closing
from pathlib import Path
from backend.store import initialize,SCHEMA_VERSION


def inventory(db):
    result={}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        quoted='"'+name.replace('"','""')+'"'
        info=list(db.execute('PRAGMA table_info('+quoted+')'));columns=[r[1] for r in info]
        count=db.execute('SELECT count(*) FROM '+quoted).fetchone()[0]
        result[name]={'rows':count,'columns':columns,'order':[r[1] for r in sorted(info,key=lambda r:r[5]) if r[5]] or ['rowid']}
    return result


def hashes(db,original):
    result={}
    for name,meta in original.items():
        names=','.join('"'+c.replace('"','""')+'"' for c in meta['columns'])
        quoted='"'+name.replace('"','""')+'"';h=hashlib.sha256()
        order=','.join('"'+c.replace('"','""')+'"' for c in meta['order'])
        for row in db.execute('SELECT '+names+' FROM '+quoted+' ORDER BY '+order):
            h.update(json.dumps(tuple(row),ensure_ascii=False,default=lambda b:{'bytes':bytes(b).hex()}).encode());h.update(b'\n')
        result[name]=h.hexdigest()
    return result


def run(source,private):
    source=source.resolve();private=private.resolve();private.mkdir(parents=True,exist_ok=True)
    report={'source_version':None,'target_version':SCHEMA_VERSION,'source_open_mode':'ro','application_started':False,'real_model_calls':0}
    with tempfile.TemporaryDirectory(prefix='migration-rehearsal-',dir=private) as directory:
        folder=Path(directory).resolve()
        if folder.parent!=private or not folder.is_relative_to(private):raise ValueError('临时副本路径越界')
        copied=folder/'memory.sqlite3'
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as db:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            report['source_version']=db.execute('PRAGMA user_version').fetchone()[0]
            if report['source_version']!=22:raise ValueError('本演练只接受 v22 源库，不对真实库迁移')
            original=inventory(db);before=hashes(db,original)
            with closing(sqlite3.connect(copied)) as destination:db.backup(destination)
        initialize(copied)
        with closing(sqlite3.connect(copied)) as db:
            after=inventory(db);report['integrity_check']=db.execute('PRAGMA integrity_check').fetchone()[0]
            report['foreign_key_check_rows']=len(db.execute('PRAGMA foreign_key_check').fetchall())
            report['migrated_version']=db.execute('PRAGMA user_version').fetchone()[0]
            report['row_counts_equal']=all(after[name]['rows']==meta['rows'] for name,meta in original.items())
            report['old_columns_equal']=before==hashes(db,original)
            report['key_rows']={name:meta['rows'] for name,meta in original.items() if name in {'message','memory','memory_evidence','job','model_request','usage_ledger','feedback'}}
        backups=list((folder/'backups').glob('migration-auto-*.sqlite3'))
        report['automatic_backups']=len(backups)
        if len(backups)!=1:raise ValueError('演练未产生唯一的升级前自动备份')
        restored=folder/'restore/memory.sqlite3';restored.parent.mkdir()
        with closing(sqlite3.connect(backups[0].resolve().as_uri()+'?mode=ro',uri=True)) as old,closing(sqlite3.connect(restored)) as dest:old.backup(dest)
        initialize(restored)
        with closing(sqlite3.connect(restored)) as db:
            report['restore_integrity_check']=db.execute('PRAGMA integrity_check').fetchone()[0]
            report['restore_old_columns_equal']=before==hashes(db,original)
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as db:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            report['source_version_after']=db.execute('PRAGMA user_version').fetchone()[0]
            report['source_logical_data_unchanged']=before==hashes(db,original)
        report['passed']=all((report['integrity_check']=='ok',report['restore_integrity_check']=='ok',report['foreign_key_check_rows']==0,report['row_counts_equal'],report['old_columns_equal'],report['restore_old_columns_equal'],report['source_logical_data_unchanged'],report['migrated_version']==SCHEMA_VERSION,report['source_version_after']==22))
    report['temporary_copy_removed']=not folder.exists()
    return report


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1];p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=root/'data/memory.sqlite3');a=p.parse_args()
    result=run(a.source,root/'private/migration-rehearsals')
    (root/'private/migration-rehearsal-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
