import threading
import time
import webbrowser
import uvicorn
from .app import create_app, ROOT
from .runtime import workbench_session

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
