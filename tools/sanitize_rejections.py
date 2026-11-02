"""Plan local legacy-response sanitization; mutation requires an approved manifest hash."""
import argparse
import hashlib
import json
from pathlib import Path
from backend.ai_settings import retained_response


def digest(raw):return hashlib.sha256(raw).hexdigest()


def plan(directory):
    changes=[]
    for path in sorted(directory.glob('rejection-*.json')):
        if path.is_symlink() or not path.is_file():raise ValueError('拒绝处理链接或非普通文件')
        before=path.read_bytes();record=json.loads(before)
        if record.get('raw_kind')!='http_json' or not isinstance(record.get('raw'),str):continue
        safe=retained_response(record['raw'])
        if safe is None:raise ValueError('旧响应无法解析，保留原文件并停止')
        clean=safe.decode('utf-8')
        if clean==record['raw']:continue
        record['raw']=clean;record['retention_version']=2
        after=json.dumps(record,ensure_ascii=False).encode('utf-8')
        changes.append((path,after,{'file':path.name,'before_sha256':digest(before),'after_sha256':digest(after),
                                    'before_bytes':len(before),'after_bytes':len(after)}))
    return changes


def run(directory,*,approved=None):
    changes=plan(directory)
    manifest={'format':1,'count':len(changes),'files':[c[2] for c in changes]}
    fingerprint=digest(json.dumps(manifest,sort_keys=True).encode())
    if approved is not None:
        if approved!=fingerprint:raise ValueError('清单发生变化，未改写任何文件；请重新审阅 dry-run')
        # Recheck every input before the first mutation; do this with all writers stopped.
        for path,_,entry in changes:
            if digest(path.read_bytes())!=entry['before_sha256']:raise ValueError('文件发生变化，停止改写')
        for path,after,_ in changes:
            temporary=path.with_suffix('.sanitize.tmp');temporary.write_bytes(after);temporary.replace(path)
    return {**manifest,'manifest_sha256':fingerprint,'dry_run':approved is None}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=Path(__file__).resolve().parents[1]/'data/model-rejections')
    parser.add_argument('--apply-approved-manifest',help='仅在用户审阅并授权后传入 dry-run 的清单 SHA256')
    args=parser.parse_args()
    print(json.dumps(run(args.directory,approved=args.apply_approved_manifest),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
