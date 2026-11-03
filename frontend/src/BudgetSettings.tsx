import {useState} from 'react';
import {usePolling,type API} from './usePolling';

export function BudgetSettings({api}:{api:API}){
  const [diagnostics,setDiagnostics]=useState<any>(null);
  const [data,setData]=useState<any>(null),[form,setForm]=useState<any>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState(false);
  usePolling(api,['budgets','model-diagnostics'],([d,diagnostic])=>{setDiagnostics(diagnostic);setData(d);setForm((old:any)=>old||d.settings)},e=>setError(String(e)),5000);
  function change(key:string,value:any){setForm({...form,[key]:value});setSaved(false)}
  async function save(){setBusy(true);setError('');try{const {id,...body}=form;const d=await api('budgets','PUT',body);setData(d);setForm(d.settings);setSaved(true)}catch(e){setError(String(e))}finally{setBusy(false)}}
  return <section><h2>调用预算与单价</h2><p>统一约束手动提炼、批量提炼、自动提炼与判断、连接测试。滚动 24 小时预算和单批预算同时生效；已有自动调用次数上限仍保留。</p>
    {form&&<>
      <label><input type="checkbox" checked={!!form.enabled} onChange={e=>change('enabled',e.target.checked)}/>启用滚动 24 小时硬预算</label>
      <label>24 小时 Token 上限<input aria-label="24 小时 Token 上限" type="number" min="4096" value={form.token_limit} onChange={e=>change('token_limit',Number(e.target.value))}/></label>
      <label>批量最多占全局额度（%）<input aria-label="批量额度比例" type="number" min="1" max="100" value={form.batch_percent} onChange={e=>change('batch_percent',Number(e.target.value))}/></label>
      <p>按所有批量运行在滚动 24 小时内的已用与在途预留合计限制 Token 和金额，单批限制同时生效。{!form.enabled&&'全局预算关闭，无法保证实时处理的额度余量。'}</p>
      <label>24 小时金额上限（元，留空只限制 Token）<input aria-label="24 小时金额上限" type="number" disabled={form.input_rate===null||form.output_rate===null||form.model!==data.current_model||form.base_url!==data.current_base_url} min="0.000001" step="0.01" value={form.amount_limit===null?'':form.amount_limit/1000000} onChange={e=>change('amount_limit',e.target.value===''?null:Math.round(Number(e.target.value)*1000000))}/></label>
      <h3>单价确认</h3><p>当前模型：{data.current_model||'未配置'}。单价按每百万 Token 的人民币金额填写；切换模型或接口后需要重新确认。按公开原价估算，未核对账单。</p>
      <button className="secondary" onClick={()=>{setForm({...form,model:data.current_model,base_url:data.current_base_url});setSaved(false)}}>将单价绑定当前模型</button>
      <p>已绑定：{form.model||'未绑定'} · {form.base_url||'未绑定接口'}</p>
      {([['input_rate','输入单价'],['output_rate','输出单价'],['cached_rate','缓存输入单价（可选）'],['reasoning_rate','推理输出单价（可选）']] as const).map(([key,label])=><label key={key}>{label}（元／百万 Token）<input type="number" aria-label={label} min="0" step="0.000001" value={form[key]===null?'':form[key]/1000000} onChange={e=>change(key,e.target.value===''?null:Math.round(Number(e.target.value)*1000000))}/></label>)}
      <p className="muted">预算按输入／缓存输入中的较高价、输出／推理输出中的较高价保守计算，不抵扣缓存优惠。若供应商在这些 Token 之外额外计费，请使用合并后的保守单价；本机估算不等同于供应商账单。</p>
      <label><input type="checkbox" checked={!!form.time_pricing} onChange={e=>change('time_pricing',e.target.checked)}/>使用忙闲时段单价（Asia/Shanghai）：忙时 08:00–22:00，闲时 22:00–次日 08:00。上方单价作为忙时价。</label>
      {form.time_pricing&&(['input','output','cached','reasoning'] as const).map((name,index)=>{const key=`idle_${name}_rate`;return <label key={key}>闲时{['输入','输出','缓存输入（可选）','推理输出（可选）'][index]}单价（元／百万 Token）<input aria-label={`闲时${name}单价`} type="number" min="0" step="0.000001" value={form[key]==null?'':form[key]/1000000} onChange={e=>change(key,e.target.value===''?null:Math.round(Number(e.target.value)*1000000))}/></label>})}
      <p>预留使用忙时较高价；发送时冻结所处时段及该时段基础费率，结算按实际输入、输出总量估算。缓存／推理单独计价尚未核对账单，明细不额外相加。</p>
      <button disabled={busy} onClick={save}>保存预算设置</button>{saved&&<span role="status">预算已保存</span>}
    </>}
    {data&&<article><h3>最近 24 小时占用</h3><p>Token：{data.rolling.tokens}；可计价金额：{(data.rolling.amount/1000000).toFixed(6)} 元；无法计价调用：{data.rolling.unpriced} 次。</p><p>当前模型单价{data.price_matches?'已绑定':'未确认或已失配'}。未计价调用不视作免费，金额预算开启后会阻止后续调用。</p>{data.rolling.legacy_unknown>0&&<p>有 {data.rolling.legacy_unknown} 条旧调用缺少输入估算。启用预算后将暂停新调用，直到这些记录移出滚动 24 小时窗口；不会清空旧账本来绕过额度。</p>}<p>用量状态：{Object.entries(data.states).map(([key,n])=>`${({reserved:'发送前预留',released:'未发送已释放',unknown:'已发送或中断，用量未知',settled:'用量已结算'} as Record<string,string>)[key]||key} ${n}`).join('；')||'暂无调用'}</p></article>}
    <p>每次发送前预留保守输入估算和最多 4096 输出 Token；得到合法 usage 后按实际 Token 结算。超时和未知用量保留预留；确认发送前失败才释放。预算不足时不启动下一次调用，已发出的请求不能撤回。硬预算控制的是本机预留和已知用量，不能保证供应商最终账单绝不超出估算。</p>
    {data?.token_details&&<article><h3>推理与缓存用量明细</h3><p>最近 24 小时推理明细覆盖 {data.token_details.reasoning_known}/{data.token_details.calls} 次调用；{data.token_details.reasoning_known?`已知推理 ${data.token_details.reasoning_tokens} Token，覆盖样本输出 ${data.token_details.covered_output_tokens} Token`:'尚无可核验推理明细，未知不算零'}。</p><p>缓存输入明细覆盖 {data.token_details.cached_known} 次；供应商 text_tokens 明细覆盖 {data.token_details.text_known} 次。明细可能互相包含，不相加计费，也不据 text_tokens 推断可见输出。历史缺失不通过重发补齐。</p></article>}
    <article><h3>模型失败诊断</h3><p>本机保留最近 20 条失败记录；原始返回每条最多 256 KiB，总文件量最多 5 MiB。返回可能包含敏感内容，按聊天数据保护，不上传到仓库。超限只记元数据，原始内容不在网页展示。</p>
      <p>当前保留 {diagnostics?.records?.length||0} 条。</p>
      {diagnostics?.records?.map((r:any)=><p key={r.id}>失败编号：{r.code} · 位置：{r.location.join(' / ')||'整体'} · 响应字节：{r.response_bytes??'未知'} · 输出 Token：{r.completion_tokens??'未知'} · 记忆数：{r.memory_count??'未知'} · 原始返回：{r.has_raw?'本机已保留':'未保留'}</p>)}
      <button className="secondary" disabled={busy} onClick={async()=>{setBusy(true);try{await api('model-diagnostics','DELETE');setDiagnostics({...diagnostics,records:[]})}catch(e){setError(String(e))}finally{setBusy(false)}}}>删除全部模型失败记录及原始返回</button>
    </article>
    {error&&<p role="alert" className="error">{error}</p>}
  </section>;
}
