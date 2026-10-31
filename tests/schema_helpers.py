"""Remove v21 additions only when constructing artificial legacy test databases."""
from backend.phase1_schema import SCHEMA


def remove_v21(db):
    db.executescript('DROP INDEX usage_batch; DROP INDEX usage_time; DROP INDEX usage_reserved_lease; DROP TABLE batch_step; DROP TABLE batch_run; DROP TABLE budget_policy; DROP INDEX message_time; DROP INDEX message_conversation_time; DROP TRIGGER deferred_cancel;')
    for field in ('batch_id','reserved_cost','actual_cost','input_rate','output_rate','lease_until'):
        db.execute('ALTER TABLE usage_ledger DROP COLUMN '+field)
    start=SCHEMA.index('CREATE TRIGGER deferred_cancel');end=SCHEMA.index('END;',start)+4
    db.executescript(SCHEMA[start:end])
