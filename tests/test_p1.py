from schema_helpers import remove_v21
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from backend import automatic, usage
from backend.store import connect, initialize
from backend.notifications import pending,deliver
from test_p0 import seed,stamp
from test_core import client
from test_automatic import setup,ready,responder
from backend import ai_settings,policy
from backend.notifications import deliver_association,pending_associations


def test_inactive_head_does_not_block(tmp_path):
    path=seed(tmp_path)
    with connect(path) as db:db.execute('UPDATE watch_item SET enabled=0 WHERE id=1')
    assert pending(path,'fixture')[0]==2
    sender=Mock(return_value='accepted')
    assert deliver(path,2,'fixture',sender)=='accepted'
    sender.assert_called_once()


def test_idle_does_not_start_write_transaction(tmp_path,monkeypatch):
    path=tmp_path/'memory.sqlite3';initialize(path);sql=[]
    @contextmanager
    def traced(p):
        with connect(p) as db:
            db.set_trace_callback(sql.append)
            yield db
    monkeypatch.setattr(automatic,'connect',traced)
    for _ in range(10):assert automatic.process_next(path)=={'processed':False}
    assert not any(s.startswith(('BEGIN','UPDATE','INSERT','DELETE')) for s in sql)


def test_deferred_100k_readonly_and_revision(tmp_path):
    path=tmp_path/'memory.sqlite3';initialize(path)
    with connect(path) as db:
        db.execute("INSERT INTO conversation VALUES('fixture','fixture','fixture')")
        db.executemany("INSERT INTO message(id,conversation_id,adapter,source_id,partition,sender_role,event_utc,text,type,revision) VALUES(?,'fixture','test',?,'test','peer',?,'合成','text',1)",((i,str(i),stamp(1000)) for i in range(1,100001)))
        db.execute("INSERT INTO deferred_message SELECT id,revision,'expired',NULL FROM message")
        init=db.execute("INSERT INTO initialization(mode,cutoff) VALUES('batch',?)",(stamp(),)).lastrowid
        db.execute('UPDATE deferred_message SET initialization_id=?',(init,))
        for start in range(1,100001,10):
            body=json.dumps({'messages':[{'id':i,'revision':1} for i in range(start,min(start+10,100001))]})
            db.execute("INSERT INTO job(initialization_id,idempotency_key,input_json,state) VALUES(?,?,?,'validated_model')",(init,str(start),body))
    with connect(path) as db:
        sql=[];db.set_trace_callback(sql.append);before=db.total_changes;start=time.perf_counter()
        assert automatic.deferred_status(db)=={'pending':0,'assigned':0,'completed':100000}
        assert time.perf_counter()-start<10
        assert db.total_changes==before and len(sql)==1
        db.execute('UPDATE message SET revision=2 WHERE id=1')
        assert automatic.deferred_status(db)=={'pending':1,'assigned':0,'completed':99999}
        assert db.execute('SELECT count(*) FROM reminder').fetchone()[0]==0


def test_usage_concurrency_and_unknown(tmp_path):
    path=tmp_path/'memory.sqlite3';initialize(path)
    with connect(path) as db:db.execute('UPDATE auto_control SET enabled=1,daily_limit=1')
    def call():
        try:return usage.invoke(path,{'model':'fixture'},'test',{},kind='automatic',sender=lambda *a:'ok')
        except HTTPException:return 'blocked'
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(lambda _:call(),range(2)))==['blocked','ok']
    with connect(path) as db:
        row=db.execute('SELECT * FROM usage_ledger').fetchone()
        assert row['state']=='unknown' and row['reserved_input']+row['reserved_output']>0
        assert automatic.used(db)==1


def test_feedback_revision_invalidates(tmp_path):
    path=seed(tmp_path)
    with connect(path) as db:
        db.execute("INSERT INTO feedback(memory_id,memory_version,action,evidence_json) VALUES(1,1,'confirm',?)",(json.dumps([{'message_id':1,'message_revision':1}]),))
        db.execute('UPDATE message SET revision=2 WHERE id=1')
        assert db.execute('SELECT valid FROM feedback').fetchone()[0]==0


