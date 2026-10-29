import uvicorn

from .app import create_app

app = create_app(enable_worker=True)
print('\n微信聊天记忆助手：http://127.0.0.1:8765')
print(f'本次会话令牌（复制到页面）：{app.state.token}\n')
uvicorn.run(app, host='127.0.0.1', port=8765, access_log=False)
