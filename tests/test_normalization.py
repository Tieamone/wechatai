from backend.store import connect, initialize
from test_core import client


def mapping(client,value):
    return client.post('/api/mappings',json={'kind':'PHONE','value':value})


def preview(client,text):
    return client.post('/api/privacy/preview',json={'text':text}).json()


def test_phone_variants_share_token_and_old_value_stays(client):
    original='+86 138-0013-8000'
    token=mapping(client,original).json()['token']
    for value in ('13800138000','138 0013 8000','0086 13800138000','+8613800138000','+86  138 - 0013 - 8000'):
        assert mapping(client,value).json()['token']==token
        result=preview(client,'电话：'+value+'，请联系')
        assert result['status']=='preview_only'
        assert result['text']=='电话：'+token+'，请联系'
    assert mapping(client,'13900139000').json()['token']!=token
    with connect(client.app.state.db_path) as db:
        assert db.execute('SELECT value FROM entity_value WHERE token=?',(token,)).fetchone()[0]==original
        assert db.execute('SELECT normalization_version FROM mapping_policy').fetchone()[0]==1


def test_does_not_infer_missing_country_or_digits(client):
    token=mapping(client,'13800138000').json()['token']
    assert mapping(client,'+1 13800138000').json()['token']!=token
    assert token not in preview(client,'号码是1380013800')['allowed_tokens']
    result=preview(client,'123456713800138000')
    assert result['status']=='blocked' and token not in result['allowed_tokens']


def test_old_conflicting_tokens_survive_upgrade_but_block(client):
    path=client.app.state.db_path
    with connect(path) as db:
        db.executescript('DROP TABLE mapping_resolution; DROP TABLE property_evidence; DROP TABLE mapping_policy; PRAGMA user_version=12;')
        db.execute("INSERT INTO entity_value(kind,value,token) VALUES('PHONE','13800138000','[PHONE_aa]')")
        db.execute("INSERT INTO entity_value(kind,value,token) VALUES('PHONE','+86 13800138000','[PHONE_bb]')")
    initialize(path)
    assert mapping(client,'138-0013-8000').status_code==409
    result=preview(client,'电话13800138000')
    assert result['status']=='blocked' and result['text'] is None
    with connect(path) as db:
        assert [r[0] for r in db.execute('SELECT token FROM entity_value ORDER BY id')]==['[PHONE_aa]','[PHONE_bb]']
