import {usePolling} from './usePolling';
import {useEffect,useState} from 'react';
import {Processing} from './Processing';

const labels:Record<string,string>={expired:'已过期，待手动提炼',pending:'等待处理',processing:'正在提炼与判断',done:'已完成',partial:'提炼完成，部分关联未完成',failed:'处理失败，未自动重试',blocked_privacy:'隐私待处理',superseded:'已由后续消息替代或已暂停'};
const reasons:Record<string,string>={expired:'已超过处理有效期',future_time:'消息时间明显超前',unknown_time:'消息时间未知',model_timeout:'模型超时',model_call_failed:'模型请求或返回校验失败',newer_context:'收到后续消息',disabled:'自动处理已暂停',call_limit:'已达到调用上限',judgment_incomplete:'关联判断未完成，请查看普通关联处理状态',interrupted:'上次运行中断，避免重复计费未重试',blocked_privacy:'窗口含隐私疑点',failed:'窗口准备失败',cancelled:'窗口已取消'};
export function Automatic({api}:{api:(p:string,m?:string,b?:unknown)=>Promise<any>}){
  const [data,setData]=useState<any>(null);
  const [limit,setLimit]=useState(200);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [loaded,setLoaded]=useState(false);
  usePolling(api,['automatic'],([d])=>{setData(d);if(!loaded){setLimit(d.daily_limit);setLoaded(true)}setError('')},()=>setError('自动处理状态读取失败，请检查本机服务。'));
  async function update(enabled:boolean){setBusy(true);setError('');try{setData(await api('automatic','PUT',{enabled,daily_limit:limit}))}catch(e){setError(String(e))}finally{setBusy(false)}}
  return <section><h2>新消息自动处理</h2><p>状态：{data?(data.enabled?'已启用':'已暂停'):'正在读取'}{data&&!data.model_ready?' · 模型配置未启用，暂不调用':''}</p>
    <p>开启后，新入库文字会等待约 10 秒合并上下文，再自动提炼中文记忆，最多判断 3 条历史关联。只有明确相关且有提醒价值时才推送；不确定时不提醒。历史导入和开启前的消息不会补发给模型。</p>
    {data?.counts.processing>0&&<Processing label={`AI 正在提炼与判断 · ${data.counts.processing} 个窗口处理中`}/>}
    {data?.deferred&&<article><h3>补采与积压消息</h3><p>有 {data.deferred.pending} 条消息未自动处理；已进入历史范围 {data.deferred.assigned} 条；已完成提炼 {data.deferred.completed} 条。</p><button disabled={busy||!data.deferred.pending} onClick={async()=>{setBusy(true);setError('');try{await api('automatic/deferred-range','POST');setData(await api('automatic'));window.location.hash='/tasks/history'}catch(e){setError(String(e))}finally{setBusy(false)}}}>为未处理消息建立历史范围</button><small>每次最多 10000 条；已有活动范围需先完成或取消。时间异常需先核对。</small></article>}
    <label>最近 24 小时自动调用上限<input aria-label="自动调用上限" type="number" min={1} max={2000} value={limit} onChange={e=>setLimit(Number(e.target.value))}/></label>
    <p>已预留调用 {data?.used??0} / {data?.daily_limit??200} 次（失败也计入）；达到上限后等待额度恢复。模型可能收费，摘要与上下文会经过隐私检查后发送到已配置的接口。</p>
    <button disabled={busy||!data||!data.model_ready} onClick={()=>update(true)}>{data?.enabled?'保存调用上限':'启用新消息自动处理'}</button>
    <button className="secondary" disabled={busy||!data?.enabled} onClick={()=>update(false)}>暂停新消息自动处理</button>
    <p className="muted">需要工作台服务保持运行。机器人通知也须单独启用；关网页不会停止后台。暂停会取消待处理窗口，已发出的请求可能仍计费；恢复后只处理之后的新消息。</p>
    {data&&<p>{Object.entries(data.counts).map(([k,v])=>`${labels[k]||'其他'}：${v}`).join(' / ')||'开启后等待新消息。'}</p>}
    {data&&<p>自动关联通知：{Object.entries(data.notifications).map(([k,v])=>`${({accepted:'已发送（通道已接受）',uncertain:'发送结果待确认',rejected:'接口拒绝',skipped_duplicate:'重复已跳过'} as Record<string,string>)[k]||'其他'} ${v}`).join(' / ')||'暂无发送记录'}。接口接受不代表手机已展示。</p>}
    <details><summary>最近自动处理记录</summary>{data?.items.map((r:any)=><p key={r.trigger_id}>消息 #{r.trigger_id}：{labels[r.state]||'其他'}{r.error?' · '+(reasons[r.error]||'请查看任务或关联状态'):''}</p>)}</details>
    {error&&<p role="alert" className="error">{error}</p>}
  </section>;
}
