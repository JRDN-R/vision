/* Queue notices share the existing toast and never infer success from a count drop. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const base=fs.readFileSync('web/base.js','utf8');
const part=(start,end)=>base.slice(base.indexOf(start),base.indexOf(end,base.indexOf(start)));
const scripts=part('function toast(','function undo(')+part('function notifyActivityQueue(','function renderActivity(');
function harness(){
 let serial=0;const timers=new Map(),messages=[];
 const element={className:'',_text:'',set textContent(value){this._text=value;messages.push(value);},get textContent(){return this._text;}};
 element.classList={contains:value=>element.className.split(' ').includes(value)};
 const context={state:{nodes:[]},accountAuthEpoch:1,activityNoticeTimer:null,activityNoticeContext:null,toastTimer:null,
  setTimeout:(fn,delay)=>{const id=++serial;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),$:()=>element,
  positionToast(){context.positioned=(context.positioned||0)+1;}};
 vm.createContext(context);vm.runInContext(scripts,context);
 const tick=()=>{for(const[id,timer]of [...timers])if(timer.delay===180){timers.delete(id);timer.fn();}};
 const render=(value={})=>{context.activity={entries:[],active:[],videos:[],documents:[],runs:[],...value};vm.runInContext('notifyActivityQueue(activity)',context);};
 const toast=(message,error=false)=>{context.message=message;context.error=error;vm.runInContext('toast(message,error)',context);};
 return{context,messages,element,timers,tick,render,toast};
}
const entry=(id,status='waiting')=>({source:{id},job:{id:'job-'+id,sourceId:id,status}});
const h=harness();h.render();h.tick();assert.deepEqual(h.messages,[]);
const tasks={entries:[entry('speech'),entry('approval','approval_waiting')],active:[{sourceId:'youtube'}],videos:[{pcVideo:{sourceId:'video',status:'working'}}]};
h.render(tasks);h.render(tasks);h.tick();assert.deepEqual(h.messages,['4 jobs queued'],'batch/polling emits one short notice');
assert.equal(h.element.className,'show');assert.equal(h.context.positioned,1,'toast asks layout to position the banner');
assert.ok([...h.timers.values()].some(timer=>timer.delay===3500),'banner auto-dismisses');
// Approval and media-to-transcription handoffs preserve their source identities.
tasks.entries[1].job.status='waiting';tasks.active=[];tasks.entries.push(entry('youtube'));
h.render(tasks);h.tick();assert.equal(h.messages.length,1);
// Failed jobs, cancellation/removal, and a completed run never manufacture success.
h.render({entries:[entry('speech','error')]});h.tick();h.render();h.tick();assert.equal(h.messages.length,1);
// Restored queues and account switches are baselines, not newly submitted work.
h.context.state={nodes:[]};h.render(tasks);h.tick();assert.equal(h.messages.length,1);
h.context.accountAuthEpoch++;h.render(tasks);h.tick();assert.equal(h.messages.length,1);
// A notice waiting to appear cannot cross an account or project boundary.
h.render({...tasks,documents:[{source:{id:'document'},job:{status:'queued'}}]});h.context.accountAuthEpoch++;h.tick();assert.equal(h.messages.length,1);
h.render(tasks);h.render({...tasks,documents:[{source:{id:'another-document'},job:{status:'queued'}}]});h.context.state={nodes:[]};h.tick();assert.equal(h.messages.length,1);
// A caller's specific queued/completion/error toast wins over the generic notice.
h.render();h.render({entries:[entry('fresh')]});h.toast('Audio queued for transcription.');h.tick();assert.equal(h.messages.at(-1),'Audio queued for transcription.');assert.equal(h.messages.length,2);
h.render({entries:[entry('fresh'),entry('new')]});h.toast('Upload needs attention.',true);h.tick();assert.equal(h.messages.at(-1),'Upload needs attention.');
h.render({entries:[entry('fresh'),entry('new'),entry('later')]});h.tick();assert.equal(h.messages.at(-1),'Upload needs attention.','queue notice cannot cover an active error');
// Completion notifications originate only in the actual successful worker path.
const video=fs.readFileSync('web/video-preview.js','utf8');
const worker=video.slice(video.indexOf('async function workPCVideo('),video.indexOf('function pcVideoArtifactSuffix('));
async function completedVideo({transcription=false,status='complete'}={}){
 const node={id:'video',title:'Clip.mov',pcVideo:{id:'accepted',requestId:'same-request',sourceId:'source'},transcriptionJobs:transcription?[{sourceId:'source'}]:[]},messages=[];
 const project={nodes:[node]},controller=new AbortController(),context={state:project,accountAuthEpoch:1,DOMException,pcVideoCurrent:()=>true,
  pcVideoRequest:async(_n,suffix)=>suffix==='/result'?{}:{status},applyPCVideoResult:async()=>{},pcVideoUpdate(_n,fields){Object.assign(node.pcVideo,fields);},
  refreshNodeAttachments(){},updateSequence(){},recordActivity(){},toast:message=>messages.push(message),pcVideoWorkers:new Map(),schedulePCVideos(){},runtime:{project,n:node,controller}};
 vm.createContext(context);vm.runInContext(worker,context);await vm.runInContext('workPCVideo(runtime)',context);return{node,messages};
}
(async()=>{
 assert.deepEqual((await completedVideo()).messages,['Video ready: Clip.mov']);
 assert.deepEqual((await completedVideo({transcription:true})).messages,[],'preview handoff waits for the transcript completion toast');
 assert.deepEqual((await completedVideo({status:'cancelled'})).messages,[],'cancelled processing is not announced as complete');
 assert.deepEqual((await completedVideo({status:'error'})).messages,[],'failed processing is not announced as complete');
 console.log('PASS: shared temporary queue notices, batch/polling and handoff deduplication, restore/account isolation, error priority, and explicit completion without false success.');
})().catch(error=>{console.error(error);process.exitCode=1;});
