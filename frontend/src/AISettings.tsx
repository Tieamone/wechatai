import {useEffect,useState} from 'react';

export function AISettings({api}:{api:(p:string,m?:string,b?:unknown)=>Promise<any>}) {
  const [url,setUrl]=useState('');
  const [model,setModel]=useState('');
  const [key,setKey]=useState('');
  const [enabled,setEnabled]=useState(false);
  const [saved,setSaved]=useState(false);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const [error,setError]=useState('');
  function fill(s:any){setUrl(s.base_url);setModel(s.model);setEnabled(s.enabled);setSaved(s.has_key);setKey('')}
  useEffect(()=>{let active=true;api('ai/settings').then(s=>{if(active)fill(s)}).catch(e=>{if(active)setError(String(e))});return()=>{active=false}},[api]);
  async function action(fn:()=>Promise<void>){setBusy(true);setMessage('');setError('');try{await fn()}catch(e){setError(String(e))}finally{setBusy(false)}}
  return <section><h2>AI 模型配置</h2>
    <p>填写兼容 Chat Completions 的接口，不需要修改源码。配置用于手动提炼；启用下方“新消息自动处理”后也用于自动提炼和关联判断，不会自动分析全部历史。</p>
    <label>API 基础地址<input aria-label="API 基础地址" placeholder="https://api.deepseek.com（其他服务按官方地址填写）" value={url} onChange={e=>setUrl(e.target.value)}/></label>
    <label>模型名称<input aria-label="模型名称" placeholder="填写供应商提供的模型 ID" value={model} onChange={e=>setModel(e.target.value)}/></label>
    <label>API Key<input aria-label="API Key" type="password" autoComplete="off" value={key} placeholder={saved?'已加密保存；留空保留，换地址需重填':'输入密钥，仅保存在本机'} onChange={e=>setKey(e.target.value)}/></label>
    <label><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/>允许模型调用：测试会产生少量请求；提炼会把隐私处理后的所选聊天发给此服务，可能产生费用。</label>
    <div className="row"><button disabled={busy} onClick={()=>action(async()=>{fill(await api('ai/settings','PUT',{base_url:url,model,api_key:key,enabled}));setMessage('配置已保存；没有发送聊天。')})}>保存模型配置</button>
    <button disabled={busy||!saved} onClick={()=>action(async()=>{const s=await api('ai/test','POST');setMessage(s.message)})}>测试已保存配置</button>
    <button className="secondary" disabled={busy||!saved} onClick={()=>action(async()=>{fill(await api('ai/settings','DELETE'));setMessage('配置与密钥已删除。')})}>删除模型配置</button></div>
    <p className="muted">密钥由 Windows 当前用户加密保存，不回显。修改后先保存再测试。取消“允许模型调用”并保存可禁止后续请求，已发出的请求无法撤回。</p>
    {message&&<p>{message}</p>}{error&&<p role="alert" className="error">{error}</p>}
  </section>;
}
