"""Exercise only synthetic calibration labels; no application server or real DB."""
import os,subprocess,tempfile
from pathlib import Path
from backend.store import initialize,connect
from .calibration_page import generate


def main():
    root=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='synthetic-calibration-') as directory:
        path=Path(directory)/'memory.sqlite3';initialize(path)
        with connect(path) as db:
            db.execute("INSERT INTO conversation VALUES('synthetic','synthetic','synthetic')")
            db.execute("INSERT INTO message(conversation_id,adapter,source_id,partition,sender_role,text,type,revision) VALUES('synthetic','fixture','1','fixture','peer','合成原文 </script><img src=x onerror=alert(1)>','text',1)")
            for i in range(3):
                mid=db.execute("INSERT INTO memory(summary,subject,polarity,assertion,temporal_kind,fingerprint) VALUES(?,'peer','positive','direct','fact',?)",('合成候选 '+str(i),'synthetic-'+str(i))).lastrowid
                db.execute('INSERT INTO memory_evidence VALUES(?,1,1,0,40)',(mid,))
        page=Path(directory)/'synthetic.html';generate(path,page)
        env={**os.environ,'PLAYWRIGHT_BROWSERS_PATH':str(root/'tools/browsers')}
        return subprocess.run(['node','e2e/calibration.mjs',str(page)],cwd=root/'frontend',env=env,timeout=90).returncode


if __name__=='__main__':raise SystemExit(main())
