/* Portable app: recorded Opus audio -> import -> local ASR -> prompt -> saved project. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const ROOT=path.resolve(__dirname,'..'),APP_URL='https://vision.test/',PC_URL='https://processor.tailtest.ts.net';
const TRANSCRIPT='[00:00:00.000] Build a page showing the weekly project timeline.';

async function main(){
 const browser=await chromium.launch({headless:true,args:['--single-process','--no-zygote','--disable-gpu'],...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 const projects=new Map(),jobs=new Map(),errors=[],external=[],submissions=[];let complete=false;
 const json=(route,status,data)=>route.fulfill({status,contentType:'application/json',headers:{'access-control-allow-origin':'*'},body:JSON.stringify(data)});
 await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(request.url()===APP_URL){
   let html=fs.readFileSync(path.join(ROOT,'Vision.html'),'utf8');const end=html.lastIndexOf('})();');
   const hooks=`
    cloudConfig={kind:'private-pc',backendUrl:${JSON.stringify(PC_URL)},publicAccess:true};cloudAuth={accessToken:'test-only-token',remember:false};projectHealthCache=null;
    const captureStats={tracks:[],decoderCalls:[],videoCalls:0,recorders:0,decoderStops:0};
    Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>{const track=new EventTarget();track.stopped=false;track.stop=()=>{track.stopped=true;};captureStats.tracks.push(track);return{getTracks:()=>[track]};}}});
    window.MediaRecorder=class{constructor(stream,options){this.stream=stream;this.mimeType=options.mimeType;this.state='inactive';captureStats.recorders++;}static isTypeSupported(mime){return mime==='audio/webm;codecs=opus';}start(){this.state='recording';}stop(){this.state='inactive';queueMicrotask(()=>{this.ondataavailable?.({data:new Blob(['recorded-opus-fixture'],{type:this.mimeType})});this.onstop?.();});}};
    embeddedBytes=async()=>new Uint8Array([0]);
    decoderClient=()=>({stop:()=>captureStats.decoderStops++,request:async(type,payload)=>{captureStats.decoderCalls.push({type,fileType:payload.file?.type,start:payload.start,end:payload.end});if(type==='init')return{};if(type==='analyze')return{duration:2,pauses:[],vadWindows:[]};if(type==='transcription_chunk')return{audio:new Uint8Array([82,73,70,70]),mimeType:'audio/wav'};throw new Error('Unexpected decoder action: '+type);}});
    processVideoFile=async()=>{captureStats.videoCalls++;throw new Error('Audio was routed to video processing');};
    if(typeof importPCVideoFiles==='function')importPCVideoFiles=async()=>{captureStats.videoCalls++;throw new Error('Audio was routed to PC video processing');};
    window.__dictationTest={state:()=>state,stats:captureStats,saved:async()=>{await flushProjectSave();await projectBackup();},validated:async()=>{const copy=await validateProject(projectSnapshot());return copy.nodes;},poll:()=>{for(const{job}of queueEntries())job.nextAttemptAt=0;scheduleTranscriptionQueue(0);}};
   `;
   html=html.slice(0,end)+hooks+html.slice(end);return route.fulfill({status:200,contentType:'text/html',body:html});
  }
  if(url.origin===PC_URL){
   if(request.method()==='OPTIONS')return route.fulfill({status:204,headers:{'access-control-allow-origin':'*','access-control-allow-methods':'GET,POST,PUT,OPTIONS','access-control-allow-headers':'authorization,content-type,x-vision-project-key'}});
   if(url.pathname==='/api/health')return json(route,200,{ok:true,service:'vision-pc',capabilities:{persistentProjects:true,projectRevision:true,persistentRuns:true,localTranscription:true},localTranscription:{ready:true,maxRequestBytes:100*1024*1024}});
   const match=url.pathname.match(/^\/api\/projects\/([^/]+)(.*)$/);
   if(match){
    const[,id,suffix]=match,key=request.headers()['x-vision-project-key'],prior=projects.get(id);
    if(!key||prior&&key!==prior.key)return json(route,403,{error:'Project key missing or incorrect'});
    if(!suffix&&request.method()==='PUT'){
     const payload=request.postDataJSON();if(payload.revision!==(prior?.revision||0))return json(route,409,{error:'Revision conflict'});
     projects.set(id,{key,project:payload.project,revision:payload.revision+1});return json(route,200,{revision:payload.revision+1});
    }
    if(!suffix&&request.method()==='GET')return prior?json(route,200,prior):json(route,404,{error:'Not found'});
    if(suffix==='/transcriptions'&&request.method()==='POST'){
     const payload=request.postDataJSON(),jobId='dictation-'+payload.clientRequestId;submissions.push(payload);
     assert.ok(prior,'project saved before transcription upload');assert.equal(payload.sections.length,1);assert.equal(payload.sections[0].mimeType,'audio/wav');
     jobs.set(jobId,{projectId:id,payload});return json(route,202,{id:jobId,status:'queued'});
    }
    if(suffix.startsWith('/transcriptions/')&&request.method()==='GET'){
     const jobId=suffix.split('/').pop(),job=jobs.get(jobId);if(!job||job.projectId!==id)return json(route,404,{error:'Not found'});
     return json(route,200,{id:jobId,status:complete?'complete':'processing',progress:complete?100:35,phase:complete?'Complete':'Transcribing on PC',...(complete?{result:{sections:job.payload.sections.map(section=>({start:section.start,end:section.end,text:TRANSCRIPT}))}}:{})});
    }
    if(suffix==='/runs')return json(route,200,{runs:[]});
   }
   return json(route,404,{error:'Unmocked PC path'});
  }
  external.push(url.origin);return route.abort();
 });
 const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
 try{
  await page.goto(APP_URL,{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>!!window.__dictationTest);
  await page.locator('#boardCaptureButton').click();assert.equal(await page.locator('#boardCaptureProvider').inputValue(),'local');
  await page.locator('#boardCaptureStart').click();await page.waitForFunction(()=>!document.getElementById('boardCaptureStop').hidden);
  await page.locator('#boardCaptureStop').click();
  await page.waitForFunction(()=>window.__dictationTest.state().nodes.some(n=>n.transcriptionJobs?.some(job=>job.remoteId)),null,{timeout:20000});
  const pending=await page.evaluate(()=>{const n=__dictationTest.state().nodes[0],source=n.attachments.find(a=>a.id===n.sourceAttachmentId);return{id:n.id,title:n.title,kind:n.kind,fileType:n.fileType,prompt:n.prompt,mime:source.mime,role:source.role,sourceKind:n.transcriptionJobs[0].sourceKind,provider:n.transcriptionJobs[0].provider,sourceId:source.id,tracksStopped:__dictationTest.stats.tracks.every(t=>t.stopped),decoderCalls:__dictationTest.stats.decoderCalls,videoCalls:__dictationTest.stats.videoCalls,decoderStops:__dictationTest.stats.decoderStops};});
  assert.equal(pending.kind,'node');assert.equal(pending.title,'Voice note');assert.equal(pending.fileType,'Audio');assert.equal(pending.mime,'audio/webm');assert.equal(pending.role,'audio');assert.equal(pending.sourceKind,'audio');assert.equal(pending.provider,'local');assert.equal(pending.prompt,'');assert.equal(pending.tracksStopped,true);assert.equal(pending.videoCalls,0);assert.equal(pending.decoderStops,1);
  assert.deepEqual(pending.decoderCalls.map(call=>call.type),['init','analyze','transcription_chunk']);assert.equal(pending.decoderCalls[1].fileType,'audio/webm;codecs=opus');assert.equal(submissions.length,1);
  complete=true;await page.evaluate(()=>__dictationTest.poll());
  await page.waitForFunction(text=>window.__dictationTest.state().nodes[0]?.prompt===text,TRANSCRIPT);
  assert.equal(await page.locator('.prompt-editor').first().inputValue(),TRANSCRIPT,'completed transcript is visible in the module editor');
  const validated=await page.evaluate(()=>__dictationTest.validated());
  assert.equal(validated[0].prompt,TRANSCRIPT);assert.equal(validated[0].fileType,'Audio');assert.equal(validated[0].sourceAttachmentId,pending.sourceId);
  const source=validated[0].attachments.find(a=>a.id===pending.sourceId);
  assert.equal(source.mime,'audio/webm');assert.equal(source.role,'audio');assert.equal(source.transcriptToPrompt,true);assert.equal(source.metadataOnly,true,'completed audio uses retained metadata with a saved transcript');assert.equal(source.promptTranscriptText,TRANSCRIPT);
  assert.ok(validated[0].attachments.some(a=>a.transcriptOf===pending.sourceId&&a.status==='complete'));
  await page.evaluate(()=>__dictationTest.saved());await page.reload({waitUntil:'domcontentloaded'});
  await page.waitForFunction(id=>window.__dictationTest?.state().nodes.some(n=>n.id===id&&n.prompt),pending.id);
  const restored=await page.evaluate(()=>__dictationTest.validated());assert.equal(restored[0].prompt,TRANSCRIPT);assert.equal(restored[0].fileType,'Audio');assert.equal(restored[0].attachments.find(a=>a.id===restored[0].sourceAttachmentId).mime,'audio/webm');
  assert.equal(submissions.length,1,'reopening never resubmits completed dictation');assert.equal(await page.evaluate(()=>__dictationTest.stats.videoCalls),0);assert.deepEqual(errors,[]);assert.deepEqual(external,[],'no external or paid-provider request');
  console.log('PASS: portable mobile record -> audio/webm import -> decoded local ASR -> visible prompt -> validated saved/reloaded Audio module; microphone released, no video path, no paid requests.');
 }finally{await context.close();await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
