"""Isolated visual demo with exact fixture responses, not a language model."""
import json
import argparse
from pathlib import Path
import tempfile

import uvicorn
from fastapi.testclient import TestClient

from backend.app import create_app

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = {
    '合成示例：最近晚上不喝咖啡了，会睡不着。': {
        'summary':'晚上不喝咖啡，避免睡不着', 'subject':'peer', 'polarity':'negative',
        'assertion':'direct', 'temporal_kind':'preference'},
    '合成示例：那下次一起喝茶。': None,
    '合成示例：周末想去公园散步。': {
        'summary':'计划周末去公园散步', 'subject':'peer', 'polarity':'positive',
        'assertion':'direct', 'temporal_kind':'plan'},
}


def fixture_provider(request):
    facts = []
    for message in request['data']['messages']:
        if message['text'] not in FIXTURES:
            raise ValueError('This demo supports only the bundled history fixture')
        fact = FIXTURES[message['text']]
        if fact:
            facts.append({**fact, 'source_refs':[message['source_ref']]})
    return json.dumps({'memories':facts}, ensure_ascii=False)


def seed(path,scenario='basic'):
    app = create_app(path, 'seed-only')
    with TestClient(app, base_url='http://127.0.0.1:8765', headers={'Authorization':'Bearer seed-only'}) as client:
        history = json.loads((ROOT/'samples'/'history.json').read_text(encoding='utf-8'))
        if scenario=='context':
            from tools.context_fixture import history as context_history
            history=context_history()
        response = client.post('/api/import', json=history)
        response.raise_for_status()
        response = client.post('/api/initializations', json={'mode':'full'})
        response.raise_for_status()


def fixture_judge(payload):
    return json.dumps({'confirmed':payload['current']=='合成示例：今天要不要去咖啡店？'
                      and payload['memory']=='晚上不喝咖啡，避免睡不着'
                      and len(payload['recent'])==1})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--scenario',choices=['basic','context'],default='basic')
    args=parser.parse_args()
    provider=fixture_provider
    if args.scenario=='context':
        from tools.context_fixture import fixture_provider as provider
    with tempfile.TemporaryDirectory(prefix='wechat-synthetic-demo-') as directory:
        path = Path(directory)
        seed(path,args.scenario)
        app = create_app(path, enable_worker=True, synthetic_provider=provider, synthetic_judge=fixture_judge)
        print('\n合成流程演示：预设返回，不是真实 AI 推理；退出后演示数据清理。')
        print('地址：http://127.0.0.1:8765')
        print(f'本次会话令牌：{app.state.token}')
        uvicorn.run(app, host='127.0.0.1', port=8765, access_log=False)


if __name__ == '__main__':
    main()
