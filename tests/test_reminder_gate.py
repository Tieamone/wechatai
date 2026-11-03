from unittest.mock import Mock
from backend.store import connect
from backend.associations import rank_candidates,context,digest,mapping_hash,judge_next
from backend.notifications import deliver_association,pending_associations,deliver
from test_core import client,batch
from test_memories import draft


def seed(client):
    client.post('/api/import',json=batch()).raise_for_status()
    client.post('/api/memories',json=draft()).raise_for_status()
    return client.app.state.db_path


def incoming(client,source='2'):
    body=batch('今天喝咖啡',source_id=source,mode='live');body['messages'][0]['event_time']='2026-10-20T19:00:00+08:00'
    client.post('/api/import',json=body).raise_for_status()


def legacy_association(path):
    with connect(path) as db:
        db.execute('UPDATE auto_control SET enabled=1')
        db.execute("INSERT INTO association(id,trigger_id,trigger_revision,memory_id,memory_version,state) VALUES(1,2,1,1,1,'confirmed_model')")
        row=db.execute('SELECT * FROM association WHERE id=1').fetchone()
        db.execute('UPDATE association SET context_hash=? WHERE id=1',(digest([context(db,row),mapping_hash(db)]),))


def test_pending_memory_searchable_recallable_but_not_enqueued(client):
    path=seed(client);incoming(client)
    assert len(client.get('/api/memories?q=咖啡').json())==1
    with connect(path) as db:
        assert rank_candidates(db,2)[0]['memory_id']==1
        assert db.execute('SELECT count(*) FROM association').fetchone()[0]==0
    assert not judge_next(path,Mock())['processed']
    client.post('/api/memories/1/review/confirm',json={'expected_version':1}).raise_for_status()
    incoming(client,'3')
    with connect(path) as db:assert db.execute('SELECT count(*) FROM association').fetchone()[0]==1


def test_manual_send_and_existing_queue_recheck_confirmation(client):
    path=seed(client);incoming(client);legacy_association(path);sender=Mock(return_value='accepted')
    assert pending_associations(path,'test')==[]
    assert deliver_association(path,1,'test',sender)=='skipped_inactive';sender.assert_not_called()
    with connect(path) as db:assert db.execute('SELECT count(*) FROM notification_record').fetchone()[0]==0
    client.post('/api/memories/1/review/confirm',json={'expected_version':1}).raise_for_status()
    assert pending_associations(path,'test')==[1]
    assert deliver_association(path,1,'test',sender)=='accepted'
    client.post('/api/memories/1/review/reopen',json={'expected_version':1}).raise_for_status()
    assert deliver_association(path,1,'another-channel',sender)=='skipped_inactive'
    assert sender.call_count==1
    with connect(path) as db:
        assert db.execute("SELECT state FROM notification_record WHERE channel='test'").fetchone()[0]=='accepted'
        assert db.execute('SELECT count(*) FROM memory').fetchone()[0]==1


def test_watch_retained_pending_plain_keyword_still_works(client):
    path=seed(client)
    wid=client.post('/api/watches',json={'user_text':'咖啡','source_memory_id':1}).json()['id']
    client.post('/api/watches',json={'user_text':'咖啡'}).raise_for_status();incoming(client)
    watches=client.get('/api/watches').json();bound=next(w for w in watches if w['id']==wid)
    assert bound['enabled']==1 and bound['source_confirmed'] is False
    cards=client.get('/api/reminders').json();assert len(cards)==1 and cards[0]['source_memory_id'] is None
    sender=Mock(return_value='accepted')
    assert deliver(path,cards[0]['id'],'test',sender)=='accepted'
    with connect(path) as db:
        rid=db.execute("INSERT INTO reminder(trigger_id,watch_id,watch_revision,reason) VALUES(2,?,1,'synthetic old queued')",(wid,)).lastrowid
    assert deliver(path,rid,'test',sender)=='skipped_inactive' and sender.call_count==1
    client.post('/api/memories/1/review/confirm',json={'expected_version':1}).raise_for_status()
    assert deliver(path,rid,'confirmed-channel',sender)=='accepted' and sender.call_count==2
    with connect(path) as db:db.execute('UPDATE memory SET version=2 WHERE id=1')
    assert deliver(path,rid,'different-channel',sender)=='skipped_inactive'
    assert sender.call_count==2
