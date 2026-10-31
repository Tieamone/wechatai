"""Browser verification with an isolated database; refuses to reuse a running service."""
import os
import argparse
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time

import uvicorn
from backend.app import create_app

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--context',action='store_true')
    parser.add_argument('--p0',action='store_true')
    parser.add_argument('--budgets',action='store_true')
    args=parser.parse_args()
    if args.budgets:
        from backend import ai_settings,usage
        def fixture_complete(config,system,data):
            usage.mark_sent()
            usage.settle({'prompt_tokens':20,'completion_tokens':10,'total_tokens':30})
            return '{"memories":[]}'
        ai_settings.complete=fixture_complete
    if not args.p0:
        # Legacy UI scenarios use fixed historical timestamps; P0 exercises real clocks.
        from backend import policy
        policy.age_reason=lambda *args,**kwargs:None
        policy.quiet=lambda *args:False
    sock = socket.socket()
    try:
        sock.bind(('127.0.0.1', 8765))
    except OSError:
        sock.close()
        raise SystemExit('Port 8765 is busy. Stop the local service first; test will not reuse it.')
    with tempfile.TemporaryDirectory(prefix='wechat-browser-') as directory:
        provider=None
        if args.context:
            from tools.synthetic_demo import seed
            from tools.context_fixture import fixture_provider
            seed(Path(directory),'context')
            provider=fixture_provider
        app = create_app(Path(directory), 'browser-test-only', enable_worker=True,synthetic_provider=provider)
        if not args.context:
            from backend.store import connect
            with connect(app.state.db_path) as db:
                db.execute("INSERT INTO entity_value(kind,value,token) VALUES('PHONE','13900139000','[PHONE_aa]')")
                db.execute("INSERT INTO entity_value(kind,value,token) VALUES('PHONE','+86 13900139000','[PHONE_bb]')")
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
        thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            if not server.started:
                raise RuntimeError('Test server did not start')
            env = {**os.environ, 'PLAYWRIGHT_BROWSERS_PATH':str(ROOT/'tools'/'browsers')}
            if args.p0: env['TEST_LOGIN_CODE']=app.state.login_codes.issue()
            script='e2e/budgets.mjs' if args.budgets else ('e2e/p0.mjs' if args.p0 else ('e2e/context.mjs' if args.context else 'e2e/workflow.mjs'))
            return subprocess.run(['node',script], cwd=ROOT/'frontend', env=env, timeout=120).returncode
        finally:
            server.should_exit = True
            thread.join(15)
            sock.close()
            if thread.is_alive():
                raise RuntimeError('Test server did not stop')


if __name__ == '__main__':
    raise SystemExit(main())
