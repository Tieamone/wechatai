"""Two real Python processes per mode; isolated synthetic databases only."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.extraction import extract_next
from backend.jobs import prepare_one
from backend.store import connect

TEXTS=[f'合成项目编号{i:02d}已登记' for i in range(16)]


def batch(indices):
    return {'account_ref':'restart-fixture','contact_ref':'restart-peer','mode':'history',
            'messages':[{'conversation_id':'restart','source':{'adapter':'fixture','partition':'one','id':str(i)},
                         'sender_role':'peer','event_time':'2026-01-01T00:00:00Z','text':TEXTS[i]} for i in indices]}


def provider(request):
    rows=request['data']['messages']
    assert all(r['text'] in TEXTS for r in rows)
    return json.dumps({'memories':[{'summary':r['text'],'subject':'peer','polarity':'positive',
                                  'assertion':'direct','temporal_kind':'fact','source_refs':[r['source_ref']]} for r in rows]})


def phase(directory,mode,action):
    app=create_app(directory,'restart-test-only')
    path=app.state.db_path
    scope=15 if mode=='full' else 13
    with TestClient(app,base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer restart-test-only'}) as client:
        if action=='seed':
            assert client.post('/api/import',json=batch(range(15))).status_code==200
            body={'mode':mode}
            if mode=='batch':
                body['message_ids']=list(range(1,14))
            started=client.post('/api/initializations',json=body)
            assert started.status_code==200 and started.json()['message_count']==scope
            assert prepare_one(path)['state']=='prepared'
            assert extract_next(path,provider)['state']=='validated_synthetic'
            assert client.post('/api/initializations/1/control',json={'action':'pause'}).status_code==200
            assert not prepare_one(path)['processed']
            assert not extract_next(path,provider)['processed']
            assert client.post('/api/import',json=batch([15])).status_code==200
            info=client.get('/api/initializations').json()[0]
            assert info['counts']=={'pending':1,'validated_synthetic':1}
            assert info['message_coverage']['validated_synthetic']==10
            with connect(path) as db:
                assert db.execute('SELECT count(*) FROM memory').fetchone()[0]==12
            return {'phase':action,'pid':os.getpid(),'mode':mode,'scope':scope,'cutoff':info['cutoff']}
        info=client.get('/api/initializations').json()[0]
        assert info['state']=='paused'
        assert info['counts']=={'pending':1,'validated_synthetic':1}
        assert not prepare_one(path)['processed']
        assert client.post('/api/initializations/1/control',json={'action':'resume'}).status_code==200
        assert prepare_one(path)['state']=='prepared'
        assert extract_next(path,provider)['state']=='validated_synthetic'
        assert not prepare_one(path)['processed']
        assert not extract_next(path,provider)['processed']
        info=client.get('/api/initializations').json()[0]
        assert info['message_coverage']['scope']==scope
        assert info['message_coverage']['validated_synthetic']==scope
        assert not info['extraction_complete']
        assert client.get('/api/reminders').json()==[]
        with connect(path) as db:
            assert db.execute('SELECT count(*) FROM memory').fetchone()[0]==scope
            assert db.execute('SELECT count(*) FROM model_request').fetchone()[0]==2
            assert not db.execute('SELECT 1 FROM memory_evidence WHERE message_id=16').fetchone()
        return {'phase':action,'pid':os.getpid(),'mode':mode,'scope':scope,'cutoff':info['cutoff'],
                'unique_memories':scope,'synthetic_covered':scope,'real_extraction_complete':False}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--phase',choices=['seed','resume'])
    parser.add_argument('--mode',choices=['full','batch'])
    parser.add_argument('--directory',type=Path)
    args=parser.parse_args()
    if args.phase:
        if not args.directory or not args.mode:
            parser.error('child phase requires directory and mode')
        print(json.dumps(phase(args.directory,args.mode,args.phase)))
        return
    reports=[]
    for mode in ('full','batch'):
        with tempfile.TemporaryDirectory(prefix='wechat-restart-') as directory:
            phases=[]
            for action in ('seed','resume'):
                completed=subprocess.run([sys.executable,'-m','tools.initialization_restart_check',
                    '--phase',action,'--mode',mode,'--directory',directory],cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,text=True,timeout=45)
                if completed.returncode:
                    raise RuntimeError(completed.stderr or completed.stdout)
                phases.append(json.loads(completed.stdout))
            assert phases[0]['pid']!=phases[1]['pid']
            assert phases[0]['cutoff']==phases[1]['cutoff']
            reports.append({'mode':mode,'separate_processes':True,'fixed_cutoff':True,**phases[1]})
    print(json.dumps({'result':'PASS','cases':reports},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
