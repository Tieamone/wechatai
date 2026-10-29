import {AssociationStatus} from './AssociationStatus';
type Row=Record<string,any>;
export function OrdinaryReminders({items,busy,refresh,api,run}:{items:Row[],busy:boolean,refresh:()=>Promise<void>,api:(p:string,m?:string)=>Promise<any>,run:(f:()=>Promise<void>)=>Promise<void>}){
  return <section><h2>普通关联提醒 <button className="secondary" disabled={busy} onClick={()=>run(refresh)}>刷新普通关联</button></h2>
    <p>候选命中不直接展示。自动处理开启后，只有模型判断明确相关且有提醒价值的结果才展示；仍需结合原文核对。</p>
    <AssociationStatus busy={busy} api={api} run={run}/>
    {items.length===0?<p className="muted">暂无通过判断的普通关联。</p>:items.map(item=><article key={item.id}><small className="tag">{item.origin==='model'?'模型判断通过 · 请核对':'合成判断通过 · 普通关联'}</small><h3>{item.memory.summary}</h3><p>当前消息：{item.trigger.text}</p>{item.memory.evidence.map((e:Row)=><blockquote key={`${e.message_id}-${e.span_start}`}><p>{e.quote}</p><small>{e.event_time} · 证据 #{e.message_id}</small></blockquote>)}<button disabled={busy} className="secondary" onClick={()=>run(async()=>{await api(`ordinary-reminders/${item.id}/dismiss`,'POST');await refresh()})}>标错此关联</button></article>)}
  </section>;
}
