import {useEffect,useState} from 'react';
import {MemoryRevision} from './MemoryRevision';
type Row=Record<string,any>;
type Props={api:(p:string,m?:string,b?:unknown)=>Promise<any>;run:(f:()=>Promise<void>)=>Promise<void>;refresh:()=>Promise<void>;busy:boolean;memories:Row[]};
const labels:Record<string,string>={peer:'对方',self:'我',third_party:'第三方',unknown:'未知',positive:'肯定',negative:'否定',uncertain:'不确定',direct:'本人陈述',reported:'转述',inferred:'推测',preference:'偏好',plan:'计划',past_event:'已发生',fact:'事实'};
export function MemoryLibrary({api,run,refresh,busy,memories}:Props){
  const [open,setOpen]=useState(false);
  const [tab,setTab]=useState('pending');
  const [query,setQuery]=useState('');
  const [search,setSearch]=useState('');
  const [pages,setPages]=useState<number[]>([0]);
  const [rows,setRows]=useState<Row[]>([]);
  const [counts,setCounts]=useState({pending:0,confirmed:0});
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');
  const [revision,setRevision]=useState(0);
  const before=pages[pages.length-1];
  useEffect(()=>{let active=true;async function poll(){try{const c=await api('memories/review-counts');if(active)setCounts(c)}catch{if(active)setError('记忆数量读取失败，请刷新。')}}void poll();const timer=setInterval(poll,5000);return()=>{active=false;clearInterval(timer)}},[api,memories,revision]);
  useEffect(()=>{
    if(!open)return;
    let active=true;setLoading(true);setError('');
    api(`memories?review=${tab}&limit=11&before=${before}&q=${encodeURIComponent(search)}`).then(r=>{if(active)setRows(r)}).catch(()=>{if(active)setError('记忆读取失败，请刷新。')}).finally(()=>{if(active)setLoading(false)});
    return()=>{active=false};
  },[open,tab,before,search,api,memories,revision]);
  async function changed(){setPages([0]);setRevision(r=>r+1);await refresh()}
  return <details className="memory-library" onToggle={e=>setOpen(e.currentTarget.open)}><summary><strong>记忆库 · 待核对 {counts.pending} 条 · 已确认 {counts.confirmed} 条</strong><span className="muted">　点击展开管理</span></summary>
    <p>记忆保留供 AI 检索。确认正确后移入“已确认”，不再出现在待核对列表；不需要每次查看原文证据。</p>
    <div className="row" role="group" aria-label="记忆核对分类">
      <button className={tab==='pending'?'':'secondary'} aria-pressed={tab==='pending'} onClick={()=>{setTab('pending');setPages([0])}}>待核对（{counts.pending}）</button>
      <button className={tab==='confirmed'?'':'secondary'} aria-pressed={tab==='confirmed'} onClick={()=>{setTab('confirmed');setPages([0])}}>已确认（{counts.confirmed}）</button>
      <button className="secondary" disabled={busy||loading} onClick={()=>setRevision(r=>r+1)}>刷新记忆库</button>
    </div>
    <form onSubmit={e=>{e.preventDefault();setSearch(query);setPages([0])}}><input aria-label="搜索记忆库" value={query} onChange={e=>setQuery(e.target.value)} placeholder="在当前分类中搜索"/><button disabled={busy}>查找记忆</button></form>
    {error&&<p role="alert" className="error">{error}</p>}
    {loading?<p>正在读取记忆…</p>:rows.length===0?<p className="muted">{search?'没有匹配的记忆。':tab==='pending'?'暂无待核对记忆。':'还没有确认过的记忆。'}</p>:rows.slice(0,10).map(m=><article key={m.id}>
      <small>{m.confirmed?'✓ 已确认':m.origin==='model'?'AI 提炼 · 待核对':m.origin==='synthetic'?'合成返回 · 待核对':'人工整理 · 待核对'} · {labels[m.subject]} · {labels[m.temporal_kind]}</small>
      <h3>{m.summary}</h3>
      <button disabled={busy} className={m.confirmed?'secondary':''} onClick={()=>run(async()=>{await api(`memories/${m.id}/review/${m.confirmed?'reopen':'confirm'}`,'POST',{expected_version:m.version});await changed()})}>{m.confirmed?'移回待核对':'✓ 确认正确'}</button>
      <details><summary>查看原文证据与更正（{m.evidence.length} 条证据）</summary>
        <small>{labels[m.polarity]} · {labels[m.assertion]}{m.confirmed?' · 当前版本已确认':''}</small>
        {m.evidence.map((e:Row)=><blockquote key={`${e.message_id}-${e.span_start}`}><p>{e.quote??'原文已修订，旧引用不可用'}</p><small>证据 #{e.message_id} · {e.event_time||'时间未知'} · 版本 {e.message_revision}</small></blockquote>)}
        <button className="secondary" disabled={busy} onClick={()=>run(async()=>{await api(`memories/${m.id}/correct`,'POST',{reason:'用户在控制台标错'});await changed()})}>标错并停用</button>
        <MemoryRevision memory={m} busy={busy} api={api} run={run} refresh={changed}/>
      </details>
    </article>)}
    <div className="row"><button className="secondary" disabled={busy||loading||pages.length===1} onClick={()=>setPages(p=>p.slice(0,-1))}>上一页记忆</button><span>第 {pages.length} 页 · 每页最多 10 条</span><button className="secondary" disabled={busy||loading||rows.length<=10} onClick={()=>setPages(p=>[...p,rows[9].id])}>下一页记忆</button></div>
    <p className="muted">确认不会删除记忆或证据，也不会关闭提醒。更正后的新版本需要重新核对；失效的原文不能继续作为有效依据。</p>
  </details>;
}
