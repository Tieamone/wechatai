"""One-shot bridge to the project-local MCP client. No chat content crosses it."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


class BotSender:
    def __init__(self, *, synthetic=False):
        self.synthetic = synthetic
        self.last_diagnostic = None

    def __call__(self, _title, _body):
        self.last_diagnostic = None
        try:
            # PowerShell/cmd are not involved; no arbitrary message arguments.
            result = subprocess.run(['node', str(ROOT/'tools/wechat_bot.mjs'),
                'notify-test' if self.synthetic else 'notify'], cwd=ROOT,
                capture_output=True, encoding='utf-8', timeout=55,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            for line in result.stderr.splitlines():
                if line.startswith('outcome uncertain '):
                    self.last_diagnostic = line[:160]
            state = json.loads(result.stdout)['state']
            return state if state in {'accepted', 'uncertain'} else 'uncertain'
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
            return 'uncertain'
