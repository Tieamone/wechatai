"""Dimension agreement on explicitly supplied labels, never an automatic decision."""
import argparse,json
from collections import Counter
from pathlib import Path


def index(rows):
    result={}
    for row in rows:
        key=(row['memory_id'],row['memory_version'])
        if any(type(v) is not int or v<1 for v in key):raise ValueError('ID 和版本必须是正整数')
        if key in result:raise ValueError('重复 ID/版本，不能静默覆盖')
        result[key]=row
    return result


def evaluate(human,reviewer):
    if not human.get('snapshot_sha256') or human['snapshot_sha256']!=reviewer.get('snapshot_sha256'):raise ValueError('证据快照不匹配，不能评估')
    humans=index(human['labels']);reviews=index(reviewer['reviews']);dimensions={}
    categories={'long':'long','short':'short','trivial':'trivial'}
    valid={'evidence_support':{'supported','partial','unsupported'},'category':set(categories.values()),
           'validity_bin':{'one_two_weeks','one_two_months','until_agreement','long_term','expired'},
           'sensitive_type':{'none','health','financial','privacy_token','health_financial','health_privacy_token','financial_privacy_token','health_financial_privacy_token'}}
    for dimension,allowed in valid.items():
        count=matched=0;confusion=Counter()
        for key,user in humans.items():
            actual=categories.get(user.get('conclusion')) if dimension=='category' else user.get(dimension)
            predicted=reviews.get(key,{}).get(dimension)
            if not isinstance(actual,str) or not isinstance(predicted,str) or actual not in allowed or predicted not in allowed:continue
            count+=1;matched+=actual==predicted;confusion[f'{actual} -> {predicted}']+=1
        dimensions[dimension]={'comparable':count,'matched':matched,'agreement':matched/count if count else None,
                               'not_comparable':len(humans)-count,'confusion':dict(confusion)}
    return {'human_rows':len(humans),'matched_id_versions':len(set(humans)&set(reviews)),'dimensions':dimensions,
            'automatic_decision_allowed':False,'warning':'小样本仅用于校准；缺失、不确定、版本不匹配不计一致率，不把价值判断推断为证据支持。'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('reviewer',type=Path);p.add_argument('human',type=Path);a=p.parse_args()
    print(json.dumps(evaluate(json.loads(a.human.read_text(encoding='utf-8')),json.loads(a.reviewer.read_text(encoding='utf-8'))),ensure_ascii=False,indent=2))
