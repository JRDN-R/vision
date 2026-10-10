/* Exercise the real export orchestration with authenticated-server fixtures.
 * No credentials, external network, model calls, or live project data. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const test=require('node:test');
const source=fs.readFileSync('web/rag-export.js','utf8').replace(/installRagExport\(\);\s*$/,'');

function harness(){
 const elements=new Map(),calls=[],downloads=[],timers=new Map();let timerId=0;
 const el=id=>{if(!elements.has(id))elements.set(id,{textContent:'',hidden:false,disabled:false,open:true,setAttribute(){},close(){this.open=false;},showModal(){this.open=true;}});return elements.get(id);};
 const c={Blob,AbortController,DOMException,Number,Error,Date,JSON,encodeURIComponent,console,$:el,
  state:{title:'Exact records',mainPrompt:'Find the 0.380 bore and its complete notes.',nodes:[{attachments:[{data:'private-original'}]}],projectCloud:{id:'project_export_12345',revision:8,key:'private-project-key'}},
  projectEpoch:1,accountAuthEpoch:1,projectGeneration:2,projectPending:false,projectAccountSwitching:false,busy:false,ioBusy:false,
  setTimeout:(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});if(delay===1500)queueMicrotask(()=>{if(timers.delete(id))fn();});return id;},clearTimeout:id=>timers.delete(id),
  projectIsTemporary:()=>false,projectCapabilities:async()=>({capabilities:{intelligentContextV1:true}}),ensureRemoteProject:async()=>c.state.projectCloud,
  projectResponse:async r=>r,normalizedMainPrompt:v=>v,DEFAULT_PROMPT:'Describe the supplied sources.',exportResponseRules:()=> 'Answer the task. Do not invent missing evidence.',
  showExport:()=>calls.push('zip-dialog'),R:{safeFilename:v=>v},download:(blob,name)=>downloads.push({blob,name}),toast:text=>calls.push(['toast',text]),
  projectRequest:async(path,options={})=>{calls.push({path,options});return path.endsWith('/search')?c.result:{ready:true,status:'ready',revision:8};}
 };
 c.result={ready:true,status:'ready',manifest:{projectId:'project_export_12345',revision:8},items:[{kind:'record',text:'OPN 0060 | 0.380 | retain the complete note.'}],text:'VISION LOCAL CONTEXT MANIFEST\nSaved revision 8\nOPN 0060 | 0.380 | retain the complete note.',complete:true,warnings:[]};
 vm.createContext(c);vm.runInContext(source,c);
 return {c,el,calls,downloads,timers,run:code=>vm.runInContext(code,c)};
}

test('RAG contains retrieved evidence and source limitations without project keys or raw files',async()=>{
 const h=harness(),before=JSON.stringify(h.c.state);await h.run('downloadRagExport()');
 assert.equal(h.downloads.length,1);const saved=h.downloads[0];
 assert.equal(saved.name,'Exact records.rag.txt');assert.match(saved.blob.type,/text\/plain/);
 const text=await saved.blob.text();assert.match(text,/0\.380/);assert.match(text,/complete note/);
 assert.match(text,/cannot automatically fetch omitted sources/);assert.match(text,/complete for the declared retrieval scope only/);
 for(const secret of ['private-project-key','private-original'])assert.ok(!text.includes(secret));
 assert.equal(JSON.stringify(h.c.state),before);
 const request=h.calls.find(v=>v.path?.endsWith('/search'));
 assert.deepEqual(JSON.parse(request.options.body),{revision:8,query:h.c.state.mainPrompt,mode:'adaptive'});
 assert.equal(h.el('downloadRag').disabled,false);assert.equal(h.run('ragExportJob'),null);
});

test('pending indexing is polled and a stable saved revision is pinned after autosave',async()=>{
 const h=harness();let saves=0,polls=0;
 h.c.ensureRemoteProject=async()=>{h.c.projectPending=++saves===1;return h.c.state.projectCloud;};
 h.c.projectRequest=async(path)=>path.endsWith('/search')?h.c.result:{revision:8,ready:++polls>1,status:polls>1?'ready':'updating'};
 await h.run('downloadRagExport()');assert.equal(saves,2);assert.equal(polls,2);assert.equal(h.downloads.length,1);
});

test('partial context is disclosed before a separate click downloads the available evidence',async()=>{
 const h=harness();h.c.result.complete=false;h.c.result.warnings=['One source exceeded the extraction limit.'];
 h.c.result.text+='\nRETRIEVAL LIMITATIONS\nOne source exceeded the extraction limit.';
 await h.run('downloadRagExport()');assert.equal(h.downloads.length,0);
 assert.match(h.el('ragExportStatus').textContent,/extraction limit/);assert.match(h.el('ragExportStatus').textContent,/again/);
 await h.run('downloadRagExport()');assert.equal(h.downloads.length,1);
 assert.match(await h.downloads[0].blob.text(),/Coverage: incomplete/);
 assert.equal(h.calls.filter(v=>v.path?.endsWith('/search')).length,1);
});

test('changed projects, accounts, edits, and revisions cannot receive an earlier export',async()=>{
 for(const change of ['account','project','edit','revision','pending']){
  const h=harness();h.c.projectRequest=async path=>{
   if(!path.endsWith('/search'))return {ready:true,status:'ready',revision:8};
   if(change==='account')h.c.accountAuthEpoch++;
   if(change==='project')h.c.state={...h.c.state};
   if(change==='edit')h.c.projectGeneration++;
   if(change==='revision')h.c.state.projectCloud.revision++;
   if(change==='pending')h.c.projectPending=true;
   return h.c.result;
  };
  await h.run('downloadRagExport()');assert.equal(h.downloads.length,0,change);
 }
 const h=harness();h.c.result.complete=false;await h.run('downloadRagExport()');h.c.projectGeneration++;
 await h.run('downloadRagExport()');assert.equal(h.downloads.length,0);assert.match(h.el('ragExportStatus').textContent,/project changed/);
});

test('mismatched server identity and empty evidence never download a misleading RAG file',async()=>{
 for(const failure of ['project','revision','empty','no-units']){
  const h=harness();
  if(failure==='project')h.c.result.manifest.projectId='another_project';
  if(failure==='revision')h.c.result.manifest.revision=7;
  if(failure==='empty')h.c.result.text='';
  if(failure==='no-units')h.c.result.items=[];
  await h.run('downloadRagExport()');assert.equal(h.downloads.length,0,failure);assert.equal(h.run('ragExportJob'),null);
 }
});

test('text-only boards can export their module instructions without binary attachments',async()=>{
 const h=harness();h.c.result.items=[{kind:'instruction',text:'Write a concise reply using these supplied notes.'}];
 h.c.result.text='Module instructions: Write a concise reply using these supplied notes.';
 await h.run('downloadRagExport()');assert.equal(h.downloads.length,1);assert.match(await h.downloads[0].blob.text(),/supplied notes/);
});

test('old servers, temporary sessions, failed saves, and oversized tasks leave ZIP available',async()=>{
 for(const failure of ['old','trial','save','query','bytes']){
  const h=harness();
  if(failure==='old')h.c.projectCapabilities=async()=>({capabilities:{}});
  if(failure==='trial')h.c.projectIsTemporary=()=>true;
  if(failure==='save')h.c.ensureRemoteProject=async()=>{throw new Error('Autosave conflict');};
  if(failure==='query')h.c.state.mainPrompt='x'.repeat(8001);
  if(failure==='bytes')h.c.state.mainPrompt='漢'.repeat(6000);
  await h.run('downloadRagExport()');assert.equal(h.downloads.length,0,failure);assert.equal(h.el('chooseZipExport').disabled,false);
  h.run('ragExportZip()');assert.ok(h.calls.includes('zip-dialog'));assert.ok(h.el('ragExportStatus').textContent);
 }
});

test('cancelling an in-flight retrieval prevents late downloads and permits another export',async()=>{
 const h=harness();let release;
 h.c.projectRequest=async(path,options)=>{
  if(!path.endsWith('/search'))return {ready:true,status:'ready',revision:8};
  await new Promise(resolve=>release=resolve);assert.equal(options.signal.aborted,true);return h.c.result;
 };
 const pending=h.run('downloadRagExport()');while(!release)await new Promise(resolve=>setImmediate(resolve));
 h.run('ragExportClose()');assert.equal(h.run('ragExportJob'),null);assert.equal(h.el('downloadRag').disabled,false);
 release();await pending;assert.equal(h.downloads.length,0);
});

test('overall timeout returns control without a file or a stuck download button',async()=>{
 const h=harness();let release;
 h.c.projectCapabilities=()=>new Promise(resolve=>release=resolve);
 const pending=h.run('downloadRagExport()');
 [...h.timers.values()].find(v=>v.delay===120000).fn();release({capabilities:{intelligentContextV1:true}});
 await pending;assert.equal(h.downloads.length,0);assert.match(h.el('ragExportStatus').textContent,/Try RAG again/);assert.equal(h.el('downloadRag').disabled,false);
});
