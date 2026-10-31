import {useRef,useState} from 'react';
import {usePolling,type API} from './usePolling';

export function BatchControls({api,initializations}:{api:API,initializations:any[]}){
  const [rows,setRows]=useState<any[]>([]),[range,setRange]=useState(''),[windows,setWindows]=useState(5),[tokens,setTokens]=useState(100000),[amount,setAmount]=useState('');
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const [priced,setPriced]=useState(false);
  const key=useRef(crypto.randomUUID());
  usePolling(api,['batches','budgets'],([d,p])=>{setRows(d);setPriced(p.price_matches)},e=>setError(String(e)),5000);
  async function action(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();setRows(await api('batches'))}catch(e){setError(String(e))}finally{setBusy(false)}}
  function reset(){key.current=crypto.randomUUID()}
  const labels:Record<string,string>={active:'执行中',paused:'已暂停',cancelled:'已取消',completed:'已结束',done:'已提炼',pending:'待处理',processing:'正在提炼',blocked:'窗口阻断',failed:'失败',unknown:'中断待核对'};
  const reasons:Record<string,string>={budget_limit:'预算不足、模型单价变化或已停止；核对设置后再继续',model_disabled:'模型未启用',model_changed:'模型或接口已变化，请重新创建运行',window_blocked:'窗口失败、隐私阻断或已取消',model_failed:'模型请求或结果失败，不自动重试',interrupted:'上次运行中断，未知窗口不会重发',initialization_cancelled:'历史范围已取消'};
  return <section><h2>有预算的批量提炼</h2><p>后台顺序提炼接下来 N 个窗口。重复提交不会重复占用窗口；刷新和重启保留进度，中断窗口不会自动重发。</p>
    <label>选择历史范围<select aria-label="批量历史范围" value={range} onChange={e=>{setRange(e.target.value);reset()}}><option value="">请选择</option>{initializations.filter(i=>i.state==='active').map(i=><option key={i.id} value={i.id}>范围 #{i.id}</option>)}</select></label>
    <label>最多窗口数<input aria-label="批量窗口数" type="number" min="1" max="100" value={windows} onChange={e=>{setWindows(Number(e.target.value));reset()}}/></label>
    <label>本批 Token 上限<input aria-label="本批 Token 上限" type="number" min="4096" value={tokens} onChange={e=>{setTokens(Number(e.target.value));reset()}}/></label>
    <label>本批金额上限（元，留空只限制 Token）<input aria-label="本批金额上限" disabled={!priced} type="number" min="0.000001" step="0.01" value={amount} onChange={e=>{setAmount(e.target.value);reset()}}/></label>
    <p>金额模式需先在 <a href="#/settings/budgets">调用预算</a> 确认当前模型单价。单批上限不会覆盖全局上限。全局额度不足可调整后手动继续；本批上限固定，不足时请取消后按新上限创建运行，已完成窗口不会重复加入。</p>
    <button disabled={busy||!range} onClick={()=>action(async()=>{await api('batches','POST',{initialization_id:Number(range),request_key:key.current,max_windows:windows,token_limit:tokens,amount_limit:amount===''?null:Math.round(Number(amount)*1000000)});reset()})}>启动有预算的批量提炼</button>
    {rows.map(r=><article key={r.id}><h3>批量 #{r.id} · {labels[r.state]||r.state}</h3><p>最多 {r.max_windows} 窗口；Token 占用 {r.usage.tokens} / {r.token_limit}；金额 {(r.usage.amount/1000000).toFixed(6)}{r.amount_limit===null?' 元（未设金额上限）':` / ${(r.amount_limit/1000000).toFixed(6)} 元`}；无法计价 {r.usage.unpriced} 次。</p><p>{Object.entries(r.counts).map(([s,n])=>`${labels[s]||s} ${n}`).join('；')}</p>{r.reason&&<p>{reasons[r.reason]||'请核对任务状态'}</p>}{!['completed','cancelled'].includes(r.state)&&(['pause','resume','cancel'] as const).map(a=><button key={a} disabled={busy} className="secondary" onClick={()=>action(async()=>{await api(`batches/${r.id}/control`,'POST',{action:a})})}>{{pause:'暂停批量',resume:'继续批量',cancel:'取消批量'}[a]}</button>)}</article>)}
    {error&&<p role="alert" className="error">{error}</p>}
  </section>;
}
