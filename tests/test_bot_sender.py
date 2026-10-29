import json
import subprocess
from types import SimpleNamespace

import pytest

from backend.bot_sender import BotSender
from backend.notifications import deliver
from backend.store import connect
from test_core import client
from test_notifications import reminder
from tools.bot_dispatch import dispatch_once


def test_bridge_does_not_pass_private_content(monkeypatch):
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        assert kwargs['capture_output'] and kwargs['timeout'] == 55
        return SimpleNamespace(stdout=json.dumps({'state':'accepted'}), stderr='')
    monkeypatch.setattr(subprocess, 'run', run)
    assert BotSender()('private name', 'private evidence') == 'accepted'
    assert commands[0][-1] == 'notify'
    assert 'private' not in str(commands)


@pytest.mark.parametrize('output', ['', '{}', 'not-json', '{"state":"delivered"}'])
def test_bad_bridge_output_never_claims_acceptance(monkeypatch, output):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=output, stderr='secret'))
    sender = BotSender()
    assert sender('', '') == 'uncertain'
    assert sender.last_diagnostic is None


def test_process_timeout_does_not_repeat(client, monkeypatch):
    path = reminder(client)
    calls = []
    def timeout(*args, **kwargs):
        calls.append(1)
        raise subprocess.TimeoutExpired('node', 55)
    monkeypatch.setattr(subprocess, 'run', timeout)
    assert deliver(path, 1, 'wechat-bot', BotSender()) == 'uncertain'
    assert dispatch_once(path, BotSender()) is None
    assert len(calls) == 1


def test_dispatcher_new_instance_does_not_resend(client, monkeypatch):
    path = reminder(client)
    calls = []
    def run(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(stdout='{"state":"accepted"}', stderr='')
    monkeypatch.setattr(subprocess, 'run', run)
    assert dispatch_once(path, BotSender(synthetic=True)) == 'accepted'
    assert dispatch_once(path, BotSender(synthetic=True)) is None
    with connect(path) as db:
        assert db.execute('SELECT attempts FROM notification_attempt').fetchone()[0] == 1
    assert len(calls) == 1
