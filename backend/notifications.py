"""Opt-in, one-shot watch notifications; no automatic worker or chat text export."""
import re
import time

import httpx

from .store import connect
from .associations import reconcile_confirmed

TITLE = '聊天记忆助手：有一条待查看提醒'
BODY = '请在电脑工作台查看详情。本通知不包含聊天正文。'
TEST_TITLE = '聊天记忆助手：合成通知测试'
TEST_BODY = '这是一条测试消息，不是真实聊天提醒。请检查手机通知栏。'


def pending(path, channel):
    with connect(path) as db:
        return [r[0] for r in db.execute('''SELECT r.id FROM reminder r
            LEFT JOIN notification_attempt n ON n.reminder_id=r.id AND n.channel=?
            WHERE r.status='active' AND n.reminder_id IS NULL ORDER BY r.id''', (channel,))]


def deliver(path, reminder_id, channel, sender, *, retry_rejected=False):
    """Reserve before I/O. A crash/timeout stays uncertain and is never auto-retried.

    accepted means transport acceptance, not phone display. One notification per
    reminder/channel; later revisions cannot cause repeated alerts. Revocation
    before reservation suppresses sending; an already accepted push cannot be recalled.
    """
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT status FROM reminder WHERE id=?', (reminder_id,)).fetchone()
        if not row or row['status'] != 'active':
            return 'skipped_inactive'
        prior = db.execute('SELECT state FROM notification_attempt WHERE reminder_id=? AND channel=?',
                           (reminder_id, channel)).fetchone()
        if prior and not (prior['state'] == 'rejected' and retry_rejected):
            return 'skipped_' + prior['state']
        db.execute('''INSERT INTO notification_attempt(reminder_id,channel,state) VALUES(?,?,'uncertain')
            ON CONFLICT(reminder_id,channel) DO UPDATE SET state='uncertain',
            attempts=attempts+1,updated_at=CURRENT_TIMESTAMP''', (reminder_id, channel))
    try:
        state = sender(TITLE, BODY)
        if state not in {'accepted', 'rejected', 'uncertain'}:
            state = 'uncertain'
    except Exception:
        # Transport errors can contain a credential-bearing URL. Do not persist them.
        state = 'uncertain'
    with connect(path) as db:
        db.execute('UPDATE notification_attempt SET state=?,updated_at=CURRENT_TIMESTAMP WHERE reminder_id=? AND channel=?',
                   (state, reminder_id, channel))
    return state


def pending_associations(path, channel):
    with connect(path) as db:
        reconcile_confirmed(db)
        return [r[0] for r in db.execute('''SELECT a.id FROM association a
            LEFT JOIN association_notification n ON n.association_id=a.id AND n.channel=?
            WHERE a.state='confirmed_model' AND n.association_id IS NULL
            AND (SELECT enabled FROM auto_control WHERE id=1)=1 ORDER BY a.id''',(channel,))]


def deliver_association(path, candidate_id, channel, sender):
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        reconcile_confirmed(db)
        row=db.execute("SELECT * FROM association WHERE id=? AND state='confirmed_model' AND (SELECT enabled FROM auto_control WHERE id=1)=1",(candidate_id,)).fetchone()
        if not row:
            return 'skipped_inactive'
        prior=db.execute('SELECT state FROM association_notification WHERE association_id=? AND channel=?',(candidate_id,channel)).fetchone()
        if prior:
            return 'skipped_'+prior['state']
        duplicate=db.execute('''SELECT 1 FROM association_notification n JOIN association a ON a.id=n.association_id
            WHERE n.channel=? AND n.state IN ('accepted','uncertain')
            AND (a.trigger_id=? OR (a.memory_id=? AND n.created_at>?))''',
            (channel,row['trigger_id'],row['memory_id'],time.time()-120)).fetchone()
        # An accepted/uncertain watch notification for the same trigger already serves as a generic alert.
        watch=db.execute('''SELECT 1 FROM notification_attempt n JOIN reminder r ON r.id=n.reminder_id
            WHERE n.channel=? AND n.state IN ('accepted','uncertain') AND r.trigger_id=?''',(channel,row['trigger_id'])).fetchone()
        state='skipped_duplicate' if duplicate or watch else 'uncertain'
        db.execute('INSERT INTO association_notification VALUES(?,?,?,?)',(candidate_id,channel,state,time.time()))
    if state=='skipped_duplicate':
        return state
    try:
        state=sender(TITLE,BODY)
        if state not in {'accepted','rejected','uncertain'}:
            state='uncertain'
    except Exception:
        state='uncertain'
    with connect(path) as db:
        db.execute('UPDATE association_notification SET state=? WHERE association_id=? AND channel=?',(state,candidate_id,channel))
    return state


class ServerChan:
    """Turbo only. Caller owns the HTTP client; redirects must stay disabled."""
    def __init__(self, key, client):
        if not re.fullmatch(r'SCT[A-Za-z0-9]+', key):
            raise ValueError('需要 Server酱 Turbo 的 SCT SendKey')
        self._key, self._client = key, client

    def __call__(self, title, body):
        try:
            response = self._client.post(f'https://sctapi.ftqq.com/{self._key}.send',
                data={'title': title, 'desp': body}, timeout=10, follow_redirects=False)
            if response.status_code != 200:
                return 'uncertain'
            result = response.json()
            code = result.get('code') if isinstance(result, dict) else None
            if type(code) is not int:
                return 'uncertain'
            return 'accepted' if code == 0 else 'rejected'
        except (httpx.HTTPError, ValueError):
            return 'uncertain'
