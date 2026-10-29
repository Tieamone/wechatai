import secrets
import time
from . import policy
from .login import LoginCode, Exchange
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .models import ImportRequest, Mapping, Preview, Watch, WatchState
from .privacy import add_mapping, preview
from .store import connect, initialize, tokens
from .memories import invalidate_source, memory_card, router as memory_router
from .jobs import invalidate_jobs, router as jobs_router
from .model_contract import router as model_router
from .worker import PreparationWorker
from .associations import enqueue_candidates, supersede_recent, router as association_router
from .watches import update_watches, invalidate_context, context_quotes
from .entities import router as entity_router, invalidate_property_source
from .mapping_conflicts import router as mapping_conflict_router
from .collector import collector_status
from . import runtime
from .ai_settings import router as ai_router, load as load_ai_settings
from .automatic import router as automatic_router, enqueue as enqueue_automatic

ROOT = Path(__file__).resolve().parents[1]


def create_app(data_dir: Path | None = None, session_token: str | None = None, *, enable_worker=False, synthetic_provider=None, synthetic_judge=None):
    data_dir = data_dir or ROOT / 'data'
    path = data_dir / 'memory.sqlite3'
    initialize(path)
    token = session_token or secrets.token_urlsafe(32)
    worker = PreparationWorker(path, synthetic_provider, synthetic_judge) if enable_worker else None

    @asynccontextmanager
    async def lifespan(app):
        managed = worker is not None and data_dir.resolve()==(ROOT/'data').resolve() and synthetic_provider is None
        with runtime.workbench_session(data_dir) if managed and not runtime.owned_workbench(data_dir) else nullcontext():
            if worker:
                worker.start()
            try:
                yield
            finally:
                if managed:
                    for name in runtime.MODULES:
                        runtime.control(data_dir,name,'stop')
                if worker:
                    worker.stop()

    app = FastAPI(title='微信聊天记忆助手', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.token = token
    app.state.login_codes = LoginCode()

    @app.post('/api/session/exchange')
    def exchange(body: Exchange):
        app.state.login_codes.consume(body.code)
        return {'token':token}
    app.state.db_path = path
    app.state.preparation_worker = worker
    app.include_router(policy.router(path))
    app.include_router(memory_router(path))
    app.include_router(jobs_router(path))
    app.include_router(model_router(path))
    app.include_router(association_router(path))
    app.include_router(entity_router(path))
    app.include_router(mapping_conflict_router(path))
    app.include_router(ai_router(path, allowed=synthetic_provider is None))
    app.include_router(automatic_router(path, allowed=synthetic_provider is None))

    @app.get('/api/worker')
    def worker_status():
        return worker.status() if worker else {'running': False, 'error': None, 'scope':'preparation_and_opt_in_automatic'}

    @app.post('/api/worker/restart')
    def restart_worker():
        if not worker:
            raise HTTPException(409, '当前服务未启用后台准备程序')
        worker.start()
        return worker.status()

    @app.middleware('http')
    async def boundary(request: Request, call_next):
        if request.headers.get('host') not in {'127.0.0.1:8765', 'localhost:8765'}:
            return JSONResponse({'detail': 'Host denied'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin not in {'http://127.0.0.1:8765', 'http://localhost:8765'}:
            return JSONResponse({'detail': 'Origin denied'}, status_code=403)
        if request.url.path.startswith('/api/') and request.url.path != '/api/session/exchange':
            supplied = request.headers.get('authorization', '')
            if not secrets.compare_digest(supplied, f'Bearer {token}'):
                return JSONResponse({'detail': '请使用启动窗口中的会话令牌'}, status_code=401)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.get('/api/status')
    def status():
        collection = collector_status(data_dir)
        ai_config = load_ai_settings(data_dir)
        with connect(path) as db:
            return {'messages': db.execute('SELECT count(*) FROM message').fetchone()[0],
                    'text_messages': db.execute("SELECT count(*) FROM message WHERE type='text'").fetchone()[0],
                    'unknown_time': db.execute('SELECT count(*) FROM message WHERE event_utc IS NULL').fetchone()[0],
                    'memories': db.execute("SELECT count(*) FROM memory WHERE status='active'").fetchone()[0], 'api': 'manual_enabled' if ai_config.get('enabled') and synthetic_provider is None else 'disabled', 'collector': collection['state'], 'collector_detail': collection,
                    'mobile': 'bot_notification_only', 'synthetic_demo': synthetic_provider is not None,
                    'stage': '本地采集、记忆与提醒工作台'}

    def runtime_enabled():
        return data_dir.resolve() == (ROOT / 'data').resolve() and synthetic_provider is None

    @app.get('/api/runtime')
    def runtime_status():
        if not runtime_enabled():
            return {'enabled': False}
        return {'enabled': True, **{name: runtime.status(data_dir, name) for name in runtime.MODULES}}

    @app.post('/api/runtime/{name}/{action}')
    def runtime_control(name: Literal['collector', 'bot'], action: Literal['start', 'stop']):
        if not runtime_enabled():
            raise HTTPException(409, '演示或测试数据目录不支持控制真实采集和通知')
        try:
            return runtime.control(data_dir, name, action)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.post('/api/import')
    def ingest(body: ImportRequest):
        ids = {m.conversation_id for m in body.messages}
        if len(ids) != 1:
            raise HTTPException(422, '首版只允许一个会话')
        conversation = next(iter(ids))
        inserted = updated = duplicates = 0
        imported_at = time.time()
        with connect(path) as db:
            config = policy.settings(db)
            existing = db.execute('SELECT * FROM conversation').fetchone()
            if existing and (existing['id'], existing['account_ref'], existing['contact_ref']) != (conversation, body.account_ref, body.contact_ref):
                raise HTTPException(409, '已固定其他账号或联系人')
            db.execute('INSERT OR IGNORE INTO conversation VALUES(?,?,?)', (conversation, body.account_ref, body.contact_ref))
            for m in body.messages:
                key = (conversation, m.source.adapter, m.source.partition, m.source.id)
                previous = db.execute('SELECT * FROM message WHERE conversation_id=? AND adapter=? AND partition=? AND source_id=?', key).fetchone()
                if previous and previous['revision'] >= m.revision:
                    if previous['revision'] == m.revision and any(previous[k] != getattr(m, k) for k in ('text', 'type', 'sender_role', 'event_time')):
                        raise HTTPException(409, '相同来源版本的内容不一致，请增加 revision')
                    duplicates += 1
                    continue
                values = (m.sender_role, m.event_time, m.utc_time(), m.observed_at, m.text if m.type == 'text' else '', m.type, m.revision)
                if previous:
                    message_id = previous['id']
                    invalidate_source(db, message_id)
                    invalidate_property_source(db, message_id)
                    invalidate_jobs(db, message_id)
                    invalidate_context(db, message_id)
                    db.execute('UPDATE message SET sender_role=?,event_time=?,event_utc=?,observed_at=?,text=?,type=?,revision=? WHERE id=?', (*values, message_id))
                    db.execute('DELETE FROM message_fts WHERE rowid=?', (message_id,))
                    db.execute("UPDATE reminder SET status='withdrawn' WHERE trigger_id=?", (message_id,))
                    updated += 1
                else:
                    cursor = db.execute('INSERT INTO message(conversation_id,adapter,partition,source_id,sender_role,event_time,event_utc,observed_at,text,type,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (*key, *values))
                    message_id = cursor.lastrowid
                    inserted += 1
                if m.type == 'text':
                    db.execute('INSERT INTO message_fts(rowid,text) VALUES(?,?)', (message_id, tokens(m.text)))
                reason = policy.age_reason(m.utc_time(), imported_at, config) if body.mode == 'live' and synthetic_provider is None else None
                live = body.mode == 'live' and reason is None
                if body.mode == 'live' and not previous and reason:
                    policy.defer(db, message_id, reason)
                if live and not previous:
                    supersede_recent(db, message_id)
                    update_watches(db, message_id)
                # Explicit exact user watch matching only. No fabricated ordinary memory.
                if live and m.sender_role == 'peer' and m.type == 'text' and not previous:
                    enqueue_candidates(db, message_id)
                if live and not previous:
                    enqueue_automatic(db, message_id)
                db.execute('INSERT INTO ingestion_checkpoint VALUES(?,?,?) ON CONFLICT(adapter,partition) DO UPDATE SET cursor=excluded.cursor', (m.source.adapter, m.source.partition, m.source.id))
        return {'inserted': inserted, 'updated': updated, 'duplicates': duplicates}

    # Trusted local collector shares exactly the same validation/import transaction.
    app.state.ingest = ingest

    @app.get('/api/search')
    def search(q: str = ''):
        q = q.strip()
        if not q or len(q) > 200:
            return []
        with connect(path) as db:
            # Quoted literal phrase: user input cannot introduce FTS query operators.
            phrase = '"' + tokens(q).replace('"', '""') + '"'
            rows = db.execute("SELECT m.* FROM message m JOIN message_fts f ON f.rowid=m.id WHERE message_fts MATCH ? AND m.type='text' ORDER BY m.event_utc DESC,m.id DESC LIMIT 50", (phrase,)).fetchall()
            return [dict(row) for row in rows]

    @app.get('/api/watches')
    def watches():
        with connect(path) as db:
            return [dict(r) for r in db.execute('SELECT w.*,m.status AS source_status,m.summary AS source_summary FROM watch_item w LEFT JOIN memory m ON m.id=w.source_memory_id ORDER BY w.id DESC')]

    @app.post('/api/watches')
    def watch_add(body: Watch):
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            if body.source_memory_id is not None and not db.execute("SELECT 1 FROM memory WHERE id=? AND status='active'", (body.source_memory_id,)).fetchone():
                raise HTTPException(409, '只能关注当前有效的记忆')
            cursor = db.execute('INSERT INTO watch_item(user_text,source_memory_id) VALUES(?,?)', (body.user_text, body.source_memory_id))
            return {'id': cursor.lastrowid}

    @app.patch('/api/watches/{watch_id}')
    def watch_state(watch_id: int, body: WatchState):
        with connect(path) as db:
            cursor = db.execute('UPDATE watch_item SET enabled=?,revision=revision+1 WHERE id=?', (body.enabled, watch_id))
            if not cursor.rowcount:
                raise HTTPException(404, '关注项不存在')
            db.execute("UPDATE reminder SET status='withdrawn' WHERE watch_id=?", (watch_id,))
        return {'ok': True}

    @app.get('/api/reminders')
    def reminders():
        with connect(path) as db:
            cards = []
            for row in db.execute("SELECT r.*,m.text AS trigger_text,m.event_time,w.user_text,w.source_memory_id FROM reminder r JOIN message m ON m.id=r.trigger_id JOIN watch_item w ON w.id=r.watch_id LEFT JOIN memory fact ON fact.id=w.source_memory_id WHERE r.status='active' AND w.enabled=1 AND r.watch_revision=w.revision AND (w.source_memory_id IS NULL OR fact.status='active') ORDER BY r.id DESC LIMIT 100").fetchall():
                card = dict(row)
                card['notification'] = policy.card_status(db, 'watch', row['id'])
                card['recent_context'] = context_quotes(db, row['context_json'])
                card['source_memory'] = None
                if row['source_memory_id']:
                    fact = db.execute('SELECT * FROM memory WHERE id=?', (row['source_memory_id'],)).fetchone()
                    card['source_memory'] = memory_card(db, fact)
                cards.append(card)
            return cards

    @app.post('/api/reminders/{reminder_id}/dismiss')
    def dismiss(reminder_id: int):
        with connect(path) as db:
            db.execute("UPDATE reminder SET status='incorrect' WHERE id=?", (reminder_id,))
        return {'ok': True}

    @app.post('/api/mappings')
    def mapping(body: Mapping):
        with connect(path) as db:
            return add_mapping(db, body.kind, body.value)

    @app.post('/api/privacy/preview')
    def privacy_preview(body: Preview):
        with connect(path) as db:
            return preview(db, body.text)

    static = ROOT / 'frontend' / 'dist'
    if static.exists():
        app.mount('/', StaticFiles(directory=static, html=True), name='console')
    return app
