import sqlite3
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation (
 id TEXT PRIMARY KEY, account_ref TEXT NOT NULL, contact_ref TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS message (
 id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversation(id),
 adapter TEXT NOT NULL, source_id TEXT NOT NULL, partition TEXT NOT NULL,
 sender_role TEXT NOT NULL, event_time TEXT, event_utc TEXT, observed_at TEXT,
 text TEXT NOT NULL, type TEXT NOT NULL, revision INTEGER NOT NULL,
 UNIQUE(conversation_id, adapter, partition, source_id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(text, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS watch_item (
 id INTEGER PRIMARY KEY, user_text TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
 revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS reminder (
 id INTEGER PRIMARY KEY, trigger_id INTEGER NOT NULL REFERENCES message(id),
 watch_id INTEGER NOT NULL REFERENCES watch_item(id), watch_revision INTEGER NOT NULL,
 reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
 UNIQUE(trigger_id, watch_id, watch_revision)
);
CREATE TABLE IF NOT EXISTS entity_value (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL,
 token TEXT NOT NULL UNIQUE, UNIQUE(kind, value)
);
CREATE TABLE IF NOT EXISTS ingestion_checkpoint (
 adapter TEXT NOT NULL, partition TEXT NOT NULL, cursor TEXT NOT NULL,
 PRIMARY KEY(adapter, partition)
);
"""

MEMORY_SCHEMA = """
CREATE TABLE memory (
 id INTEGER PRIMARY KEY, summary TEXT NOT NULL, subject TEXT NOT NULL,
 polarity TEXT NOT NULL, assertion TEXT NOT NULL, temporal_kind TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active', origin TEXT NOT NULL DEFAULT 'manual',
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 fingerprint TEXT NOT NULL UNIQUE
);
CREATE TABLE memory_evidence (
 memory_id INTEGER NOT NULL REFERENCES memory(id),
 message_id INTEGER NOT NULL REFERENCES message(id), message_revision INTEGER NOT NULL,
 span_start INTEGER NOT NULL, span_end INTEGER NOT NULL,
 PRIMARY KEY(memory_id,message_id,span_start,span_end)
);
CREATE TABLE correction (
 id INTEGER PRIMARY KEY, memory_id INTEGER NOT NULL REFERENCES memory(id),
 reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE suppressed_evidence (
 message_id INTEGER NOT NULL REFERENCES message(id), message_revision INTEGER NOT NULL,
 PRIMARY KEY(message_id,message_revision)
);
CREATE VIRTUAL TABLE memory_fts USING fts5(summary, tokenize='unicode61');
"""

TASK_SCHEMA = """
CREATE TABLE initialization (
 id INTEGER PRIMARY KEY, mode TEXT NOT NULL, cutoff TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE job (
 id INTEGER PRIMARY KEY, initialization_id INTEGER NOT NULL REFERENCES initialization(id),
 idempotency_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL DEFAULT 'prepare_window',
 state TEXT NOT NULL DEFAULT 'pending', input_json TEXT NOT NULL,
 owner TEXT, lease_until REAL, attempt INTEGER NOT NULL DEFAULT 0,
 result_json TEXT, error TEXT
);
CREATE INDEX job_state ON job(state,initialization_id);
"""

MODEL_SCHEMA = """
CREATE TABLE model_request (
 id TEXT PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES job(id),
 input_hash TEXT NOT NULL, mapping_hash TEXT NOT NULL, prompt_version TEXT NOT NULL,
 provider TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'issued',
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 result_hash TEXT, memory_ids_json TEXT
);
"""

WATCH_SCHEMA = """
ALTER TABLE watch_item ADD COLUMN source_memory_id INTEGER REFERENCES memory(id);
"""

OUTBOX_SCHEMA = """
CREATE TABLE outbox (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, reminder_id INTEGER NOT NULL REFERENCES reminder(id),
 revision INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(reminder_id,revision)
);
INSERT INTO outbox(reminder_id,revision) SELECT id,1 FROM reminder ORDER BY id;
CREATE TRIGGER reminder_created AFTER INSERT ON reminder BEGIN
 INSERT INTO outbox(reminder_id,revision) VALUES(NEW.id,1);
END;
CREATE TRIGGER reminder_changed AFTER UPDATE OF status,reason ON reminder
WHEN OLD.status!=NEW.status OR OLD.reason!=NEW.reason BEGIN
 INSERT INTO outbox(reminder_id,revision)
 SELECT NEW.id,COALESCE(MAX(revision),0)+1 FROM outbox WHERE reminder_id=NEW.id;
END;
"""

ASSOCIATION_SCHEMA = """
CREATE TABLE association (
 id INTEGER PRIMARY KEY, trigger_id INTEGER NOT NULL REFERENCES message(id),
 trigger_revision INTEGER NOT NULL, memory_id INTEGER NOT NULL REFERENCES memory(id),
 memory_version INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 owner TEXT, lease_until REAL, error TEXT,
 UNIQUE(trigger_id,trigger_revision,memory_id,memory_version)
);
"""


@contextmanager
def connect(path: Path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version > 18:
            raise RuntimeError('Database schema is newer than this application')
        db.execute('PRAGMA journal_mode=WAL')
        # Each migration commits its schema and version together, including DDL.
        if version == 0:
            db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nPRAGMA user_version=1;\nCOMMIT;')
            version = 1
        if version == 1:
            db.executescript('BEGIN IMMEDIATE;\n' + MEMORY_SCHEMA + '\nPRAGMA user_version=2;\nCOMMIT;')
            version = 2
        if version == 2:
            db.executescript('BEGIN IMMEDIATE;\n' + TASK_SCHEMA + '\nPRAGMA user_version=3;\nCOMMIT;')
            version = 3
        if version == 3:
            db.executescript('BEGIN IMMEDIATE;\n' + MODEL_SCHEMA + '\nPRAGMA user_version=4;\nCOMMIT;')
            version = 4
        if version == 4:
            db.executescript('BEGIN IMMEDIATE;\n' + WATCH_SCHEMA + '\nPRAGMA user_version=5;\nCOMMIT;')
            version = 5
        if version == 5:
            db.executescript('BEGIN IMMEDIATE;\n' + OUTBOX_SCHEMA + '\nPRAGMA user_version=6;\nCOMMIT;')
            version = 6
        if version == 6:
            db.executescript('BEGIN IMMEDIATE;\n' + ASSOCIATION_SCHEMA + '\nPRAGMA user_version=7;\nCOMMIT;')
            version = 7
        if version == 7:
            db.executescript('BEGIN IMMEDIATE; ALTER TABLE association ADD COLUMN context_hash TEXT; PRAGMA user_version=8; COMMIT;')
            version = 8
        if version == 8:
            db.executescript("""BEGIN IMMEDIATE;
ALTER TABLE reminder ADD COLUMN context_json TEXT NOT NULL DEFAULT '[]';
CREATE TRIGGER reminder_context_changed AFTER UPDATE OF trigger_id,context_json ON reminder
WHEN (OLD.trigger_id!=NEW.trigger_id OR OLD.context_json!=NEW.context_json)
 AND OLD.status=NEW.status AND OLD.reason=NEW.reason BEGIN
 INSERT INTO outbox(reminder_id,revision)
 SELECT NEW.id,COALESCE(MAX(revision),0)+1 FROM outbox WHERE reminder_id=NEW.id;
END;
PRAGMA user_version=9; COMMIT;""")
            version = 9
        if version == 9:
            db.executescript('BEGIN IMMEDIATE; ALTER TABLE memory ADD COLUMN supersedes_id INTEGER REFERENCES memory(id); PRAGMA user_version=10; COMMIT;')
            version = 10
        if version == 10:
            db.executescript("""BEGIN IMMEDIATE;
CREATE TABLE ordinary_outbox (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 association_id INTEGER NOT NULL REFERENCES association(id),
 revision INTEGER NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(association_id,revision)
);
INSERT INTO ordinary_outbox(association_id,revision)
 SELECT id,1 FROM association WHERE state='confirmed_synthetic' ORDER BY id;
CREATE TRIGGER association_published AFTER UPDATE OF state ON association
WHEN OLD.state!=NEW.state AND (NEW.state='confirmed_synthetic'
 OR EXISTS(SELECT 1 FROM ordinary_outbox WHERE association_id=NEW.id)) BEGIN
 INSERT INTO ordinary_outbox(association_id,revision)
 SELECT NEW.id,COALESCE(MAX(revision),0)+1 FROM ordinary_outbox WHERE association_id=NEW.id;
END;
PRAGMA user_version=11; COMMIT;""")
            version = 11
        if version == 11:
            db.executescript("""BEGIN IMMEDIATE;
CREATE TABLE person (
 id INTEGER PRIMARY KEY, label TEXT NOT NULL, token TEXT NOT NULL UNIQUE
);
CREATE TABLE person_property (
 id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES person(id),
 kind TEXT NOT NULL, value TEXT NOT NULL, value_token TEXT,
 valid_from TEXT, valid_to TEXT, confirmation_note TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
PRAGMA user_version=12; COMMIT;""")
            version = 12
        if version == 12:
            db.executescript('BEGIN IMMEDIATE; CREATE TABLE mapping_policy (normalization_version INTEGER NOT NULL); INSERT INTO mapping_policy VALUES(1); PRAGMA user_version=13; COMMIT;')
            version = 13
        if version == 13:
            db.executescript('''BEGIN IMMEDIATE;
CREATE TABLE property_evidence (
 property_id INTEGER NOT NULL REFERENCES person_property(id),
 message_id INTEGER NOT NULL REFERENCES message(id), message_revision INTEGER NOT NULL,
 span_start INTEGER NOT NULL, span_end INTEGER NOT NULL,
 PRIMARY KEY(property_id,message_id,span_start,span_end)
);
PRAGMA user_version=14; COMMIT;''')
            version = 14
        if version == 14:
            db.executescript('''BEGIN IMMEDIATE;
CREATE TABLE mapping_resolution (
 kind TEXT NOT NULL, normalized_value TEXT NOT NULL, token_group TEXT NOT NULL,
 selected_token TEXT NOT NULL REFERENCES entity_value(token), note TEXT NOT NULL,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(kind,normalized_value)
);
PRAGMA user_version=15; COMMIT;''')
            version = 15
        if version == 15:
            db.executescript('''BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS notification_attempt (
 reminder_id INTEGER NOT NULL REFERENCES reminder(id),
 channel TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('uncertain','accepted','rejected')),
 attempts INTEGER NOT NULL DEFAULT 1,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(reminder_id,channel)
);
PRAGMA user_version=16; COMMIT;''')
            version = 16
        if version == 16:
            db.executescript('''BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS auto_control (
 id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0,
 initialization_id INTEGER REFERENCES initialization(id), daily_limit INTEGER NOT NULL DEFAULT 200
);
INSERT OR IGNORE INTO auto_control(id) VALUES(1);
CREATE TABLE IF NOT EXISTS auto_message (
 trigger_id INTEGER PRIMARY KEY REFERENCES message(id), job_id INTEGER NOT NULL REFERENCES job(id),
 state TEXT NOT NULL DEFAULT 'pending', ready_at REAL NOT NULL, lease_until REAL,
 error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS auto_call (id INTEGER PRIMARY KEY, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS association_notification (
 association_id INTEGER NOT NULL REFERENCES association(id), channel TEXT NOT NULL,
 state TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(association_id,channel)
);
DROP TRIGGER association_published;
CREATE TRIGGER association_published AFTER UPDATE OF state ON association
WHEN OLD.state!=NEW.state AND (NEW.state IN ('confirmed_synthetic','confirmed_model')
 OR EXISTS(SELECT 1 FROM ordinary_outbox WHERE association_id=NEW.id)) BEGIN
 INSERT INTO ordinary_outbox(association_id,revision)
 SELECT NEW.id,COALESCE(MAX(revision),0)+1 FROM ordinary_outbox WHERE association_id=NEW.id;
END;
PRAGMA user_version=17; COMMIT;''')
            version = 17
        if version == 17:
            db.executescript('''BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS memory_review (
 memory_id INTEGER PRIMARY KEY REFERENCES memory(id),
 confirmed_version INTEGER NOT NULL, confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
PRAGMA user_version=18; COMMIT;''')
        if db.execute('SELECT normalization_version FROM mapping_policy').fetchone()[0] != 1:
            raise RuntimeError('Unsupported mapping normalization version')


def tokens(text: str) -> str:
    # Character segmentation keeps one/two-character Chinese queries searchable.
    # This is a recall baseline, not semantic retrieval.
    return ' '.join(text.casefold())
