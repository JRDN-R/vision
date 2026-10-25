const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};

async function fixture(fetcher,{valid=true}={}){
 const elements={connect:{disabled:true},status:{},return:{hidden:true}},timers=new Map();
 const calls={google:0,fetch:0,refresh:[],cleared:0};let serial=0;
 const auth={currentUser:null,authStateReady:async()=>{}};
 const user={getIdToken:async force=>{calls.refresh.push(force);return force?'fresh-id-token':'first-id-token';}};
 const sdk={inMemoryPersistence:{},initializeAuth:()=>auth,GoogleAuthProvider:{credential:(_id,token)=>token},
  signInWithCredential:async()=>{auth.currentUser=user;return{user};},signOut:async()=>{calls.cleared++;auth.currentUser=null;}};
 const context={URLSearchParams,AbortController,Promise,
  location:{origin:'https://jrdn-r.github.io',pathname:'/vision/ios-signin.html',hash:valid?'#id='+('a'.repeat(43))+'&state='+('b'.repeat(43)):'',href:'original'},
  history:{replaceState(){}},document:{getElementById:id=>elements[id]},
  window:{VisionFirebaseSDK:{auth:sdk,app:{initializeApp:()=>({})}}},
  accountGoogleLogin:{prepare:async()=>{},cancel(){},request:async options=>{calls.google++;assert.equal(options.selectAccount,true);return'google-token';}},
  setTimeout:(fn,ms)=>{timers.set(++serial,{fn,ms});return serial;},clearTimeout:id=>timers.delete(id),
  fetch:async(...args)=>{calls.fetch++;return fetcher(...args);}};
 vm.createContext(context);vm.runInContext(fs.readFileSync('web/ios-signin.js','utf8'),context);await tick();
 return{context,elements,calls,timers,auth};
}
const response=(status,body)=>({status,ok:status===200,json:async()=>body});

test('a late server approval cannot revive an expired iPhone connection',async()=>{
 const pending=deferred(),f=await fixture(()=>pending.promise);
 const click=f.elements.connect.onclick();await tick();assert.equal(f.calls.fetch,1);
 [...f.timers.values()].find(t=>t.ms===300000).fn();
 pending.resolve(response(200,{code:'c'.repeat(43)}));await click;
 assert.equal(f.context.location.href,'original');assert.equal(f.elements.return.hidden,true);
 assert.equal(f.elements.connect.disabled,true);assert.match(f.elements.status.textContent,/connection expired/);
 assert.equal(f.auth.currentUser,null);
});
test('rejected credentials get one refresh, no duplicate popup, and a usable retry button',async()=>{
 const f=await fixture(()=>response(401,{code:'sign-in-invalid',error:'Sign in again.'}));
 const first=f.elements.connect.onclick();await f.elements.connect.onclick();await first;
 assert.equal(f.calls.google,1);assert.equal(f.calls.fetch,2);assert.deepEqual(f.calls.refresh,[false,true]);
 assert.equal(f.elements.connect.disabled,false);assert.match(f.elements.status.textContent,/Google signed in, but FUPCJ Server/);
 assert.equal(f.auth.currentUser,null);assert.equal(f.context.location.href,'original');
});
test('known server clock errors do not loop or refresh credentials',async()=>{
 const f=await fixture(()=>response(401,{code:'sign-in-clock',error:'Check automatic date and time on the PC.'}));
 await f.elements.connect.onclick();assert.equal(f.calls.fetch,1);assert.deepEqual(f.calls.refresh,[false]);
 assert.match(f.elements.status.textContent,/automatic date and time/);assert.equal(f.auth.currentUser,null);
});
test('opening the broker outside the app never starts Google or sends credentials',async()=>{
 const f=await fixture(()=>{throw Error('Unexpected request');},{valid:false});
 assert.equal(f.calls.google,0);assert.equal(f.calls.fetch,0);assert.equal(f.elements.connect.onclick,undefined);
 assert.match(f.elements.status.textContent,/inside the Vision iPhone app/);
});
