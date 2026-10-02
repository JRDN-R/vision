/* Exercise microphone lifecycle without granting a real microphone or calling an AI. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const ROOT=path.resolve(__dirname,'..');
const fixture=`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>*{box-sizing:border-box}body{background:#111;color:white}dialog{background:#222;color:white}button,select{font:16px sans-serif}button{padding:10px}button[hidden]{display:none!important}.tools{width:50px}.tools button{width:44px;height:44px}.full{width:100%}${fs.readFileSync(path.join(ROOT,'web/capture.css'),'utf8')}</style><div class="tools"></div><div id="board" style="width:300px;height:300px"></div><script>
const $=id=>document.getElementById(id),board=$('board');let state={settings:{transcriptionProvider:'local'}},busy=false,ioBusy=false;
function transcriptionProvider(){return state.settings.transcriptionProvider;}function setTranscriptionProvider(value){state.settings.transcriptionProvider=value;syncTranscriptionProviderUI();}function syncTranscriptionProviderUI(){}
const captures=[],notices=[],streams=[],recorders=[];function toast(message){notices.push(message);}async function importBoardFiles(files,location,options){captures.push({files:files.map(f=>({name:f.name,type:f.type,size:f.size})),location,options});}
let permissionMode='allow',permissionResolve,permissionReject,recorderFailure=false;
function makeStream(){const track=new EventTarget();track.stopped=false;track.stop=()=>{track.stopped=true;};const stream={getTracks:()=>[track],track};streams.push(stream);return stream;}
Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:()=>{if(permissionMode==='deny')return Promise.reject(new DOMException('Denied','NotAllowedError'));if(permissionMode==='pending')return new Promise((resolve,reject)=>{permissionResolve=resolve;permissionReject=reject;});return Promise.resolve(makeStream());}}});
class FakeMediaRecorder{constructor(stream,options){if(recorderFailure)throw new Error('Recorder could not start');this.stream=stream;this.mimeType=options.mimeType||'audio/webm';this.state='inactive';recorders.push(this);}static isTypeSupported(type){return type.includes('webm');}start(){this.state='recording';}stop(){this.state='inactive';queueMicrotask(()=>{this.ondataavailable?.({data:new Blob(['audio'],{type:this.mimeType})});this.onstop?.();});}}
window.MediaRecorder=FakeMediaRecorder;
${fs.readFileSync(path.join(ROOT,'web/capture.js'),'utf8')}
window.captureTest={captures,notices,streams,recorders,provider:()=>transcriptionProvider(),active:()=>!!boardCaptureSession,mode:value=>permissionMode=value,resolve:()=>permissionResolve(makeStream()),breakRecorder:value=>recorderFailure=value,changeProject:()=>state={settings:{transcriptionProvider:'local'}},clearMicrophone:()=>Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:undefined})};
</script>`;
async function main(){
 const browser=await chromium.launch({headless:true,args:['--single-process','--no-zygote','--disable-gpu'],...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});const errors=[],external=[];
 await context.route('**/*',route=>{if(route.request().url()==='https://capture.test/')return route.fulfill({status:200,contentType:'text/html',body:fixture});external.push(route.request().url());return route.abort();});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const open=()=>page.locator('#boardCaptureButton').click();
 const start=async()=>{await page.locator('#boardCaptureStart').click();await page.waitForFunction(()=>document.getElementById('boardCaptureStop').hidden===false);};
 try{
  await page.goto('https://capture.test/');await open();
  assert.equal(await page.locator('#boardCaptureProvider').inputValue(),'local');
  assert.ok(await page.locator('#boardCaptureDialog').evaluate(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&el.scrollWidth<=el.clientWidth+1;}),'voice controls fit mobile');
  assert.equal(await page.evaluate(()=>captureTest.streams.length),0,'opening settings does not request the microphone');
  await start();assert.equal(await page.locator('#boardCaptureProvider').isDisabled(),true);
  await page.locator('#boardCaptureStop').click();await page.waitForFunction(()=>captureTest.captures.length===1);
  let result=await page.evaluate(()=>({capture:captureTest.captures[0],stopped:captureTest.streams[0].track.stopped,active:captureTest.active()}));
  assert.equal(result.capture.options.dictation,true);assert.equal(result.capture.options.provider,'local');assert.equal(result.capture.files[0].type,'audio/webm;codecs=opus');assert.equal(result.capture.files[0].size,5);assert.equal(result.stopped,true);assert.equal(result.active,false);
  await open();await page.locator('#boardCaptureProvider').selectOption('gemini');assert.match(await page.locator('#boardCaptureProviderNote').innerText(),/may incur API charges/);await start();await page.locator('#boardCaptureStop').click();await page.waitForFunction(()=>captureTest.captures.length===2);assert.equal(await page.evaluate(()=>captureTest.captures[1].options.provider),'gemini');
  await open();assert.equal(await page.locator('#boardCaptureProvider').inputValue(),'gemini');await page.locator('#boardCaptureProvider').selectOption('local');await start();await page.locator('#boardCaptureCancel').click();assert.equal(await page.evaluate(()=>captureTest.captures.length),2);assert.equal(await page.evaluate(()=>captureTest.streams.at(-1).track.stopped),true);
  // A delayed permission result must be released after cancellation, never imported.
  await page.evaluate(()=>captureTest.mode('pending'));await open();await page.locator('#boardCaptureStart').click();await page.locator('#boardCaptureCancel').click();await page.evaluate(()=>captureTest.resolve());await page.waitForFunction(()=>captureTest.streams.at(-1).track.stopped);assert.equal(await page.evaluate(()=>captureTest.captures.length),2);
  await page.evaluate(()=>captureTest.mode('deny'));await open();await page.locator('#boardCaptureStart').click();await page.waitForFunction(()=>document.getElementById('boardCaptureStatus').textContent.includes('permission was denied'));assert.equal(await page.locator('#boardCaptureStart').isEnabled(),true);await page.locator('#boardCaptureClose').click();
  await page.evaluate(()=>{captureTest.mode('allow');captureTest.breakRecorder(true);});await open();await page.locator('#boardCaptureStart').click();await page.waitForFunction(()=>captureTest.streams.at(-1).track.stopped);assert.equal(await page.evaluate(()=>captureTest.active()),false);await page.locator('#boardCaptureClose').click();
  await page.evaluate(()=>captureTest.breakRecorder(false));await open();await start();await page.evaluate(()=>captureTest.streams.at(-1).track.dispatchEvent(new Event('ended')));assert.match(await page.locator('#boardCaptureStatus').innerText(),/microphone disconnected/);assert.equal(await page.evaluate(()=>captureTest.captures.length),2);await page.locator('#boardCaptureClose').click();
  await open();await start();await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));assert.equal(await page.evaluate(()=>captureTest.streams.at(-1).track.stopped),true);assert.equal(await page.evaluate(()=>captureTest.captures.length),2);await page.locator('#boardCaptureClose').click();
  await open();await start();await page.evaluate(()=>captureTest.changeProject());await page.locator('#boardCaptureStop').click();await page.waitForFunction(()=>document.getElementById('boardCaptureStatus').textContent.includes('project changed'));assert.equal(await page.evaluate(()=>captureTest.captures.length),2);await page.locator('#boardCaptureClose').click();
  await page.evaluate(()=>captureTest.clearMicrophone());await open();assert.equal(await page.locator('#boardCaptureStart').isDisabled(),true);assert.match(await page.locator('#boardCaptureStatus').innerText(),/unavailable/);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  console.log('PASS: mobile voice note controls, provider selection, recording/import, cancellation, delayed permission, recorder failures, navigation, and project isolation.');
 }finally{await context.close();await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
