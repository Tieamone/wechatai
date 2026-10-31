"""Check commit email formats without echoing names or email values into logs."""
import argparse
import re
import subprocess

ALLOWED=re.compile(r'(?:[A-Za-z0-9+._-]+@users\.noreply\.github\.com|noreply@github\.com)',re.I)
SHA=re.compile(r'[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?')


def git(*args):return subprocess.check_output(['git',*args])


def inspect(base=None,*,all_history=False):
    if all_history:commits=git('rev-list','--all').decode().splitlines()
    else:
        if base and set(base)!={'0'}:
            if not SHA.fullmatch(base):raise ValueError('base 必须是提交 SHA')
        else:base=git('merge-base','HEAD','refs/remotes/origin/main').decode().strip()
        commits=git('rev-list',base+'..HEAD').decode().splitlines()
    failures=[]
    for commit in commits:
        values=git('show','-s','--format=%ae%x00%ce%x00',commit).decode('utf-8',errors='replace').split('\0')
        for role,email in zip(('author','committer'),values[:2]):
            if not ALLOWED.fullmatch(email):failures.append((commit,role))
    return failures,len(commits)


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--base');parser.add_argument('--all',action='store_true');parser.add_argument('--warn-only',action='store_true');args=parser.parse_args(argv)
    failures,count=inspect(args.base,all_history=args.all)
    level='warning' if args.warn_only else 'error'
    for sha,role in failures:
        print(f'::{level} title=Commit email metadata::{sha[:12]} {role}: email must use an approved noreply domain. Values are redacted.')
    print(f'Email metadata: {count} commits checked; {len(failures)} violations.')
    return int(bool(failures) and not args.warn_only)


if __name__=='__main__':raise SystemExit(main())
