/* Account boundaries, cross-device discovery and encrypted-vault client behavior.
   No Google login or paid API request is made. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),{webcrypto}=require('node:crypto');
const projectSource=fs.readFileSync('web/projects.js','utf8');
const authSource=fs.readFileSync('web/auth.js','utf8').split('const accountDialog=')[0];
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};}
function fixture(){
 const elements=new Map(),storage=new Map(),session=new Map(),requests=[];let api=async()=>({});
 class Element{constructor(){this.textContent='';this.value='';this.hidden=false;this.children=[];this.dataset={};this.onclick=()=>{};this.type='password';}set innerHTML(s){for(const m of s.matchAll(/id="([^"]+)"/g))elements.set(m[1],new Element());}get innerHTML(){return'';}setAttribute(){}append(...e){this.children.push(...e);}appendChild(e){this.children.push(e);}replaceChildren(){this.children=[];}closest(){return this;}contains(){return true;}focus(){}showModal(){this.open=true;}close(){this.open=false;}}
 const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const context={console,Blob,Headers,URL,AbortController,crypto:webcrypto,Uint8Array,Date,JSON,Promise,Map,Set,Number,String,Array,Error,RegExp,Math,navigator:{onLine:true},location:{protocol:'https:'},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},sessionStorage:{getItem:k=>session.get(k)||null,setItem:(k,v)=>session.set(k,v),removeItem:k=>session.delete(k)},document:{documentElement:{classList:{toggle(){}}},querySelectorAll:()=>[],createElement:()=>new Element(),body:new Element(),addEventListener(){},visibilityState:'visible'},window:{addEventListener(){}},setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,$:el,state:{title:'Legacy draft',nodes:[],edges:[],settings:{},view:{},mainPrompt:'Task'},cloudConfig:{kind:'private-pc',backendUrl:'https://desktop-vjt2br2.tail385c9d.ts.net'},cloudAuth:{kind:'private-pc',accessToken:'a'.repeat(32)},validCloudConfig:x=>/^https:\/\/[a-z0-9.-]+\.ts\.net$/.test(x.backendUrl)?x:null,withoutVideoPayloads:x=>x,snapshot:()=>JSON.parse(JSON.stringify(context.state)),markDirty:()=>{context.dirty=true;},validateProject:async raw=>{const{projectCloud,...out}=raw;return out;},dirty:false,busy:false,ioBusy:false,history:[],future:[],selected:null,selectedMark:null,selectedEdge:null,pending:null,action:null,cancelTranscriptionQueue(){},consoleRefreshProject(){},resumeYouTubeImports(){},scheduleTranscriptionQueue(){},renderAll(){},updateRefreshNotice(){},R:{clearImageCache(){}},saveProject(){},openProject:async()=>{},confirm:()=>true,toast(){},DEFAULT_PROMPT:'Task',defaults:()=>({}),CONSOLE_KEY_KEY:'tab-key',cloudFetch:async(path,options={})=>{requests.push({path,options,uid:context.cloudAuth?.uid});return{ok:true,status:200,json:async()=>api(path,options)};}};
 vm.createContext(context);vm.runInContext(projectSource+'\n'+fs.readFileSync('web/launch.js','utf8').split("$('accountGateTrial').onclick")[0]+'\n'+authSource,context);const run=s=>vm.runInContext(s,context);
 return{context,storage,session,requests,el,run,api:set=>api=set};
}
(async()=>{
 const f=fixture();await Promise.resolve();f.run('markDirty()');await f.run('projectBackup()');const legacyID=f.context.state.projectCloud.id;
 f.el('consoleKey').value='legacy-key';f.session.set('tab-key','legacy-key');
 await f.run('accountSetUser({uid:"user-a",email:"a@example.com"})');
 assert.equal(f.context.state.title,'Untitled timeline');assert.equal(f.el('consoleKey').value,'');assert.equal(f.session.size,0);assert.equal(f.run('projectStorageScope'),'user-a');assert.equal(f.context.cloudAuth.kind,'firebase-google');
 f.context.state.title='A private board';f.run('markDirty()');await f.run('projectBackup()');const aID=f.context.state.projectCloud.id;
 assert.equal(f.context.state.projectCloud.ownerUid,'user-a');assert.notEqual(aID,legacyID);
 f.el('consoleKey').value='A-secret';await f.run('accountSetUser({uid:"user-b",email:"b@example.com"})');
 assert.equal(f.context.state.title,'Untitled timeline');assert.equal(f.el('consoleKey').value,'');assert.equal(f.run('projectRecentList().length'),0);assert.equal(f.requests.filter(r=>['PUT','POST'].includes(r.options.method)).length,0,'account switch must not automatically claim or upload previous board');
 await assert.rejects(f.run('validateProject({projectCloud:{id:"project-aaaaaaaaaaaaaaaa",key:"'+'k'.repeat(32)+'",ownerUid:"user-a"}})'),/another account/);
 await f.run('accountSetUser({uid:"user-a",email:"a@example.com"})');assert.equal(f.context.state.title,'A private board');assert.equal(f.context.state.projectCloud.id,aID);
 await f.run('accountSetUser(null)');assert.equal(f.context.state.title,'Untitled timeline');assert.equal(f.context.cloudAuth,null);assert.equal(f.run('accountUsesGoogle()'),true,'signout blocks legacy fallback');assert.equal(f.run('projectRecentList().length'),0);
 // A second device discovers and opens by ID without having a legacy key first.
 const second=fixture();await Promise.resolve();await second.run('accountSetUser({uid:"user-a",email:"a@example.com"})');
 const remote={id:'project-remoteboard123456',key:'k'.repeat(32),backendUrl:second.context.cloudConfig.backendUrl,revision:3};
 second.api(async(path)=>path==='/health'?{capabilities:{persistentProjects:true,projectRevision:true,accountProjects:true}}:path==='/projects'?{projects:[{id:remote.id,title:'From desktop',revision:3,updatedAt:new Date().toISOString()}]}:{revision:3,project:{format:'Vision',version:4,title:'From desktop',nodes:[],edges:[],settings:{},view:{},projectCloud:remote}});
 await second.run('projectRefreshAccountList()');assert.equal(second.run('projectAccountList.length'),1);await second.run('projectOpenAccount(projectAccountList[0])');assert.equal(second.context.state.title,'From desktop');assert.equal(second.context.state.projectCloud.ownerUid,'user-a');assert.equal(second.context.state.projectCloud.key,remote.key);
 // A delayed saved-key response must never refill the input after account change.
 const gate=deferred();second.api(async()=>gate.promise);const restore=second.run('accountRestoreRunKey()');await Promise.resolve();await second.run('accountSetUser({uid:"user-b",email:"b@example.com"})');gate.resolve({saved:true,apiKey:'A-private-api-key'});await restore;assert.equal(second.el('consoleKey').value,'');
 // Save/delete are serialized so a late PUT cannot recreate a removed key.
 const started=deferred(),finish=deferred(),operations=[];second.api(async(path,opts)=>{operations.push(opts.method);if(opts.method==='PUT'){started.resolve();await finish.promise;}return{saved:opts.method!=='DELETE'};});
 second.el('consoleKey').value='B-private-api-key';const write=second.run('accountSaveRunKey()');await started.promise;const remove=second.run('accountRemoveRunKey()');await Promise.resolve();assert.deepEqual(operations,['PUT']);finish.resolve();await Promise.all([write,remove]);assert.deepEqual(operations,['PUT','DELETE']);assert.equal(second.el('consoleKey').value,'');
 assert.ok(!Array.from(second.storage.values()).join('').includes('B-private-api-key'),'no API key in local project storage');
 // Refresh ID tokens on demand and reject an identity change during the refresh.
 second.context.tokenGate=deferred();second.run('accountFirebase={currentUser:{uid:"user-b",getIdToken:()=>tokenGate.promise}}');const token=second.run('accountIdToken()');await second.run('accountSetUser({uid:"user-c",email:"c@example.com"})');second.context.tokenGate.resolve('old-user-token');await assert.rejects(token,/account changed/);
 console.log('Account boundaries, recovery scopes, cross-device discovery, token-refresh isolation, key restore race and serialized key deletion passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
