"""Frozen pre-refactor candidate algorithm for behavioral comparison only."""
from backend.associations import recent_messages,terms,evidence_before
from legacy_entity_context import mention_candidates

def enqueue_candidates(db, trigger_id):
    trigger=db.execute('SELECT * FROM message WHERE id=?',(trigger_id,)).fetchone()
    if trigger['sender_role']!='peer' or trigger['type']!='text' or not trigger['event_utc']:
        return
    recent=recent_messages(db,trigger)
    query=terms(' '.join(r['text'] for r in recent if r['type']=='text'))
    current_people={r['person_token'] for r in mention_candidates(db,[(r['text'],r['event_utc']) for r in recent if r['type']=='text'],recent[0]['event_utc'])}
    ranked=[]
    for memory in db.execute("SELECT * FROM memory WHERE status='active'").fetchall():
        score=len(query & terms(memory['summary']))
        if current_people:
            history=[(r['text'],r['event_utc']) for r in db.execute('SELECT m.* FROM memory_evidence e JOIN message m ON m.id=e.message_id WHERE memory_id=?',(memory['id'],))]
            history_people={r['person_token'] for r in mention_candidates(db,history,recent[0]['event_utc'])}
            score+=len(current_people & history_people)
        if score and evidence_before(db,memory['id'],recent[0]['event_utc']):
            ranked.append((score,memory))
    for _,memory in sorted(ranked,key=lambda pair:(-pair[0],-pair[1]['id']))[:10]:
        db.execute('INSERT OR IGNORE INTO association(trigger_id,trigger_revision,memory_id,memory_version) VALUES(?,?,?,?)',
                   (trigger_id,trigger['revision'],memory['id'],memory['version']))
    return [{'trigger_revision':trigger['revision'],'memory_id':memory['id'],
             'memory_version':memory['version'],'score':score}
            for score,memory in sorted(ranked,key=lambda pair:(-pair[0],-pair[1]['id']))[:10]]

