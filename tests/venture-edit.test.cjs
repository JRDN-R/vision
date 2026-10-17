const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const test=require('node:test');

function fixture({persistentStorage=new Map()}={}){
 const storage=new Map(),elements=new Map();
 const element=id=>{if(!elements.has(id))elements.set(id,{value:'',hidden:true,disabled:false,textContent:'',innerHTML:'',classList:{toggle(){}},parentElement:{classList:{toggle(){}}},replaceChildren(){},focus(){}});return elements.get(id);};
 const c={console,Map,Set,FormData,Blob,URLSearchParams,crypto:require('node:crypto').webcrypto,
  $:element,sessionStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  localStorage:{getItem:k=>persistentStorage.get(k)??null,setItem:(k,v)=>persistentStorage.set(k,v),removeItem:k=>persistentStorage.delete(k)},
  normalizeRunOptions:o=>o,ventureParameterProfile:()=>({tools:{},efforts:['auto']}),
  accountUpdateGate(){},openVisionConsole(){},consoleArtifactBlob(){},
  clearTimeout(){},setTimeout(){},matchMedia:()=>({matches:false})};
 vm.createContext(c);
 vm.runInContext(fs.readFileSync('web/venture-ultra.js','utf8'),c);
 const source=fs.readFileSync('web/venture.js','utf8');
 assert.ok(source.includes('ventureInstall();$(\'runProjectBtn\').onclick=openVisionConsole;'));
 vm.runInContext(source.replace("ventureInstall();$('runProjectBtn').onclick=openVisionConsole;",'')+'\nglobalThis.state=venture;',c);
 Object.assign(c,{venturePaint(){c.venturePaintStatus();c.venturePaintAttachments();},ventureStopDictation(){},venturePaintModelPicker(){},ventureDictationBusy:()=>false,ventureIsLocal:()=>true,ventureLocalVisionReady:()=>false,escapeHTML:String,ventureSchedule(){}});
 Object.assign(c.state,{uid:'owner',epoch:1,selection:1,ready:true,current:{id:'old-conversation'},settings:{model:'qwen3.8-local',runOptions:{}}});
 c.element=element;c.storage=storage;c.persistentStorage=persistentStorage;
 c.restorePending=()=>{c.ventureRemember('pending-old-conversation',{cid:'old-conversation',clientRequestId:'original-request'});c.state.pending=c.ventureRecall('pending-old-conversation');};
 return c;
}

test('copy check persists for two seconds and repeated clicks restart the timer',()=>{
 const c=fixture(),timers=new Map();let id=0;
 c.setTimeout=(fn,delay)=>{assert.equal(delay,2000);timers.set(++id,fn);return id;};c.clearTimeout=id=>timers.delete(id);
 for(const action of ['copy','copy-prompt']){
  const button={dataset:{vAction:action},setAttribute(k,v){this[k]=v;}};
  c.ventureCopyFeedback(button);assert.equal(button.dataset.copyFeedback,'true');assert.match(button.innerHTML,/m5 12/);
  c.ventureCopyFeedback(button);assert.equal(timers.size,1);
  [...timers.values()][0]();timers.clear();assert.equal(button.dataset.copyFeedback,undefined);assert.equal(button['aria-label'],action==='copy'?'Copy response':'Copy prompt');
 }
});

test('editing restores prompt, removes following turns and retains selected model',async()=>{
 const c=fixture();const first={runId:'first',status:'completed'},edit={runId:'edit',status:'completed',message:'Original prompt',projectPrompt:'Board context'},later={runId:'later',status:'completed'};
 c.state.runs=[first,edit,later];c.state.settings.model='selected-model';
 c.ventureResizeComposer=()=>{};c.ventureLoadHistory=()=>{};c.venturePaint=()=>{};
 c.ventureJSON=async(path,method,body)=>{assert.equal(path,'/venture/conversations/old-conversation/truncate');assert.equal(method,'POST');assert.equal(body.runId,'edit');return {retainedRunIds:['first'],conversation:c.state.current};};
 await c.ventureEditPrompt(edit);
 assert.equal(c.state.runs.length,1);assert.equal(c.state.runs[0].runId,'first');assert.equal(c.element('ventureMessage').value,'Original prompt');assert.equal(c.state.settings.model,'selected-model');assert.equal(c.state.editDraft.projectPrompt,'Board context');assert.equal(c.state.sending,false);
});

test('editing the oldest loaded turn restores its unloaded predecessor for resend',async()=>{
 const c=fixture(),edit={runId:'edit',status:'completed',message:'Edit me'};c.state.runs=[edit];
 c.ventureResizeComposer=()=>{};c.ventureLoadHistory=()=>{};c.venturePaint=()=>{};
 c.ventureJSON=async()=>({retainedRunIds:['older'],previousRun:{runId:'older',status:'completed'},conversation:c.state.current});
 await c.ventureEditPrompt(edit);assert.equal(c.state.runs.at(-1).runId,'older');
});

test('editing is blocked during active or unconfirmed work and retains text on network failure',async()=>{
 const c=fixture(),edit={runId:'edit',status:'completed',message:'Keep this prompt'};
 c.ventureResizeComposer=()=>{};c.ventureLoadHistory=()=>{};c.venturePaint=()=>{};
 c.state.runs=[edit,{runId:'active',status:'in_progress'}];let calls=0;c.ventureJSON=async()=>{calls++;throw Error('Offline');};
 await c.ventureEditPrompt(edit);assert.equal(calls,0);
 c.state.runs=[edit];c.state.pending={};await c.ventureEditPrompt(edit);assert.equal(calls,0);
 c.state.pending=null;await c.ventureEditPrompt(edit);assert.equal(calls,1);assert.equal(c.element('ventureMessage').value,'Keep this prompt');assert.equal(c.state.runs[0],edit);assert.equal(c.state.sending,false);
});
