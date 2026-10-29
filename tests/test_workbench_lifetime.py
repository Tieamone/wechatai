import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
from backend.runtime import current_session, session_alive, workbench_session
from backend.store import initialize


def test_session_restart_does_not_adopt_old_workers(tmp_path):
    assert current_session(tmp_path) is None
    with workbench_session(tmp_path) as old:
        assert session_alive(tmp_path,old)
    assert not session_alive(tmp_path,old)
    with workbench_session(tmp_path) as new:
        assert new!=old and not session_alive(tmp_path,old)
        assert session_alive(tmp_path,new)


@pytest.mark.parametrize('abrupt',[False,True])
def test_real_runner_processes_exit_when_workbench_ends(tmp_path,abrupt):
    data=tmp_path/'data'
    initialize(data/'memory.sqlite3')
    collector=data/'collector';collector.mkdir()
    (collector/'binding.json').write_text(json.dumps({'pid':1,'process_birth':1,'account_dir':'synthetic','peer':'peer','self_id':'self'}))
    (collector/'initialized.json').write_text('{}')
    root=Path(__file__).resolve().parents[1]
    def spawn(code):
        return subprocess.Popen([sys.executable,'-u','-c',code,str(tmp_path)],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
    parent=spawn("import sys,time; from pathlib import Path; from backend.runtime import workbench_session; p=Path(sys.argv[1]);\nwith workbench_session(p/'data'):\n print('ready',flush=True)\n while not (p/'close').exists(): time.sleep(.05)")
    children=[]
    try:
        for _ in range(100):
            if current_session(data):break
            time.sleep(.05)
        assert current_session(data)
        children.append(spawn("import sys;from pathlib import Path;from tools import bot_dispatch as d;d.ROOT=Path(sys.argv[1]);sys.argv=['bot','--watch','--managed'];raise SystemExit(d.main())"))
        children.append(spawn("""import sys
from pathlib import Path
from tools import collect_contact as c
c.ROOT=Path(sys.argv[1]);c.DIRECTORY=c.ROOT/'data/collector'
c.process_birth=lambda _:1
class Reader:
 self_id='self'
 def __init__(self,*args):pass
 def unlock(self):pass
 def resolve(self):return 'peer'
 def messages(self):return []
c.ContactReader=Reader
c.import_rows=lambda *args,**kwargs:{'inserted':0,'updated':0,'duplicates':0,'source_missing':0}
sys.argv=['collector','--watch','--managed']
raise SystemExit(c.main())
"""))
        for _ in range(150):
            try:
                states=[json.loads((data/name/'status.json').read_text())['state'] for name in ('bot','collector')]
                if states==['running','watching']:break
            except (OSError,ValueError):pass
            time.sleep(.05)
        else:pytest.fail('Test runners failed to start')
        if abrupt:parent.kill()
        else:(tmp_path/'close').touch()
        parent.wait(5)
        for child in children:
            assert child.wait(6)==0
        assert [json.loads((data/name/'status.json').read_text())['state'] for name in ('bot','collector')]==['stopped','stopped']
    finally:
        for process in [parent,*children]:
            if process.poll() is None:process.kill()
            process.communicate(timeout=5)
