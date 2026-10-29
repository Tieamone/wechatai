import re
import secrets
import json
from fastapi import HTTPException


TOKEN = re.compile(r'\[(?:PERSON|PHONE|EMAIL|ADDRESS|ID)_[a-f0-9]+\]')
SUSPICIOUS = re.compile(r'(?<!\d)1[3-9](?:[ -]*\d){9}(?!\d)|[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|\d{17}[\dXx]|身份证|住址|门牌|病历|诊断')
PHONE_VALUE = r'(?<![0-9A-Za-z+])\+?[0-9](?:[ -]*[0-9]){6,16}(?![0-9])'


def normalized_value(kind, value):
    if kind != 'PHONE':
        return value
    compact=re.sub(r'[ -]', '', value.strip())
    # Only an explicit, complete Chinese mobile number loses its country prefix.
    if re.fullmatch(r'(?:\+86|0086)1[3-9][0-9]{9}', compact):
        return compact[-11:]
    return compact if re.fullmatch(r'\+?[0-9]{7,15}',compact) else value


def add_mapping(db, kind, value):
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    key=normalized_value(kind,value)
    matches=[dict(r) for r in db.execute('SELECT kind,value,token FROM entity_value WHERE kind=?',(kind,)) if normalized_value(kind,r['value'])==key]
    if len(matches)>1:
        group=json.dumps(sorted(r['token'] for r in matches))
        choice=db.execute('SELECT selected_token FROM mapping_resolution WHERE kind=? AND normalized_value=? AND token_group=?',(kind,key,group)).fetchone()
        if choice:
            return {'kind':kind,'token':choice[0]}
        raise HTTPException(409,'同一规范化值已有多个旧代号，请在映射冲突中核对；不会自动改写历史映射')
    if matches:
        return {k:matches[0][k] for k in ('kind','token')}
    db.execute('INSERT OR IGNORE INTO entity_value(kind,value,token) VALUES(?,?,?)',
               (kind, value, f'[{kind}_{secrets.token_hex(8)}]'))
    return dict(db.execute('SELECT kind,token FROM entity_value WHERE kind=? AND value=?',
                           (kind, value)).fetchone())


def preview(db, text):
    if TOKEN.search(text):
        return {'status': 'blocked', 'reason': '原文包含代号样式，请先人工核对', 'text': None, 'allowed_tokens': [], 'external_enabled': False}
    mappings = db.execute('SELECT kind,value,token FROM entity_value ORDER BY length(value) DESC').fetchall()
    lookup,phones={},{}
    for row in mappings:
        lookup.setdefault(row['value'],set()).add(row['token'])
        if row['kind']=='PHONE':
            phones.setdefault(normalized_value('PHONE',row['value']),set()).add(row['token'])
    ambiguous=False
    resolutions={r['token_group']:r['selected_token'] for r in db.execute('SELECT token_group,selected_token FROM mapping_resolution')}
    def replace(match):
        nonlocal ambiguous
        value=match[0]
        candidates=lookup.get(value,set()) | phones.get(normalized_value('PHONE',value),set())
        if len(candidates)>1:
            chosen=resolutions.get(json.dumps(sorted(candidates)))
            if chosen in candidates:
                return chosen
            ambiguous=True
            return value
        return next(iter(candidates)) if candidates else value
    # One substitution pass prevents replacements from being processed again.
    phone_literals={r['value'] for r in mappings if r['kind']=='PHONE'}
    literals=[r'(?<![0-9A-Za-z+])'+re.escape(x)+r'(?![0-9])' if x in phone_literals else re.escape(x) for x in lookup]
    pattern='|'.join([PHONE_VALUE]+literals)
    replaced = re.sub(pattern, replace, text)
    if ambiguous:
        return {'status':'blocked','reason':'映射存在多个候选代号，请人工核对','text':None,'allowed_tokens':[],'external_enabled':False}
    remainder = TOKEN.sub('', replaced)
    blocked = bool(SUSPICIOUS.search(remainder))
    return {'status': 'blocked' if blocked else 'preview_only',
            'reason': '存在未处理的敏感疑点' if blocked else '本次仅本地预览，不会发送；自由文本检测不完备，模型调用由模型配置与任务开关控制',
            'text': replaced, 'allowed_tokens': TOKEN.findall(replaced), 'external_enabled': False}
