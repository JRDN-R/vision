const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto,createHash}=require('node:crypto');
const server='https://desktop-vjt2br2.tail385c9d.ts.net',nonce='a'.repeat(48);
const sample='<!doctype html><html><body>Vision — ✓</body></html>';
const release={server,size:Buffer.byteLength(sample),sha256:createHash('sha256').update(sample).digest('hex')};
function bridge({cached,withOpener=true}={}){
 const events={},sent=[],written=[],element={textContent:''},sender={postMessage:(...v)=>sent.push(v)};
 const c={crypto:webcrypto,TextEncoder,Uint8Array,Array,Error,location:{origin:server,hash:withOpener?'#'+nonce:'',search:'?view=venture'},history:{replaceState(){}},document:{getElementById:()=>element,open(){},write:v=>written.push(v),close(){}},setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){}};
 if(cached!==undefined)c.indexedDB={open(){
  const request={result:{close(){},transaction(){const tx={objectStore(){return {get(){const get={result:cached};queueMicrotask(()=>{get.onsuccess();tx.oncomplete();});return get;}};}};return tx;}}};
  queueMicrotask(()=>request.onsuccess());return request;
 }};
 c.window=c;c.opener=withOpener?sender:null;c.addEventListener=(type,fn)=>events[type]=fn;c.removeEventListener=()=>{};
 const script=fs.readFileSync('vision-pc/standalone-bridge.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1].replace('__VISION_TRUSTED_RELEASE__',JSON.stringify(release));
 vm.createContext(c);vm.runInContext(script,c);
 const send=(data={},source=sender,origin='null')=>events.message({source,origin,data:{type:'vision-standalone-load',nonce,html:sample,...data}});
 return {c,sent,written,sender,send};
}
test('HTTPS bootstrap accepts only the nonce-bound opener and exact UTF-8 build',async()=>{
 const b=bridge();await b.send({},{});assert.equal(b.written.length,0);
 await b.send({nonce:'b'.repeat(48)});assert.equal(b.written.length,0);
 await b.send();assert.deepEqual(b.written,[sample]);assert.equal(b.c.VisionStandaloneSource,sample);
 assert.equal(b.c.opener,null);assert.equal(b.c.VisionStandaloneLaunch.origin,'null');
 await b.send();assert.equal(b.written.length,1,'no second injection');
});
test('edited, stale, oversized, and wrong-type HTML never executes at the server origin',async()=>{
 for(const html of [sample.replace('Vision','Vxsion'),sample+' ',42,sample.slice(0,-1)]){
  const b=bridge();await b.send({html});assert.equal(b.written.length,0);
  assert.equal(b.sent.at(-1)[0].type,'vision-standalone-error');
 }
});
test('reopening HTTPS uses cached bytes only after checking the installed release hash again',async()=>{
 for(const cached of [sample,sample.replace('Vision','Vxsion')]){
  const b=bridge({cached,withOpener:false});
  await new Promise(resolve=>setTimeout(resolve,20));
  assert.equal(b.written.length,cached===sample?1:0);
  assert.equal(b.c.VisionStandaloneLaunch,undefined,'no launcher receipt on a refresh');
 }
});
test('desktop launcher loads no external assets and sends bytes only to the expected HTTPS tab',()=>{
 const events={},sent=[],button={disabled:false,addEventListener:(type,fn)=>events.click=fn},status={textContent:''};
 const popup={closed:false,postMessage:(...v)=>sent.push(v)},opened=[];
 const c={crypto:webcrypto,Uint8Array,TextDecoder,Array,atob:s=>Buffer.from(s,'base64').toString('binary'),setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,clearInterval(){},document:{getElementById:id=>id==='open'?button:id==='status'?status:{textContent:Buffer.from(sample).toString('base64')}}};
 c.window=c;c.open=(...v)=>{opened.push(v);return popup;};c.addEventListener=(type,fn)=>events[type]=fn;
 vm.createContext(c);vm.runInContext(fs.readFileSync('web/standalone/launcher.js','utf8'),c);
 assert.equal(opened.length,0,'only a user click opens the server');events.click();
 const token=new URL(opened[0][0]).hash.slice(1);assert.match(token,/^[a-f0-9]{48}$/);
 const packet={type:'vision-standalone-ready',nonce:token};
 events.message({origin:'https://evil.example',source:popup,data:packet});assert.equal(sent.length,0);
 events.message({origin:server,source:{},data:packet});assert.equal(sent.length,0);
 events.message({origin:server,source:popup,data:{...packet,nonce:'wrong'}});assert.equal(sent.length,0);
 events.message({origin:server,source:popup,data:packet});assert.equal(sent[0][0].html,sample);assert.equal(sent[0][1],server);
 events.message({origin:server,source:popup,data:packet});assert.equal(sent.length,1);
 events.message({origin:server,source:popup,data:{type:'vision-standalone-opened',nonce:token}});assert.equal(button.disabled,false);assert.match(status.textContent,/Vision is open/);
});
test('one-file routing keeps Vision and Venture on the same HTTPS entry',()=>{
 const events={},c={URL,URLSearchParams,Set,CustomEvent:class{constructor(type,o){this.type=type;this.detail=o.detail;}},location:new URL(server+'/standalone/'),document:{documentElement:{dataset:{}}}};
 c.window=c;c.history={pushState(_,__,u){c.location=new URL(u);},replaceState(_,__,u){c.location=new URL(u);}};c.addEventListener=(t,f)=>events[t]=f;c.dispatchEvent=()=>{};
 vm.createContext(c);vm.runInContext(fs.readFileSync('web/standalone/route.js','utf8'),c);
 c.VisionWorkspaceRoute.commit('venture');assert.equal(c.location.href,server+'/standalone/?view=venture');
 c.VisionWorkspaceRoute.commit('vision');assert.equal(c.location.href,server+'/standalone/?view=vision');
});
test('downloading the app preserves the original verified bytes, including Unicode',()=>{
 const source=fs.readFileSync('web/standalone/runtime.js','utf8').split('downloadDesktopApp=')[0].replace('__VISION_LAUNCHER_TEMPLATE__',JSON.stringify('before__VISION_EMBEDDED_APP__after'));
 const c={TextEncoder,String,btoa:s=>Buffer.from(s,'binary').toString('base64')};vm.createContext(c);vm.runInContext(source,c);
 const result=vm.runInContext('standaloneFileSource('+JSON.stringify(sample)+')',c);
 assert.equal(Buffer.from(result.slice(6,-5),'base64').toString('utf8'),sample);
});
