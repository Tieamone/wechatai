"""Synthetic end-to-end notification demo; network disabled unless explicitly selected."""
import argparse
from getpass import getpass
import os
from pathlib import Path
from datetime import datetime, timezone
import tempfile

import httpx
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.notifications import ServerChan, deliver, pending, TEST_TITLE, TEST_BODY


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serverchan-test', action='store_true', help='Send ONE synthetic message using your Turbo account')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='wechat-notification-') as directory:
        app = create_app(Path(directory), 'local-test')
        with TestClient(app, base_url='http://127.0.0.1:8765', headers={'Authorization':'Bearer local-test'}) as api:
            api.post('/api/watches', json={'user_text':'通知测试'}).raise_for_status()
            data = {'account_ref':'synthetic', 'contact_ref':'synthetic', 'mode':'live', 'messages':[
                {'conversation_id':'notification-test', 'source':{'adapter':'manual-json','id':'1','partition':'test'},
                 'sender_role':'peer', 'event_time':datetime.now(timezone.utc).isoformat(), 'text':'合成消息：通知测试', 'revision':1}]}
            api.post('/api/import', json=data).raise_for_status()
            api.post('/api/import', json=data).raise_for_status()
            path = app.state.db_path
            channel = 'serverchan' if args.serverchan_test else 'local-preview'
            ids = pending(path, channel)
            assert len(ids) == 1
            with httpx.Client(trust_env=False) as client:
                if args.serverchan_test:
                    print('将发送一条合成测试到你的 Server酱默认通道；请先在控制台确认它是你本人的微信。')
                    key = os.environ.get('SERVERCHAN_SENDKEY') or getpass('粘贴 SCT SendKey（不显示、不保存）：')
                    transport = ServerChan(key.strip(), client)
                    def sender(title, body):
                        return transport(TEST_TITLE, TEST_BODY)
                else:
                    def sender(title, body):
                        print(f'本地预览（未联网）：{title}\n{body}')
                        return 'accepted'
                result = deliver(path, ids[0], channel, sender, synthetic=True)
                duplicate = deliver(path, ids[0], channel, sender, synthetic=True)
            print(f'首次处理：{result}；重复处理：{duplicate}')
            api.post(f'/api/reminders/{ids[0]}/dismiss').raise_for_status()
            assert deliver(path, ids[0], channel, sender, synthetic=True) == 'skipped_inactive'
            print('重复导入只产生一条提醒；重复处理不再次发送；停用提醒不会发送。')
            print('手机显示状态：待人工确认。' if args.serverchan_test else '本地闭环通过；本次没有向微信发送消息。')
            return 0 if result == 'accepted' else 1


if __name__ == '__main__':
    raise SystemExit(main())
