import pytest

from backend.app import create_app
from backend.collector import import_rows
from backend.store import connect

BINDING={'self_id':'synthetic-self','peer':'synthetic-peer'}


def row(id='1', text='coffee', partition='message/message_0.db', role='peer'):
    return {'id':id,'text':text,'type':'text','role':role,'time':1793116800,'partition':partition,'seq':1}


def test_history_live_revision_and_restart(tmp_path):
    app=create_app(tmp_path)
    with connect(app.state.db_path) as db:
        db.execute("INSERT INTO watch_item(user_text) VALUES('coffee')")
    assert import_rows(app,BINDING,[row()],history=True)['inserted']==1
    with connect(app.state.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM reminder').fetchone()[0]==0
    app=create_app(tmp_path)
    assert import_rows(app,BINDING,[row()],history=False)['duplicates']==1
    assert import_rows(app,BINDING,[row(),row('2')],history=False)['inserted']==1
    with connect(app.state.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM reminder').fetchone()[0]==1
    assert import_rows(app,BINDING,[row(),row('2',text='tea')],history=False)['updated']==1
    with connect(app.state.db_path) as db:
        assert db.execute("SELECT revision FROM message WHERE source_id='2'").fetchone()[0]==2
        assert db.execute("SELECT COUNT(*) FROM reminder WHERE status='active'").fetchone()[0]==0


def test_identical_messages_and_shard_ids_remain_distinct(tmp_path):
    app=create_app(tmp_path)
    rows=[row(),row('2'),row(partition='message/message_1.db')]
    assert import_rows(app,BINDING,rows,history=True)['inserted']==3
    assert import_rows(app,BINDING,rows,history=False)['duplicates']==3


def test_other_contact_and_invalid_batch_are_rejected(tmp_path):
    app=create_app(tmp_path)
    import_rows(app,BINDING,[row()],history=True)
    with pytest.raises(ValueError):
        import_rows(app,{**BINDING,'peer':'other'},[row('2')],history=False)
    with pytest.raises(ValueError):
        import_rows(app,BINDING,[row('2'),row('2')],history=False)
    with connect(app.state.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM message').fetchone()[0]==1


def test_missing_source_is_reported_not_deleted(tmp_path):
    app=create_app(tmp_path)
    import_rows(app,BINDING,[row(),row('2')],history=True)
    assert import_rows(app,BINDING,[row()],history=False)['source_missing']==1
    with connect(app.state.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM message').fetchone()[0]==2
