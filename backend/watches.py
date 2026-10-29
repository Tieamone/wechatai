"""Update watch cards in a bounded local context, without asserting intent."""
import json

from .associations import recent_messages, evidence_before


def invalidate_context(db, message_id):
    for row in db.execute("SELECT id,context_json FROM reminder WHERE status='active'").fetchall():
        if message_id in json.loads(row['context_json']):
            db.execute("UPDATE reminder SET status='withdrawn' WHERE id=?",(row['id'],))


def update_watches(db, message_id):
    trigger=db.execute('SELECT * FROM message WHERE id=?',(message_id,)).fetchone()
    if trigger['type']!='text':
        return
    recent=recent_messages(db,trigger) or [trigger]
    ids=[r['id'] for r in recent]
    serialized=json.dumps(ids)
    for watch in db.execute("SELECT w.* FROM watch_item w LEFT JOIN memory m ON m.id=w.source_memory_id WHERE w.enabled=1 AND (w.source_memory_id IS NULL OR m.status='active')").fetchall():
        if watch['source_memory_id'] is not None and not evidence_before(db,watch['source_memory_id'],recent[0]['event_utc']):
            continue
        previous=db.execute('SELECT * FROM reminder WHERE watch_id=? AND watch_revision=? AND trigger_id IN ('+','.join('?' for _ in ids)+') ORDER BY id DESC LIMIT 1',
                            (watch['id'],watch['revision'],*ids)).fetchone()
        if previous and previous['status']!='active':
            continue  # Do not revive a dismissed card within the same bounded context.
        matched=any(r['type']=='text' and r['sender_role']=='peer' and watch['user_text'].casefold() in r['text'].casefold() for r in recent)
        if not matched:
            continue
        reason='近期文字包含你设置的关注词；不代表已确认意图'
        if previous:
            db.execute('UPDATE reminder SET trigger_id=?,context_json=?,reason=? WHERE id=?',(message_id,serialized,reason,previous['id']))
        elif trigger['sender_role']=='peer':
            db.execute('INSERT OR IGNORE INTO reminder(trigger_id,watch_id,watch_revision,reason,context_json) VALUES(?,?,?,?,?)',
                       (message_id,watch['id'],watch['revision'],reason,serialized))


def context_quotes(db, serialized):
    result=[]
    for message_id in json.loads(serialized):
        row=db.execute('SELECT id,sender_role,event_time,text,type FROM message WHERE id=?',(message_id,)).fetchone()
        if row:
            result.append(dict(row))
    return result
