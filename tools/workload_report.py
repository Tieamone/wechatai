"""Read-only workload and current behavior report, without conversation text."""
import json
from pathlib import Path
from backend import diagnostics


def report(path):
    with diagnostics.connection(path,True) as db:
        db.execute('BEGIN')
        rows=db.execute("""SELECT j.state,count(*) FROM job j JOIN initialization i ON i.id=j.initialization_id
            WHERE i.mode!='auto' AND i.state IN ('active','paused') GROUP BY j.state""").fetchall()
        states=dict(rows)
        remaining=sum(count for state,count in states.items() if state not in ('validated_model','cancelled'))
        total=remaining*2600
        confirmed=db.execute("SELECT count(*) FROM memory m JOIN memory_review r ON r.memory_id=m.id AND r.confirmed_version=m.version WHERE m.status='active'").fetchone()[0]
        active=db.execute("SELECT count(*) FROM memory WHERE status='active'").fetchone()[0]
        temporal=dict(db.execute('SELECT temporal_kind,count(*) FROM memory GROUP BY temporal_kind'))
        unassigned=[r[0] for r in db.execute("""SELECT count(*) FROM message m WHERE m.type='text' AND m.event_utc IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM job_message x JOIN job j ON j.id=x.job_id JOIN initialization i ON i.id=j.initialization_id
            WHERE x.message_id=m.id AND x.revision=m.revision AND ((i.mode!='auto' AND i.state IN ('active','paused')) OR j.state='validated_model'))
            GROUP BY m.conversation_id""")]
        return {'schema_version':db.execute('PRAGMA user_version').fetchone()[0],
            'remaining_history_windows':remaining,'history_states':states,'tokens_per_window_assumption':2600,'estimated_total_tokens':total,
            'known_text_not_yet_assigned':sum(unassigned),'additional_windows_estimate':sum((n+9)//10 for n in unassigned),
            'planning_total_tokens_including_unassigned':(remaining+sum((n+9)//10 for n in unassigned))*2600,
            'public_price_cny_no_cache_no_discounts':{'busy_input_only_to_output_only':[round(total*2/1000000,4),round(total*8/1000000,4)],
                'idle_input_only_to_output_only':[round(total/1000000,4),round(total*4/1000000,4)]},
            'one_million_tokens_windows':1000000//2600,'ninety_percent_one_million_windows':900000//2600,
            'active_memories':active,'human_confirmed_memories':confirmed,'pending_memories':active-confirmed,'temporal_kind_counts':temporal,
            'definition':'active/paused historical ranges; exclude validated_model/cancelled, include failed/privacy/synthetic for eventual real extraction; no auto ranges',
            'price_source':'https://help.aliyun.com/zh/model-studio/model-pricing','price_verified_date':'2026-11-03',
            'notes':['按公开原价估算，未核对账单；区间仅由输入/输出比例变化产生，不包含缓存、折扣或免费额度',
                     '2600 Token 为 v2 单阶段近似；不包含未来精修审核、失败重试与提醒判断；不能推断真实免费额度可用',
                     '失败或隐私阻断窗口需要先解决阻断；不代表当前全部可直接启动调用']}


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1];result=report(root/'data/memory.sqlite3')
    (root/'private/read-only-workload-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
