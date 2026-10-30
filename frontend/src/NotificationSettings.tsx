import {useState} from 'react';
import {usePolling,type API} from './usePolling';

const reasons:Record<string,string>={quiet:'勿扰时段',cooldown:'冷却中',expired:'已超过通知有效期',future_time:'消息时间明显超前',unknown_time:'消息时间未知',duplicate:'重复提醒'};
export function notificationText(record:any){return record.state==='suppressed'?'未发送：'+(reasons[record.reason]||'策略限制'):({accepted:'已发送（通道已接受）',uncertain:'发送结果待确认',rejected:'通道拒绝'} as Record<string,string>)[record.state]||'等待发送'}
export function NotificationSettings({api}:{api:API}){
  const [data,setData]=useState<any>(null),[form,setForm]=useState<any>(null);
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[saved,setSaved]=useState(false);
  usePolling(api,['notification-policy'],([d])=>{setData(d);setForm((old:any)=>old||d.settings)},e=>setError(String(e)),5000);
  function change(key:string,value:any){setForm({...form,[key]:value});setSaved(false)}
  async function save(){setBusy(true);setError('');try{const {id,...body}=form;const result=await api('notification-policy','PUT',body);setData(result);setForm(result.settings);setSaved(true)}catch(e){setError(String(e))}finally{setBusy(false)}}
  return <section><h2>通知与备份</h2>{form&&<>
    <p>补采旧消息仍保存原文；发送有效期与入库新鲜度独立设置。勿扰按发送时电脑的本地时间判断。</p>
    {([['freshness_seconds','入库新鲜度（分钟）',60],['validity_seconds','发送有效期（分钟）',60],['future_seconds','未来时间容差（秒）',1],['cooldown_seconds','同一记忆冷却（分钟）',60]] as const).map(([key,label,scale])=><label key={key}>{label}<input type="number" aria-label={label} min="0" value={form[key]/scale} onChange={e=>change(key,Number(e.target.value)*scale)}/></label>)}
    <label><input type="checkbox" checked={!!form.quiet_enabled} onChange={e=>change('quiet_enabled',e.target.checked)}/>启用勿扰</label>
    <div className="row"><label>勿扰开始<input aria-label="勿扰开始" type="time" value={form.quiet_start} onChange={e=>change('quiet_start',e.target.value)}/></label><label>勿扰结束<input aria-label="勿扰结束" type="time" value={form.quiet_end} onChange={e=>change('quiet_end',e.target.value)}/></label></div>
    <p className="muted">开始与结束相同表示关闭勿扰。被勿扰或冷却抑制的通知不补发，卡片仍保留。合成通道测试豁免且不占真实冷却。</p>
    <button disabled={busy} onClick={save}>保存通知策略</button>{saved&&<span role="status">已保存</span>}
  </>}
  {data&&<article><h3>迁移备份</h3>{data.backup.last_failure&&<p role="alert">上次启动或升级失败：{data.backup.last_failure.reason}（{data.backup.last_failure.at}）</p>}<p>自动备份 {data.backup.count} 份，保留最近 {data.backup.retention} 份。最近：{data.backup.latest||'暂无版本升级备份'}。</p><p>只有升级数据库版本时备份，失败则停止升级。这些是本机明文聊天副本，不应放在云同步目录；人工备份不参与自动清理。</p></article>}
  <details><summary>最近通知结果</summary>{data?.records.map((r:any,i:number)=><p key={i}>{r.synthetic?'合成测试':'真实提醒'} · {r.kind==='watch'?'特别关注':'普通关联'} #{r.item_id}：{notificationText(r)}</p>)}</details>
  {error&&<p role="alert" className="error">{error}</p>}</section>;
}
