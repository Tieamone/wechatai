"""Revalidate a local rejection against current evidence; no network or writes."""
import argparse
import json
from pathlib import Path
from fastapi import HTTPException
from backend import diagnostics
from backend.ai_settings import parse_response
from backend.model_contract import _commit_response


def revalidate(path,artifact, *, normalize_d304=False):
    artifact=artifact.resolve()
    if artifact.parent!=diagnostics.directory(path).resolve() or not artifact.name.startswith('rejection-'):
        raise ValueError('请选择本机 model-rejections 目录中的失败记录')
    record=json.loads(artifact.read_text(encoding='utf-8'))
    result={'original_code':record['code'],'lifecycle_checks':'not_replayed','evidence':'current_versions'}
    if record.get('raw') is None:return {**result,'valid':False,'code':'NO_RAW'}
    token=diagnostics.CURRENT.set({})
    try:
        raw=parse_response(record['raw']) if record['raw_kind']=='http_json' else record['raw']
        return {**result,**_commit_response(path,record['request_id'],raw,replay=True,normalize_d304=normalize_d304)}
    except HTTPException as error:
        data=diagnostics.details(error,diagnostics.CURRENT.get())
        return {**result,'valid':False,**data}
    finally:diagnostics.CURRENT.reset(token)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='离线只读重校验，不调用模型、不写入记忆。')
    parser.add_argument('artifact',type=Path)
    parser.add_argument('--normalize-d304',action='store_true',help='显式启用角色引用规整，默认关闭；只读报告')
    args=parser.parse_args()
    path=Path(__file__).resolve().parents[1]/'data/memory.sqlite3'
    print(json.dumps(revalidate(path,args.artifact,normalize_d304=args.normalize_d304),ensure_ascii=False,indent=2))
