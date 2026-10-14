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

test('restored submission without upload gets a safe escape, not an automatic retry or erase',async()=>{
 const c=fixture();c.restorePending();let gets=0;
 c.ventureJSON=async(path,method)=>{assert.equal(path,'/projects/old-conversation/runs');assert.equal(method,undefined);gets++;return {runs:[]};};
 await c.ventureCheckPending();assert.equal(gets,1);
 assert.equal(c.state.pending.clientRequestId,'original-request');
 assert.match(c.state.notice,/no longer has its upload/);
 assert.equal(c.element('venturePendingNew').hidden,false);
 c.ventureAddFiles([{name:'photo.jpg',size:20}]);assert.equal(c.state.files.length,0);
 assert.match(c.state.notice,/Start new venture/);
 await c.ventureNew();
 assert.equal(c.state.pending,null);assert.equal(c.state.current,null);
 assert.equal(c.ventureRecall('pending-old-conversation').clientRequestId,'original-request','old request remains recoverable if accepted late');
 c.ventureAddFiles([{name:'photo.jpg',size:20}]);assert.equal(c.state.files.length,1);
 assert.equal(c.element('ventureSend').disabled,false);
});

test('status check finds accepted work outside the conversation page without submitting again',async()=>{
 const c=fixture();c.restorePending();
 c.ventureJSON=async()=>({runs:[{clientRequestId:'original-request',runId:'accepted',status:'queued',createdAt:'2026-10-12T00:00:00Z'}]});
 await c.ventureCheckPending();
 assert.equal(c.state.pending,null);assert.equal(c.state.runs[0].runId,'accepted');
 assert.equal(c.ventureRecall('pending-old-conversation'),null);
 assert.equal(c.element('venturePendingNew').hidden,true);
 assert.equal(c.element('ventureStop').hidden,false);
});

test('status replies cannot overwrite a different account or conversation',async()=>{
 for(const change of ['epoch','selection']){
  const c=fixture();c.restorePending();let resolve;
  c.ventureJSON=()=>new Promise(r=>resolve=r);
  const request=c.ventureCheckPending();c.state[change]++;
  resolve({runs:[{clientRequestId:'original-request',runId:'accepted'}]});await request;
  assert.equal(c.state.runs.length,0);assert.equal(c.state.pending.clientRequestId,'original-request');
  assert.equal(c.state.notice,'');
 }
});

test('offline checks preserve pending work and in-memory retries retain their original form',async()=>{
 const c=fixture();c.restorePending();
 c.ventureJSON=async()=>{throw Error('Offline');};await c.ventureCheckPending();
 assert.equal(c.state.notice,'Offline');assert.ok(c.state.pending);
 const form=new FormData();form.append('options',JSON.stringify({clientRequestId:'original-request'}));c.state.pending.form=form;
 c.ventureFetch=async(path,options)=>{assert.equal(options.body,form);assert.equal(path,'/projects/old-conversation/runs');throw Error('Offline');};
 await c.ventureCheckPending();assert.equal(c.state.pending.form,form);
 assert.equal(c.element('venturePendingNew').hidden,true);
 assert.equal(c.element('ventureCheck').textContent,'Retry same submission');
});

test('an active upload cannot switch conversations or change attachments',async()=>{
 const c=fixture();c.restorePending();c.state.sending=true;
 c.ventureJSON=async()=>{throw Error('Should not check during upload');};
 await c.ventureCheckPending();await c.ventureNew();c.ventureAddFiles([{name:'new.png',size:5}]);
 assert.equal(c.state.current.id,'old-conversation');assert.ok(c.state.pending);assert.equal(c.state.files.length,0);
});

test('closing the browser restores the selected server job and reconciles its request ID',async()=>{
 const persistentStorage=new Map(),original=fixture({persistentStorage});
 original.ventureRemember('selection','old-conversation');
 original.ventureRemember('pending-old-conversation',{cid:'old-conversation',clientRequestId:'original-request'});
 original.ventureRemember('funding-return',{temporary:'session-only'});
 const reopened=fixture({persistentStorage}); // a new page session, with fresh sessionStorage
 assert.equal(reopened.ventureRecall('selection'),'old-conversation');
 assert.equal(reopened.ventureRecall('pending-old-conversation').clientRequestId,'original-request');
 assert.equal(reopened.ventureRecall('funding-return'),null,'only IDs, not unrelated session data, survive');
 reopened.state.current=null;reopened.state.runs=[];
 reopened.ventureClosePopover=()=>{};reopened.venturePaintSettings=()=>{};reopened.venturePaint=()=>{};reopened.ventureSchedule=()=>{};
 const calls=[];
 reopened.ventureJSON=async(path,method)=>{
  calls.push([path,method]);
  if(path==='/venture/conversations/old-conversation')return {conversation:{id:'old-conversation',title:'Ongoing answer',settings:{model:'qwen3.8-local',runOptions:{}},revision:1},runs:[],cursor:0,nextCursor:null};
  if(path==='/projects/old-conversation/runs')return {runs:[{clientRequestId:'original-request',runId:'accepted',status:'in_progress',createdAt:'2026-10-14T12:00:00Z',text:'Saved so far.'}]};
  throw Error('Unexpected request: '+path);
 };
 await reopened.ventureSelect(reopened.ventureRecall('selection'));
 await new Promise(resolve=>setImmediate(resolve)); // let automatic server reconciliation settle
 assert.equal(reopened.state.current.id,'old-conversation');
 assert.equal(reopened.state.runs[0].runId,'accepted');
 assert.equal(reopened.state.runs[0].text,'Saved so far.');
 assert.equal(reopened.state.pending,null);
 assert.equal(reopened.ventureRecall('pending-old-conversation'),null);
 assert.ok(calls.some(([path])=>path==='/projects/old-conversation/runs'));
 assert.ok(calls.every(([,method])=>method===undefined),'reopening must never resubmit a model request');
 reopened.state.uid='another-account';
 assert.equal(reopened.ventureRecall('selection'),null,'another account cannot inherit the last selected conversation');
 reopened.state.uid='owner';
 reopened.ventureRemember('selection',null);
 assert.equal(fixture({persistentStorage}).ventureRecall('selection'),null,'new conversation clears durable selection');
});

test('browser storage restrictions fall back to the existing session behavior',()=>{
 const c=fixture();
 Object.defineProperty(c,'localStorage',{get(){throw Error('Storage disabled');},configurable:true});
 c.ventureRemember('selection','old-conversation');
 assert.equal(c.ventureRecall('selection'),'old-conversation');
 c.ventureRemember('selection',null);
 assert.equal(c.ventureRecall('selection'),null);
});
