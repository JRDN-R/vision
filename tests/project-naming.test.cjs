'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/project-naming.js','utf8');
function fixture(){
 const elements={projectTitle:{}},c={state:{title:'Untitled timeline',settings:{autoNameProject:true},nodes:[{id:'first',kind:'image',attachments:[]}]},busy:false,ioBusy:false,projectLoading:false,projectAccountSwitching:false,cloudAuth:{},projectIsTemporary:()=>false,uid:()=> 'abc',markDirty(){},renderActivity(){},projectCapabilities:async()=>({capabilities:{projectAutoNamingV1:true}}),flushProjectSave:async()=>{},ensureProjectIdentity:()=>({id:'project_test_name'}),projectResponse:async r=>r,projectRequest:async()=>({requestId:'name-abc',status:'complete',title:'Church Renovation'}),boardAsyncContext:()=>({project:c.state}),boardAsyncCurrent:ctx=>ctx.project===c.state,checkpoint(){},$:id=>elements[id],renderProjectMenu(){},toast(){},setInterval(){}};
 vm.createContext(c);vm.runInContext(source,c);return {c,elements,run:code=>vm.runInContext(code,c)};
}
test('auto naming runs once after a file and preserves a manual name',async()=>{
 const f=fixture();await f.run('refreshProjectNaming()');assert.equal(f.c.state.title,'Church Renovation');assert.equal(f.c.state.settings.autoNameDone,true);
 assert.equal(f.run('projectNamingEligible()'),false);
 f.c.state.settings.autoNameDone=false;f.c.state.title='My title';assert.equal(f.run('projectNamingEligible()'),false);
 f.c.state.title='Untitled timeline';f.c.state.settings.autoNameManual=true;assert.equal(f.run('projectNamingEligible()'),false);
});
test('late results cannot rename switched projects or a title typed during inference',async()=>{
 for(const change of ['manual','project','toggle']){
  const f=fixture();let release,started;const ready=new Promise(resolve=>started=resolve);
  f.c.projectRequest=()=>new Promise(resolve=>{release=resolve;started();});const pending=f.run('refreshProjectNaming()');await ready;
  if(change==='project')f.c.state={title:'Other project',settings:{},nodes:[]};
  if(change==='manual'){f.c.state.title='My title';f.c.state.settings.autoNameManual=true;}
  if(change==='toggle')f.c.state.settings.autoNameProject=false;
  release({requestId:'name-abc',status:'complete',title:'Wrong name'});await pending;assert.notEqual(f.c.state.title,'Wrong name');
 }
});
test('awaiting preparation and persisted settings survive validation',async()=>{
 const f=fixture();f.c.state.nodes[0].pcVideo={status:'working'};await f.run('refreshProjectNaming()');assert.equal(f.c.state.settings.autoNaming.status,'awaiting');assert.equal(f.c.state.title,'Untitled timeline');
 const normalized=f.run('normalizeProjectNamingSettings(state.settings)');assert.equal(normalized.autoNameProject,true);assert.equal(normalized.autoNaming.nodeId,'first');
 f.c.state.settings.autoNameProject=false;assert.equal(f.run('projectNamingEligible()'),false);
});
