// Every board import owns a module. Dropping onto a module still attaches files.
let boardImportRunning=false;
let boardImportGeneration=0;
const boardImportControllers=new Set();
function boardImportContext(){if(typeof projectAccountSwitching!=='undefined'&&projectAccountSwitching)return null;return{context:boardAsyncContext(),generation:boardImportGeneration};}
function boardImportCurrent(value){return !!value&&value.generation===boardImportGeneration&&boardAsyncCurrent(value.context);}
function assertBoardImportContext(value){if(!boardImportCurrent(value))throw new DOMException('The project or signed-in account changed. Import canceled.','AbortError');}
function cancelBoardImports(){boardImportGeneration++;for(const controller of boardImportControllers)controller.abort();boardImportControllers.clear();boardImportRunning=false;document.getElementById('boardAddDialog')?.close();const paste=document.getElementById('boardPasteText');if(paste)paste.value='';}
function isBoardTextEditing(target){return !!target?.closest?.('input,textarea,select,[contenteditable=""],[contenteditable="true"],[contenteditable="plaintext-only"]');}
function boardFileInfo(file){
 const name=String(file.name||'File'),ext=name.includes('.')?name.split('.').pop().toLowerCase():'',mime=(file.type||file.mime||mimeFromName(name)).split(';')[0].trim().toLowerCase();
 if(/^audio\//i.test(mime)||/^(mp3|mp2|wav|wave|m4a|aac|flac|ogg|oga|opus|aiff|aif|wma|amr|caf)$/.test(ext))return{kind:'audio',label:'Audio',mime};
 if(videoFile(file))return{kind:'video',label:'Video',mime};
 if(/^image\/(png|jpe?g|webp|gif|bmp|avif)$/i.test(mime)||/^(png|jpe?g|webp|gif|bmp|avif)$/.test(ext))return{kind:'image',label:ext==='gif'?'GIF':'Image',mime};
 const labels={pdf:'PDF',xlsx:'Excel',xls:'Excel',xlsm:'Excel',xlsb:'Excel',csv:'CSV',tsv:'Table',ods:'Spreadsheet',html:'HTML',htm:'HTML',doc:'Word',docx:'Word',odt:'Document',ppt:'PowerPoint',pptx:'PowerPoint',odp:'Presentation',txt:'Text',md:'Markdown',rtf:'Rich text',json:'JSON',xml:'XML',zip:'ZIP',svg:'SVG'};
 const mimes={html:'text/html',htm:'text/html',md:'text/markdown',tsv:'text/tab-separated-values',xml:'application/xml',svg:'image/svg+xml',docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',pptx:'application/vnd.openxmlformats-officedocument.presentationml.presentation',zip:'application/zip'};
 return{kind:'file',label:labels[ext]||(mime==='application/pdf'?'PDF':ext?ext.toUpperCase():'File'),mime:mime==='application/octet-stream'?(mimes[ext]||mime):mime,text:/^(txt|md|csv|tsv|html|htm|json|xml|svg)$/.test(ext)||/^text\//i.test(mime),html:/^(html|htm)$/.test(ext)||mime==='text/html'};
}
function boardImportLocation(location,index=0){
 if(!location)return undefined;
 return{x:location.x+(index%3)*380*state.view.scale,y:location.y+Math.floor(index/3)*420*state.view.scale};
}
function boardMarkupText(value){return String(value).replace(/<(script|style|noscript)\b[^>]*>[\s\S]*?<\/\1\s*>/gi,'').replace(/<[^>]*>/g,' ').replace(/[ \t]+/g,' ').trim();}
async function fileModulePoster(file,info){
 const canvas=document.createElement('canvas');canvas.width=480;canvas.height=300;const ctx=canvas.getContext('2d');
 ctx.fillStyle='#19221c';ctx.fillRect(0,0,480,300);ctx.fillStyle='#a8bd9d';ctx.font='600 30px system-ui, sans-serif';ctx.fillText(info.label.slice(0,18),32,54);
 let snippet='';if(info.text){snippet=await file.slice(0,12000).text();if(info.html)snippet=boardMarkupText(snippet);}
 if(snippet.trim()){
  ctx.fillStyle='#d6ded2';ctx.font='16px ui-monospace, monospace';let row=0;
  for(const line of snippet.replace(/\t/g,'   ').split(/\r?\n/)){const text=line.trim();if(!text)continue;for(let i=0;i<text.length&&row<8;i+=43){ctx.fillText(text.slice(i,i+43),32,90+row*21);row++;}if(row>=8)break;}
 }else{
  ctx.strokeStyle='#657260';ctx.lineWidth=3;ctx.strokeRect(190,88,100,125);ctx.beginPath();ctx.moveTo(210,119);ctx.lineTo(270,119);ctx.moveTo(210,143);ctx.lineTo(270,143);ctx.moveTo(210,167);ctx.lineTo(252,167);ctx.stroke();
  ctx.fillStyle='#8b9c86';ctx.font='16px system-ui, sans-serif';ctx.textAlign='center';ctx.fillText(readableBytes(file.size),240,257);
 }
 return canvas.toDataURL('image/png');
}
function refreshImportedModule(n,el=document.getElementById('node-'+n.id)){
 if(!el||!n.fileType)return;el.classList.add('file-module');el.dataset.fileType=n.fileType;
 let tag=el.querySelector('.module-file-type');if(!tag){tag=document.createElement('span');tag.className='module-file-type';el.querySelector('.node-head').insertBefore(tag,el.querySelector('.node-more'));}tag.textContent=n.fileType;tag.title=n.fileType+' module';
 const source=n.attachments?.find(a=>a.id===n.sourceAttachmentId),editor=el.querySelector('.prompt-editor');
 if(editor&&source?.transcriptToPrompt&&!n.prompt)editor.placeholder=source.status==='failed'?'Audio kept. Retry transcription from Files.':'Your transcript will appear here…';
}
const importsCreateNode=createNode;createNode=function(n){const el=importsCreateNode(n);refreshImportedModule(n,el);return el;};
const importsRenderNode=renderNode;renderNode=async function(n){await importsRenderNode(n);refreshImportedModule(n);};
async function createTextModule(text,options={}){
 const context=options.importContext||boardImportContext();if(!boardImportCurrent(context))return null;
 if(busy||ioBusy||boardImportRunning){toast('Finish the current import first.');return null;}
 text=String(text||'');if(!text.trim())return null;
 ioBusy=true;try{const title=options.title||text.trim().split(/\r?\n/)[0].slice(0,70)||'Text',n=await createModuleNode('node',title,options.location);assertBoardImportContext(context);n.prompt=text;n.promptEditing=true;n.fileType='Text';renderNode(n);selectNode(n.id);markDirty();updateSequence();return n;}
 catch(error){if(boardImportCurrent(context))toast('Could not paste text: '+error.message,true);return null;}
 finally{if(boardImportCurrent(context)){ioBusy=false;updateRefreshNotice();scheduleTranscriptionQueue();}}
}
function applyImportedTranscript(n,source,text){
 if(!source.transcriptToPrompt)return;const transcript=String(text||'').trim();if(!transcript||source.promptTranscriptText===transcript)return;
 const apply=(node,a)=>{const old=a.promptTranscriptText,current=String(node.prompt||'');node.prompt=old&&current.endsWith(old)?current.slice(0,-old.length)+transcript:current.trim()?current.trimEnd()+'\n\n'+transcript:transcript;node.promptEditing=true;a.promptTranscriptText=transcript;};
 apply(n,source);
 // Completed output remains available when undo restores an earlier queued state.
 const snapshots=[...history,...future];if(typeof touchBackup!=='undefined'&&touchBackup)snapshots.push(touchBackup,...touchBackup.history,...touchBackup.future);
 for(const saved of snapshots){const node=saved.nodes?.find(v=>v.id===n.id),a=node?.attachments?.find(v=>v.id===source.id);if(a&&a.promptTranscriptText!==transcript)apply(node,a);}
 renderNode(n);if(selected===n.id)$('modulePrompt').value=n.prompt;
}
async function queueImportedAudio(n,source,file,provider,context=boardImportContext(),includeSoundEvents=soundEventsSelected(provider)){
 assertBoardImportContext(context);
 const activity={title:source.name,detail:'Preparing audio',progress:0,finished:false};mediaActivity.push(activity);renderActivity();
 let decoder;const controller=new AbortController();boardImportControllers.add(controller);
 try{
  const wasmBinary=await embeddedBytes('ffmpeg-wasm-source',controller.signal);assertBoardImportContext(context);const fvadBinary=await embeddedBytes('fvad-wasm-source',controller.signal);assertBoardImportContext(context);
  decoder=decoderClient();await decoder.request('init',{wasmBinary,fvadBinary},[wasmBinary.buffer,fvadBinary.buffer]);assertBoardImportContext(context);
  await prepareTranscriptionQueue(n,source,file,decoder,controller.signal,(message,fraction)=>{if(!boardImportCurrent(context)){controller.abort();return;}activity.detail=message;activity.progress=fraction*100;renderActivity();},provider,includeSoundEvents);assertBoardImportContext(context);
 }catch(error){if(boardImportCurrent(context)){source.status='failed';recordActivity(source.name,'Audio preparation failed. The file is kept; open Files to retry.');}throw error;}
 finally{boardImportControllers.delete(controller);decoder?.stop();activity.finished=true;if(boardImportCurrent(context)){renderActivity();refreshImportedModule(n);}}
}
async function importBoardFiles(files,location,options={}){
 const context=options.importContext||boardImportContext();if(!boardImportCurrent(context))return[];
 if(busy||ioBusy||boardImportRunning){toast('Finish the current import first.');return[];}
 const list=Array.from(files||[]);if(!list.length)return[];const added=[],errors=[],provider=options.provider==='gemini'?'gemini':options.provider==='local'?'local':transcriptionProvider(),includeSoundEvents=provider==='local'&&(typeof options.includeSoundEvents==='boolean'?options.includeSoundEvents:soundEventsSelected(provider)),rect=board.getBoundingClientRect(),origin=location||(list.length>1?{x:rect.left+board.clientWidth/2,y:rect.top+board.clientHeight/2}:undefined);boardImportRunning=true;
 try{for(let index=0;index<list.length;index++){
  if(!boardImportCurrent(context))break;
  const file=list[index],info=boardFileInfo(file),point=boardImportLocation(origin,index);let n;
  try{
   if(info.kind==='video'&&typeof importPCVideoFiles==='function'){
    const nodes=await importPCVideoFiles([file],point,{provider,includeSoundEvents});assertBoardImportContext(context);for(const node of nodes||[]){node.fileType='Video';if(options.title)node.title=String(options.title).slice(0,150);else node.title=file.name;refreshImportedModule(node);added.push(node);}continue;
   }
   if(info.kind==='image'||info.kind==='video'){
    const before=new Set(state.nodes.map(v=>v.id));if(info.kind==='image')await importImages([file],point);else await importMedia([file],point);
    assertBoardImportContext(context);for(const node of state.nodes.filter(v=>!before.has(v.id))){node.fileType=info.label;node.title=String(options.title||file.name).slice(0,150);refreshImportedModule(node);added.push(node);}continue;
   }
   ioBusy=true;n=await createModuleNode('node',String(options.title||file.name||info.label).slice(0,150),point);assertBoardImportContext(context);added.push(n);n.fileType=info.label;
   const data=await readFile(file);assertBoardImportContext(context);const source={id:uid(),name:file.name||'File',mime:info.mime,size:file.size,data,createdAt:new Date().toISOString(),generated:false,status:null};n.attachments.push(source);n.sourceAttachmentId=source.id;
   const poster=await fileModulePoster(file,info);assertBoardImportContext(context);n.src=poster;n.width=480;n.height=300;
   if(info.kind==='audio'){source.role='audio';source.transcriptToPrompt=true;source.status='pending';n.promptEditing=true;renderNode(n);markDirty();await queueImportedAudio(n,source,file,provider,context,includeSoundEvents);assertBoardImportContext(context);}
   renderNode(n);refreshNodeAttachments(n);markDirty();
  }catch(error){if(!boardImportCurrent(context))break;errors.push((file.name||'File')+': '+error.message);if(n){renderNode(n);refreshNodeAttachments(n);markDirty();}}
  finally{if(boardImportCurrent(context))ioBusy=false;}
 }}finally{if(boardImportCurrent(context)){boardImportRunning=false;ioBusy=false;updateRefreshNotice();updateSequence();if(added.length){selectNode(added[added.length-1].id);markDirty();}scheduleTranscriptionQueue();}}
 if(!boardImportCurrent(context))return[];
 toast(errors.length?errors.join('\n'):`${added.length} module${added.length===1?'':'s'} added.`,!!errors.length);return added;
}

// The Add menu is shared by desktop and mobile, with a manual paste fallback
// for browsers that do not grant Clipboard API access to local HTML files.
const boardAddDialog=document.createElement('dialog');boardAddDialog.id='boardAddDialog';boardAddDialog.innerHTML='<div class="board-add-header"><h2>Add to board</h2><button type="button" class="ghost" id="closeBoardAdd" aria-label="Close add menu">×</button></div><div class="board-add-grid"><button type="button" id="boardAddMedia"><strong>Images & videos</strong><span>Photos, GIFs, video clips</span></button><button type="button" id="boardAddAudio"><strong>Audio</strong><span>Voice memos and recordings</span></button><button type="button" id="boardAddFiles"><strong>Documents & files</strong><span>PDF, Excel, HTML, or any file</span></button><button type="button" id="boardPaste"><strong>Paste from clipboard</strong><span>Text, images, or copied files</span></button><button type="button" id="boardAddBlank"><strong>Blank module</strong><span>Start with your own text</span></button></div><div id="boardPasteFallback" hidden><label for="boardPasteText">Paste text here</label><textarea id="boardPasteText" rows="4" placeholder="Touch and hold to paste, or use Ctrl / Command V"></textarea><button type="button" id="boardPasteTextAdd">Add text module</button></div><p class="mini-note" id="boardAddStatus" aria-live="polite">Drop files anywhere on the empty board to give each one its own module.</p>';
document.body.appendChild(boardAddDialog);$('closeBoardAdd').onclick=()=>boardAddDialog.close();
function openBoardAdd(){if(busy||ioBusy){toast('Finish the current import first.');return;}$('boardAddStatus').textContent='Drop files anywhere on the empty board to give each one its own module.';boardAddDialog.showModal();}
for(const id of ['addBtn','emptyAdd']){$(id).onclick=openBoardAdd;$(id).title='Add files, paste, or create a module';$(id).setAttribute('aria-label','Add to board');}
let boardFileChooserContext=null;
function chooseBoardFiles(accept){boardFileChooserContext=boardImportContext();$('imageInput').accept=accept;boardAddDialog.close();$('imageInput').click();}
$('boardAddMedia').onclick=()=>chooseBoardFiles('image/*,video/*,.mp4,.mov,.m4v,.webm,.mkv,.avi');
$('boardAddAudio').onclick=()=>chooseBoardFiles('audio/*,.m4a,.mp3,.wav,.aac,.flac,.ogg,.opus,.aiff,.wma');
$('boardAddFiles').onclick=()=>chooseBoardFiles('');
$('imageInput').onchange=e=>{const context=boardFileChooserContext||boardImportContext();boardFileChooserContext=null;importBoardFiles(e.target.files,undefined,{importContext:context});e.target.value='';};
$('boardAddBlank').onclick=()=>{boardAddDialog.close();addBlankNode();};
function showBoardPasteFallback(message){$('boardPasteFallback').hidden=false;$('boardAddStatus').textContent=message;$('boardPasteText').focus();}
function clipboardFileName(type,index){const ext=({'image/png':'png','image/jpeg':'jpg','image/webp':'webp','image/gif':'gif','text/plain':'txt','text/html':'html','application/pdf':'pdf'})[type]||'bin';return'Clipboard '+(index+1)+'.'+ext;}
async function pasteBoardClipboard(){
 const context=boardImportContext();if(!boardImportCurrent(context))return;
 const checked=async promise=>{const result=await promise;assertBoardImportContext(context);return result;};
 try{
  const files=[];let text='';
  if(navigator.clipboard?.read){const items=await checked(navigator.clipboard.read());for(const item of items){const types=item.types.filter(t=>!/^text\//.test(t)),type=types.find(t=>t.startsWith('image/'))||types[0];if(type){const blob=await checked(item.getType(type));files.push(new File([blob],clipboardFileName(type,files.length),{type:blob.type||type}));}else if(item.types.includes('text/plain'))text+=(text?'\n':'')+await checked((await checked(item.getType('text/plain'))).text());else if(item.types.includes('text/html'))text+=(text?'\n':'')+boardMarkupText(await checked((await checked(item.getType('text/html'))).text()));}}
  else if(navigator.clipboard?.readText)text=await checked(navigator.clipboard.readText());
  else{showBoardPasteFallback('Paste into the field below. You can also use Documents & files to choose an image or file.');return;}
  if(files.length){boardAddDialog.close();await importBoardFiles(files,undefined,{importContext:context});}else if(text.trim()){boardAddDialog.close();await createTextModule(text,{importContext:context});}else showBoardPasteFallback('The clipboard has no readable text or files. Copy something, then paste below.');
 }catch{if(boardImportCurrent(context))showBoardPasteFallback('Clipboard access was not granted. Paste below, or choose Documents & files.');}
}
$('boardPaste').onclick=pasteBoardClipboard;
$('boardPasteTextAdd').onclick=()=>{const text=$('boardPasteText').value;if(!text.trim())return;boardAddDialog.close();$('boardPasteText').value='';createTextModule(text);};
$('boardPasteText').addEventListener('paste',e=>{const files=Array.from(e.clipboardData?.files||[]);if(files.length){e.preventDefault();boardAddDialog.close();importBoardFiles(files);}});
