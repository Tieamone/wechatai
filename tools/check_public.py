"""Portable tracked-file publication checks; never print matched secret values."""
from pathlib import PurePosixPath
import re
import subprocess


def main():
    paths=subprocess.check_output(['git','ls-files','-z']).decode().split('\0')
    patterns=[rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',rb'\bsk-[A-Za-z0-9_-]{24,}\b',rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b']
    bad=[]
    for name in filter(None,paths):
        path=PurePosixPath(name)
        if any(p in {'data','private','.venv','node_modules'} for p in path.parts) or path.suffix in {'.sqlite3','.db','.log'} or path.name.startswith('.env') and path.name!='.env.example':
            bad.append(name);continue
        with open(name,'rb') as file:raw=file.read()
        if any(re.search(pattern,raw) for pattern in patterns):bad.append(name)
    if bad:raise SystemExit('发布检查失败：'+', '.join(bad))
    print('PASS: tracked file and credential-pattern checks')


if __name__=='__main__':main()
