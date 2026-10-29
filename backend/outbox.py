"""Local-only reminder change journal. No device registration or network routes."""

from .associations import reconcile_confirmed


def ordinary_changes(db, after=0, limit=50):
    """Separate cursor/ID namespace from watch reminders; local state feed only."""
    if after < 0 or not 1 <= limit <= 100:
        raise ValueError('Invalid cursor or page size')
    reconcile_confirmed(db)
    events=db.execute('SELECT * FROM ordinary_outbox WHERE seq>? ORDER BY seq LIMIT ?',(after,limit)).fetchall()
    cursor=events[-1]['seq'] if events else after
    items=[]
    for candidate_id in dict.fromkeys(e['association_id'] for e in events):
        row=db.execute('SELECT state FROM association WHERE id=?',(candidate_id,)).fetchone()
        revision=db.execute('SELECT MAX(revision) FROM ordinary_outbox WHERE association_id=?',(candidate_id,)).fetchone()[0]
        items.append({'id':candidate_id,'status':row['state'],'revision':revision})
    return {'cursor':cursor,'items':items,'has_more':bool(db.execute('SELECT 1 FROM ordinary_outbox WHERE seq>? LIMIT 1',(cursor,)).fetchone())}


def changes(db, after=0, limit=50):
    if after < 0 or not 1 <= limit <= 100:
        raise ValueError('Invalid cursor or page size')
    events = db.execute('SELECT * FROM outbox WHERE seq>? ORDER BY seq LIMIT ?', (after, limit)).fetchall()
    cursor = events[-1]['seq'] if events else after
    items = []
    # Coalesce repeated events to current state, with the current revision.
    # Consumers must upsert by id/revision; this is a state feed, not an audit replay.
    for reminder_id in dict.fromkeys(e['reminder_id'] for e in events):
        row = db.execute('SELECT id,status FROM reminder WHERE id=?', (reminder_id,)).fetchone()
        revision = db.execute('SELECT max(revision) FROM outbox WHERE reminder_id=?', (reminder_id,)).fetchone()[0]
        items.append({'id':row['id'], 'status':row['status'], 'revision':revision})
    return {'cursor':cursor, 'items':items,
            'has_more':bool(db.execute('SELECT 1 FROM outbox WHERE seq>? LIMIT 1', (cursor,)).fetchone())}
