"""Exercise an actual loopback server with an isolated temporary database."""
import socket
import tempfile
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

import uvicorn

from backend.app import create_app


with tempfile.TemporaryDirectory() as directory:
    app = create_app(Path(directory), 'smoke-only-token')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started, 'server did not start'
        for path in ('/', '/api/status'):
            request = Request(f'http://127.0.0.1:{port}{path}', headers={
                'Host': '127.0.0.1:8765', 'Authorization': 'Bearer smoke-only-token'})
            with urlopen(request, timeout=5) as response:
                assert response.status == 200
                body = response.read().decode()
                assert ('<div id="root">' in body) if path == '/' else ('not_connected' in body)
        print('PASS: real HTTP server, built console and authenticated API')
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive(), 'server failed to stop'
