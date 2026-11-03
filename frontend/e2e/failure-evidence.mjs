import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';

// These callers use isolated synthetic fixtures, never an existing user session.
export async function failureEvidence(context,name){
  if(!/^[a-z-]+$/.test(name))throw Error('Invalid synthetic suite name');
  await context.tracing.start({screenshots:true,snapshots:true,sources:true});
  return async page=>{
    const directory=resolve('../test-artifacts',name);
    try{await mkdir(directory,{recursive:true})}
    catch(error){console.error('Failure evidence directory unavailable:',error.message);return}
    try{await page.screenshot({path:resolve(directory,'failure.png'),fullPage:true,timeout:5000})}
    catch(error){console.error('Failure screenshot unavailable:',error.message)}
    try{await context.tracing.stop({path:resolve(directory,'trace.zip')})}
    catch(error){console.error('Failure trace unavailable:',error.message)}
  };
}
