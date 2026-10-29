"""Freshness and notification policy; reserve inside caller's write transaction."""
import time
from datetime import datetime
from fastapi import APIRouter
from pydantic import Field, model_validator
from .models import StrictModel
from .store import connect
from .backups import status as backup_status


class Settings(StrictModel):
    freshness_seconds: int = Field(default=300, ge=60, le=3600)
    validity_seconds: int = Field(default=900, ge=60, le=86400)
    future_seconds: int = Field(default=120, ge=0, le=300)
    quiet_enabled: bool = True
    quiet_start: str = Field(default='23:00', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    quiet_end: str = Field(default='08:00', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    cooldown_seconds: int = Field(default=7200, ge=0, le=86400)

    @model_validator(mode='after')
    def validate_window(self):
        if self.validity_seconds < self.freshness_seconds:
            raise ValueError('发送有效期不能短于入库新鲜度')
        if self.quiet_start == self.quiet_end:
            self.quiet_enabled = False
        return self


def settings(db):
    return dict(db.execute('SELECT * FROM notification_policy WHERE id=1').fetchone())


def age_reason(event_utc, now, config, *, sending=False):
    if not event_utc:
        return 'unknown_time'
    age = now - datetime.fromisoformat(event_utc).timestamp()
    if age < -config['future_seconds']:
        return 'future_time'
    if age > config['validity_seconds' if sending else 'freshness_seconds']:
        return 'expired'
    return None


def quiet(config, now):
    start,end=config['quiet_start'],config['quiet_end']
    current=datetime.fromtimestamp(now).strftime('%H:%M')
    return config['quiet_enabled'] and start!=end and (start<=current<end if start<end else current>=start or current<end)


def defer(db, message_id, reason):
    db.execute('''INSERT INTO deferred_message(message_id,message_revision,reason)
        SELECT id,revision,? FROM message WHERE id=? AND type='text'
        ON CONFLICT(message_id) DO UPDATE SET message_revision=excluded.message_revision,
        reason=excluded.reason,initialization_id=NULL''', (reason,message_id))


def reserve(db, kind, item_id, channel, *, synthetic=False, retry_rejected=False):
    assert db.in_transaction
    prior=db.execute('SELECT * FROM notification_record WHERE kind=? AND item_id=? AND channel=? AND synthetic=?',
                     (kind,item_id,channel,int(synthetic))).fetchone()
    if prior and not (retry_rejected and prior['state']=='rejected'):
        return 'skipped_'+prior['state']
    if kind=='watch':
        row=db.execute('''SELECT r.trigger_id,w.source_memory_id AS memory_id,w.id AS watch_id,m.event_utc
            FROM reminder r JOIN watch_item w ON w.id=r.watch_id JOIN message m ON m.id=r.trigger_id
            WHERE r.id=?''',(item_id,)).fetchone()
    else:
        row=db.execute('''SELECT a.trigger_id,a.memory_id,NULL AS watch_id,m.event_utc
            FROM association a JOIN message m ON m.id=a.trigger_id WHERE a.id=?''',(item_id,)).fetchone()
    now=time.time(); config=settings(db); reason=None
    if not synthetic:
        reason=age_reason(row['event_utc'],now,config,sending=True)
        if not reason and quiet(config,now): reason='quiet'
        since=now-config['cooldown_seconds']
        # Read both schemas: upgrades cannot erase cooldown or cross-kind deduplication.
        old=db.execute('''SELECT r.trigger_id,w.source_memory_id AS memory_id,w.id AS watch_id,
                CAST(strftime('%s',n.updated_at) AS REAL) AS at
            FROM notification_attempt n JOIN reminder r ON r.id=n.reminder_id JOIN watch_item w ON w.id=r.watch_id
            WHERE n.channel=? AND n.state IN ('accepted','uncertain')
            UNION ALL SELECT a.trigger_id,a.memory_id,NULL,n.created_at
            FROM association_notification n JOIN association a ON a.id=n.association_id
            WHERE n.channel=? AND n.state IN ('accepted','uncertain')
            UNION ALL SELECT trigger_id,memory_id,watch_id,created_at FROM notification_record
            WHERE channel=? AND synthetic=0 AND state IN ('accepted','uncertain')''',(channel,channel,channel)).fetchall()
        for sent in old:
            if reason: break
            if sent['trigger_id']==row['trigger_id']:
                reason='duplicate'
            elif sent['at'] is not None and sent['at']>since and (
                (row['memory_id'] is not None and sent['memory_id']==row['memory_id']) or
                (row['memory_id'] is None and row['watch_id'] is not None and sent['watch_id']==row['watch_id'])):
                reason='cooldown'
    state='suppressed' if reason else 'uncertain'
    db.execute('''INSERT INTO notification_record(kind,item_id,channel,trigger_id,memory_id,watch_id,state,reason,synthetic,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(kind,item_id,channel,synthetic)
        DO UPDATE SET state=excluded.state,reason=excluded.reason,created_at=excluded.created_at,updated_at=excluded.updated_at''',
        (kind,item_id,channel,row['trigger_id'],row['memory_id'],row['watch_id'],state,reason,int(synthetic),now,now))
    return 'skipped_'+reason if reason else None


def finish(db,kind,item_id,channel,state,synthetic=False):
    db.execute('UPDATE notification_record SET state=?,updated_at=? WHERE kind=? AND item_id=? AND channel=? AND synthetic=?',
               (state,time.time(),kind,item_id,channel,int(synthetic)))


def card_status(db,kind,item_id):
    row=db.execute('SELECT state,reason FROM notification_record WHERE kind=? AND item_id=? AND synthetic=0 ORDER BY id DESC LIMIT 1',(kind,item_id)).fetchone()
    return dict(row) if row else None


def router(path):
    routes=APIRouter(prefix='/api/notification-policy')

    @routes.get('')
    def get():
        with connect(path) as db:
            return {'settings':settings(db),'backup':backup_status(path),
                    'records':[dict(r) for r in db.execute('SELECT kind,item_id,state,reason,created_at,synthetic FROM notification_record ORDER BY id DESC LIMIT 50')]}

    @routes.put('')
    def update(body:Settings):
        values=body.model_dump()
        with connect(path) as db:
            db.execute('UPDATE notification_policy SET '+','.join(k+'=?' for k in values)+' WHERE id=1',list(values.values()))
        return get()

    return routes
