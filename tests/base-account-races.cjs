const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('web/base.js','utf8');
const part=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
// Keep extraction independent of the event-handler wiring immediately after imports.
const helpers=part('function boardAsyncContext(','async function importImages(');
const imports=part('async function importImages(','\ndocument.addEventListener(');
const create=part('async function createModuleNode(','async function addBlankNode(');
const attach=part('async function attachFiles(','function removeAttachment(');
const open=part('async function openProject(','function deleteSelection(');
function context(){
 let release;const pending=new Promise(resolve=>release=resolve),c={state:{nodes:[],edges:[],view:{scale:1}},accountAuthEpoch:1,projectAccountSwitching:false,ioBusy:false,busy:false,lastPointer:null,tool:'select',Promise,Error,console,
 board:{clientWidth:100,clientHeight:100,getBoundingClientRect:()=>({left:0,top:0})},nodePoster:()=>pending,readFile:()=>pending,R:{loadImage:async()=>({naturalWidth:20,naturalHeight:20})},screenToWorld:(x,y)=>({x,y}),uid:()=>String(Math.random()),toast(){},checkpoint(){},autoConnectionSource:()=>null,autoConnectModules(){},createNode(){},selectNode(){},cardWidth:()=>100,updateView(){},markDirty(){c.dirtyCalls=(c.dirtyCalls||0)+1;},updateSequence(){},drawCables(){},setTool(){},fitBoard(){},updateRefreshNotice(){},scheduleTranscriptionQueue(){},nodeById:id=>c.state.nodes.find(n=>n.id===id),videoFile:()=>false,mimeFromName:()=>'',mediaFile:()=>false,refreshNodeAttachments(){},offerTranscription(){}};
 c.dirty=false;c.readProjectInput=()=>pending;c.validateProject=async raw=>{c.validations=(c.validations||0)+1;return raw;};
 vm.createContext(c);vm.runInContext(helpers+imports+create+attach+open,c);return{c,release,run:code=>vm.runInContext(code,c)};
}
(async()=>{
 const createRace=context(),node=createRace.run('createModuleNode()');createRace.c.accountAuthEpoch++;createRace.c.state={nodes:[],edges:[],view:{scale:1}};createRace.release('data:image/png;base64,AA==');await assert.rejects(node,/project or account changed/);assert.equal(createRace.c.state.nodes.length,0);
 const imageRace=context();imageRace.c.files=[{name:'private.png',type:'image/png'}];const importing=imageRace.run('importImages(files)');imageRace.c.accountAuthEpoch++;imageRace.c.state={nodes:[],edges:[],view:{scale:1}};imageRace.release('data:image/png;base64,AA==');await importing;assert.equal(imageRace.c.state.nodes.length,0);assert.equal(imageRace.c.dirtyCalls,undefined);assert.equal(imageRace.c.ioBusy,false);
 const attachmentRace=context(),old={id:'original',attachments:[]};attachmentRace.c.state.nodes.push(old);attachmentRace.c.files=[{name:'private.txt',type:'text/plain'}];const attaching=attachmentRace.run('attachFiles("original",files)');attachmentRace.c.accountAuthEpoch++;attachmentRace.c.state={nodes:[],edges:[],view:{scale:1}};attachmentRace.release('data:text/plain;base64,c2VjcmV0');await attaching;assert.equal(old.attachments.length,0);assert.equal(attachmentRace.c.dirtyCalls,undefined);
 const happy=context();happy.c.files=[{name:'image.png',type:'image/png'}];const completed=happy.run('importImages(files)');happy.release('data:image/png;base64,AA==');await completed;assert.equal(happy.c.state.nodes.length,1);assert.equal(happy.c.dirtyCalls,1);
 const openRace=context(),opening=openRace.run('openProject({name:"account-A.vision.json"})');openRace.c.accountAuthEpoch++;const newBoard={nodes:[],edges:[],view:{scale:1}};openRace.c.state=newBoard;openRace.release({title:'Private A board',nodes:[],edges:[]});await opening;assert.equal(openRace.c.state,newBoard);assert.equal(openRace.c.validations,undefined,'a file read under A must not begin validation under B');
 const video=fs.readFileSync('web/video-preview.js','utf8'),videoRace=context();videoRace.run(video.slice(video.indexOf('async function importPCVideoFiles('),video.indexOf('function schedulePCVideos(')));videoRace.c.files=[{name:'private.mp4',type:'video/mp4'}];const creatingVideo=videoRace.run('importPCVideoFiles(files)');videoRace.c.accountAuthEpoch++;videoRace.release('data:image/png;base64,AA==');await assert.rejects(creatingVideo,/project or account changed/);assert.equal(videoRace.c.state.nodes.length,0);
 console.log('Base account races: delayed node, image, attachment, project-file and video imports cannot mutate a switched account; normal image import passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
