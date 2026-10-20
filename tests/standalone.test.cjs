const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto,createHash}=require('node:crypto');
const server='https://desktop-vjt2br2.tail385c9d.ts.net';
const sample='<!doctype html><html><body>Vision — ✓</body></html>';
const release={server,size:Buffer.byteLength(sample),sha256:createHash('sha256').update(sample).digest('hex')};
const packaged=(source=sample)=>'<script id="vision-payload" type="application/octet-stream">'+Buffer.from(source).toString('base64')+'</script>';
async function until(condition){for(let i=0;i<200;i++){if(condition())return;await new Promise(resolve=>setTimeout(resolve,2));}assert.fail('launch did not settle');}
function bridge({cached,storage='available',origin=server,trusted=release}={}){
 const events={},written=[],status={textContent:''},input={disabled:true,value:'',files:[],addEventListener:(type,fn)=>events[type]=fn};
 const saved=new Map(cached===undefined?[]:[[trusted.sha256,cached]]);
 const c={crypto:webcrypto,TextEncoder,TextDecoder,Uint8Array,Array,Error,Math,atob:s=>Buffer.from(s,'base64').toString('binary'),location:{origin,search:'?view=venture'},history:{replaceState(...args){c.address=args[2];}},document:{getElementById:id=>id==='status'?status:input,open(){},write:v=>written.push(v),close(){}},setTimeout:(fn,ms)=>setTimeout(fn,Math.min(ms,25)),clearTimeout};
 c.window=c;c.opener=null;
 if(storage==='denied')Object.defineProperty(c,'indexedDB',{get(){throw new Error('SecurityError');}});
 else if(storage!=='missing')c.indexedDB={open(){
  const request={result:{close(){},transaction(_,mode){
   const tx={objectStore(){return {
    get(key){const get={result:saved.get(key)};queueMicrotask(()=>{get.onsuccess();tx.oncomplete();});return get;},
    clear(){},put(html,key){queueMicrotask(()=>{if(storage==='quota')tx.onabort();else{saved.clear();saved.set(key,html);tx.oncomplete();}});}
   };}};return tx;
  }}};
  if(storage!=='stalled')queueMicrotask(()=>request.onsuccess());return request;
 }};
 const script=fs.readFileSync('vision-pc/standalone-bridge.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1].replace('__VISION_TRUSTED_RELEASE__',JSON.stringify(trusted));
 vm.createContext(c);vm.runInContext(script,c);
 async function choose(text=packaged(),extra={}){
  input.files=[{size:Buffer.byteLength(text),text:async()=>text,...extra}];return events.change();
 }
 return {c,status,input,saved,events,written,choose};
}
test('isolated HTTPS tab opens a selected file with no opener or cross-window handshake',async()=>{
 const b=bridge();await until(()=>!b.input.disabled);
 assert.match(b.status.textContent,/Choose Vision-Standalone/);
 await b.choose();assert.deepEqual(b.written,[sample]);assert.equal(b.c.VisionStandaloneSource,sample);
 assert.equal(b.saved.get(release.sha256),sample);assert.equal(b.c.address,'/standalone/?view=venture');
 assert.equal(b.c.opener,null);await b.choose();assert.equal(b.written.length,1);
});
test('the actual large standalone download opens through the file-selection handler', {skip:!fs.existsSync('Vision-Standalone.html')}, async()=>{
 const trusted=JSON.parse(fs.readFileSync('vision-pc/standalone-manifest.json','utf8'));
 const b=bridge({trusted,storage:'missing'});await until(()=>!b.input.disabled);
 await b.choose(fs.readFileSync('Vision-Standalone.html','utf8'));
 assert.equal(b.written.length,1);assert.equal(Buffer.byteLength(b.written[0]),trusted.size);
 assert.equal(createHash('sha256').update(b.written[0]).digest('hex'),trusted.sha256);
});
test('selected outer HTML is never executed; only the exact pinned payload runs',async()=>{
 const b=bridge();await until(()=>!b.input.disabled);
 await b.choose('<script>throw new Error("untrusted outer code")</script><img src="https://evil.example/">'+packaged());
 assert.deepEqual(b.written,[sample]);
});
test('changed, stale, oversized, and non-app files can be rejected then retried',async()=>{
 for(const html of [packaged(sample.replace('Vision','Vxsion')),packaged(sample+' '),packaged(sample.slice(0,-1)),'<h1>Another HTML</h1>']){
  const b=bridge();await until(()=>!b.input.disabled);await b.choose(html);
  assert.equal(b.written.length,0);assert.equal(b.input.disabled,false);assert.equal(b.input.value,'');
  assert.match(b.status.textContent,/matching|downloaded/);await b.choose();assert.deepEqual(b.written,[sample]);
 }
 const b=bridge();await until(()=>!b.input.disabled);let read=false;
 await b.choose(packaged(),{size:1e9,text:async()=>{read=true;return packaged();}});
 assert.equal(read,false);assert.equal(b.written.length,0);assert.equal(b.input.disabled,false);
});
test('file-read failures and cancel allow another selection',async()=>{
 const b=bridge();await until(()=>!b.input.disabled);await b.events.change();assert.equal(b.written.length,0);
 await b.choose(packaged(),{text:async()=>{throw new Error('read failed');}});
 assert.equal(b.input.disabled,false);assert.match(b.status.textContent,/could not be opened/);
 await b.choose();assert.deepEqual(b.written,[sample]);
});
test('missing, denied, stalled, or full storage never prevents opening a valid file',async()=>{
 for(const storage of ['missing','denied','stalled','quota']){
  const b=bridge({storage});await until(()=>!b.input.disabled);await b.choose();assert.deepEqual(b.written,[sample],storage);
 }
});
test('refresh rechecks cached bytes and corrupt cache returns to the file chooser',async()=>{
 const valid=bridge({cached:sample});await until(()=>valid.written.length===1);assert.deepEqual(valid.written,[sample]);
 for(const cached of [sample.replace('Vision','Vxsion'),42,sample+' ']){
  const b=bridge({cached});await until(()=>!b.input.disabled);assert.equal(b.written.length,0);
  assert.match(b.status.textContent,/saved copy/);await b.choose();assert.deepEqual(b.written,[sample]);
 }
});
test('the bootstrap refuses to run on another origin',async()=>{
 const b=bridge({origin:'https://other.example'});assert.equal(b.input.disabled,true);
 assert.equal(b.events.change,undefined);assert.equal(b.written.length,0);
});
test('desktop launch is a repeatable native link with no receipt or disabled state',()=>{
 const html=fs.readFileSync('web/standalone/launcher.html','utf8');
 assert.match(html,/<a id="open" href="https:\/\/desktop-vjt2br2\.tail385c9d\.ts\.net\/standalone\/" target="_blank" rel="noopener noreferrer">Open Vision<\/a>/);
 const events={},link={addEventListener:(type,fn)=>events[type]=fn},status={textContent:''};
 const c={document:{getElementById:id=>id==='open'?link:status}};
 vm.createContext(c);vm.runInContext(fs.readFileSync('web/standalone/launcher.js','utf8'),c);
 for(let i=0;i<3;i++){events.click();assert.equal(link.disabled,undefined);assert.match(status.textContent,/choose this Vision-Standalone/);}
 assert.doesNotMatch(status.textContent,/Vision is open/);
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
