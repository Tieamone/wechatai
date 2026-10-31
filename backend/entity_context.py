"""Evidence-backed mention candidates; never an automatic identity decision."""
import re

from .privacy import PHONE_VALUE, normalized_value


def prepare_mentions(db, cutoff):
    properties=db.execute("SELECT r.*,p.token FROM person_property r JOIN person p ON p.id=r.person_id WHERE r.status='active' ORDER BY r.id").fetchall()
    if not properties:return []
    evidence_by_property={}
    for row in db.execute("SELECT e.*,m.text,m.revision,m.event_utc FROM property_evidence e JOIN message m ON m.id=e.message_id JOIN person_property p ON p.id=e.property_id WHERE p.status='active' ORDER BY m.id,e.span_start"):
        evidence_by_property.setdefault(row['property_id'],[]).append(row)
    eligible=[]
    for prop in properties:
        evidence=evidence_by_property.get(prop['id'],[])
        if not evidence or not cutoff or any(r['revision']!=r['message_revision'] or not r['event_utc'] or r['event_utc']>=cutoff for r in evidence):
            continue
        eligible.append((prop,evidence))
    return eligible


def mention_candidates(db, sources, cutoff, *, prepared=None):
    eligible=prepare_mentions(db,cutoff) if prepared is None else prepared
    result=[]
    for index,(text,instant) in enumerate(sources):
        phones={normalized_value('PHONE',m[0]) for m in re.finditer(PHONE_VALUE,text)}
        hits=[]
        for prop,evidence in eligible:
            if instant and ((prop['valid_from'] and instant<prop['valid_from']) or (prop['valid_to'] and instant>=prop['valid_to'])):
                continue
            key=normalized_value('PHONE',prop['value']) if prop['kind']=='phone' else prop['value']
            if key in phones if prop['kind']=='phone' else key in text:
                hits.append((prop,evidence,key))
        for prop,evidence,key in hits:
            ambiguous=len({p['person_id'] for p,_,k in hits if k==key and p['kind']==prop['kind']})>1
            result.append({'source_index':index,'person_token':prop['token'],'property_id':prop['id'],
                           'mention':prop['value'],'ambiguous':ambiguous,
                           'time_uncertain':not instant or not prop['valid_from'] or not prop['valid_to'],
                           'evidence':[r['text'][r['span_start']:r['span_end']] for r in evidence]})
    return result
