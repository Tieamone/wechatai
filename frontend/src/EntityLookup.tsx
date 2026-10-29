import {useState} from 'react';
import {EntityMessages} from './EntityMessages';
type Props={busy:boolean;api:(path:string,method?:string,body?:unknown)=>Promise<any>;run:(action:()=>Promise<void>)=>Promise<void>};
export function EntityLookup({busy,api,run}:Props){
  const [q,setQ]=useState('');
  const [at,setAt]=useState('');
  const [result,setResult]=useState<any>(null);
  return <section><h2>按别名或号码找人物</h2><p>按当前保存的关系回查，可筛选关系有效时间。这不代表当时系统已经知道该关系；同名或共用号码需要你核对。</p>
    <form onSubmit={e=>{e.preventDefault();setResult(null);run(async()=>{
      const params=new URLSearchParams({q});if(at)params.set('at',new Date(at).toISOString());
      setResult(await api('entities/lookup?'+params));
    })}}>
      <input aria-label="查询别名或号码" required maxLength={200} value={q} onChange={e=>{setQ(e.target.value);setResult(null)}}/>
      <label>关系有效时间（本机时区，可选）<input aria-label="人物关系查询时间" type="datetime-local" value={at} onChange={e=>{setAt(e.target.value);setResult(null)}}/></label>
      <button disabled={busy}>查找人物候选</button>
    </form>
    {result&&<div><p>{result.ambiguous?'存在多个人物候选，请核对原话，不能自动认定身份。':result.candidates.length?'找到人物候选，仍需核对关系依据。':'没有匹配的有效关系。'}</p>
      {result.candidates.map((p:any)=><article key={p.id}><h3>{p.label} · #{p.id}</h3><EntityMessages personId={p.id} busy={busy} api={api} run={run}/>{p.matches.map((r:any)=><div key={r.id}><p>{r.value} · {r.valid_from||'开始未知'} — {r.valid_to||'结束未知'}</p>{r.time_uncertain&&<p>有效时间边界不完整，无法确认查询时刻一定适用。</p>}<small>确认依据：{r.confirmation_note}</small>{r.evidence.length?r.evidence.map((e:any)=><blockquote key={`${e.message_id}-${e.span_start}`}>{e.quote}<br/><small>证据 #{e.message_id} · 版本 {e.message_revision} · {e.event_time||'时间未知'}</small></blockquote>):<p>仅人工确认，未绑定原文</p>}</div>)}</article>)}
    </div>}
  </section>;
}
