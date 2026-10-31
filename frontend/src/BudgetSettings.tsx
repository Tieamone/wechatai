import {useState} from 'react';
import {usePolling,type API} from './usePolling';

export function BudgetSettings({api}:{api:API}){
  const [data,setData]=useState<any>(null),[form,setForm]=useState<any>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState(false);
  usePolling(api,['budgets'],([d])=>{setData(d);setForm((old:any)=>old||d.settings)},e=>setError(String(e)),5000);
  function change(key:string,value:any){setForm({...form,[key]:value});setSaved(false)}
  async function save(){setBusy(true);setError('');try{const {id,...body}=form;const d=await api('budgets','PUT',body);setData(d);setForm(d.settings);setSaved(true)}catch(e){setError(String(e))}finally{setBusy(false)}}
  return <section><h2>调用预算与单价</h2><p>统一约束手动提炼、批量提炼、自动提炼与判断、连接测试。滚动 24 小时预算和单批预算同时生效；已有自动调用次数上限仍保留。</p>
    {form&&<>
      <label><input type="checkbox" checked={!!form.enabled} onChange={e=>change('enabled',e.target.checked)}/>启用滚动 24 小时硬预算</label>
      <label>24 小时 Token 上限<input aria-label="24 小时 Token 上限" type="number" min="4096" value={form.token_limit} onChange={e=>change('token_limit',Number(e.target.value))}/></label>
      <label>24 小时金额上限（元，留空只限制 Token）<input aria-label="24 小时金额上限" type="number" disabled={form.input_rate===null||form.output_rate===null||form.model!==data.current_model||form.base_url!==data.current_base_url} min="0.000001" step="0.01" value={form.amount_limit===null?'':form.amount_limit/1000000} onChange={e=>change('amount_limit',e.target.value===''?null:Math.round(Number(e.target.value)*1000000))}/></label>
      <h3>单价确认</h3><p>当前模型：{data.current_model||'未配置'}。单价按每百万 Token 的人民币金额填写；切换模型或接口后需要重新确认。</p>
      <button className="secondary" onClick={()=>{setForm({...form,model:data.current_model,base_url:data.current_base_url});setSaved(false)}}>将单价绑定当前模型</button>
      <p>已绑定：{form.model||'未绑定'} · {form.base_url||'未绑定接口'}</p>
      {([['input_rate','输入单价'],['output_rate','输出单价'],['cached_rate','缓存输入单价（可选）'],['reasoning_rate','推理输出单价（可选）']] as const).map(([key,label])=><label key={key}>{label}（元／百万 Token）<input type="number" aria-label={label} min="0" step="0.000001" value={form[key]===null?'':form[key]/1000000} onChange={e=>change(key,e.target.value===''?null:Math.round(Number(e.target.value)*1000000))}/></label>)}
      <p className="muted">预算按输入／缓存输入中的较高价、输出／推理输出中的较高价保守计算，不抵扣缓存优惠。若供应商在这些 Token 之外额外计费，请使用合并后的保守单价；本机估算不等同于供应商账单。</p>
      <button disabled={busy} onClick={save}>保存预算设置</button>{saved&&<span role="status">预算已保存</span>}
    </>}
    {data&&<article><h3>最近 24 小时占用</h3><p>Token：{data.rolling.tokens}；可计价金额：{(data.rolling.amount/1000000).toFixed(6)} 元；无法计价调用：{data.rolling.unpriced} 次。</p><p>当前模型单价{data.price_matches?'已绑定':'未确认或已失配'}。未计价调用不视作免费，金额预算开启后会阻止后续调用。</p>{data.rolling.legacy_unknown>0&&<p>有 {data.rolling.legacy_unknown} 条旧调用缺少输入估算。启用预算后将暂停新调用，直到这些记录移出滚动 24 小时窗口；不会清空旧账本来绕过额度。</p>}<p>用量状态：{Object.entries(data.states).map(([key,n])=>`${({reserved:'发送前预留',released:'未发送已释放',unknown:'已发送或中断，用量未知',settled:'用量已结算'} as Record<string,string>)[key]||key} ${n}`).join('；')||'暂无调用'}</p></article>}
    <p>每次发送前预留保守输入估算和最多 4096 输出 Token；得到合法 usage 后按实际 Token 结算。超时和未知用量保留预留；确认发送前失败才释放。预算不足时不启动下一次调用，已发出的请求不能撤回。硬预算控制的是本机预留和已知用量，不能保证供应商最终账单绝不超出估算。</p>
    {error&&<p role="alert" className="error">{error}</p>}
  </section>;
}
