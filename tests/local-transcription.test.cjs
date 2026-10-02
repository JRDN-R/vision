/* Exercise the real queue, persistence validation and project auth helper without network or API charges. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const base=fs.readFileSync(path.join(__dirname,'../web/base.js'),'utf8');
const projects=fs.readFileSync(path.join(__dirname,'../web/projects.js'),'utf8');
const queue=base.slice(base.indexOf('// Durable, sequential ASR:'),base.indexOf('async function prepareTranscriptionQueue('));
const projectRequests=projects.slice(projects.indexOf('async function projectRequest('),projects.indexOf('async function projectCapabilities('));
const section=(start,done=false)=>({start,end:start+10,mimeType:'audio/mpeg',audioData:done?null:'data:audio/mpeg;base64,YXVkaW8=',text:done?'[00:00:00.000] Already complete.':null,done});
const source={id:'source-1',name:'speech.mp3',mime:'audio/mpeg',data:'data:audio/mpeg;base64,YXVkaW8='};
const makeJob=(extra={})=>({id:'job-1',sourceId:source.id,sourceName:source.name,sourceKind:'audio',provider:'local',status:'waiting',sections:[section(0),section(10)],...extra});
function fixture(job=makeJob()){
 const state={nodes:[{id:'node-1',attachments:[{...source}],transcriptionJobs:[job]}],settings:{transcriptionProvider:'local'},projectCloud:{id:'project-test',key:'test-project-secret-012345678901234567890',backendUrl:'https://pc.example.ts.net',revision:1}};
 const calls=[],timers=[],transcripts=[],stats={gemini:0,released:0,saved:0,registered:0},control={ready:true,post:()=>({id:'remote-1',status:'queued'}),get:()=>({id:'remote-1',status:'processing',phase:'Transcribing',progress:20})};let serial=0;
 const context={state,history:[],future:[],busy:false,ioBusy:false,selected:null,navigator:{onLine:true},cloudConfig:{kind:'private-pc',backendUrl:state.projectCloud.backendUrl},sessionApiKey:'test-key',URL,Headers,Blob,AbortController,DOMException,TypeError,Date,Math,JSON,Number,String,Set,
  window:{JEWCredential:{key:'test-key'},JEWTranscription:{requestAudio:async()=>{stats.gemini++;return '[00:00:10.000] Paid provider explicitly selected.';}}},
  $:()=>null,videoFile:()=>false,uid:()=>`new-request-${++serial}`,checkpoint:()=>{},markDirty:()=>stats.saved++,refreshNodeAttachments:()=>{},renderAttachments:()=>{},toast:()=>{},recordActivity:()=>{},
  bytesFromDataURL:()=>new Uint8Array([1]),videoTranscriptClock:text=>text,saveTranscript:(n,s,text,status)=>transcripts.push({text,status}),storeVideoTranscript:(n,s,text,status)=>transcripts.push({text,status}),releaseCompletedSourceAudio:()=>stats.released++,
  setTimeout:(fn,delay)=>{timers.push({fn,delay});return timers.length;},clearTimeout:()=>{},
  ensureProjectIdentity:()=>state.projectCloud,projectCapabilities:async()=>{if(control.healthError)throw control.healthError;return{capabilities:{localTranscription:control.ready},localTranscription:{ready:control.ready,maxRequestBytes:104857600}};},
  ensureRemoteProject:async()=>{stats.registered++;return state.projectCloud;},projectBackup:async()=>stats.saved++,
  cloudFetch:async(route,options)=>{calls.push({route,options});assert.equal(options.headers.get('X-Vision-Project-Key'),state.projectCloud.key);assert.ok(route.startsWith('/projects/'+state.projectCloud.id+'/transcriptions'));const data=options.method==='POST'?await control.post(JSON.parse(options.body)):await control.get();return{ok:true,status:options.method==='POST'?202:200,json:async()=>data};}
 };
 vm.createContext(context);vm.runInContext(projectRequests+'\n'+queue,context);
 const run=code=>vm.runInContext(code,context);
 return{state,job,calls,stats,control,timers,transcripts,run,context,pump:()=>run('pumpTranscriptionQueue()'),due:()=>{for(const j of state.nodes[0].transcriptionJobs)j.nextAttemptAt=0;}};
}
(async()=>{
 // Legacy queues must not resume a billed provider after migration.
 {
  const f=fixture();f.context.raw=[makeJob({provider:undefined,status:'error',error:'Google billing denied',sections:[section(0,true),section(10)]})];
  const restored=f.run('validateTranscriptionJobs(raw,state.nodes[0].attachments)')[0];
  assert.equal(restored.provider,'local');assert.equal(restored.status,'waiting');assert.equal(restored.error,null);assert.equal(restored.sections[0].text,'[00:00:00.000] Already complete.');
 }
 // Missing installation is actionable and never silently sends audio to Gemini.
 {
  const f=fixture();f.control.ready=false;await f.pump();assert.equal(f.job.status,'error');assert.match(f.job.error,/InstallLocalTranscription/);assert.equal(f.calls.length,0);assert.equal(f.stats.gemini,0);assert.equal(f.stats.released,0);
 }
 // A failed connection before acceptance must not look like processing at 0%.
 {
  const f=fixture();f.control.healthError=Object.assign(new Error('Processing server unavailable.'),{code:'VISION_SERVER_UNAVAILABLE'});
  await f.pump();assert.equal(f.job.status,'waiting');assert.equal(f.job.connectionError,true);assert.equal(f.calls.length,0);assert.ok(!f.job.submitted);
  assert.match(f.run('queueStatusText(state.nodes[0].transcriptionJobs[0])'),/Cannot reach PC from this device.*Audio has not reached the PC/);
  assert.ok(f.run('canRetryTranscription(state.nodes[0].transcriptionJobs[0])'));
  f.control.healthError=null;f.run("retryTranscriptionQueue('job-1')");assert.equal(f.job.nextAttemptAt,0);await f.pump();
  assert.equal(f.job.remoteId,'remote-1');assert.equal(f.job.connectionError,false);assert.equal(f.stats.gemini,0);
 }
 // The health-check AbortController is a connection timeout, not a terminal job error.
 {
  const f=fixture();f.control.healthError=new DOMException('The operation was aborted.','AbortError');await f.pump();
  assert.equal(f.job.status,'waiting');assert.equal(f.job.connectionError,true);assert.ok(f.job.nextAttemptAt>Date.now());assert.equal(f.calls.length,0);
 }
 // Entire manifest acceptance, saved remote identity, reopen, one matching result per pending section.
 {
  const f=fixture(makeJob({preservedSections:true,sections:[section(0,true),section(10),section(20)]}));await f.pump();
  const posted=JSON.parse(f.calls[0].options.body);assert.equal(f.stats.registered,1);assert.equal(posted.clientRequestId,'job-1');assert.deepEqual(posted.sections.map(s=>s.start),[10,20]);assert.equal(f.stats.released,0);assert.equal(f.job.remoteId,'remote-1');
  f.context.raw=JSON.parse(JSON.stringify([f.job]));const reopened=f.run('validateTranscriptionJobs(raw,state.nodes[0].attachments)')[0];assert.equal(reopened.remoteId,'remote-1');assert.equal(reopened.backendUrl,f.state.projectCloud.backendUrl);assert.equal(reopened.remoteRequestId,'job-1');f.state.nodes[0].transcriptionJobs=[reopened];
  f.control.get=()=>({id:'remote-1',status:'complete',result:{sections:[{start:10,end:20,text:'[00:00:10.000] Second.'},{start:20,end:30,text:'[00:00:20.000] Third.'}]}});
  await f.pump();assert.equal(f.calls.filter(call=>call.options.method==='POST').length,1);assert.equal(f.transcripts[0].text,'[00:00:00.000] Already complete.\n\n[00:00:10.000] Second.\n\n[00:00:20.000] Third.');assert.equal(f.stats.released,1);assert.equal(f.stats.gemini,0);assert.equal(f.state.nodes[0].transcriptionJobs.length,0);
 }
 // Progress polling does not repeatedly autosave the pending audio payload.
 {
  const f=fixture();await f.pump();const saves=f.stats.saved;f.due();await f.pump();assert.equal(f.job.progress,20);assert.equal(f.stats.saved,saves);
 }
 // A lost POST response retains identity and payload for server deduplication.
 {
  const f=fixture();let sent;f.control.post=payload=>{sent=payload;const error=new Error('Processing server unavailable.');error.code='VISION_SERVER_UNAVAILABLE';throw error;};await f.pump();assert.equal(f.job.status,'waiting');assert.equal(f.job.submitted,true);assert.ok(f.job.nextAttemptAt>Date.now());assert.ok(f.timers.some(timer=>timer.delay>=14000));
  assert.match(f.run('queueStatusText(state.nodes[0].transcriptionJobs[0])'),/Checking whether the PC accepted/);assert.doesNotMatch(f.run('queueStatusText(state.nodes[0].transcriptionJobs[0])'),/Audio has not reached/);
  f.context.raw=JSON.parse(JSON.stringify([f.job]));f.state.nodes[0].transcriptionJobs=f.run('validateTranscriptionJobs(raw,state.nodes[0].attachments)');f.control.post=payload=>{assert.deepEqual(payload,sent);return{id:'remote-1',status:'queued'};};await f.pump();assert.equal(f.state.nodes[0].transcriptionJobs[0].remoteId,'remote-1');assert.equal(f.stats.gemini,0);
 }
 // Losing the connection after acceptance retains the receipt and reconnects without re-uploading.
 {
  const f=fixture();await f.pump();f.due();f.control.get=()=>{throw Object.assign(new Error('Processing server unavailable.'),{code:'VISION_SERVER_UNAVAILABLE'});};await f.pump();
  assert.equal(f.job.connectionError,true);assert.match(f.run('queueStatusText(state.nodes[0].transcriptionJobs[0])'),/PC may still be processing/);
  f.control.get=()=>({status:'processing',phase:'Transcribing',progress:40});f.run('resumePCTranscriptionQueue()');assert.equal(f.job.nextAttemptAt,0);await f.pump();
  assert.equal(f.job.connectionError,false);assert.equal(f.job.progress,40);assert.equal(f.calls.filter(call=>call.options.method==='POST').length,1);assert.equal(f.stats.gemini,0);
 }
 // Slow/polling first job cannot prevent the next job being accepted.
 {
  const f=fixture();f.state.nodes[0].attachments.push({...source,id:'source-2'});const second=makeJob({id:'job-2',sourceId:'source-2'});f.state.nodes[0].transcriptionJobs.push(second);await f.pump();await f.pump();assert.equal(f.calls.filter(call=>call.options.method==='POST').length,2);assert.equal(second.remoteRequestId,'job-2');
 }
 // Terminal error retries have fresh IDs; accepted jobs cannot silently switch provider.
 {
  const f=fixture();await f.pump();f.run("changeQueuedProvider('job-1','gemini')");assert.equal(f.job.provider,'local');f.due();f.control.get=()=>({status:'error',error:'Local model stopped'});await f.pump();assert.equal(f.job.remoteTerminal,true);f.run("retryTranscriptionQueue('job-1')");assert.equal(f.job.remoteId,null);assert.notEqual(f.job.remoteRequestId,'job-1');
  f.run("changeQueuedProvider('job-1','gemini')");assert.equal(f.job.provider,'gemini');await f.pump();assert.equal(f.stats.gemini,1);
 }
 // Invalid complete response cannot discard source audio or completed text.
 {
  const f=fixture();await f.pump();f.due();f.control.get=()=>({status:'complete',result:{sections:[{start:100,end:110,text:'Wrong source.'}]}});await f.pump();assert.equal(f.job.status,'error');assert.equal(f.stats.released,0);assert.ok(f.job.sections.every(s=>s.audioData));assert.equal(f.transcripts.length,0);
 }
 console.log('Local transcription lifecycle, migration, authorization, deduplication, retry and no-paid-fallback checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
