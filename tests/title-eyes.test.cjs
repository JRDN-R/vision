const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const source = fs.readFileSync('web/title-eyes/client.js','utf8');
const moduleReady = import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
test('client submits multipart with bearer auth, keeps request ID, and reports failures', async () => {
 const {createTitleEyesClient}=await moduleReady;const calls=[];
 let response={ok:true,json:async()=>({id:'a'.repeat(24),status:'queued'})};
 const client=createTitleEyesClient({getToken:async()=>'test-token',fetchImpl:async(url,options)=>{calls.push({url,options});return response;}});
 await client.submit(new Blob(['image']),{requestId:'repeat-request'});
 assert.equal(calls[0].options.headers.Authorization,'Bearer test-token');
 assert.equal(calls[0].options.body.get('requestId'),'repeat-request');
 assert.equal(calls[0].options.headers['Content-Type'],undefined);
 assert(!calls[0].url.includes('test-token'));
 await client.get('a'.repeat(24));await client.remove('a'.repeat(24));
 assert.equal(calls.at(-1).options.method,'DELETE');
 assert.throws(()=>client.get('../other-account'),/Invalid/);
 response={ok:false,status:404,json:async()=>({})};
 await assert.rejects(client.submit(new Blob(['image'])),/needs the title-eyes update/);
 response={ok:false,status:429,json:async()=>({error:'Queue full'})};
 await assert.rejects(client.submit(new Blob(['image'])),/Queue full/);
});
function fixture() {
 const elements=new Map(),events={},storage=new Map(),timers=[];let identity=null,callback,mode='complete',submitted=0,deleted=0,copied='';
 class Element {constructor(){this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.dataset={};}setAttribute(){}removeAttribute(){}showModal(){this.open=true;}close(){this.open=false;}focus(){}select(){}}
 for(const m of fs.readFileSync('title-eyes/index.html','utf8').matchAll(/id="([^"]+)"/g))elements.set(m[1],new Element());
 const context={console,Blob,FormData,AbortController,crypto:webcrypto,URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},
 document:{getElementById:id=>{assert(elements.has(id),'unknown element '+id);return elements.get(id);},body:{classList:{add(){},remove(){}}}},
 window:{addEventListener:(name,fn)=>{events[name]=fn;}},navigator:{clipboard:{writeText:async text=>{copied=text;}}},
 sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
 initAuth:async fn=>{callback=fn;fn(identity);},user:()=>identity,accountEpoch:()=>0,authError:error=>error.message,signOut:async()=>{identity=null;callback(null);},
 createTitleEyesClient:()=>({submit:async()=>{submitted++;return {id:'a'.repeat(24)};},get:async()=>mode==='complete'?{status:'complete',title:'The Cost of a Wedding',uncertain:false}:{status:'processing',phase:'Reading the title'},remove:async()=>{deleted++;}}),
 accountGoogleLogin:{prepare:async()=>{},ready:()=>true}};
 vm.createContext(context);vm.runInContext(fs.readFileSync('web/title-eyes/app.js','utf8').replace(/^import .*;\n/gm,''),context);
 return {el:id=>elements.get(id),events,timers,setMode:value=>{mode=value;},signIn:uid=>{identity={uid};callback(identity);},stats:()=>({submitted,deleted,copied}),flush:async()=>{for(let i=0;i<12;i++)await Promise.resolve();}};
}
test('page accepts a dropped image, formats result, copies edits, and clears on account change',async()=>{
 const f=fixture();f.signIn('one');await f.flush();
 const image=Object.assign(new Blob(['image'],{type:'image/png'}),{name:'slide.png'});
 f.events.drop({preventDefault(){},dataTransfer:{files:[image]}});await f.flush();
 assert.equal(f.stats().submitted,1);assert.equal(f.el('titleText').value,'The Cost of a Wedding');assert.equal(f.el('copy').disabled,false);
 f.el('titleText').value='Edited Title';f.el('titleText').oninput();await f.el('copy').onclick();
 assert.equal(f.stats().copied,'Edited Title');assert.equal(f.el('copyLabel').textContent,'Copied');f.timers.at(-1)();assert.equal(f.el('copyLabel').textContent,'Copy');
 f.signIn('two');await f.flush();assert.equal(f.el('titleText').value,'');assert.equal(f.el('preview').hidden,true);
});
test('page asks for sign-in after selection and can cancel accepted work',async()=>{
 const f=fixture();f.setMode('processing');
 const image=Object.assign(new Blob(['image'],{type:'image/png'}),{name:'slide.png'});
 f.events.drop({preventDefault(){},dataTransfer:{files:[image]}});await f.flush();
 assert.equal(f.stats().submitted,0);assert.equal(f.el('login').open,true);
 f.signIn('one');await f.flush();assert.equal(f.stats().submitted,1);assert.equal(f.el('cancel').hidden,false);
 await f.el('cancel').onclick();assert.equal(f.stats().deleted,1);assert.equal(f.el('cancel').hidden,true);
});
