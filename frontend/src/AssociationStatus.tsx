import {useState} from 'react';
const states:Record<string,string>={confirmed_model:'模型判断通过',pending:'等待判断',judging:'判断中',confirmed_synthetic:'合成判断通过',rejected:'判断未通过',failed:'判断失败',blocked_privacy:'隐私阻断',stale:'已失效',dismissed:'已标错'};
const reasons:Record<string,string>={candidate_limit:'已超过本窗口的 3 条候选上限',disabled:'自动处理已暂停',model_timeout:'判断超时，未生成提醒',judgment_invalid:'返回不符合判断格式',lease_expired:'处理租约过期',blocked_privacy:'存在未处理的隐私疑点',stale_evidence:'引用原文或记忆版本已变化',stale_result:'返回时证据或租约已失效',evidence_or_policy_changed:'证据、人物关系或隐私策略已变化',newer_context:'收到后续消息，旧判断已失效',context_character_limit:'上下文超过字符上限',entity_context_limit:'人物提及超过上限'};
type Props={busy:boolean;api:(path:string)=>Promise<any>;run:(action:()=>Promise<void>)=>Promise<void>};
export function AssociationStatus({busy,api,run}:Props){
  const [data,setData]=useState<any>(null);
  return <details><summary>查看普通关联处理状态</summary>
    <p>这里显示候选处理记录，不等于提醒。启用新消息自动处理后，模型只判断新窗口的有限候选；旧候选不会自动补跑。</p>
    <button className="secondary" disabled={busy} onClick={()=>run(async()=>setData(await api('ordinary-reminders/diagnostics')))}>刷新处理状态</button>
    {data&&<div><p>{Object.entries(data.counts).map(([state,count])=>`${states[state]||state}：${count}`).join(' / ')||'暂无处理记录'}</p>
      {data.truncated&&<p>下方仅列最近 100 条，汇总包含全部记录。</p>}
      <ul>{data.items.map((r:any)=><li key={r.id}>记录 #{r.id} · 消息 #{r.trigger_id} · 记忆 #{r.memory_id}：{states[r.state]||r.state}{r.error?`；${reasons[r.error]||r.error}`:''}</li>)}</ul>
      <p>状态为本次刷新时的快照。未通过、阻断和失败记录不会作为普通提醒展示；本地原文搜索仍可使用。</p>
    </div>}
  </details>;
}
