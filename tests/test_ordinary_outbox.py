from backend.associations import judge_next
from backend.outbox import ordinary_changes
from backend.store import connect, initialize
from test_associations import setup
from test_core import client


def read(path, **kwargs):
    with connect(path) as db:
        return ordinary_changes(db, **kwargs)


def test_only_confirmed_published_dismissal_idempotent(client):
    path,_=setup(client)
    assert read(path)['items']==[]
    judge_next(path,lambda _:'{"confirmed":true}')
    first=read(path)
    assert first['items']==[{'id':1,'status':'confirmed_synthetic','revision':1}]
    client.post('/api/ordinary-reminders/1/dismiss')
    client.post('/api/ordinary-reminders/1/dismiss')
    assert read(path,after=first['cursor'])['items']==[{'id':1,'status':'dismissed','revision':2}]
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM ordinary_outbox').fetchone()[0]==2


def test_correction_withdraws_without_opening_ui(client):
    path,_=setup(client)
    judge_next(path,lambda _:'{"confirmed":true}')
    first=read(path)
    client.post('/api/memories/1/correct',json={})
    assert read(path,after=first['cursor'])['items']==[{'id':1,'status':'stale','revision':2}]
    assert client.get('/api/ordinary-reminders').json()==[]
    assert read(path,after=2)['items']==[]


def test_pagination_rollback_and_restart(client):
    path,_=setup(client)
    judge_next(path,lambda _:'{"confirmed":true}')
    try:
        with connect(path) as db:
            db.execute("UPDATE association SET state='dismissed' WHERE id=1")
            raise RuntimeError('crash')
    except RuntimeError:
        pass
    assert read(path)['items'][0]['revision']==1
    client.post('/api/ordinary-reminders/1/dismiss')
    initialize(path)
    first=read(path,limit=1)
    assert first['has_more'] and first['items'][0]['revision']==2
    second=read(path,after=first['cursor'],limit=1)
    assert not second['has_more'] and second['items']==first['items']


def test_upgrade_seeds_existing_confirmation_once(client):
    path,_=setup(client)
    judge_next(path,lambda _:'{"confirmed":true}')
    # Recreate the prior schema, retaining the real confirmed candidate.
    with connect(path) as db:
        db.executescript('DROP TRIGGER association_published; DROP TABLE ordinary_outbox; DROP TABLE mapping_resolution; DROP TABLE property_evidence; DROP TABLE person_property; DROP TABLE person; DROP TABLE mapping_policy; PRAGMA user_version=10;')
    initialize(path)
    initialize(path)
    assert read(path)['items']==[{'id':1,'status':'confirmed_synthetic','revision':1}]
    client.post('/api/ordinary-reminders/1/dismiss')
    assert read(path)['items']==[{'id':1,'status':'dismissed','revision':2}]
