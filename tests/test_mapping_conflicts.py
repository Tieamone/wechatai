from backend.store import connect
from test_core import client
from backend.model_contract import mapping_hash


def seed(client):
    with connect(client.app.state.db_path) as db:
        db.execute("INSERT INTO entity_value(kind,value,token) VALUES('PHONE','13900139000','[PHONE_aa]')")
        db.execute("INSERT INTO entity_value(kind,value,token) VALUES('PHONE','+86 13900139000','[PHONE_bb]')")
    return {'kind':'PHONE','value':'13900139000','expected_tokens':['[PHONE_aa]','[PHONE_bb]'],
            'selected_token':'[PHONE_bb]','confirmed':True,'note':'合成旧库同号码核对'}


def test_choice_preserves_old_values_and_changes_fingerprint(client):
    body=seed(client)
    assert client.post('/api/privacy/preview',json={'text':body['value']}).json()['status']=='blocked'
    assert len(client.get('/api/mapping-conflicts').json())==1
    with connect(client.app.state.db_path) as db:
        before=[tuple(r) for r in db.execute('SELECT * FROM entity_value ORDER BY id')]
        fingerprint=mapping_hash(db)
    assert client.post('/api/mapping-conflicts',json=body).status_code==200
    assert client.post('/api/privacy/preview',json={'text':'139-0013-9000'}).json()['text']=='[PHONE_bb]'
    assert client.post('/api/mappings',json={'kind':'PHONE','value':body['value']}).json()['token']=='[PHONE_bb]'
    with connect(client.app.state.db_path) as db:
        assert before==[tuple(r) for r in db.execute('SELECT * FROM entity_value ORDER BY id')]
        assert fingerprint!=mapping_hash(db)
        db.execute("INSERT INTO entity_value(kind,value,token) VALUES('PHONE','139 0013 9000','[PHONE_cc]')")
    assert client.post('/api/privacy/preview',json={'text':body['value']}).json()['status']=='blocked'
    assert client.post('/api/mapping-conflicts',json=body).status_code==409


def test_conflict_requires_valid_explicit_selection(client):
    body=seed(client)
    for change,status in [({'confirmed':False},422),({'confirmed':'true'},422),({'selected_token':'[PHONE_cc]'},409),({'note':' '},422)]:
        assert client.post('/api/mapping-conflicts',json={**body,**change}).status_code==status
    assert client.get('/api/mapping-conflicts').json()[0]['resolution'] is None
