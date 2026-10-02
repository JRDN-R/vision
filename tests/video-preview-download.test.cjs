/* Body downloads must remain bounded and abortable after headers arrive. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/video-preview.js'),'utf8');
const body=source.slice(source.indexOf('async function pcVideoRequest('),source.indexOf('\nfunction pcVideoSetPoster'));
const context={AbortController,Blob,Response,ReadableStream,Uint8Array,DOMException,PC_VIDEO_PREVIEW_LIMIT:1024,pcVideoPath:()=>'/projects/mock/media/mock/preview',projectResponse:response=>response.json(),setTimeout:(fn,delay)=>setTimeout(fn,Math.min(delay,25)),clearTimeout};
vm.createContext(context);vm.runInContext(body+'\nthis.request=pcVideoRequest;',context);
async function main(){
 context.projectRequest=async(_path,options)=>new Response(new ReadableStream({start(controller){controller.enqueue(new Uint8Array([1,2,3]));options.signal.addEventListener('abort',()=>controller.error(new DOMException('Download aborted','AbortError')),{once:true});}}),{headers:{'Content-Type':'video/mp4'}});
 const started=Date.now();await assert.rejects(context.request({},'',{binary:true}),error=>error.name==='AbortError');assert.ok(Date.now()-started<2000,'binary body is still covered by request timeout');
 let cancelled=false;context.projectRequest=async()=>new Response(new ReadableStream({start(controller){controller.enqueue(new Uint8Array(20));},cancel(){cancelled=true;}}));
 await assert.rejects(context.request({},'',{binary:true,maxBytes:10}),/size limit/);assert.ok(cancelled,'overflow cancels the remaining body');
 context.projectRequest=async()=>new Response('audio',{headers:{'Content-Type':'audio/mpeg'}});
 const blob=await context.request({},'',{binary:true,maxBytes:10});assert.equal(blob.type,'audio/mpeg');assert.equal(await blob.text(),'audio');
 console.log('PASS: video/audio response body timeout, bounded download, cancellation, valid binary result.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
