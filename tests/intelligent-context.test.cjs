/* No network, provider credentials, or paid API requests. Exercise the real
 * revision preparation and Venture submission functions, with server fixtures. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const test=require('node:test');
const contextSource=fs.readFileSync('web/intelligent-context.js','utf8');
const ventureSource=fs.readFileSync('web/venture.js','utf8');
const sendSource=ventureSource.slice(ventureSource.indexOf('async function ventureSend('),ventureSource.indexOf('async function venturePostPending('));

function harness(){
 const elements=new Map(),calls=[];
 const el=id=>{if(!elements.has(id))elements.set(id,{value:'Create all records',checked:false,hidden:false,dataset:{},textContent:'',close(){this.open=false;}});return elements.get(id);};
 const c={console,Blob,FormData,AbortController,Number,Error,Date,JSON,encodeURIComponent,setTimeout:()=>1,clearTimeout(){},$ :el,
  state:{title:'Original board',nodes:[{id:'one',prompt:'Keep exact values',attachments:[{name:'records.pdf',data:'original bytes'}]}],edges:[],projectCloud:{id:'project-1234567890123456',key:'secret-project-key',revision:8}},
  projectEpoch:1,projectPending:false,venture:{uid:'alice',epoch:1,open:true,current:{id:'conversation-a'},ready:true,sending:false,pending:null,runs:[],files:[],settings:{model:'model-fixture',runOptions:{codeInterpreter:true}}},
  escapeHTML:s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'),
  ventureActive:r=>r.status==='queued',ventureDictationBusy:()=>false,ventureSetNotice:s=>{c.notice=s;},ventureError:e=>e.message,venturePaintStatus(){},ventureSchedule(){},ventureRemember(){},ventureId:()=> 'request-fixture',
  ensureRemoteProject:async()=>c.state.projectCloud,
  consoleBoardFiles:async()=>{calls.push('legacy-export');return [{name:'MAIN_PROMPT.txt'}];},consoleFileText:async()=> 'Full original prompt',R:{zip:async()=>new Blob(['ZIP fixture'])},
  venturePostPending:async()=>{calls.push(c.venture.pending.form);c.venture.pending=null;},
  projectRequest:async()=>({revision:c.state.projectCloud.revision,status:'ready'}),projectResponse:async v=>v};
 el('ventureIncludeBoard').checked=true;
 vm.createContext(c);vm.runInContext(contextSource+'\n'+sendSource,c);
 const run=code=>vm.runInContext(code,c);
 run("ventureContext.capable=true;ventureContext.scope=ventureContextScope();");
 return {c,el,calls,run};
}
function submitted(h){const form=h.calls.find(v=>v instanceof FormData);assert.ok(form,'one accepted form');return {form,options:JSON.parse(form.get('options'))};}

test('Include board sends only an immutable reference; original board bytes stay unchanged',async()=>{
 const h=harness(),before=JSON.stringify(h.c.state);await h.run('ventureSend()');
 const {form,options}=submitted(h);
 assert.deepEqual(options.boardContext,{projectId:'project-1234567890123456',revision:8,mode:'adaptive'});
 assert.equal(form.has('file'),false);assert.equal('projectPrompt' in options,false);
 assert.equal(JSON.stringify(options).includes('secret-project-key'),false);
 assert.equal(h.calls.includes('legacy-export'),false);assert.equal(JSON.stringify(h.c.state),before);
});
test('text retrieval works with Code & files off; separate file uploads still require it',async()=>{
 const h=harness();h.c.venture.settings.runOptions.codeInterpreter=false;await h.run('ventureSend()');assert.ok(submitted(h).options.boardContext);
 const blocked=harness();blocked.c.venture.settings.runOptions.codeInterpreter=false;blocked.c.venture.files=[new Blob(['file'])];await blocked.run('ventureSend()');assert.equal(blocked.calls.length,0);assert.match(blocked.c.notice,/Code & files/);
});
test('Include board remains an explicit opt-in',async()=>{
 const h=harness();h.el('ventureIncludeBoard').checked=false;await h.run('ventureSend()');
 assert.equal('boardContext' in submitted(h).options,false);assert.equal(h.calls.includes('legacy-export'),false);
});
test('older servers preserve legacy board ZIP and prompt pathway with full-board indicator',async()=>{
 const h=harness();h.run('ventureContext.capable=false;ventureContextPaint()');assert.equal(h.el('ventureContextStatus').textContent,'Full board');
 await h.run('ventureSend()');const {form,options}=submitted(h);assert.equal(form.has('file'),true);assert.equal(options.projectPrompt,'Full original prompt');assert.equal('boardContext' in options,false);
});
test('full-source override changes retrieval policy without duplicating board uploads',async()=>{
 const h=harness();h.run("ventureContext.mode='full'");await h.run('ventureSend()');assert.equal(submitted(h).options.boardContext.mode,'full');assert.equal(submitted(h).form.has('file'),false);
});
test('edits arriving during autosave are saved again before pinning a revision',async()=>{
 const h=harness();let saves=0;h.c.ensureRemoteProject=async()=>{saves++;h.c.projectPending=saves===1;h.c.state.projectCloud.revision++;return h.c.state.projectCloud;};
 await h.run('ventureSend()');assert.equal(saves,2);assert.equal(submitted(h).options.boardContext.revision,10);
});
test('continuous edits, save failure, and account or board switches never silently upload legacy context',async()=>{
 for(const failure of ['continuous','save','account','board']){
  const h=harness(),before=h.el('ventureMessage').value;
  h.c.ensureRemoteProject=async()=>{if(failure==='continuous')h.c.projectPending=true;if(failure==='save')throw new Error('Autosave conflict');if(failure==='account')h.c.venture.epoch++;if(failure==='board')h.c.state={...h.c.state};return h.c.state.projectCloud;};
  await h.run('ventureSend()');assert.equal(h.calls.length,0,failure);assert.equal(h.el('ventureMessage').value,before,failure);assert.equal(h.c.venture.pending,null,failure);
 }
});
test('regeneration preserves the original referenced revision instead of today’s board',async()=>{
 const h=harness();h.c.retry={runId:'run-before',message:'Retry full record script',boardContext:{projectId:'project-original12345',revision:2,mode:'full'},attachments:[],previousRunId:null};
 h.c.ensureRemoteProject=()=>{throw new Error('Must not read current board');};await h.run('ventureSend(retry)');assert.deepEqual(submitted(h).options.boardContext,h.c.retry.boardContext);
});
test('context status rejects the wrong revision and never shows old-account results',async()=>{
 const h=harness();h.c.projectRequest=async()=>({revision:7,status:'ready'});await h.run('ventureContextRefresh()');assert.match(h.run('ventureContext.error'),/different revision/);
 h.c.projectRequest=async()=>{h.c.venture.epoch++;return {revision:8,status:'ready'};};h.run("ventureContext.error=''");await h.run('ventureContextRefresh()');assert.equal(h.run('ventureContext.status'),null);
});
test('full-source mode is reset when moving to another project or account',()=>{
 const h=harness();h.run("ventureContext.mode='full'");h.c.projectEpoch++;h.run('ventureContextPaint()');assert.equal(h.run('ventureContext.mode'),'adaptive');
 h.run("ventureContext.mode='full';ventureContextReset()");assert.equal(h.run('ventureContext.mode'),'adaptive');assert.equal(h.run('ventureContext.capable'),false);
});
test('run diagnostics distinguish provider-reported partial usage and escape source warnings',()=>{
 const h=harness();h.c.report={boardContext:{revision:8,mode:'adaptive'},contextMetrics:{retrievedCharacters:10000,retrievalOperations:3,complete:false,warnings:['<img src=x onerror=alert(1)>'],providerUsage:{input_tokens:1234,cached_tokens:1000,output_tokens:600,reasoning_tokens:200,complete:false,missingResponses:1}}};
 const html=h.run('ventureContextRunDetails(report)');assert.match(html,/Provider-reported tokens/);assert.match(html,/Usage is partial/);assert.match(html,/reasoning \(within output\)/);assert.match(html,/coverage is incomplete/);assert.ok(!html.includes('<img'));assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('official balance'));
});
test('rebuild queues the exact saved revision and ignores responses after an account switch',async()=>{
 const h=harness();let body;h.c.projectRequest=async(path,opts)=>{body=JSON.parse(opts.body);assert.match(path,/\/context$/);return {revision:8,status:'updating'};};
 await h.run('ventureContextRebuild()');assert.deepEqual(body,{action:'rebuild',revision:8});assert.equal(h.run('ventureContext.status.status'),'updating');assert.equal(h.run('ventureContext.rebuilding'),false);
 h.c.projectRequest=async()=>{h.c.venture.epoch++;h.run('ventureContextReset()');return {revision:8,status:'ready'};};
 await h.run('ventureContextRebuild()');assert.equal(h.run('ventureContext.status'),null);assert.equal(h.run('ventureContext.rebuilding'),false);
});
