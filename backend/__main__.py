import threading
import sqlite3
import time
import webbrowser
import uvicorn
from .app import create_app, ROOT
from .runtime import workbench_session

try:
    with workbench_session(ROOT/'data'):
        app=create_app(enable_worker=True)
        print('\n聊天记忆助手：http://127.0.0.1:8765')
        print(f'备用会话令牌（自动登录不可用时输入）：{app.state.token}\n')
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8765,access_log=False))
        def open_console():
            while not server.started and not server.should_exit:
                time.sleep(.1)
            if server.started:
                code=app.state.login_codes.issue()
                webbrowser.open('http://127.0.0.1:8765/#login='+code)
        threading.Thread(target=open_console,daemon=True).start()
        server.run()
except (OSError, RuntimeError, sqlite3.Error) as error:
    from .backups import record_failure
    if isinstance(error,OSError):
        reason='启动或升级受阻：请先停止工作台、采集和机器人，再检查数据目录权限及磁盘空间后重试。'
    else:
        reason=str(error) if str(error).startswith('备份') else '数据库升级未完成，请检查数据库版本和本地备份后重试。'
    record_failure(ROOT/'data/memory.sqlite3',reason)
    print(reason)
    raise SystemExit(1)
