"""Transactional policy reservation for watch and ordinary notifications."""
import re
import time

import httpx

from .store import connect
from .associations import reconcile_confirmed

TITLE = '聊天记忆助手：有一条待查看提醒'
BODY = '请在电脑工作台查看详情。本通知不包含聊天正文。'
TEST_TITLE = '聊天记忆助手：合成通知测试'
TEST_BODY = '这是一条测试消息，不是真实聊天提醒。请检查手机通知栏。'


def pending(path, channel, *, synthetic=False):
    with connect(path) as db:
        return [r[0] for r in db.execute("""SELECT r.id FROM reminder r
            LEFT JOIN notification_attempt n ON n.reminder_id=r.id AND n.channel=?
            WHERE r.status='active' AND (n.reminder_id IS NULL OR ?)
            AND NOT EXISTS(SELECT 1 FROM notification_record x WHERE x.kind='watch' AND x.item_id=r.id AND x.channel=? AND x.synthetic=?)
            ORDER BY r.id""", (channel,synthetic,channel,synthetic))]


def pending_associations(path, channel):
    with connect(path) as db:
        reconcile_confirmed(db)
        return [r[0] for r in db.execute("""SELECT a.id FROM association a
            LEFT JOIN association_notification n ON n.association_id=a.id AND n.channel=?
            WHERE a.state='confirmed_model' AND n.association_id IS NULL
            AND (SELECT enabled FROM auto_control WHERE id=1)=1
            AND NOT EXISTS(SELECT 1 FROM notification_record x WHERE x.kind='ordinary' AND x.item_id=a.id AND x.channel=? AND x.synthetic=0)
            ORDER BY a.id""",(channel,channel))]


def _deliver(path, kind, item_id, channel, sender, *, synthetic=False, retry_rejected=False):
    from . import policy
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if kind=='ordinary':
            reconcile_confirmed(db)
            valid=db.execute("SELECT 1 FROM association WHERE id=? AND state='confirmed_model' AND (SELECT enabled FROM auto_control WHERE id=1)=1",(item_id,)).fetchone()
            prior=db.execute('SELECT state FROM association_notification WHERE association_id=? AND channel=?',(item_id,channel)).fetchone()
        else:
            valid=db.execute("""SELECT 1 FROM reminder r JOIN watch_item w ON w.id=r.watch_id
                LEFT JOIN memory m ON m.id=w.source_memory_id WHERE r.id=? AND r.status='active'
                AND w.enabled=1 AND w.revision=r.watch_revision AND (w.source_memory_id IS NULL OR m.status='active')""",(item_id,)).fetchone()
            prior=db.execute('SELECT state FROM notification_attempt WHERE reminder_id=? AND channel=?',(item_id,channel)).fetchone()
        if not valid:return 'skipped_inactive'
        if not synthetic and prior and not (retry_rejected and prior['state']=='rejected'):
            return 'skipped_'+prior['state']
        skipped=policy.reserve(db,kind,item_id,channel,synthetic=synthetic,retry_rejected=retry_rejected)
        if skipped:return skipped
        if not synthetic:
            if kind=='watch':
                db.execute("""INSERT INTO notification_attempt(reminder_id,channel,state) VALUES(?,?,'uncertain')
                    ON CONFLICT(reminder_id,channel) DO UPDATE SET state='uncertain',attempts=attempts+1,updated_at=CURRENT_TIMESTAMP""",(item_id,channel))
            else:
                db.execute("INSERT INTO association_notification VALUES(?,?,'uncertain',?)",(item_id,channel,time.time()))
    try:
        state=sender(TITLE,BODY)
        if state not in {'accepted','rejected','uncertain'}:state='uncertain'
    except Exception:
        state='uncertain'
    with connect(path) as db:
        policy.finish(db,kind,item_id,channel,state,synthetic)
        if not synthetic:
            if kind=='watch':
                db.execute('UPDATE notification_attempt SET state=?,updated_at=CURRENT_TIMESTAMP WHERE reminder_id=? AND channel=?',(state,item_id,channel))
            else:
                db.execute('UPDATE association_notification SET state=? WHERE association_id=? AND channel=?',(state,item_id,channel))
    return state


def deliver(path, reminder_id, channel, sender, *, retry_rejected=False, synthetic=False):
    return _deliver(path,'watch',reminder_id,channel,sender,retry_rejected=retry_rejected,synthetic=synthetic)


def deliver_association(path,candidate_id,channel,sender):
    return _deliver(path,'ordinary',candidate_id,channel,sender)


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
