"""Explicitly started watch-notification dispatcher; no automatic startup."""
import argparse
from pathlib import Path
from datetime import datetime, timezone
import time

from backend.bot_sender import BotSender
from backend.notifications import deliver, pending, pending_associations, deliver_association
from backend.store import connect, initialize
from backend.runtime import worker_lock, write_status, current_session, session_alive

ROOT = Path(__file__).resolve().parents[1]
CHANNEL = 'wechat-bot'


def seed_demo(directory):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    app = create_app(directory, 'synthetic-test')
    with TestClient(app, base_url='http://127.0.0.1:8765', headers={'Authorization':'Bearer synthetic-test'}) as api:
        with connect(app.state.db_path) as db:
            exists = db.execute('SELECT 1 FROM watch_item').fetchone()
        if not exists:
            api.post('/api/watches', json={'user_text':'队列测试'}).raise_for_status()
        body = {'account_ref':'synthetic', 'contact_ref':'synthetic', 'mode':'live', 'messages':[
            {'conversation_id':'bot-queue-test', 'source':{'adapter':'manual-json','id':'1','partition':'test'},
             'sender_role':'peer', 'event_time':datetime.now(timezone.utc).isoformat(), 'text':'合成消息：队列测试', 'revision':1}]}
        for _ in range(2):
            api.post('/api/import', json=body).raise_for_status()
    return app.state.db_path


def dispatch_once(path, sender):
    # One per cycle. Crash/timeout reservations survive process restart.
    ids = pending(path, CHANNEL, synthetic=sender.synthetic)
    if ids:
        state = deliver(path, ids[0], CHANNEL, sender, synthetic=sender.synthetic)
    else:
        ids = pending_associations(path, CHANNEL)
        if not ids:
            return None
        state = deliver_association(path, ids[0], CHANNEL, sender)
    print(f'提醒 #{ids[0]}：{state}', flush=True)
    if sender.last_diagnostic:
        print(f'接口诊断：{sender.last_diagnostic}', flush=True)
    return state


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--demo', action='store_true', help='Persistent isolated synthetic fixture, sends at most once')
    mode.add_argument('--watch', action='store_true', help='Dispatch local workbench watch reminders until Ctrl+C')
    parser.add_argument('--managed', action='store_true')
    args = parser.parse_args()
    path = ROOT/'data/memory.sqlite3'
    if args.demo:
        path = seed_demo(ROOT/'data/bot-acceptance')
    elif not path.exists():
        print('请先启动 run.cmd 创建本地工作台，或用 --demo 测试。')
        return 1
    if args.watch:
        directory = ROOT/'data/bot'
        try:
            with worker_lock(directory, 'bot'):
                if not args.managed:
                    (directory/'stop.request').unlink(missing_ok=True)
                write_status(directory, 'starting')
                try:
                    result = run(path, args, directory)
                    write_status(directory, 'uncertain' if result == 2 else 'stopped')
                    return result
                except Exception:
                    write_status(directory, 'stopped_error')
                    print('通知程序异常停止，请检查登录状态。', flush=True)
                    return 1
        except OSError:
            print('通知程序已运行，或无法访问状态目录。', flush=True)
            return 1
    return run(path, args)


def run(path, args, directory=None):
    initialize(path)
    sender = BotSender(synthetic=args.demo)
    session=current_session(ROOT/'data') if directory else None
    if directory and not session:
        print('工作台未运行，通知程序已停止。',flush=True)
        return 0
    print('机器人通知：仅向扫码本人发送通用提示；accepted 表示接口接受，不代表手机显示。', flush=True)
    try:
        while True:
            if directory:
                if (directory/'stop.request').exists() or not session_alive(ROOT/'data',session):
                    return 0
                write_status(directory, 'running')
            state = dispatch_once(path, sender)
            if state == 'uncertain':
                print('结果待确认，已保留记录并停止；重启也不会重发这条提醒。', flush=True)
                return 2
            if not args.watch:
                with connect(path) as db:
                    counts = [dict(r) for r in db.execute('SELECT state,count(*) AS count FROM notification_attempt WHERE channel=? GROUP BY state', (CHANNEL,))]
                print(f'本次结束；通知记录：{counts}。无待发送项时不会调用微信。')
                return 0
            for _ in range(10):
                if directory and ((directory/'stop.request').exists() or not session_alive(ROOT/'data',session)):
                    return 0
                time.sleep(1)
    except KeyboardInterrupt:
        print('通知程序已停止。')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
