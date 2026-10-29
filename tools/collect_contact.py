"""Initial local history import and explicit background updates for the pinned account."""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import msvcrt
import os
from pathlib import Path
import time

from backend.app import create_app
from backend.collector import import_rows
from backend.runtime import current_session, session_alive
from tools.contact_reader import ContactReader
from tools.wechat_source import ROOT

DIRECTORY = ROOT/'data/collector'


def save(name, value):
    path = DIRECTORY/name
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def process_birth(pid):
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)]*4
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x1000,False,pid)
    if not handle:
        raise ValueError('Bound Weixin process is unavailable')
    try:
        stamps = [wintypes.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(handle, *(ctypes.byref(s) for s in stamps)):
            raise ValueError('Cannot verify process identity')
        # Exit time nonzero means the original process already terminated.
        if stamps[1].dwHighDateTime or stamps[1].dwLowDateTime:
            raise ValueError('Bound process exited')
        return (stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime
    finally:
        api.CloseHandle(handle)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--watch',action='store_true')
    parser.add_argument('--managed',action='store_true')
    args=parser.parse_args()
    DIRECTORY.mkdir(parents=True,exist_ok=True)
    with (DIRECTORY/'collector.lock').open('a+b') as lock:
        if lock.tell()==0:
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            print('Collector already running.',flush=True)
            return 1
        binding=json.loads((DIRECTORY/'binding.json').read_text(encoding='utf-8'))
        app=create_app(ROOT/'data')
        initialized=(DIRECTORY/'initialized.json').exists()
        status={'state':'starting','pid':os.getpid(),'updated_at':datetime.now(timezone.utc).isoformat()}
        save('status.json',status)
        try:
            if not args.watch:
                rows=json.loads((DIRECTORY/'initial.json').read_text(encoding='utf-8'))
                counts=import_rows(app,binding,rows,history=True,
                    progress=lambda done,total:print(f'History import {done}/{total}',flush=True) if done%5000==0 or done==total else None)
                save('initialized.json',{'at':datetime.now(timezone.utc).isoformat(),'rows':len(rows)})
                status.update(state='history_imported',rows=len(rows),counts=counts)
                print(json.dumps(status,ensure_ascii=False),flush=True)
                return 0
            if not initialized:
                raise ValueError('Run initial history import before watching')
            session=current_session(ROOT/'data')
            if not session:
                raise ValueError('Start the workbench before collecting')
            born=process_birth(binding['pid'])
            if binding.get('process_birth') != born:
                raise ValueError('Weixin was restarted; rebind and verify before collecting')
            reader=ContactReader(binding['account_dir'],binding['pid'])
            reader.unlock()
            if reader.resolve()!=binding['peer'] or reader.self_id!=binding['self_id']:
                raise ValueError('Account/contact binding changed')
            stop=DIRECTORY/'stop.request'
            if not args.managed:
                stop.unlink(missing_ok=True)
            failures=0
            while not stop.exists() and session_alive(ROOT/'data',session):
                if process_birth(binding['pid'])!=born:
                    raise ValueError('Bound Weixin process changed')
                try:
                    rows=reader.messages()
                    if stop.exists() or not session_alive(ROOT/'data',session):
                        break
                    counts=import_rows(app,binding,rows,history=False)
                    failures=0
                    status.update(state='watching',rows=len(rows),counts=counts)
                    if counts['inserted'] or counts['updated']:
                        print(json.dumps(counts),flush=True)
                except (OSError,ValueError) as error:
                    failures+=1
                    status.update(state='retrying',error=type(error).__name__)
                    if failures>=3:
                        raise
                status['updated_at']=datetime.now(timezone.utc).isoformat()
                save('status.json',status)
                for _ in range(15):
                    if stop.exists() or not session_alive(ROOT/'data',session): break
                    time.sleep(1)
            status['state']='stopped'
        except Exception as error:
            status.update(state='stopped_error',error=type(error).__name__)
            # No traceback/source text/keys in operational logs.
            print('Collector stopped: '+type(error).__name__+'. Inspect binding/source; no automatic account fallback.',flush=True)
            return 1
        finally:
            status['updated_at']=datetime.now(timezone.utc).isoformat()
            save('status.json',status)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
