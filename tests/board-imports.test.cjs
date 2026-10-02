'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const base=fs.readFileSync(path.join(__dirname,'../web/base.js'),'utf8'),imports=fs.readFileSync(path.join(__dirname,'../web/imports.js'),'utf8');
const oneLine=name=>base.split('\n').find(line=>line.startsWith('function '+name+'('));
function fixture(){
 const state={nodes:[],edges:[],settings:{transcriptionProvider:'local'},view:{scale:1}},calls=[],painted=[];let serial=0;
 const ctx={fillRect(){},strokeRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},fillText(text){painted.push(text);}};
 const elements={modulePrompt:{value:''}},document={getElementById:()=>null,createElement:tag=>{assert.equal(tag,'canvas');return{getContext:()=>ctx,toDataURL:()=> 'data:image/png;base64,aWNvbg=='};}};
 const c={state,history:[],future:[],selected:null,busy:false,ioBusy:false,document,File,Blob,TextEncoder,Uint8Array,AbortController,DOMException,Date,Set,Math,String,Number,Promise,console,atob,btoa,
  videoExtensions:/\.(mp4|mov|webm|mkv)$/i,board:{getBoundingClientRect:()=>({left:0,top:0}),clientWidth:800,clientHeight:600},
  $:id=>elements[id],uid:()=> 'id-'+(++serial),toast:(...args)=>calls.push(['toast',...args]),createNode:()=>null,renderNode:async()=>{},markDirty(){},updateSequence(){},updateRefreshNotice(){},refreshNodeAttachments(){},scheduleTranscriptionQueue(){},renderActivity(){},recordActivity(){},mediaActivity:[],transcriptionProvider:()=>state.settings.transcriptionProvider,readableBytes:n=>n+' B',
  createModuleNode:async(kind,title,point)=>{const n={id:'node-'+(++serial),kind,title,prompt:'',attachments:[],annotations:[],src:'data:image/png;base64,aWNvbg==',width:480,height:300,x:point?.x||0,y:point?.y||0};state.nodes.push(n);return n;},
  selectNode:id=>{c.selected=id;},readFile:async file=>'data:'+(file.type||'application/octet-stream')+';base64,'+Buffer.from(await file.arrayBuffer()).toString('base64'),
  embeddedBytes:async()=>new Uint8Array([1]),decoderClient:()=>({stop(){calls.push(['stop']);},request:async()=>({})}),
  prepareTranscriptionQueue:async(n,a,file,decoder,signal,progress,provider)=>{calls.push(['queue',provider,a.mime]);n.transcriptionJobs=[{id:'job',sourceId:a.id,provider}];a.status='queued';progress('Queued',1);},
  bytesFromDataURL:data=>new Uint8Array(Buffer.from(data.slice(data.indexOf(',')+1),'base64')),isAudioSource:a=>a.role==='audio'||/^audio\//.test(a.mime||''),
  importImages:async(files,location)=>{for(const f of files)state.nodes.push({id:'image-'+(++serial),kind:'image',title:f.name,attachments:[],x:location?.x||0,y:location?.y||0});},
  importMedia:async()=>{throw Error('Legacy video should not run');},importPCVideoFiles:async(files,location)=>{calls.push(['video',files[0].name]);const n=await c.createModuleNode('video',files[0].name,location);return[n];}
 };
 vm.createContext(c);vm.runInContext([oneLine('mimeFromName'),oneLine('videoFile'),oneLine('exportIsVideo'),oneLine('stripVideoPayload')].join('\n')+'\n'+base.slice(base.indexOf('function validateAttachments('),base.indexOf('async function validateProject('))+'\n'+imports.slice(0,imports.indexOf('// The Add menu')),c);
 return{c,state,calls,painted,run:code=>vm.runInContext(code,c)};
}
(async()=>{
 const f=fixture();
 f.c.files=[new File(['%PDF'],'Budget.pdf',{type:'application/pdf'}),new File(['sheet'],'Totals.xlsx',{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}),new File(['<h1>Project title</h1><script>doNotRun()</script><img src="https://untrusted.example/track">'],'Page.html',{type:'text/html'})];
 const nodes=await f.run('importBoardFiles(files)');assert.equal(nodes.length,3);assert.deepEqual(Array.from(nodes,n=>n.fileType),['PDF','Excel','HTML']);assert.deepEqual(Array.from(nodes,n=>n.title),['Budget.pdf','Totals.xlsx','Page.html']);assert.equal(new Set(nodes.map(n=>n.x+','+n.y)).size,3);assert.equal(nodes[2].attachments[0].mime,'text/html');assert.ok(f.painted.some(text=>text.includes('Project title')));assert.ok(!f.painted.some(text=>text.includes('doNotRun')||text.includes('untrusted.example')));
 const text='My actual words\nwith a second line.';f.c.text=text;const note=await f.run('createTextModule(text)');assert.equal(note.prompt,text);assert.equal(note.promptEditing,true);assert.equal(note.fileType,'Text');
 f.c.files=[new File(['audio'],'Voice note.webm',{type:'audio/webm;codecs=opus'})];const audio=(await f.run("importBoardFiles(files,null,{dictation:true,title:'Voice note',provider:'gemini'})"))[0];assert.equal(audio.fileType,'Audio');assert.equal(audio.title,'Voice note');const source=audio.attachments[0];assert.equal(source.mime,'audio/webm');assert.equal(source.transcriptToPrompt,true);assert.equal(audio.transcriptionJobs[0].provider,'gemini');assert.equal(f.calls.filter(c=>c[0]==='video').length,0);
 f.c.audio=audio;f.c.source=source;assert.equal(f.run('videoFile(source)'),false);assert.equal(f.run('exportIsVideo(source)'),false);assert.equal(f.run('stripVideoPayload(source)').data,source.data);
 audio.prompt='Keep my note.';f.c.history.push({nodes:[JSON.parse(JSON.stringify(audio))]});f.run("applyImportedTranscript(audio,source,'[00:00:01.000] Hello from dictation.')");assert.equal(audio.prompt,'Keep my note.\n\n[00:00:01.000] Hello from dictation.');assert.equal(f.c.history[0].nodes[0].prompt,audio.prompt);f.run("applyImportedTranscript(audio,source,'[00:00:01.000] Hello from dictation.')");assert.equal(audio.prompt.split('Hello from').length,2);
 const restored=f.run('validateAttachments(audio.attachments)')[0];assert.equal(restored.mime,'audio/webm');assert.equal(restored.data,source.data);assert.equal(restored.transcriptToPrompt,true);assert.equal(restored.promptTranscriptText,source.promptTranscriptText);
 f.c.files=[new File(['photo'],'One.png',{type:'image/png'}),new File(['photo'],'Two.png',{type:'image/png'})];const images=await f.run('importBoardFiles(files)');assert.equal(images.length,2);assert.notEqual(images[0].x,images[1].x);
 f.c.files=[new File(['video'],'Clip.mov',{type:'video/quicktime'})];const video=(await f.run('importBoardFiles(files)'))[0];assert.equal(video.fileType,'Video');assert.equal(f.calls.filter(c=>c[0]==='video').length,1);
 assert.equal(f.run("isBoardTextEditing({closest:()=>({tagName:'TEXTAREA'})})"),true);assert.equal(f.run('isBoardTextEditing({closest:()=>null})'),false);
 console.log('PASS: file modules, safe previews, text paste, distinct placement, frozen ASR provider, audio/webm persistence, and transcript prompt/history.');
})().catch(error=>{console.error(error);process.exitCode=1;});
