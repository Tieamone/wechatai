"""Remove v21 additions only when constructing artificial legacy test databases."""
from backend.phase1_schema import SCHEMA


def remove_v21(db):
    remove_pricing(db)
    fields={r[1] for r in db.execute('PRAGMA table_info(usage_ledger)')}
    for name in ('reasoning_tokens','cached_input_tokens','reported_text_tokens'):
        if name in fields:db.execute('ALTER TABLE usage_ledger DROP COLUMN '+name)
    db.executescript('DROP INDEX usage_batch; DROP INDEX usage_time; DROP INDEX usage_reserved_lease; DROP TABLE batch_step; DROP TABLE batch_run; DROP TABLE budget_policy; DROP INDEX message_time; DROP INDEX message_conversation_time; DROP TRIGGER deferred_cancel;')
    for field in ('batch_id','reserved_cost','actual_cost','input_rate','output_rate','lease_until'):
        db.execute('ALTER TABLE usage_ledger DROP COLUMN '+field)
    start=SCHEMA.index('CREATE TRIGGER deferred_cancel');end=SCHEMA.index('END;',start)+4
    db.executescript(SCHEMA[start:end])


def remove_pricing(db):
    for table,names in (
        ('usage_ledger',('price_snapshot','sent_at','price_period')),
        ('budget_policy',('time_pricing','idle_input_rate','idle_output_rate','idle_cached_rate','idle_reasoning_rate'))):
        fields={r[1] for r in db.execute('PRAGMA table_info('+table+')')}
        for name in names:
            if name in fields:db.execute('ALTER TABLE '+table+' DROP COLUMN '+name)
