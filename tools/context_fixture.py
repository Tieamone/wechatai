"""A08 preset responses: exercises wiring, not model comprehension."""
import json

TEXTS=['合成上下文：以前每天喝咖啡。','但现在已经不喝了，会睡不着。',
       '合成上下文：特别喜欢骑行，每周都去。','刚才说的是我同事，不是我本人。']
EXPECTED=[
 {'summary':'现在不喝咖啡，因为会睡不着','subject':'peer','polarity':'negative','assertion':'direct','temporal_kind':'preference','source_refs':['s1','s2']},
 {'summary':'据对方转述，其同事喜欢每周骑行','subject':'third_party','polarity':'positive','assertion':'reported','temporal_kind':'preference','source_refs':['s3','s4']},
]


def history():
    messages=[{'conversation_id':'demo','source':{'adapter':'manual-json','partition':'context','id':str(i)},
               'sender_role':'peer','event_time':f'2026-10-01T10:00:0{i}+08:00','text':text} for i,text in enumerate(TEXTS)]
    messages.append({**messages[-1],'source':{'adapter':'manual-json','partition':'context','id':'image'},'type':'image','text':''})
    return {'account_ref':'synthetic-account','contact_ref':'synthetic-peer','mode':'history','messages':messages}


def fixture_provider(request):
    wanted=[{'source_ref':f's{i+1}','role':'peer','text':text} for i,text in enumerate(TEXTS)]
    if request['data']['messages']!=wanted:
        raise ValueError('This fixture requires the exact complete bundled context')
    return json.dumps({'memories':EXPECTED},ensure_ascii=False)
