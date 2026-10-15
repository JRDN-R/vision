// The bridge authenticates this Site, while Vision independently authorizes
// the linked person. Never accept an actor ID from tool arguments.
const ENDPOINT='https://desktop-vjt2br2.tail385c9d.ts.net/api/church-admin/bridge';
export class BridgeError extends Error {}
export function visionBridge(env,fetcher=fetch){
 const configured=typeof env.FUPCJ_BRIDGE_SECRET==='string'&&/^[a-f0-9]{64}$/.test(env.FUPCJ_BRIDGE_SECRET);
 const required=env.FUPCJ_ACCESS_MODE==='required';
 async function call(actor,action,args={}){
  if(!configured)throw new BridgeError('FUPCJ Server integration is not configured yet. Existing scheduling is unchanged.');
  if(!/^[A-Za-z0-9:_-]{1,200}$/.test(actor||''))throw new BridgeError('Sign in to link your ChatGPT connection.');
  const payload={actor,action};
  if(action==='link_vision_account')payload.code=args.code;else payload.arguments=args;
  if(action==='send_scheduling_email'){
   if(!required)throw new BridgeError('The PC notification connection is awaiting activation. No email was queued.');
   if(!env.PCO_CLIENT_ID||!env.PCO_SECRET)throw new BridgeError('Planning Center authorization is unavailable.');
   payload.credentials={client_id:env.PCO_CLIENT_ID,secret:env.PCO_SECRET};
  }
  const body=JSON.stringify(payload),stamp=String(Math.floor(Date.now()/1000)),nonce=crypto.randomUUID().replaceAll('-','');
  const key=await crypto.subtle.importKey('raw',new TextEncoder().encode(env.FUPCJ_BRIDGE_SECRET),{name:'HMAC',hash:'SHA-256'},false,['sign']);
  const bytes=await crypto.subtle.sign('HMAC',key,new TextEncoder().encode(stamp+'\n'+nonce+'\n'+body));
  const signature=Array.from(new Uint8Array(bytes),v=>v.toString(16).padStart(2,'0')).join('');
  let response;try{response=await fetcher(ENDPOINT,{method:'POST',redirect:'manual',headers:{'Content-Type':'application/json','X-FUPCJ-Time':stamp,'X-FUPCJ-Nonce':nonce,'X-FUPCJ-Signature':signature},body,signal:AbortSignal.timeout(15000)});}catch{throw new BridgeError('FUPCJ Server did not respond. Check the notification request before retrying with the same request ID.');}
  if(response.status>=300&&response.status<400)throw new BridgeError('Unexpected PC redirect. No redirect was followed.');
  const value=await response.json().catch(()=>null);
  if(!response.ok)throw new BridgeError(value?.error||'FUPCJ Server could not authorize this request.');
  if(!value||typeof value!=='object'||Array.isArray(value))throw new BridgeError('FUPCJ Server returned an invalid result.');
  return value;
 }
 return {configured,required,call};
}
