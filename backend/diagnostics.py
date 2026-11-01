"""Bounded local rejection artifacts. Never expose response text over HTTP."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import threading
import uuid
import sqlite3
from contextlib import closing
from fastapi import APIRouter, HTTPException

CURRENT=ContextVar('model_diagnostic',default=None)
LOCK=threading.RLock()
MAX_RAW=256*1024
MAX_TOTAL=5*1024*1024
CODES={
 '模型返回过大或类型错误':'D101', '模型返回结构不符合契约':'D110',
 '请求不存在':'D201','任务已暂停、取消或失效':'D202','执行租约已过期':'D203',
 '请求证据已过期':'D204','证据已被纠错停用':'D205','上下文含未处理隐私疑点':'D206',
 '上下文超过字符上限，请选择更小范围':'D207','证据或映射变化，拒绝迟到返回':'D208',
 '同一请求不能提交不同结果':'D209','请求已被新请求替代':'D210',
 '模型摘要未使用中文，结果未保存；请重试此窗口':'D301',
 '返回引用不在本次允许证据集合中':'D302','返回包含未知或改写的代号':'D303',
 '本人陈述必须由对应角色的原文支持；转述请单独标记':'D304',
 '证据片段重复':'D305','证据必须指向本地文字消息':'D306',
 '证据版本已变化，请重新选择原文':'D307','证据引用范围无效':'D308',
 '该版本证据已被用户标错停用，不可重复提炼；可使用新消息或修订后的证据':'D309',
 '记忆已停用，不能自动复活':'D310',
}


def failure(status,detail,code,location=None):
    error=HTTPException(status,detail)
    error.diagnostic={'code':code,'location':location or []}
    return error


def observe(**values):
    current=CURRENT.get()
    if current is not None:current.update(values)


def directory(path):return path.parent/'model-rejections'


def files(path):return sorted(directory(path).glob('rejection-*.json'))


def save(path,request_id,error,values):
    detail=getattr(error,'detail',None)
    diagnostic=getattr(error,'diagnostic',{'code':CODES.get(detail,'D999') if isinstance(detail,str) else 'D999','location':values.get('location',[])})
    raw=values.get('raw')
    if isinstance(raw,str):raw=raw.encode('utf-8')
    size=len(raw) if isinstance(raw,bytes) else values.get('response_bytes')
    body={'id':uuid.uuid4().hex,'request_id':request_id,'at':datetime.now(timezone.utc).isoformat(),
          **diagnostic,'response_bytes':size,'completion_tokens':values.get('completion_tokens'),
          'memory_count':values.get('memory_count'),'raw_kind':values.get('raw_kind','extraction'),
          'raw':raw.decode('utf-8',errors='replace') if isinstance(raw,bytes) and len(raw)<=MAX_RAW else None}
    with LOCK:
        target=directory(path);target.mkdir(exist_ok=True)
        name=target/('rejection-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+body['id']+'.json')
        name.write_text(json.dumps(body,ensure_ascii=False),encoding='utf-8')
        records=files(path);total=sum(p.stat().st_size for p in records)
        while records and (len(records)>20 or total>MAX_TOTAL):
            old=records.pop(0);total-=old.stat().st_size;old.unlink()


@contextmanager
def capture(path,request_id):
    if CURRENT.get() is not None:
        yield
        return
    values={};token=CURRENT.set(values)
    try:yield
    except Exception as error:
        # Admission failures are not rejected model responses.
        if not isinstance(error,HTTPException) or error.status_code!=429:
            try:save(path,request_id,error,values)
            except OSError:pass
        raise
    finally:CURRENT.reset(token)


def listing(path):
    with LOCK:
        rows=[]
        for file in reversed(files(path)):
            try:
                item=json.loads(file.read_text(encoding='utf-8'))
                rows.append({k:v for k,v in item.items() if k!='raw'})
                rows[-1]['has_raw']=item.get('raw') is not None
            except (OSError,ValueError):continue
        return {'records':rows,'count_limit':20,'raw_byte_limit':MAX_RAW,'total_byte_limit':MAX_TOTAL}


@contextmanager
def connection(path,readonly):
    from .store import connect
    if not readonly:
        with connect(path) as db:yield db
    else:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            yield db


def router(path):
    routes=APIRouter(prefix='/api/model-diagnostics')
    @routes.get('')
    def status():return listing(path)
    @routes.delete('')
    def clear():
        with LOCK:
            for file in files(path):file.unlink()
        return {'ok':True}
    return routes
