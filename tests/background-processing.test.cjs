'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const processing=fs.readFileSync('web/processing.js','utf8'),video=fs.readFileSync('web/video-preview.js','utf8'),base=fs.readFileSync('web/base.js','utf8');
test('disconnect and lost final report do not pause the server or lose completion',async()=>{
 const source={id:'source',name:'clip.mov',frameCollection:{},visualAnalysis:{status:'processing',completed:2}},n={attachments:[source]};
 const c={cloudAuth:{},state:{nodes:[n]},boardAsyncContext:()=>({project:c.state}),boardAsyncCurrent:x=>x.project===c.state,
  projectRequest:async()=>{throw new Error('offline');},projectResponse:async x=>x,frameCollectionPath:()=>'/frames',renderActivity(){},markDirty(){},textDataURL:x=>x,frameReportText:()=> 'complete report',Date};
 vm.createContext(c);vm.runInContext(processing.slice(processing.indexOf('let framePollBusy='),processing.indexOf('async function controlFrameAnalysis')),c);
 await vm.runInContext('refreshFrameAnalysis()',c);
 assert.equal(source.visualAnalysis.status,'processing');assert.equal(source.visualAnalysis.connectionError,true);
 let reads=0;c.projectRequest=async()=>{if(++reads===2)throw new Error('lost report');return{status:'complete',completed:3,count:3};};
 await vm.runInContext('refreshFrameAnalysis()',c);
 assert.equal(source.visualAnalysis.status,'processing','do not mark complete before the full report is saved');
 c.projectRequest=async()=>({status:'complete',completed:3,count:3});await vm.runInContext('refreshFrameAnalysis()',c);
 assert.equal(source.visualAnalysis.status,'complete');assert.equal(source.visualAnalysis.connectionError,undefined);
 assert.equal(n.attachments.find(a=>a.id==='source-visual').data,'complete report');
});
test('returning video adopts the durable transcript receipt without audio transfer',async()=>{
 const source={id:'source',name:'clip.mov'},n={attachments:[source],pcVideo:{id:'a'.repeat(24),requestId:'request',sourceId:'source',projectId:'project_video_test1',backendUrl:'https://pc.test'}};
 const c={state:{nodes:[n]},Number,Date,DOMException,uid:()=> 'job',pcVideoCurrent:()=>true,pcVideoArtifactSuffix(){},normalizePCVideo:x=>x,
 frameAttachment:()=>({id:'frame',role:'video-frame',videoOf:'source'}),attachFrameCollection(){},pcVideoUpdate(){},pcVideoSetPoster(){},queueChanged(){},scheduleTranscriptionQueue(){},markDirty(){},
 pcVideoRequest:()=>{throw new Error('Audio must not roundtrip through browser');}};
 vm.createContext(c);vm.runInContext(video.slice(video.indexOf('async function applyPCVideoResult'),video.indexOf('function retryPCVideo')),c);
 c.runtime={project:c.state,n,controller:new AbortController()};c.result={frames:[{frameRef:{}}],preview:{mime:'video/mp4',size:5},audioSections:[{start:0,end:1,mime:'audio/mpeg'}],transcription:{id:'b'.repeat(24),provider:'gemini',includeSoundEvents:true,status:'approval_waiting'}};
 await vm.runInContext('applyPCVideoResult(runtime,result)',c);
 const job=n.transcriptionJobs[0];assert.equal(job.remoteId,'b'.repeat(24));assert.equal(job.sections[0].audioData,null);assert.equal(job.provider,'gemini');assert.equal(job.status,'approval_waiting');
 c.validASRReference=x=>/^[-\w]{1,120}$/.test(x||'')?x:null;c.validASRBackend=x=>x==='https://pc.test'?x:null;c.videoFile=()=>true;c.Set=Set;
 vm.runInContext(base.slice(base.indexOf('function validateTranscriptionJobs'),base.indexOf('function queueEntries')),c);
 c.raw=JSON.parse(JSON.stringify([job]));c.attachments=n.attachments;
 const restored=vm.runInContext('validateTranscriptionJobs(raw,attachments)',c)[0];
 assert.equal(restored.remoteId,job.remoteId);assert.equal(restored.mediaId,n.pcVideo.id);assert.equal(restored.sections[0].audioData,null);
 await vm.runInContext('applyPCVideoResult(runtime,result)',c);assert.equal(n.transcriptionJobs.length,1,'repeated video result must not duplicate transcription');
});
