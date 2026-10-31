import {BatchControls} from './BatchControls';
import {usePolling} from './usePolling';
import {useState,useEffect} from 'react';
import {Processing} from './Processing';
type Row=Record<string,any>;
export function Tasks({api,run,busy,selected,onExtracted}:{api:(p:string,m?:string,b?:unknown)=>Promise<any>,run:(f:()=>Promise<void>)=>Promise<void>,busy:boolean,selected:Row[],onExtracted:()=>Promise<void>}){
  const [extracting,setExtracting]=useState<number|null>(null);
  const [items,setItems]=useState<Row[]>([]);
  const [worker,setWorker]=useState<Row>({});
  const [request,setRequest]=useState<Row|null>(null);
  const [loaded,setLoaded]=useState(false);
  const [pollError,setPollError]=useState('');
  usePolling(api,['initializations','worker'],([i,w])=>{setItems(i);setWorker(w);setLoaded(true);setPollError('')},()=>setPollError('任务进度刷新失败，当前显示上次数据。'),5000);
  const hasRange=items.some(i=>i.state==='active'||i.state==='paused');
  const refresh=async()=>{setItems(await api('initializations'));setWorker(await api('worker'))};
  const labels:Record<string,string>={extracting:'正在提炼',pending:'等待处理',running:'处理中',prepared:'准备完成（未提炼）',validated_model:'AI 提炼完成',validated_synthetic:'合成返回验证完成',failed:'失败',blocked_privacy:'隐私待处理',cancelled:'已取消',active:'启用',paused:'已暂停'};
  const errorLabels:Record<string,string>={budget_limit:'预算不足，尚未发送；请核对预算设置',model_call_failed:'模型调用或返回失败，请核对后再重试',interrupted:'上次运行中断，未自动重试'};
  const warningLabels:Record<string,string>={conversation_has_unprocessed_nontext:'会话含未处理的图片、语音等非文字内容',time_gap_over_30_minutes:'窗口内消息间隔超过 30 分钟',bounded_or_selected_history:'窗口或选择范围有限'};
  return <section><h2>历史初始化任务</h2><p>固定当前历史范围并准备上下文。每个窗口最多 12 条、重叠 2 条；准备完成不等于已提炼。先配置 AI，预览输入后按窗口手动提炼。</p>
    <button disabled={busy||!loaded||hasRange} onClick={()=>run(async()=>{await api('initializations','POST',{mode:'full'});await refresh()})}>创建全量范围</button>
    <button disabled={busy||!loaded||hasRange||selected.length===0} onClick={()=>run(async()=>{await api('initializations','POST',{mode:'batch',message_ids:selected.map(m=>m.id)});await refresh()})}>创建所选消息分批范围</button>
    <button disabled={busy} className="secondary" onClick={()=>run(async()=>{await api('initializations/process-next','POST');await refresh()})}>处理下一窗口</button>
    <button disabled={busy} className="secondary" onClick={()=>run(refresh)}>刷新任务</button>
    <p>本地准备执行器：{worker.error==='database_busy_retrying'?'数据库暂忙，正在自动重试':worker.error?'异常停止，可点击恢复':worker.running?'运行中':'未运行或尚未刷新'}。历史默认只准备窗口；明确启动的有预算批量会在后台提炼，新消息自动调用由独立开关控制。进度每 5 秒刷新。</p>
    {loaded&&!worker.running&&<button disabled={busy} onClick={()=>run(async()=>{await api('worker/restart','POST');await refresh()})}>恢复后台准备</button>}
    {hasRange&&<p className="muted">已有初始化范围，无需重复创建。可以继续处理或暂停；只有需要重新选择范围时才取消。</p>}
    {pollError&&<p role="alert">{pollError}</p>}
    {(extracting!==null||items.some(i=>i.counts.extracting>0))&&<Processing label={extracting!==null?`AI 正在提炼窗口 #${extracting}`:'AI 正在提炼历史窗口'}/>}
    {items.map(i=><article key={i.id}><h3>#{i.id} · {i.mode==='full'?'全量':'分批'} · {labels[i.state]}</h3><small>固定截止时间 {i.cutoff}</small><p>{Object.entries(i.counts).map(([k,v])=>`${labels[k]||k}：${v}`).join(' / ')}</p>
      {i.message_coverage&&<p>去重消息：范围 {i.message_coverage.scope} 条；全部相关窗口已准备 {i.message_coverage.prepared} 条，其中合成验证 {i.message_coverage.validated_synthetic} 条；失败或阻断 {i.message_coverage.failed_or_blocked} 条；原文版本失效 {i.message_coverage.stale} 条；真实提炼 {i.message_coverage.real_extracted} 条。</p>}
      {i.message_coverage?.scope>0&&<div><small>已提炼 {i.message_coverage.real_extracted} / {i.message_coverage.scope} 条消息</small><progress className="coverage-progress" aria-label="历史消息提炼覆盖" value={i.message_coverage.real_extracted} max={i.message_coverage.scope}/></div>}
      {i.context_warnings?.length>0&&<p>上下文限制：{i.context_warnings.map((w:string)=>warningLabels[w]||w).join('；')}。请结合原话核对。</p>}
      {i.state!=='cancelled'&&(['pause','resume','retry','cancel'] as const).map(a=><button disabled={busy} className="secondary" key={a} onClick={()=>run(async()=>{await api(`initializations/${i.id}/control`,'POST',{action:a});await refresh()})}>{{pause:'暂停',resume:'恢复',retry:'重试失败/阻断',cancel:'取消'}[a]}</button>)}
      {i.jobs.filter((j:Row)=>j.error).map((j:Row)=><p key={j.id}>窗口 #{j.id}：{errorLabels[j.error]||'请核对窗口状态与原文'}（尝试 {j.attempt} 次）</p>)}
      {i.jobs.filter((j:Row)=>j.state==='prepared').map((j:Row)=><div key={j.id}><button disabled={busy} className="secondary" onClick={()=>run(async()=>setRequest(await api(`model-requests/preview/${j.id}`,'POST')))}>预览窗口 #{j.id} 模型输入</button><button disabled={busy} onClick={()=>run(async()=>{setExtracting(j.id);try{await api(`ai/extract/${j.id}`,'POST');await refresh();await onExtracted()}finally{setExtracting(null)}})}>用 AI 提炼此窗口 #{j.id}</button><small>点击将发送此窗口隐私处理后的聊天，可能产生费用。</small></div>)}
    </article>)}
    <BatchControls api={api} initializations={items}/>
    {request&&<article><h3>本地请求预览 · 不会发送</h3><p>精确日期已省略，因此此版本不能推理相对日期；映射值与账号标识不包含在输入中。</p><pre>{JSON.stringify(request.data,null,2)}</pre></article>}
  </section>;
}
