const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
function fixture({loaded=true}={}){
 const timers=new Map(),scripts=[];let serial=0,options,calls=0;
 const google={accounts:{oauth2:{initTokenClient:value=>{options=value;return{requestAccessToken(){calls++;}};}}}};
 const context={window:{google:loaded?google:undefined},document:{
  createElement:()=>({remove(){this.removed=true;}}),head:{appendChild:script=>scripts.push(script)}
 },setTimeout:(fn,ms)=>{timers.set(++serial,{fn,ms});return serial;},clearTimeout:id=>timers.delete(id),
 sessionStorage:{getItem(){throw new Error('Storage blocked');},setItem(){throw new Error('Storage blocked');}},
 localStorage:{getItem(){throw new Error('Storage blocked');},setItem(){throw new Error('Storage blocked');}}};
 vm.createContext(context);vm.runInContext(fs.readFileSync('web/google-login.js','utf8'),context);
 return {api:vm.runInContext('accountGoogleLogin',context),context,google,timers,scripts,get options(){return options;},get calls(){return calls;}};
}
test('opens Google synchronously without browser storage; credentials stay in memory',async()=>{
 const f=fixture(),result=f.api.request();assert.equal(f.calls,1);
 assert.equal(f.options.scope,'openid email profile');assert.equal(f.options.include_granted_scopes,false);
 assert.equal(f.options.prompt,'');assert.match(f.options.client_id,/^150865729216-.*\.apps\.googleusercontent\.com$/);
 f.options.callback({access_token:'google-token'});assert.equal(await result,'google-token');assert.equal(f.timers.size,0);
});
test('iPhone reconnect explicitly offers account selection without changing ordinary login',async()=>{
 const f=fixture(),result=f.api.request({selectAccount:true});
 assert.equal(f.calls,1);assert.equal(f.options.prompt,'select_account');
 assert.equal(f.options.login_hint,undefined);
 f.options.callback({access_token:'selected-account-token'});
 assert.equal(await result,'selected-account-token');
 const ordinary=f.api.request();assert.equal(f.options.prompt,'');
 f.options.callback({access_token:'ordinary-token'});await ordinary;
});
test('blocks double clicks, handles cancellation and allows a fresh attempt',async()=>{
 const f=fixture(),first=f.api.request();await assert.rejects(f.api.request(),{code:'auth/cancelled-popup-request'});
 const old=f.options;old.error_callback({type:'popup_closed'});await assert.rejects(first,{code:'auth/popup-closed-by-user'});
 const next=f.api.request();old.callback({access_token:'stale-token'});f.options.callback({access_token:'new-token'});
 assert.equal(await next,'new-token');assert.equal(f.calls,2);
});
test('timeout ignores a late Google callback',async()=>{
 const f=fixture(),result=f.api.request(),old=f.options;
 [...f.timers.values()].find(t=>t.ms===180000).fn();await assert.rejects(result,{code:'auth/google-timeout'});
 const next=f.api.request();old.callback({access_token:'late-token'});f.options.callback({access_token:'current-token'});
 assert.equal(await next,'current-token');
});
test('denial, malformed responses, blocked popup and explicit cancellation recover',async()=>{
 const f=fixture();
 for(const [reply,code] of [[{error:'access_denied'},'auth/google-denied'],[{},'auth/google-invalid-credential'],[{access_token:42},'auth/google-invalid-credential']]){
  const result=f.api.request();f.options.callback(reply);await assert.rejects(result,{code});
 }
 let result=f.api.request();f.options.error_callback({type:'popup_failed_to_open'});await assert.rejects(result,{code:'auth/popup-blocked'});
 result=f.api.request();f.api.cancel();await assert.rejects(result,{code:'auth/popup-closed-by-user'});
});
test('script loads once, never opens Google itself, retries after failure',async()=>{
 const f=fixture({loaded:false});await assert.rejects(f.api.request(),{code:'auth/google-not-ready'});
 const first=f.api.prepare();assert.equal(f.api.prepare(),first);assert.equal(f.scripts.length,1);
 assert.equal(f.scripts[0].src,'https://accounts.google.com/gsi/client');f.scripts[0].onerror();
 await assert.rejects(first,{code:'auth/network-request-failed'});assert.equal(f.scripts[0].removed,true);
 const retry=f.api.prepare();f.context.window.google=f.google;f.scripts[1].onload();await retry;
 assert.equal(f.api.ready(),true);assert.equal(f.calls,0);assert.equal(f.timers.size,0);
});
