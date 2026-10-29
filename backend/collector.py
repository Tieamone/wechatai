"""Normalize one bound source conversation and reuse the existing import transaction."""
from datetime import datetime, timezone
import hashlib
import json

from .models import ImportRequest, Message, Source
from .store import connect

ADAPTER = 'wechat-local-db-v1'


def collector_status(directory):
    try:
        value=json.loads((directory/'collector/status.json').read_text(encoding='utf-8'))
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(value['updated_at'])).total_seconds()
        state=value['state'] if value['state'] in {'starting','watching','retrying','stopped','stopped_error','history_imported'} else 'unknown'
        if state in {'starting','watching','retrying'} and (age<0 or age>90):
            state='stale'
        return {'state':state,'updated_at':value['updated_at'],'rows':value.get('rows',0)}
    except (OSError,ValueError,KeyError,TypeError):
        return {'state':'not_connected'}


def identity(binding):
    account = hashlib.sha256(binding['self_id'].encode()).hexdigest()
    peer = hashlib.sha256(binding['peer'].encode()).hexdigest()
    return account, peer, 'wechat-' + peer


def import_rows(app, binding, rows, *, history, progress=None):
    account, peer, conversation = identity(binding)
    with connect(app.state.db_path) as db:
        bound = db.execute('SELECT * FROM conversation').fetchone()
        if bound and (bound['id'],bound['account_ref'],bound['contact_ref']) != (conversation,account,peer):
            raise ValueError('Workbench is bound to a different conversation')
        existing = {(r['partition'],r['source_id']):dict(r) for r in db.execute(
            'SELECT partition,source_id,text,type,event_time,sender_role,revision FROM message WHERE adapter=?', (ADAPTER,))}
    keys = [(r['partition'],r['id']) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate source IDs in snapshot')
    observed = datetime.now(timezone.utc).isoformat()
    changes = []
    for row in rows:
        event = datetime.fromtimestamp(row['time'], timezone.utc).isoformat() if row['time'] else None
        previous = existing.get((row['partition'],row['id']))
        values = (row['text'],row['type'],event,row['role'])
        if previous and values == tuple(previous[k] for k in ('text','type','event_time','sender_role')):
            continue
        changes.append(Message(conversation_id=conversation,
            source=Source(adapter=ADAPTER, partition=row['partition'], id=row['id']),
            sender_role=row['role'], event_time=event, observed_at=observed,
            text=row['text'], type=row['type'], revision=previous['revision']+1 if previous else 1))
    counts = {'inserted':0,'updated':0,'duplicates':len(rows)-len(changes),
              'source_missing':len(set(existing)-set(keys))}
    # Validate all rows before the first batch. On interruption, replay is idempotent.
    for start in range(0, len(changes), 500):
        result = app.state.ingest(ImportRequest(account_ref=account,contact_ref=peer,
            mode='history' if history else 'live', messages=changes[start:start+500]))
        for key in ('inserted','updated','duplicates'):
            counts[key] += result[key]
        if progress:
            progress(min(start+500,len(changes)),len(changes))
    return counts
