import {useState} from 'react';
type Props={personId:number;busy:boolean;api:(path:string)=>Promise<any>;run:(action:()=>Promise<void>)=>Promise<void>};
export function EntityMessages({personId,busy,api,run}:Props){
  const [result,setResult]=useState<any>(null);
  async function load(more=false){
    const page=await api(`entities/${personId}/messages`+(more?`?before=${result.next_before}`:''));
    setResult({...page,items:more?[...result.items,...page.items]:page.items});
  }
  return <div><button disabled={busy} onClick={()=>run(()=>load())}>查找此人物的原文候选</button>
    {result&&<div><p>按导入顺序倒序展示。别名或号码命中不代表消息一定在谈论此人。</p>{!result.items.length&&<p>暂无原文候选。</p>}
      {result.items.map((m:any)=><blockquote key={m.id}><p>{m.text}</p><small>原文 #{m.id} · 版本 {m.revision} · {m.event_time||'时间未知'}</small>{m.matches.map((r:any)=><p key={r.property_id}>命中：{r.value}{r.ambiguous?' · 多个人物共用此别名或号码，身份不确定':''}{r.time_uncertain?' · 有效时间不完整':''}</p>)}</blockquote>)}
      {result.has_more&&<button disabled={busy} onClick={()=>run(()=>load(true))}>继续加载原文候选</button>}
    </div>}
  </div>;
}
