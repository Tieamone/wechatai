import subprocess
import pytest
from tools.check_commit_emails import ALLOWED,inspect,main


@pytest.mark.parametrize('email,expected',[
    ('123+synthetic@users.noreply.github.com',True),('synthetic@users.noreply.github.com',True),
    ('noreply@github.com',True),('private@example.invalid',False),
    ('a@users.noreply.github.com.evil.invalid',False),('a@github.com',False),('',False)])
def test_allowed_email_format(email,expected):assert bool(ALLOWED.fullmatch(email))==expected


def test_history_alarm_and_new_commit_gate(tmp_path,monkeypatch,capsys):
    def git(*args):return subprocess.check_output(['git',*args],cwd=tmp_path).decode().strip()
    git('init');monkeypatch.chdir(tmp_path)
    git('-c','user.name=Synthetic','-c','user.email=private@example.invalid','commit','--allow-empty','-m','synthetic initial')
    base=git('rev-parse','HEAD')
    git('update-ref','refs/remotes/origin/main',base)
    git('-c','user.name=Synthetic','-c','user.email=synthetic@users.noreply.github.com','commit','--allow-empty','-m','synthetic safe')
    assert inspect(base)==([],1)
    assert main(['--all','--warn-only'])==0
    assert '::warning' in capsys.readouterr().out
    assert main(['--all'])==1
    assert 'private@example.invalid' not in capsys.readouterr().out
    git('-c','user.name=Synthetic','-c','user.email=synthetic@users.noreply.github.com','commit','--allow-empty','-m','synthetic committer')
    monkeypatch.setenv('GIT_COMMITTER_EMAIL','private@example.invalid')
    git('-c','user.name=Synthetic','-c','user.email=synthetic@users.noreply.github.com','commit','--amend','--no-edit','--allow-empty')
    failures,count=inspect(base)
    assert count==2 and len(failures)==1 and failures[0][1]=='committer'
