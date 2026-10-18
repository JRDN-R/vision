'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/processing.js','utf8');
function fixture(){
 const state={settings:{processingVersion:1,screenshotDetail:'maximum',describeFrames:true}},meta={id:'project_frame_test1',backendUrl:'https://pc.test'},requests=[];
 const c={state,cloudConfig:{backendUrl:meta.backendUrl},Date,Number,JSON,Math,Blob,AbortController,setTimeout,clearTimeout,DOMException,
 ensureProjectIdentity:()=>meta,boardAsyncContext:()=>({project:c.state}),boardAsyncCurrent:x=>x.project===c.state,assertBoardAsyncContext:x=>{if(x.project!==c.state)throw new Error('Project changed');},
 projectRequest:async(path)=>{requests.push(path);return{ok:true,headers:new Headers(),blob:async()=>new Blob(['jpeg'],{type:'image/jpeg'})};},projectResponse:async()=>{throw new Error('Missing');},readFile:async()=> 'data:image/jpeg;base64,anBlZw==',bytesFromDataURL:data=>Buffer.from(data.split(',')[1],'base64'),videoTimestamp:n=>n.toFixed(3)};
 vm.createContext(c);vm.runInContext(source.slice(0,source.indexOf('let framePollBusy=')),c);return {c,requests,meta,run:code=>vm.runInContext(code,c)};
}
test('presets are capped by duration and project settings stay independent',()=>{
 const f=fixture();for(const [mode,count]of [['maximum',1200],['medium',300],['balanced',100],['minimal',30]])assert.equal(f.run('Math.ceil(300/processingInterval(300,"'+mode+'"))'),count);
 assert.equal(f.run('processingSettings().screenshotDetail'),'maximum');f.c.state={settings:{screenshotDetail:'minimal',describeFrames:false}};assert.equal(f.run('processingSettings().describeFrames'),false);assert.equal(f.run('processingSettings().screenshotDetail'),'minimal');
});
test('frame references contain identifiers, not credentials or arbitrary URLs',()=>{
 const f=fixture();f.c.ref={projectId:f.meta.id,collectionId:'a'.repeat(24),index:1199};assert.equal(f.run('normalizeFrameRef(ref).index'),1199);
 f.c.ref.index=1200;assert.equal(f.run('normalizeFrameRef(ref)'),null);f.c.ref.index=0;f.c.ref.collectionId='../private';assert.equal(f.run('normalizeFrameRef(ref)'),null);
 f.c.frame={frameRef:{projectId:f.meta.id,collectionId:'a'.repeat(24),index:0},timestamp:.25,size:4};f.c.video={id:'source'};
 const a=f.run('frameAttachment(frame,video,0)');assert.equal(a.data,undefined);assert.equal(a.metadataOnly,true);assert.equal(a.timestamp,.25);
 f.c.frame.frameRef.projectId='project_someone_else';assert.throws(()=>f.run('frameAttachment(frame,video,0)'),/different project/);
});
test('on-demand frames use project auth and discard late account/project responses',async()=>{
 const f=fixture();f.c.a={frameRef:{projectId:f.meta.id,collectionId:'a'.repeat(24),index:0}};
 assert.match(await f.run('frameData(a)'),/^data:image\/jpeg/);assert.equal(f.requests[0],'/projects/'+f.meta.id+'/frames/'+'a'.repeat(24)+'/0');
 let release;f.c.projectRequest=()=>new Promise(resolve=>{release=resolve;});const pending=f.run('frameData(a)');f.c.state={settings:{}};release({ok:true});await assert.rejects(pending,/Project changed/);
});
test('timeline includes uncertainty, timestamps, and duplicate provenance',()=>{
 const f=fixture();f.c.report={model:'Qwen3.5-0.8B',status:'complete',completed:2,count:2,grouping:'Sampled observations.',frames:[{index:1,timestamp:.25,width:64,height:36,aspectRatio:'16:9',prominentColors:['#ff0000'],reusedFrom:0,description:'A red surface.',independentlyInspected:false}],timeline:[]};
 const text=f.run('frameReportText(report)');assert.match(text,/0.250/);assert.match(text,/reused from frame 1/);assert.match(text,/may be incorrect/);
});
