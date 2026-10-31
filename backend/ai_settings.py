"""User-configured compatible API, explicit calls only; credentials protected by Windows."""
import base64
import ctypes
from ctypes import wintypes
import json
import threading
import time
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import Field, SecretStr, field_validator

from .models import StrictModel
from .model_contract import issue_request, commit_response
from .store import connect

LOCK = threading.Lock()


def protect(value: bytes, *, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]
    buffer = ctypes.create_string_buffer(value)
    source, output = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), Blob()
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    function = api.CryptUnprotectData if decrypt else api.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise HTTPException(409, '无法访问此 Windows 用户保存的密钥，请重新填写')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel = ctypes.WinDLL('kernel32')
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


class Settings(StrictModel):
    base_url: str = Field(max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = SecretStr('')
    enabled: bool = False

    @field_validator('base_url')
    @classmethod
    def valid_url(cls, value):
        value = value.strip().rstrip('/')
        parsed = urlsplit(value)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('API 地址不能包含凭据、查询参数或片段')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost','127.0.0.1','::1'}):
            raise ValueError('远程 API 必须使用 HTTPS；HTTP 只允许本机')
        if parsed.path.endswith('/chat/completions'):
            raise ValueError('填写基础地址，不包含 /chat/completions')
        return value


def load(directory):
    try:
        return json.loads((directory/'ai-settings.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'base_url':'', 'model':'', 'enabled':False}


def public(config):
    return {**{key:config[key] for key in ('base_url','model','enabled')}, 'has_key':bool(config.get('key_encrypted'))}


def save(directory, body):
    with LOCK:
        previous = load(directory)
        config = body.model_dump(exclude={'api_key'})
        secret = body.api_key.get_secret_value().strip()
        if secret:
            config['key_encrypted'] = base64.b64encode(protect(secret.encode())).decode()
        elif previous.get('base_url') == body.base_url:
            config['key_encrypted'] = previous.get('key_encrypted', '')
        # Never silently transfer the saved credential to a different API address.
        if not config.get('key_encrypted'):
            raise HTTPException(409, '请填写 API Key；更换 API 地址时需要重新填写')
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory/'ai-settings.tmp'
        temporary.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        temporary.replace(directory/'ai-settings.json')
        return public(config)


def complete(config, system, data):
    from .usage import trace, mark_sent
    key = protect(base64.b64decode(config['key_encrypted']), decrypt=True).decode()
    try:
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            with client.stream('POST', config['base_url']+'/chat/completions',
                               extensions={'trace':trace},
                               headers={'Authorization':'Bearer '+key},
                               json={'model':config['model'], 'stream':False, 'max_tokens':4096,
                                     'response_format':{'type':'json_object'},
                                     'messages':[{'role':'system','content':system},
                                                 {'role':'user','content':json.dumps(data, ensure_ascii=False)}]}) as response:
                mark_sent()
                if response.status_code != 200:
                    raise HTTPException(502, f'模型接口返回 HTTP {response.status_code}，请检查地址、密钥、模型和余额')
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 256000:
                        raise ValueError('too large')
                payload = json.loads(raw)
                from .usage import settle
                settle(payload.get('usage'))
                choice = payload['choices'][0]
                if choice.get('finish_reason') != 'stop' or not isinstance(choice['message']['content'], str):
                    raise ValueError('incomplete')
                return choice['message']['content']
    except httpx.TimeoutException:
        raise HTTPException(504, '模型请求超时，未自动重试；可能已产生费用') from None
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, '模型连接失败或返回不完整；请检查兼容接口配置') from None


def extract_job(path, job_id, config, *, sender=None):
    request = issue_request(path, job_id, claim=True, provider='compatible')
    try:
        with connect(path) as db:
            if not db.execute("SELECT 1 FROM job WHERE id=? AND state='extracting' AND owner=? AND lease_until>?",(job_id,request['request_id'],time.time())).fetchone():
                raise HTTPException(409,'提炼任务已取消或租约失效，未发送请求')
        from .usage import invoke
        send=sender or (lambda c,s,d:invoke(path,c,s,d,kind='manual',sender=complete))
        response = send(config, request['system']+'\nJSON schema:\n'+json.dumps(request['response_schema']), request['data'])
        return commit_response(path, request['request_id'], response)
    except Exception as error:
        with connect(path) as db:
            limited=isinstance(error,HTTPException) and error.status_code==429
            db.execute("UPDATE job SET state=?,error=?,owner=NULL,lease_until=NULL WHERE id=? AND state='extracting' AND owner=?", ('prepared' if limited else 'failed','budget_limit' if limited else 'model_call_failed',job_id, request['request_id']))
            db.execute("UPDATE model_request SET state='failed' WHERE id=? AND state='issued'", (request['request_id'],))
        raise


def router(path, *, allowed=True):
    routes = APIRouter(prefix='/api/ai')

    def config_for_send():
        if not allowed:
            raise HTTPException(409, '合成演示模式不能调用外部模型')
        config = load(path.parent)
        if not config.get('enabled') or not config.get('key_encrypted'):
            raise HTTPException(409, '请先保存配置并允许手动调用模型')
        return config

    @routes.get('/settings')
    def settings():
        return public(load(path.parent))

    @routes.put('/settings')
    def update(body: Settings):
        return save(path.parent, body)

    @routes.delete('/settings')
    def delete():
        with LOCK:
            (path.parent/'ai-settings.json').unlink(missing_ok=True)
        return public(load(path.parent))

    @routes.post('/test')
    def test():
        from .usage import invoke
        result = invoke(path,config_for_send(), 'Return JSON only: {"ok":true}', {'test':'connection only, no chat data'},kind='connection_test',sender=complete)
        try:
            valid = json.loads(result) == {'ok':True}
        except ValueError:
            valid = False
        if not valid:
            raise HTTPException(502, '连接成功但 JSON 输出不符合测试要求')
        return {'ok':True, 'message':'连接和 JSON 输出测试通过；未发送聊天内容'}

    @routes.post('/extract/{job_id}')
    def extract(job_id: int):
        config = config_for_send()
        return extract_job(path, job_id, config)

    return routes
