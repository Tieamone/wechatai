import {usePolling,MemoryCountsContext} from './usePolling';
import {NotificationSettings,notificationText} from './NotificationSettings';
import React, {useState, useCallback, useEffect} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';
import {MemoryEditor} from './MemoryEditor';
import {Tasks} from './Tasks';
import {OrdinaryReminders} from './OrdinaryReminders';
import {Entities} from './Entities';
import {EntityLookup} from './EntityLookup';
import {MappingConflicts} from './MappingConflicts';
import {RuntimeControls} from './RuntimeControls';
import {AISettings} from './AISettings';
import {Help} from './Help';
import {Automatic} from './Automatic';
import {Navigation,Page,groups,currentRoute} from './Navigation';
import {Processing} from './Processing';

const storageKey='workbench-session';
function savedToken(){try{return sessionStorage.getItem(storageKey)||''}catch{return ''}}
function storeToken(value:string){try{if(value)sessionStorage.setItem(storageKey,value);else sessionStorage.removeItem(storageKey)}catch{}}
let initialLoginCode=window.location.hash.startsWith('#login=')?window.location.hash.slice(7):'';
if(initialLoginCode)window.history.replaceState(null,'',window.location.pathname+window.location.search+'#/home');
type Row = Record<string, any>;
function App() {
  const [route,setRoute]=useState(currentRoute);
  const [reviewCounts,setReviewCounts]=useState({pending:0,confirmed:0});
  useEffect(()=>{const change=()=>setRoute(currentRoute());window.addEventListener('hashchange',change);return()=>window.removeEventListener('hashchange',change)},[]);
  useEffect(()=>{document.querySelector('.workspace-scroll')?.scrollTo({top:0})},[route]);
  const group=groups.find(g=>g.pages.some(p=>p[0]===route))!;
  const [token,setToken]=useState(savedToken);
  const [connected,setConnected]=useState(false);
  const [status,setStatus]=useState<Row>({});
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const [q,setQ]=useState('');
  const [results,setResults]=useState<Row[]>([]);
  const [watches,setWatches]=useState<Row[]>([]);
  const [reminders,setReminders]=useState<Row[]>([]);
  const [watch,setWatch]=useState('');
  const [payload,setPayload]=useState('');
  const [previewText,setPreviewText]=useState('');
  const [preview,setPreview]=useState<Row|null>(null);
  const [kind,setKind]=useState('PERSON');
  const [value,setValue]=useState('');
  const [busy,setBusy]=useState(false);
  const [selected,setSelected]=useState<Row[]>([]);
  const [memories,setMemories]=useState<Row[]>([]);
  const [ordinary,setOrdinary]=useState<Row[]>([]);
  const [people,setPeople]=useState<Row[]>([]);
  const [statusError,setStatusError]=useState('');
  const [updatedAt,setUpdatedAt]=useState('');
  const api=useCallback(async (path:string,method='GET',body?:unknown,signal?:AbortSignal) => {
    const res=await fetch('/api/'+path,{method,signal,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
    const data=await res.json();
    if(res.status===401){storeToken('');setToken('');setConnected(false)}
    if(!res.ok) throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));
    return data;
  },[token]);
  useEffect(()=>{
    let active=true;
    const code=initialLoginCode;initialLoginCode='';
    async function restore(){
      let value=savedToken();
      try{
        if(code){const r=await fetch('/api/session/exchange',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});const d=await r.json();if(!r.ok)throw new Error(d.detail);value=d.token}
        if(!value)return;
        const response=await fetch('/api/status',{headers:{Authorization:'Bearer '+value}});
        if(!response.ok)throw new Error('登录已失效，请使用当前启动窗口的令牌');
        const data=await response.json();
        if(active){storeToken(value);setToken(value);setStatus(data);setConnected(true)}
      }catch(e){if(active){storeToken('');setToken('');setError(String(e))}}
    }
    void restore();return()=>{active=false};
  },[]);
  usePolling(api,['status','reminders','ordinary-reminders','memories/review-counts'],([s,r,o,c])=>{setReviewCounts(c);setStatus(s);setReminders(r);setOrdinary(o);setUpdatedAt(new Date().toLocaleTimeString());setStatusError('')},()=>setStatusError('自动刷新失败，当前显示上次数据；请检查本机服务。'),3000,connected);
  useEffect(()=>{if(connected)void refresh().catch(e=>setError(String(e)))},[connected,api]);
  async function refresh(){
    const [s,w,r,m,o,p]=await Promise.all([api('status'),api('watches'),api('reminders'),api('memories'),api('ordinary-reminders'),api('entities')]);
    setStatus(s);setWatches(w);setReminders(r);setMemories(m);setOrdinary(o);setPeople(p);
  }
  async function run(action:()=>Promise<void>){setBusy(true);setError('');setNotice('');try{await action()}catch(e){setError(String(e))}finally{setBusy(false)}}
  return <MemoryCountsContext.Provider value={reviewCounts}><div className={connected?'app-shell':'login-shell'}>
    {connected&&<Navigation route={route} pending={reviewCounts.pending}/>}
    <main className={connected?'workspace':'login-panel'}>
    {!connected?<><header><small>本地 · 私人记忆工作台</small><h1>让重要的小事，有迹可循。</h1><p>记住原话，在需要的时候想起来。</p></header><section><h2>连接本机服务</h2><p>从启动窗口复制本次会话令牌。令牌保存在当前标签页会话中，刷新无需重新输入。</p><input aria-label="会话令牌" type="password" value={token} onChange={e=>setToken(e.target.value)}/><button disabled={busy} onClick={()=>run(async()=>{await refresh();storeToken(token);setConnected(true)})}>打开工作台</button></section></>:<>
      <header className="workspace-header"><div><small>本地工作台 / {group.name}</small><h1>{group.pages.find(p=>p[0]===route)?.[1]}</h1></div><div className="connection-badge"><span className="live-dot"/>{statusError?'连接待检查':'已连接本机'}<small>{updatedAt?'更新于 '+updatedAt:'正在读取'}</small></div></header>
      {group.pages.length>1&&<nav className="subnav" aria-label="分区导航">{group.pages.map(([id,label])=><a key={id} href={'#/'+id} aria-current={route===id?'page':undefined}>{label}</a>)}</nav>}
      <div className="workspace-scroll">
      {statusError&&<p role="alert" className="error">{statusError}</p>}
      {selected.length>0&&<div className="selection-bar">已选 {selected.length} 条证据 <a href="#/memory/library">整理记忆</a><a href="#/memory/people">关联人物</a><a href="#/tasks/history">分批提炼</a><button className="secondary" onClick={()=>setSelected([])}>取消全部选择</button></div>}
      <Page id="home" route={route}>
        <div className="welcome"><div><small>今天，也让记忆有据可查</small><h2>工作台概况</h2><p>在左侧选择要做的事。采集、提醒和记忆各归其位。</p></div><a className="primary-link" href="#/memory/search">寻找一段原话 →</a></div>
        <div className="stats"><span>已导入 <b>{status.messages}</b></span><span>文字 <b>{status.text_messages}</b></span><span>有效记忆 <b>{status.memories}</b></span><span>当前提醒 <b>{reminders.length+ordinary.length}</b></span></div>
        {status.synthetic_demo&&<p className="banner">合成流程演示，不是真实 AI 推理。</p>}
        <RuntimeControls api={api}/>
        <div className="quick-links"><a href="#/tasks/automatic"><b>新消息自动处理 →</b><small>管理 AI 提炼、关联判断和调用额度</small></a><a href="#/reminders/list"><b>查看提醒 →</b><small>看看哪些旧事和现在有关</small></a><a href="#/memory/library"><b>打开记忆库 →</b><small>核对、确认和整理已有记忆</small></a></div>
      </Page>
      <Page id="memory/search" route={route}><section><h2>寻找历史</h2><form onSubmit={e=>{e.preventDefault();run(async()=>{setResults(await api('search?q='+encodeURIComponent(q)));setMemories(await api('memories?q='+encodeURIComponent(q)))})}}><input aria-label="搜索关键词" placeholder="试试：咖啡、过敏、周末" value={q} onChange={e=>setQ(e.target.value)}/><button disabled={busy}>搜索</button></form>{results.length===0?<p className="muted">暂无结果。先导入样本，再输入关键词。</p>:results.map(r=><article key={r.id}><small>{r.sender_role==='peer'?'对方':'我'} · {r.event_time||'时间未知'} · 原文候选</small><p>{r.text}</p><small>证据 #{r.id} · {r.source_id}</small><button className="secondary" onClick={()=>setSelected(old=>old.some(m=>m.id===r.id)?old.filter(m=>m.id!==r.id):[...old,r])}>{selected.some(m=>m.id===r.id)?'取消证据':'选作记忆证据'}</button></article>)}</section></Page>
      <Page id="memory/library" route={route}><MemoryEditor selected={selected} clear={()=>setSelected([])} api={api} run={run} refresh={refresh} busy={busy} memories={memories}/></Page>
      <Page id="memory/people" route={route}><Entities selected={selected} people={people} busy={busy} api={api} run={run} refresh={refresh}/><EntityLookup key={JSON.stringify(people)} busy={busy} api={api} run={run}/></Page>
      <Page id="reminders/watch" route={route}><section><h2>特别关注</h2><p>输入精确关注词，例如“咖啡”。命中仅表示可能相关。</p><form onSubmit={e=>{e.preventDefault();run(async()=>{await api('watches','POST',{user_text:watch});setWatch('');await refresh()})}}><input aria-label="关注词" value={watch} onChange={e=>setWatch(e.target.value)}/><button disabled={busy}>添加</button></form>{watches.map(w=><article key={w.id}><span>你设置的关注：{w.user_text}</span>{w.source_memory_id&&<p>关联记忆：{w.source_summary}<br/><small>{w.source_status==='active'?'证据有效':'证据已失效，暂停触发；关注设置保留'}</small></p>}<button disabled={busy} className="secondary" onClick={()=>run(async()=>{await api('watches/'+w.id,'PATCH',{enabled:!w.enabled});await refresh()})}>{w.enabled?'关闭':'重新启用'}</button></article>)}</section></Page>
      <Page id="reminders/list" route={route}><OrdinaryReminders items={ordinary} busy={busy} refresh={refresh} api={api} run={run}/><section><h2>关联提醒 <button disabled={busy} className="secondary" onClick={()=>run(refresh)}>刷新</button></h2>{reminders.length===0?<p className="muted">暂无提醒。历史导入不会触发提醒；实时模拟消息可触发已启用的关注词。</p>:reminders.map(r=><article key={r.id}><small className="tag">可能相关 · 你设置的关注</small><h3>{r.user_text}</h3><p>最新片段：{r.trigger_text}</p><p>{r.reason}</p>{r.notification&&<p className="muted">{notificationText(r.notification)}</p>}{r.recent_context?.length>1&&<details><summary>查看近期上下文（包含后续补充）</summary>{r.recent_context.map((m:Row)=><p key={m.id}>{m.sender_role==='peer'?'对方':'我'}：{m.type==='text'?m.text:'[非文字消息]'}<br/><small>{m.event_time||'时间未知'}</small></p>)}</details>}{r.source_memory&&<div><h4>{r.source_memory.summary}</h4>{r.source_memory.evidence.map((e:Row)=><blockquote key={`${e.message_id}-${e.span_start}`}>{e.quote}<br/><small>{e.event_time} · 证据 #{e.message_id}</small></blockquote>)}</div>}<button disabled={busy} className="secondary" onClick={()=>run(async()=>{await api('reminders/'+r.id+'/dismiss','POST');await refresh()})}>标错并隐藏</button></article>)}</section></Page>
      <Page id="tasks/automatic" route={route}><Automatic api={api}/></Page>
      <Page id="tasks/history" route={route}><Tasks api={api} run={run} busy={busy} selected={selected} onExtracted={refresh}/></Page>
      <Page id="settings/notifications" route={route}><NotificationSettings api={api}/></Page>
      <Page id="settings/model" route={route}><AISettings api={api}/></Page>
      <Page id="settings/privacy" route={route}><section><h2>隐私替换预览</h2><p>映射只存本地。此页面不会调用模型；未检出疑点也不代表可以安全外发。</p><div className="row"><select aria-label="映射类型" value={kind} onChange={e=>setKind(e.target.value)}>{['PERSON','PHONE','EMAIL','ADDRESS','ID'].map(k=><option key={k}>{k}</option>)}</select><input aria-label="真实值" value={value} onChange={e=>setValue(e.target.value)} placeholder="需要替换的真实值"/><button disabled={busy} onClick={()=>run(async()=>{await api('mappings','POST',{kind,value});setValue('');setNotice('映射已保存')})}>添加映射</button></div><textarea aria-label="隐私预览文本" value={previewText} onChange={e=>setPreviewText(e.target.value)}/><button disabled={busy} onClick={()=>run(async()=>setPreview(await api('privacy/preview','POST',{text:previewText})))}>本地预览</button>{preview&&<article><b>{preview.status==='blocked'?'已阻断':'仅预览'}</b><p>{preview.reason}</p><pre>{preview.text}</pre></article>}</section><MappingConflicts busy={busy} api={api} run={run}/></Page>
      <Page id="settings/import" route={route}><section><h2>导入统一格式 JSON</h2><p>选择 samples 中的历史样本；添加关注后，再导入实时样本。真实聊天暂不建议导入未加密的原型目录。</p><input aria-label="JSON 文件" type="file" accept=".json" onChange={e=>{const f=e.target.files?.[0];if(f)run(async()=>setPayload(await f.text()))}}/><textarea aria-label="导入 JSON" rows={7} value={payload} onChange={e=>setPayload(e.target.value)} placeholder="粘贴统一导入格式"/><button disabled={busy} onClick={()=>run(async()=>{const result=await api('import','POST',JSON.parse(payload));await refresh();setNotice(`导入完成：新增 ${result.inserted}，更新 ${result.updated}，重复 ${result.duplicates}`)})}>导入到本地</button></section></Page>
      <Page id="settings/help" route={route}><Help/></Page>
      </div>
    </>}
    {busy&&<div className="operation-indicator"><Processing label="正在执行当前操作"/></div>}
    {error&&<aside role="alert" className="error">{error}</aside>}{notice&&<aside role="status">{notice}</aside>}
    </main>
  </div></MemoryCountsContext.Provider>
}
createRoot(document.getElementById('root')!).render(<App/>);
