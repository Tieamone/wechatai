import {useState} from 'react';
import {MemoryLibrary} from './MemoryLibrary';

type Row = Record<string, any>;
type Props = {
  selected: Row[];
  clear: () => void;
  api: (path:string, method?:string, body?:unknown)=>Promise<any>;
  run: (action:()=>Promise<void>)=>Promise<void>;
  refresh: ()=>Promise<void>;
  busy: boolean;
  memories: Row[];
};
const labels:Record<string,string>={peer:'对方',self:'我',third_party:'第三方',unknown:'未知',positive:'肯定',negative:'否定',uncertain:'不确定',direct:'本人陈述',reported:'转述',inferred:'推测',preference:'偏好',plan:'计划',past_event:'已发生',fact:'事实'};

export function MemoryEditor({selected,clear,api,run,refresh,busy,memories}:Props){
  const [summary,setSummary]=useState('');
  const [subject,setSubject]=useState('peer');
  const [polarity,setPolarity]=useState('uncertain');
  const [assertion,setAssertion]=useState('direct');
  const [temporal,setTemporal]=useState('unknown');
  const [watchMemory,setWatchMemory]=useState('');
  const [watchWord,setWatchWord]=useState('');
  const choice=(label:string,value:string,set:(v:string)=>void,options:string[])=><label>{label} <select aria-label={label} value={value} onChange={e=>set(e.target.value)}>{options.map(v=><option key={v} value={v}>{labels[v]}</option>)}</select></label>;
  return <section><h2>有证据的记忆</h2><p>记忆默认收纳在库中，供 AI 持续检索。需要核对时再展开，确认过的内容会移到“已确认”。</p>
    {selected.length>0&&<form onSubmit={e=>{e.preventDefault();run(async()=>{
      await api('memories','POST',{summary,subject,polarity,assertion,temporal_kind:temporal,evidence:selected.map(m=>({message_id:m.id,message_revision:m.revision,span_start:0,span_end:Array.from(m.text as string).length}))});
      clear();setSummary('');await refresh();
    })}}><div>{selected.map(m=><p key={m.id}>#{m.id} · {labels[m.sender_role]}：“{m.text}”</p>)}</div>
      <textarea aria-label="记忆内容" required value={summary} onChange={e=>setSummary(e.target.value)} placeholder="根据原话整理，不把猜测写成确定事实"/>
      {choice('主体',subject,setSubject,['peer','self','third_party','unknown'])}
      {choice('肯定或否定',polarity,setPolarity,['positive','negative','uncertain'])}
      {choice('陈述来源',assertion,setAssertion,['direct','reported','inferred'])}
      {choice('事项类型',temporal,setTemporal,['preference','plan','past_event','fact','unknown'])}
      <button disabled={busy}>保存记忆</button><button type="button" className="secondary" onClick={clear}>取消选择</button>
    </form>}
    <MemoryLibrary api={api} run={run} refresh={refresh} busy={busy} memories={memories}/>

    {memories.length>0&&<details><summary>将记忆设为特别关注</summary><form onSubmit={e=>{e.preventDefault();run(async()=>{await api('watches','POST',{source_memory_id:Number(watchMemory),user_text:watchWord});setWatchWord('');await refresh()})}}>
      <label>关注已有记忆 <select required aria-label="关注已有记忆" value={watchMemory} onChange={e=>setWatchMemory(e.target.value)}><option value="">请选择记忆</option>{memories.map(m=><option key={m.id} value={m.id}>{m.summary}</option>)}</select></label>
      <input required aria-label="记忆关注关键词" placeholder="触发关键词，例如咖啡" value={watchWord} onChange={e=>setWatchWord(e.target.value)}/><button disabled={busy}>设为特别关注</button>
    </form></details>}
  </section>;
}
