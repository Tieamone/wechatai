"""Temporary human-confirmation gate for memory-derived proactive work only."""


def confirmed_sql(alias='m'):
    return f"""{alias}.status='active' AND EXISTS(SELECT 1 FROM memory_review gate_review
        WHERE gate_review.memory_id={alias}.id AND gate_review.confirmed_version={alias}.version)
        AND NOT EXISTS(SELECT 1 FROM memory_evidence gate_e JOIN message gate_message ON gate_message.id=gate_e.message_id
            WHERE gate_e.memory_id={alias}.id AND gate_e.message_revision!=gate_message.revision)"""


def confirmed(db,memory_id):
    return bool(db.execute('SELECT 1 FROM memory m WHERE m.id=? AND '+confirmed_sql(),(memory_id,)).fetchone())
