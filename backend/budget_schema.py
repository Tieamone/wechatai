SCHEMA = '''
CREATE INDEX message_time ON message(event_utc DESC,id DESC);
CREATE INDEX message_conversation_time ON message(conversation_id,event_utc DESC,id DESC);
CREATE TABLE budget_policy (
 id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0,
 token_limit INTEGER NOT NULL DEFAULT 1000000, amount_limit INTEGER,
 model TEXT NOT NULL DEFAULT '', base_url TEXT NOT NULL DEFAULT '',
 input_rate INTEGER, output_rate INTEGER, cached_rate INTEGER, reasoning_rate INTEGER
);
INSERT INTO budget_policy(id) VALUES(1);
CREATE TABLE batch_run (
 id INTEGER PRIMARY KEY, initialization_id INTEGER NOT NULL REFERENCES initialization(id),
 request_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'active', reason TEXT,
 max_windows INTEGER NOT NULL, token_limit INTEGER NOT NULL, amount_limit INTEGER,
 model TEXT NOT NULL, base_url TEXT NOT NULL,
 created_at REAL NOT NULL
);
CREATE TABLE batch_step (
 run_id INTEGER NOT NULL REFERENCES batch_run(id), job_id INTEGER NOT NULL REFERENCES job(id),
 state TEXT NOT NULL DEFAULT 'pending', lease_until REAL,
 PRIMARY KEY(run_id,job_id)
);
CREATE UNIQUE INDEX batch_one_running ON batch_run(initialization_id) WHERE state IN ('active','paused');
CREATE UNIQUE INDEX batch_step_active ON batch_step(job_id) WHERE state IN ('pending','processing');
ALTER TABLE usage_ledger ADD COLUMN batch_id INTEGER REFERENCES batch_run(id);
ALTER TABLE usage_ledger ADD COLUMN reserved_cost INTEGER;
ALTER TABLE usage_ledger ADD COLUMN actual_cost INTEGER;
ALTER TABLE usage_ledger ADD COLUMN input_rate INTEGER;
ALTER TABLE usage_ledger ADD COLUMN output_rate INTEGER;
ALTER TABLE usage_ledger ADD COLUMN lease_until REAL;
CREATE INDEX usage_batch ON usage_ledger(batch_id);
CREATE INDEX usage_time ON usage_ledger(at);
CREATE INDEX usage_reserved_lease ON usage_ledger(lease_until) WHERE state='reserved';
DROP TRIGGER deferred_cancel;
CREATE TRIGGER deferred_cancel AFTER UPDATE OF state ON initialization
WHEN NEW.state='cancelled' BEGIN
 UPDATE deferred_message SET initialization_id=NULL WHERE initialization_id=NEW.id AND NOT (
  EXISTS(SELECT 1 FROM job_message x JOIN job j ON j.id=x.job_id
   WHERE j.initialization_id=NEW.id AND x.message_id=deferred_message.message_id AND x.revision=deferred_message.message_revision)
  AND NOT EXISTS(SELECT 1 FROM job_message x JOIN job j ON j.id=x.job_id
   WHERE j.initialization_id=NEW.id AND x.message_id=deferred_message.message_id AND x.revision=deferred_message.message_revision AND j.state!='validated_model')
 );
 UPDATE batch_run SET state='cancelled',reason='initialization_cancelled' WHERE initialization_id=NEW.id AND state IN ('active','paused');
 UPDATE batch_step SET state='cancelled' WHERE run_id IN (SELECT id FROM batch_run WHERE initialization_id=NEW.id) AND state='pending';
END;
'''
