SCHEMA='''
ALTER TABLE budget_policy ADD COLUMN time_pricing INTEGER NOT NULL DEFAULT 0;
ALTER TABLE budget_policy ADD COLUMN idle_input_rate INTEGER;
ALTER TABLE budget_policy ADD COLUMN idle_output_rate INTEGER;
ALTER TABLE budget_policy ADD COLUMN idle_cached_rate INTEGER;
ALTER TABLE budget_policy ADD COLUMN idle_reasoning_rate INTEGER;
ALTER TABLE usage_ledger ADD COLUMN price_snapshot TEXT;
ALTER TABLE usage_ledger ADD COLUMN sent_at REAL;
ALTER TABLE usage_ledger ADD COLUMN price_period TEXT;
'''
