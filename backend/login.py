import secrets
import threading
import time
from fastapi import HTTPException
from pydantic import Field
from .models import StrictModel


class Exchange(StrictModel):
    code: str = Field(min_length=20, max_length=200)


class LoginCode:
    def __init__(self):
        self.lock=threading.Lock()
        self.code=None
        self.expires=0

    def issue(self):
        with self.lock:
            self.code=secrets.token_urlsafe(32)
            self.expires=time.monotonic()+60
            return self.code

    def consume(self,code):
        with self.lock:
            if not self.code or time.monotonic()>self.expires or not secrets.compare_digest(code,self.code):
                raise HTTPException(401,'登录链接已失效，请使用启动窗口中的会话令牌')
            self.code=None