def test_enabled_model_expires_without_call(client,monkeypatch):
    path,body=setup(client)
    body['messages'][0]['event_time']=stamp()
    client.post('/api/import',json=body).raise_for_status()
    ready(path)
    with connect(path) as db:db.execute('UPDATE message SET event_utc=? WHERE source_id=?',(stamp(1000),'2'))
    sender=Mock();monkeypatch.setattr(ai_settings,'complete',sender)
    assert automatic.process_next(path)=={'processed':False}
    sender.assert_not_called()
    with connect(path) as db:assert automatic.deferred_status(db)['pending']==1


def test_ordinary_deliver_quiet(client,monkeypatch):
    path,body=setup(client)
    body['messages'][0]['event_time']=stamp()
    client.post('/api/import',json=body).raise_for_status();ready(path)
    monkeypatch.setattr(ai_settings,'complete',responder([]))
    assert automatic.process_next(path)['state']=='done'
    aid=pending_associations(path,'fixture')[0]
    monkeypatch.setattr(policy,'quiet',lambda *args:True)
    sender=Mock()
    assert deliver_association(path,aid,'fixture',sender)=='skipped_quiet'
    sender.assert_not_called()


def test_startup_failure_is_chinese_and_saved(tmp_path,monkeypatch,capsys):
    import runpy
    import backend.app as app_module
    from backend.backups import status
    monkeypatch.setattr(app_module,'ROOT',tmp_path)
    monkeypatch.setattr(app_module,'create_app',Mock(side_effect=OSError('private path must not be printed')))
    with pytest.raises(SystemExit):runpy.run_module('backend.__main__',run_name='__main__')
    output=capsys.readouterr().out
    assert '请先停止工作台' in output and 'private path' not in output
    assert status(tmp_path/'data/memory.sqlite3')['last_failure']['reason'] in output


def test_legacy_calls_migrate_into_single_counter(tmp_path):
    path=tmp_path/'memory.sqlite3';initialize(path)
    with connect(path) as db:
        remove_v21(db)
        db.executescript('DROP TRIGGER job_message_insert; DROP TRIGGER deferred_cancel; DROP TRIGGER deferred_revision; DROP TRIGGER feedback_revision; DROP TABLE job_message; DROP TABLE usage_ledger; DROP TABLE feedback; DROP INDEX job_initialization; PRAGMA user_version=19;')
        db.execute('INSERT INTO auto_call(at) VALUES(?)',(time.time(),))
    initialize(path)
    with connect(path) as db:
        assert automatic.used(db)==1
        assert db.execute('SELECT count(*) FROM usage_ledger').fetchone()[0]==1


@pytest.mark.parametrize('sent',[False,True])
def test_reservation_failure_lifecycle(tmp_path,sent):
    path=tmp_path/'memory.sqlite3';initialize(path)
    with connect(path) as db:db.execute('UPDATE auto_control SET enabled=1,daily_limit=1')
    def sender(*args):
        if sent:usage.mark_sent()
        raise RuntimeError('synthetic failure')
    with pytest.raises(RuntimeError):usage.invoke(path,{'model':'fixture'},'test',{},kind='automatic',sender=sender)
    with connect(path) as db:
        row=db.execute('SELECT * FROM usage_ledger').fetchone()
        assert row['state']==('unknown' if sent else 'released')
        assert (row['reserved_input']+row['reserved_output']>0)==sent
        assert automatic.used(db)==int(sent)


def test_real_http_trace_and_connection_failure(tmp_path,monkeypatch):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import threading
    path=tmp_path/'memory.sqlite3';initialize(path)
    monkeypatch.setattr(ai_settings,'protect',lambda *a,**k:b'synthetic-key')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            with connect(path) as db:
                assert db.execute('SELECT state FROM usage_ledger ORDER BY id DESC LIMIT 1').fetchone()[0]=='unknown'
            response=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    config={'model':'fixture','key_encrypted':'eA==','base_url':f'http://127.0.0.1:{server.server_port}'}
    try:assert usage.invoke(path,config,'test',{},kind='manual',sender=ai_settings.complete)=='{}'
    finally:server.shutdown();server.server_close();thread.join(2)
    with pytest.raises(HTTPException):usage.invoke(path,config,'test',{},kind='manual',sender=ai_settings.complete)
    with connect(path) as db:assert [r[0] for r in db.execute('SELECT state FROM usage_ledger ORDER BY id')]==['unknown','released']
