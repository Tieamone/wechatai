from backend.store import connect
from test_core import batch,client


def message(client,text,source_id,when='2026-10-18T19:00:00+08:00',revision=1):
    data=batch(text,source_id=source_id,mode='live',revision=revision)
    data['messages'][0]['event_time']=when
    client.post('/api/import',json=data).raise_for_status()


def test_followup_updates_same_card_and_outbox(client):
    client.post('/api/watches',json={'user_text':'咖啡'})
    message(client,'去喝咖啡吗','1')
    first=client.get('/api/reminders').json()[0]
    message(client,'算了，不去了','2','2026-10-18T19:00:30+08:00')
    cards=client.get('/api/reminders').json()
    assert len(cards)==1 and cards[0]['id']==first['id']
    assert cards[0]['trigger_text']=='算了，不去了'
    assert [r['text'] for r in cards[0]['recent_context']]==['去喝咖啡吗','算了，不去了']
    with connect(client.app.state.db_path) as db:
        assert db.execute('SELECT max(revision) FROM outbox').fetchone()[0]==2


def test_dismissed_context_does_not_revive_but_later_event_can(client):
    client.post('/api/watches',json={'user_text':'咖啡'})
    message(client,'咖啡','1')
    client.post('/api/reminders/1/dismiss')
    message(client,'咖啡店','2','2026-10-18T19:00:30+08:00')
    assert client.get('/api/reminders').json()==[]
    message(client,'咖啡','3','2026-10-19T19:00:00+08:00')
    assert len(client.get('/api/reminders').json())==1


def test_revision_of_earlier_fragment_withdraws_card(client):
    client.post('/api/watches',json={'user_text':'咖啡'})
    message(client,'咖啡','1')
    message(client,'好','2','2026-10-18T19:00:30+08:00')
    message(client,'其实是茶','1',revision=2)
    assert client.get('/api/reminders').json()==[]
