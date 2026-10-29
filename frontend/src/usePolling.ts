import {createContext,useContext,useEffect,useRef} from 'react';

export const PollingContext=createContext(true);
export const MemoryCountsContext=createContext({pending:0,confirmed:0});
export type API=(path:string,method?:string,body?:unknown,signal?:AbortSignal)=>Promise<any>;

export function usePolling(api:API,paths:string[],apply:(rows:any[])=>void,onError:(error:unknown)=>void,delay=3000,enabled=true,global=false){
  const visible=useContext(PollingContext);
  const callbacks=useRef({apply,onError});callbacks.current={apply,onError};
  const key=JSON.stringify(paths);
  useEffect(()=>{
    let timer:ReturnType<typeof setTimeout>|undefined;
    let controller:AbortController|undefined;
    let disposed=false;
    function stop(){if(timer)clearTimeout(timer);controller?.abort()}
    async function poll(){
      if(disposed||document.hidden||(!visible&&!global)||!enabled)return;
      const current=new AbortController();controller=current;
      try{const values=await Promise.all((JSON.parse(key) as string[]).map(p=>api(p,'GET',undefined,current.signal)));if(!disposed&&!current.signal.aborted)callbacks.current.apply(values)}
      catch(error){if(!disposed&&!current.signal.aborted)callbacks.current.onError(error)}
      finally{if(!disposed&&!current.signal.aborted)timer=setTimeout(poll,delay)}
    }
    function change(){stop();if(!document.hidden)void poll()}
    document.addEventListener('visibilitychange',change);void poll();
    return()=>{disposed=true;stop();document.removeEventListener('visibilitychange',change)};
  },[api,key,delay,visible,enabled,global]);
}
