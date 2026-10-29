import {useState} from 'react';

type Props = {
  memory: Record<string, any>;
  busy: boolean;
  api: (path:string, method?:string, body?:unknown)=>Promise<any>;
  run: (action:()=>Promise<void>)=>Promise<void>;
  refresh: ()=>Promise<void>;
};
const choices:Record<string,Record<string,string>>={
  subject:{peer:'对方',self:'我',third_party:'第三方',unknown:'未知'},
  polarity:{positive:'肯定',negative:'否定',uncertain:'不确定'},
  assertion:{direct:'本人陈述',reported:'转述',inferred:'推测'},
  temporal_kind:{preference:'偏好',plan:'计划',past_event:'已发生',fact:'事实',unknown:'未知'},
};
const labels:Record<string,string>={subject:'更正主体',polarity:'更正肯定或否定',assertion:'更正陈述来源',temporal_kind:'更正事项类型'};

export function MemoryRevision({memory,busy,api,run,refresh}:Props){
  const [editing,setEditing]=useState(false);
  const [draft,setDraft]=useState<Record<string,string>>(()=>Object.fromEntries(['summary',...Object.keys(choices)].map(k=>[k,memory[k]])));
  const [history,setHistory]=useState<Record<string,any>[]>([]);
  return <div>
    <button className="secondary" disabled={busy} onClick={()=>setEditing(!editing)}>{editing?'取消更正':'更正内容'}</button>
    {editing&&<form onSubmit={e=>{e.preventDefault();run(async()=>{
      await api(`memories/${memory.id}/replace`,'POST',{...draft,expected_version:memory.version});
      setEditing(false);await refresh();
    })}}>
      <label>更正后的记忆<textarea aria-label="更正后的记忆" required maxLength={2000} value={draft.summary} onChange={e=>setDraft({...draft,summary:e.target.value})}/></label>
      {Object.entries(choices).map(([field,options])=><label key={field}>{labels[field]} <select aria-label={labels[field]} value={draft[field]} onChange={e=>setDraft({...draft,[field]:e.target.value})}>{Object.entries(options).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>)}
      <p className="muted">沿用原引用，保留旧版本；相同证据支持的其他记忆及旧关注提醒会停用。需要关注更正后的记忆时，请重新设置。</p>
      <button disabled={busy}>保存更正</button>
    </form>}
    {memory.supersedes_id&&<button className="secondary" disabled={busy} onClick={()=>run(async()=>setHistory(await api(`memories/${memory.id}/history`)))}>查看更正历史</button>}
    {history.map((item,index)=><blockquote key={item.id}><small>{index===0?'当前版本':'历史版本'} · #{item.id}</small><p>{item.summary}</p><small>{Object.keys(choices).map(k=>choices[k][item[k]]).join(' · ')}</small></blockquote>)}
  </div>;
}
