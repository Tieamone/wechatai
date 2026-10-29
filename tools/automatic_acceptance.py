"""Real configured model, synthetic conversation, fake notification sender only."""
import json
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.ai_settings import load
from backend.automatic import process_next
from backend.jobs import prepare_one
from backend.store import connect
from backend.notifications import pending_associations, deliver_association

ROOT=Path(__file__).resolve().parents[1]


def main():
    config=load(ROOT/'data')
    if not config.get('enabled') or not config.get('key_encrypted'):
        raise SystemExit('No enabled model configuration')
    with tempfile.TemporaryDirectory(prefix='automatic-acceptance-',dir=ROOT/'data') as folder:
        directory=Path(folder)
        (directory/'ai-settings.json').write_text(json.dumps(config),encoding='utf-8')
        app=create_app(directory,'isolated-acceptance')
        with TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer isolated-acceptance'}) as api:
            body={'account_ref':'synthetic','contact_ref':'synthetic','mode':'history','messages':[
                {'conversation_id':'synthetic','source':{'adapter':'manual-json','id':'1','partition':'test'},
                 'sender_role':'peer','event_time':'2026-01-01T12:00:00+08:00','text':'我对花生过敏，不能吃花生。','revision':1}]}
            api.post('/api/import',json=body).raise_for_status()
            api.post('/api/memories',json={'summary':'对方对花生过敏，不能食用花生','subject':'peer','polarity':'negative',
                'assertion':'direct','temporal_kind':'fact','evidence':[{'message_id':1,'message_revision':1,'span_start':0,'span_end':len(body['messages'][0]['text'])}]}).raise_for_status()
            api.put('/api/automatic',json={'enabled':True,'daily_limit':4}).raise_for_status()
            body['mode']='live'
            body['messages'][0].update(source={'adapter':'manual-json','id':'2','partition':'test'},event_time='2026-01-02T12:00:00+08:00',text='我刚买的点心配料有花生，我还没吃。')
            api.post('/api/import',json=body).raise_for_status()
            prepare_one(app.state.db_path)
            with connect(app.state.db_path) as db:
                db.execute('UPDATE auto_message SET ready_at=0')
            result=process_next(app.state.db_path)
            ids=pending_associations(app.state.db_path,'acceptance-fake')
            sends=[]
            def sender(title,body):
                sends.append((title,body))
                return 'accepted'
            if ids:
                deliver_association(app.state.db_path,ids[0],'acceptance-fake',sender)
                deliver_association(app.state.db_path,ids[0],'acceptance-fake',sender)
            status=api.get('/api/automatic').json()
            report={'processing_state':result.get('state'),'calls':status['used'],
                    'confirmed_associations':len(ids),'fake_sends':len(sends),
                    'real_phone_sends':0,'used_real_chat':False}
            (ROOT/'data/automatic-acceptance-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report),flush=True)
            if result.get('state')!='done' or not ids or len(sends)!=1:
                raise SystemExit(1)


if __name__=='__main__':
    main()
