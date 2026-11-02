SCHEMA = '''
ALTER TABLE usage_ledger ADD COLUMN reasoning_tokens INTEGER CHECK(reasoning_tokens>=0);
ALTER TABLE usage_ledger ADD COLUMN cached_input_tokens INTEGER CHECK(cached_input_tokens>=0);
ALTER TABLE usage_ledger ADD COLUMN reported_text_tokens INTEGER CHECK(reported_text_tokens>=0);
'''
