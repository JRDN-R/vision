'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/node-activity.js','utf8');
function fixture(){
 const elements={},make=()=>({dataset:{},span:{},setAttribute(k,v){this[k]=v;},querySelector(){return this.span;},addEventListener(){},remove(){this.removed=true;}});
 const c={state:{nodes:[{id:'a',title:'First',attachments:[{id:'source-a'}]},{id:'b',title:'Second',attachments:[]}],settings:{}},document:{createElement:make,documentElement:{dataset:{workspacePage:'vision'}}},$:id=>elements[id],queueStatusText:j=>j.phase||j.status,openActivity:id=>{c.opened=id;}};
 for(const n of c.state.nodes)elements['node-'+n.id]={badge:null,querySelector(){return this.badge?.removed?null:this.badge;},appendChild(b){this.badge=b;}};
 elements.queueIndicator=make();
 vm.createContext(c);vm.runInContext(source,c);
 c.activity={entries:[{n:c.state.nodes[0],job:{sourceId:'source-a',status:'working',sourceName:'Audio'}}],active:[],videos:[],documents:[{n:c.state.nodes[1],source:{name:'Report'},job:{status:'queued'}}],visual:[],runs:[]};
 return {c,elements,render:()=>vm.runInContext('renderNodeActivities(activity)',c)};
}
test('separate source-node badges show working and awaiting and open that node',()=>{
 const f=fixture();f.render();assert.equal(f.elements['node-a'].badge.dataset.state,'working');assert.equal(f.elements['node-b'].badge.dataset.state,'awaiting');
 assert.equal(f.elements['node-b'].badge.span.textContent,'1 Awaiting');assert.equal(f.elements.queueIndicator.hidden,true);
 f.elements['node-b'].badge.onclick({stopPropagation(){}});assert.equal(f.c.opened,'b');
 f.c.activity.entries=[];f.render();assert.equal(f.elements['node-a'].badge.removed,true);
});
test('source ownership survives media handoff; naming stays on its triggering node',()=>{
 const f=fixture();f.c.activity.entries=[];f.c.activity.active=[{sourceId:'source-a',title:'Video',detail:'Queued'}];
 f.c.state.settings={autoNameProject:true,autoNaming:{nodeId:'a',status:'awaiting'}};f.render();
 assert.equal(f.elements['node-a'].badge.span.textContent,'2 Awaiting');
 f.c.activity.active[0].detail='Extracting';f.render();assert.equal(f.elements['node-a'].badge.dataset.state,'working');
 f.c.state.settings.autoNameProject=false;f.render();assert.equal(f.elements['node-a'].badge.span.textContent,'1 Activity');
});
test('global tasks remain accessible without a board node and failed tasks stay visible',()=>{
 const f=fixture();f.c.activity.active=[{title:'Export',detail:'Packaging'}];f.c.activity.documents[0].job.status='paused';f.render();
 assert.equal(f.elements.queueIndicator.hidden,false);assert.equal(f.elements.queueIndicator.span.textContent,'1');assert.equal(f.elements['node-b'].badge.dataset.state,'error');
});
