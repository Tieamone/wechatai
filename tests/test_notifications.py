from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs

import httpx
import pytest

from backend.notifications import BODY, TITLE, ServerChan, deliver, pending
from backend.store import connect, initialize
from test_core import batch, client


def reminder(client):
    client.post('/api/watches', json={'user_text':'咖啡'}).raise_for_status()
    client.post('/api/import', json=batch(mode='live')).raise_for_status()
    return client.app.state.db_path


def test_dedup_restart_private_payload_and_dismiss(client):
    path = reminder(client)
    calls = []
    def send(title, body):
        calls.append((title, body))
        return 'accepted'
    assert pending(path, 'test') == [1]
    assert deliver(path, 1, 'test', send) == 'accepted'
    initialize(path)
    assert pending(path, 'test') == []
    assert deliver(path, 1, 'test', send) == 'skipped_accepted'
    assert calls == [(TITLE, BODY)]
    assert '咖啡' not in str(calls)
    client.post('/api/reminders/1/dismiss').raise_for_status()
    assert deliver(path, 1, 'another-channel', send) == 'skipped_inactive'
    assert len(calls) == 1


def test_timeout_no_secret_log_no_auto_retry(client):
    path = reminder(client)
    def fail(*args):
        raise RuntimeError('https://example.invalid/secret-key')
    assert deliver(path, 1, 'test', fail) == 'uncertain'
    assert deliver(path, 1, 'test', fail, retry_rejected=True) == 'skipped_uncertain'
    with connect(path) as db:
        row = dict(db.execute('SELECT * FROM notification_attempt').fetchone())
    assert row['attempts'] == 1 and 'secret-key' not in str(row)


def test_rejected_requires_explicit_retry(client):
    path = reminder(client)
    assert deliver(path, 1, 'test', lambda *_: 'rejected') == 'rejected'
    assert deliver(path, 1, 'test', lambda *_: 'accepted') == 'skipped_rejected'
    assert deliver(path, 1, 'test', lambda *_: 'accepted', retry_rejected=True) == 'accepted'
    with connect(path) as db:
        assert db.execute('SELECT attempts FROM notification_attempt').fetchone()[0] == 2


def test_concurrent_dispatch_reserves_once(client):
    path = reminder(client)
    calls = []
    def send(*args):
        calls.append(args)
        return 'accepted'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: deliver(path, 1, 'test', send), range(2)))
    assert results.count('accepted') == 1
    assert len(calls) == 1


@pytest.mark.parametrize('status,payload,expected', [
    (200, {'code':0}, 'accepted'), (200, {'code':40001}, 'rejected'),
    (200, {'code':False}, 'uncertain'), (200, {}, 'uncertain'),
    (302, {'code':0}, 'uncertain'), (503, {'code':0}, 'uncertain'),
])
def test_serverchan_response_and_fixed_endpoint(status, payload, expected):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.host == 'sctapi.ftqq.com'
        assert parse_qs(request.content.decode()) == {'title':[TITLE], 'desp':[BODY]}
        return httpx.Response(status, json=payload, headers={'Location':'https://example.invalid/'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert ServerChan('SCTtest123', http)(TITLE, BODY) == expected
    assert len(requests) == 1


def test_serverchan_rejects_url_in_key_and_sanitizes_timeout():
    with httpx.Client(transport=httpx.MockTransport(lambda _: (_ for _ in ()).throw(httpx.ReadTimeout('secret')))) as http:
        with pytest.raises(ValueError):
            ServerChan('SCT../other', http)
        assert ServerChan('SCTtest', http)(TITLE, BODY) == 'uncertain'
