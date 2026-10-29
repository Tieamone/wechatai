import {usePolling} from './usePolling';
import {useEffect, useState} from 'react';

const labels: Record<string,string> = {running:'运行中', starting:'启动中', stopping:'正在停止', stopped:'已停止', error:'异常停止，请检查微信登录和绑定', unresponsive:'心跳超时，请检查后台程序'};
export function RuntimeControls({api}:{api:(path:string,method?:string)=>Promise<any>}) {
  const [status,setStatus]=useState<any>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  usePolling(api,['runtime'],([s])=>{setStatus(s);setError('')},e=>setError(String(e)),3000,true,true);
  async function change(name:string,action:string){
    setBusy(true);setError('');
    try{await api(`runtime/${name}/${action}`,'POST');setStatus(await api('runtime'))}catch(e){setError(String(e))}finally{setBusy(false)}
  }
  return <section className="runtime-card"><h2>采集与微信通知</h2>
    {status?.enabled===false?<p>演示模式：真实采集与通知控制不可用。</p>:<>
      <div className="runtime-grid">{(['collector','bot'] as const).map(name=><article key={name}>
        <b>{name==='collector'?'指定联系人会话采集':'微信机器人通知'}：{status?labels[status[name].state]:'读取状态中…'}</b>
        <div className="row"><button disabled={busy||!status||status[name].active} onClick={()=>change(name,'start')}>{name==='collector'?'继续采集':'启用机器人通知'}</button>
        <button className="secondary" disabled={busy||!status||!status[name].active||status[name].state==='stopping'} onClick={()=>change(name,'stop')}>{name==='collector'?'停止采集':'关闭机器人通知'}</button></div>
      </article>)}</div>
      <details><summary>运行与退出说明</summary><p>停止采集保留已有记录，继续后补采新增消息。微信重启后需重新核验绑定。</p>
      <p>机器人登录成功不代表通知已启用。启用后仅向你本人发送通用提示，处理尚未发送的有效关注提醒；关闭时已提交的消息仍可能送达。采集与通知开关相互独立，关闭浏览器不停止后台；关闭工作台命令行窗口会同时停止采集和机器人。已提交的发送请求可能仍会完成。</p></details>
    </>}{error&&<p role="alert" className="error">状态或操作失败：{error}</p>}
  </section>;
}
