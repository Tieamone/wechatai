SCHEMA = '''
CREATE TABLE job_message (
 job_id INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
 message_id INTEGER NOT NULL REFERENCES message(id), revision INTEGER NOT NULL,
 PRIMARY KEY(job_id,message_id)
);
CREATE INDEX job_message_source ON job_message(message_id,revision,job_id);
CREATE INDEX job_initialization ON job(initialization_id,id);
INSERT INTO job_message SELECT j.id,json_extract(x.value,'$.id'),json_extract(x.value,'$.revision')
 FROM job j,json_each(j.input_json,'$.messages') x;
CREATE TRIGGER job_message_insert AFTER INSERT ON job BEGIN
 INSERT INTO job_message SELECT NEW.id,json_extract(value,'$.id'),json_extract(value,'$.revision')
 FROM json_each(NEW.input_json,'$.messages');
END;
CREATE TRIGGER deferred_cancel AFTER UPDATE OF state ON initialization
WHEN NEW.state='cancelled' BEGIN
 UPDATE deferred_message SET initialization_id=NULL WHERE initialization_id=NEW.id;
END;
CREATE TRIGGER deferred_revision AFTER UPDATE OF revision ON message
WHEN NEW.revision!=OLD.revision BEGIN
 UPDATE deferred_message SET message_revision=NEW.revision,initialization_id=NULL,reason='source_revised'
 WHERE message_id=NEW.id;
END;
CREATE TABLE usage_ledger (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, model TEXT NOT NULL, at REAL NOT NULL,
 state TEXT NOT NULL, reserved_input INTEGER NOT NULL, reserved_output INTEGER NOT NULL,
 input_tokens INTEGER, output_tokens INTEGER, legacy_id INTEGER UNIQUE
);
CREATE INDEX usage_at ON usage_ledger(kind,at);
INSERT INTO usage_ledger(kind,model,at,state,reserved_input,reserved_output,legacy_id)
 SELECT 'automatic','legacy',at,'unknown',0,4096,id FROM auto_call;
CREATE TABLE feedback (
 id INTEGER PRIMARY KEY, memory_id INTEGER NOT NULL REFERENCES memory(id), memory_version INTEGER NOT NULL,
 action TEXT NOT NULL, evidence_json TEXT NOT NULL, valid INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER feedback_revision AFTER UPDATE OF revision ON message WHEN NEW.revision!=OLD.revision BEGIN
 UPDATE feedback SET valid=0 WHERE EXISTS(SELECT 1 FROM json_each(evidence_json) e
 WHERE json_extract(e.value,'$.message_id')=NEW.id AND json_extract(e.value,'$.message_revision')!=NEW.revision);
END;
UPDATE deferred_message SET initialization_id=NULL WHERE initialization_id IN
 (SELECT id FROM initialization WHERE state='cancelled');
UPDATE deferred_message SET initialization_id=NULL,message_revision=(SELECT revision FROM message WHERE id=message_id),reason='source_revised'
 WHERE message_revision!=(SELECT revision FROM message WHERE id=message_id);
'''
