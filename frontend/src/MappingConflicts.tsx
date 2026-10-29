import {useState} from 'react';
type Props={busy:boolean;api:(path:string,method?:string,body?:unknown)=>Promise<any>;run:(action:()=>Promise<void>)=>Promise<void>};
export function MappingConflicts({busy,api,run}:Props){
  const [groups,setGroups]=useState<any[]|null>(null);
  const refresh=async()=>setGroups(await api('mapping-conflicts'));
  return <section><h2>旧映射冲突核对</h2><p>仅用于同一信息曾分配多个代号的情况。选择后续替换使用的代号，旧代号、旧值及证据仍保留。无法确认时保持阻断。</p>
    <button disabled={busy} onClick={()=>run(refresh)}>检查映射冲突</button>
    {groups?.length===0&&<p>未发现同类型规范化值冲突。</p>}
    {groups?.map(g=><Conflict key={`${g.kind}:${g.value}:${g.resolution?.selected_token||''}`} group={g} busy={busy} api={api} run={run} refresh={refresh}/>)}
  </section>;
}
function Conflict({group:g,busy,api,run,refresh}:Props&{group:any;refresh:()=>Promise<void>}){
  const [selected,setSelected]=useState(g.resolution?.selected_token||'');
  const [note,setNote]=useState('');
  const [confirmed,setConfirmed]=useState(false);
  return <article><h3>{g.kind}：{g.value}</h3><p>{g.resolution?'已选择后续替换代号':'尚未处理，相关预览保持阻断'}</p>
    <form onSubmit={e=>{e.preventDefault();run(async()=>{await api('mapping-conflicts','POST',{kind:g.kind,value:g.value,expected_tokens:g.mappings.map((m:any)=>m.token),selected_token:selected,note,confirmed});await refresh()})}}>
      <label>后续替换代号<select aria-label="后续替换代号" required value={selected} onChange={e=>setSelected(e.target.value)}><option value="">请选择</option>{g.mappings.map((m:any)=><option key={m.token} value={m.token}>{m.value} → {m.token}</option>)}</select></label>
      <input aria-label="映射核对依据" required maxLength={500} value={note} onChange={e=>setNote(e.target.value)} placeholder="填写核对依据"/>
      <label><input type="checkbox" required checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我已确认这些值代表同一项信息</label>
      <button disabled={busy||!confirmed}>保存后续替换选择</button>
    </form>
  </article>;
}
