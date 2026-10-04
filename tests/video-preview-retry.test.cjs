/* Recover temporary upload failures without duplicating accepted server work. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/video-preview.js'),'utf8');
const workerSource=source.slice(0,source.indexOf('\nlet pcVideoStorageContext='));
const onlineSource=source.slice(source.indexOf("window.addEventListener('online'"),source.indexOf("window.addEventListener('beforeunload'"));
const networkError=()=>Object.assign(new Error('This device cannot reach FUPCJ Server. It may be offline.'),{code:'VISION_SERVER_UNAVAILABLE',retryable:true});
const notFound=()=>Object.assign(new Error('No accepted upload'),{status:404});
function harness({id='',file=true}={}){
 const node={id:'node-1',title:'sample.mov',attachments:[],pcVideo:{requestId:'request-original',id,status:'waiting',error:'',progress:0}};
 let now=1000;
 const listeners={},context={state:{nodes:[node]},history:[],future:[],accountAuthEpoch:1,AbortController,DOMException,Blob,FormData,
  Date:{now:()=>now},Map,Math,String,Number,console,setTimeout:()=>1,clearTimeout(){},markDirty(){},renderActivity(){},
  projectCapabilities:async()=>({capabilities:{uploadedMedia:true}}),ensureRemoteProject:async()=>{},
  cleanupPCVideoPlayers(){},window:{addEventListener:(name,callback)=>listeners[name]=callback}};
 vm.createContext(context);vm.runInContext(workerSource+'\n'+onlineSource+`\ninstallPCVideoControls=()=>{};pcVideoSetPoster=()=>{};
 this.api={pump:pumpPCVideos,work:workPCVideo,retry:retryPCVideo,resume:pcVideoResume,uploads:pcVideoUploads,retries:pcVideoRetryAt,workers:pcVideoWorkers,setRequest:fn=>{pcVideoRequest=fn;}};`,context);
 if(file){const blob=new Blob(['video'],{type:'video/quicktime'});blob.name='sample.mov';context.api.uploads.set(node.pcVideo.requestId,blob);}
 return {node,context,api:context.api,listeners,advance:ms=>{now+=ms;},runtime:()=>({project:context.state,n:node,controller:new AbortController()})};
}
async function settle(h){for(let i=0;i<12&&h.api.workers.size;i++)await new Promise(resolve=>setImmediate(resolve));assert.equal(h.api.workers.size,0,'worker completed');}
async function main(){
 // The server accepted a POST, but the connection dropped before its response.
 const h=harness();let accepted=false,posts=0,receipts=0,calls=0;
 h.api.setRequest(async(_node,suffix,options)=>{calls++;
  if(suffix.startsWith('/request/')){receipts++;assert.equal(suffix,'/request/request-original');if(!accepted)throw notFound();return {id:'accepted-media'};}
  if(options.method==='POST'){posts++;assert.equal(options.body.get('requestId'),'request-original');accepted=true;throw networkError();}
  return {status:'processing',phase:'Creating preview',progress:30};
 });
 await h.api.pump();await settle(h);
 assert.equal(h.node.pcVideo.status,'waiting');assert.equal(h.node.pcVideo.error,'');assert.match(h.node.pcVideo.phase,/retrying in 15 seconds/);assert.equal(h.api.retries.get('request-original'),16000);
 assert.equal(h.api.uploads.size,1,'original file retained until receipt is confirmed');
 const firstCalls=calls;await h.api.pump();await settle(h);h.advance(14999);await h.api.pump();await settle(h);assert.equal(calls,firstCalls,'scheduler does not hammer the failed request');
 h.advance(1);await h.api.pump();await settle(h);
 assert.equal(posts,1,'accepted upload is never posted again');assert.equal(receipts,2);assert.equal(h.node.pcVideo.requestId,'request-original');assert.equal(h.node.pcVideo.id,'accepted-media');assert.equal(h.node.pcVideo.status,'working');assert.equal(h.api.uploads.size,0);assert.equal(h.api.retries.size,0);

 // A timed-out request queues a reconnect with the same accepted receipt.
 const timeout=harness({id:'accepted-media',file:false});timeout.api.setRequest(async()=>{throw new DOMException('Timed out','AbortError');});
 await timeout.api.pump();await settle(timeout);assert.equal(timeout.node.pcVideo.status,'waiting');assert.match(timeout.node.pcVideo.phase,/reconnecting automatically/);assert.equal(timeout.api.retries.get('request-original'),16000);

 // Application/HTTP errors still require a user action; a missing original is
 // not falsely presented as something automatic reconnection can fix.
 for(const status of [401,403,413,429,500]){const failure=harness({id:'accepted-media'});failure.api.setRequest(async()=>{throw Object.assign(new Error('Server rejected this request'),{status,retryable:true});});await failure.api.pump();await settle(failure);assert.equal(failure.node.pcVideo.status,'error');assert.equal(failure.api.retries.size,0);}
 const missing=harness({file:false});missing.api.setRequest(async()=>{throw notFound();});await missing.api.pump();await settle(missing);assert.equal(missing.node.pcVideo.status,'error');assert.equal(missing.node.pcVideo.needsFile,true);assert.equal(missing.api.retries.size,0);

 // Cancellation, leaving a project, and account changes never enqueue retries.
 for(const change of ['cancel','project','account']){const stopped=harness({id:'accepted-media'}),runtime=stopped.runtime();stopped.api.setRequest(async()=>{if(change==='cancel')runtime.controller.abort();if(change==='project')stopped.context.state={nodes:[]};if(change==='account')stopped.context.accountAuthEpoch++;throw networkError();});await stopped.api.work(runtime);assert.equal(stopped.api.retries.size,0,change+' must not queue a retry');assert.equal(stopped.node.pcVideo.phase,undefined,change+' must not mutate the stale job');}

 // Returning online can retry an unconfirmed upload, provided the file is
 // still available, and can clear the delay for accepted receipts.
 const reconnect=harness();reconnect.node.pcVideo.status='error';reconnect.node.pcVideo.error=networkError().message;reconnect.listeners.online();assert.equal(reconnect.node.pcVideo.status,'waiting');assert.equal(reconnect.node.pcVideo.requestId,'request-original');
 timeout.listeners.online();assert.equal(timeout.api.retries.size,0);assert.equal(timeout.node.pcVideo.status,'waiting');
 missing.listeners.online();assert.equal(missing.node.pcVideo.status,'error','missing original remains manual');
 // A restored project may have neither its original File nor the accepted ID;
 // reconnecting must still recover the server receipt before requesting a file.
 const restored=harness({file:false});let restoredOnline=false,restoredPosts=0;
 restored.api.setRequest(async(_node,suffix,options)=>{if(options.method==='POST')restoredPosts++;if(!restoredOnline)throw networkError();if(suffix.startsWith('/request/'))return {id:'restored-media'};return {status:'processing',phase:'Creating preview',progress:20};});
 await restored.api.pump();await settle(restored);assert.equal(restored.node.pcVideo.status,'waiting');assert.equal(restored.api.retries.size,1);
 restoredOnline=true;restored.listeners.online();assert.equal(restored.api.retries.size,0,'online clears receipt-recovery delay without the original File');await restored.api.pump();await settle(restored);assert.equal(restored.node.pcVideo.id,'restored-media');assert.equal(restored.node.pcVideo.status,'working');assert.equal(restoredPosts,0,'restored receipt recovery never uploads again');
 reconnect.api.retries.set('removed-request',9000);reconnect.api.resume();assert.equal(reconnect.api.retries.has('removed-request'),false,'removed jobs do not retain retry state');
 console.log('PASS: bounded upload retries, lost-response receipt recovery, timeout reconnect, manual HTTP/missing-file errors, cancellation/account isolation and online recovery.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
