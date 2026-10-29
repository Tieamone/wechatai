import {useState} from 'react';
type Row=Record<string,any>;
type Props={selected:Row[];people:Row[];busy:boolean;api:(path:string,method?:string,body?:unknown)=>Promise<any>;run:(action:()=>Promise<void>)=>Promise<void>;refresh:()=>Promise<void>};
export function Entities({selected,people,busy,api,run,refresh}:Props){
  const [label,setLabel]=useState('');
  const [person,setPerson]=useState('');
  const [kind,setKind]=useState('alias');
  const [value,setValue]=useState('');
  const [note,setNote]=useState('');
  const [from,setFrom]=useState('');
  const [to,setTo]=useState('');
  const [confirmed,setConfirmed]=useState(false);
  return <section><h2>人物与历史号码</h2>
    <p>同名人物分别建立。别名关系由你确认，号码可关联多人。这里的别名暂不用于自动替换或模型推理。</p>
    <form onSubmit={e=>{e.preventDefault();run(async()=>{const p=await api('entities','POST',{label});setPerson(String(p.id));setLabel('');await refresh()})}}>
      <input aria-label="人物备注名称" required maxLength={200} value={label} onChange={e=>setLabel(e.target.value)} placeholder="例如：同事小王"/><button disabled={busy}>新建独立人物</button>
    </form>
    {people.length>0&&<form onSubmit={e=>{e.preventDefault();run(async()=>{
      await api(`entities/${person}/properties`,'POST',{kind,value,confirmation_note:note,confirmed,evidence:selected.map(m=>({message_id:m.id,message_revision:m.revision,span_start:0,span_end:Array.from(m.text as string).length})),valid_from:from?new Date(from).toISOString():null,valid_to:to?new Date(to).toISOString():null});
      setValue('');setNote('');setConfirmed(false);await refresh();
    })}}>
      <p>{selected.length?`将绑定已选的 ${selected.length} 条原文，请核对是否支持此关系。`:"未选择原文：保存为人工确认关系。可先在搜索结果中选择证据。"}</p>
      {selected.map(m=><blockquote key={m.id}>证据 #{m.id}：{m.text}</blockquote>)}
      <label>人物 <select aria-label="关系所属人物" required value={person} onChange={e=>setPerson(e.target.value)}><option value="">请选择</option>{people.map(p=><option key={p.id} value={p.id}>{p.label} · #{p.id}</option>)}</select></label>
      <label>关系 <select aria-label="人物属性类型" value={kind} onChange={e=>setKind(e.target.value)}><option value="alias">别名</option><option value="phone">号码</option></select></label>
      <input aria-label="人物属性值" required maxLength={200} value={value} onChange={e=>setValue(e.target.value)} placeholder="别名或完整号码"/>
      <label>开始时间（本机时区，可留空）<input aria-label="关系开始时间" type="datetime-local" value={from} onChange={e=>setFrom(e.target.value)}/></label>
      <label>结束时间（不含，可留空）<input aria-label="关系结束时间" type="datetime-local" value={to} onChange={e=>setTo(e.target.value)}/></label>
      <input aria-label="关系确认依据" required maxLength={1000} value={note} onChange={e=>setNote(e.target.value)} placeholder="写明你确认此关系的依据"/>
      <label><input type="checkbox" required checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我已确认此人物关系</label>
      <p className="muted">空白时间表示未知；新增关系不会自动结束旧关系。号码加入本地替换表；撤回关系后保留旧号码代号。</p>
      <button disabled={busy||!confirmed}>保存人物关系</button>
    </form>}
    {people.map(p=><article key={p.id}><h3>{p.label} · #{p.id}</h3>{p.properties.length===0?<p>尚无确认关系</p>:p.properties.map((r:Row)=><blockquote key={r.id}><p>{r.kind==='alias'?'别名':'号码'}：{r.value} · {r.status==='active'?'已确认':r.status==='stale'?'证据已修订，关系失效':'已撤回'}</p><small>{r.valid_from||'开始未知'} — {r.valid_to||'结束未知'}<br/>依据：{r.confirmation_note}</small>{r.evidence.length===0?<p>仅人工确认，未绑定原文</p>:r.evidence.map((e:Row)=><p key={`${e.message_id}-${e.span_start}`}>引用 #{e.message_id} · 版本 {e.message_revision}：{e.quote??'原文已修订，旧引用不可用'}</p>)}{r.status==='active'&&<button className="secondary" disabled={busy} onClick={()=>run(async()=>{await api(`entities/${p.id}/properties/${r.id}/retract`,'POST');await refresh()})}>撤回此关系</button>}</blockquote>)}</article>)}
  </section>;
}
