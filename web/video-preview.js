// The original upload stays in memory only until FUPCJ Server acknowledges it.
// Saved nodes hold a receipt and lightweight preview metadata, never a bearer or project key.
const pcVideoUploads=new Map(),pcVideoWorkers=new Map(),pcVideoPlayers=new Map(),pcVideoRetryAt=new Map();
let pcVideoTimer=null;
const PC_VIDEO_UPLOAD_LIMIT=5*1024*1024*1024,PC_VIDEO_LEGACY_UPLOAD_LIMIT=100*1024*1024,PC_VIDEO_UPLOAD_CHUNK=16*1024*1024,PC_VIDEO_PREVIEW_LIMIT=128*1024*1024;
function normalizePCVideo(value){
 if(!value||typeof value!=='object'||!/^[-\w]{8,120}$/.test(value.requestId||''))return null;
 const config=validCloudConfig({kind:'private-pc',backendUrl:value.backendUrl});
 if(!config||!/^[-\w]{16,120}$/.test(value.projectId||''))return null;
 const id=/^[-\w]{1,120}$/.test(value.id||'')?value.id:'';
 const status=['uploading','waiting','working','error','complete'].includes(value.status)?value.status:'waiting';
 const preview=value.preview&&value.preview.mime==='video/mp4'&&Number(value.preview.size)>0&&Number(value.preview.size)<=PC_VIDEO_PREVIEW_LIMIT?{mime:'video/mp4',size:Number(value.preview.size),width:Math.min(1920,Math.max(1,Number(value.preview.width)||480)),height:Math.min(1920,Math.max(1,Number(value.preview.height)||270))}:null;
 return{requestId:value.requestId,id,projectId:value.projectId,backendUrl:config.backendUrl,sourceId:String(value.sourceId||'').slice(0,100),sourceName:String(value.sourceName||'video.mp4').slice(0,180),sourceSize:Math.max(0,Number(value.sourceSize)||0),provider:value.provider==='gemini'?'gemini':'local',includeSoundEvents:value.includeSoundEvents===true,status,phase:String(value.phase||'').slice(0,250),error:String(value.error||'').slice(0,500),progress:Math.max(0,Math.min(100,Number(value.progress)||0)),preview,previewDeleted:value.previewDeleted===true,needsFile:value.needsFile===true};
}
function pcVideoCurrent(project,n,requestId){return state===project&&state.nodes.includes(n)&&n.pcVideo?.requestId===requestId;}
function pcVideoUpdate(n,values,{save=true}={}){
 n.pcVideo={...n.pcVideo,...values};
 // Keep accepted receipts through undo/redo without replaying an original upload.
 if(n.pcVideo.id&&(values.id||values.preview))for(const saved of [...history,...future]){
  const target=saved.nodes?.find(item=>item.id===n.id&&item.pcVideo?.requestId===n.pcVideo.requestId);
  if(target)target.pcVideo={...target.pcVideo,id:n.pcVideo.id,preview:n.pcVideo.preview,status:'waiting',phase:'Reconnecting to FUPCJ Server',error:''};
 }
 installPCVideoControls(n);if(save)markDirty();if(typeof renderActivity==='function')renderActivity();
}
function pcVideoPath(n,suffix=''){
 const media=n.pcVideo,meta=ensureProjectIdentity();
 if(media.projectId!==meta.id||media.backendUrl!==cloudConfig?.backendUrl)throw new Error('Open this video’s original project and connect its original FUPCJ Server to continue.');
 return '/projects/'+encodeURIComponent(meta.id)+'/media'+(media.id?'/'+encodeURIComponent(media.id):'')+suffix;
}
async function pcVideoRequest(n,suffix='',options={}){
 const controller=new AbortController(),parent=options.signal,abort=()=>controller.abort();
 parent?.addEventListener('abort',abort,{once:true});if(parent?.aborted)abort();
 const timeout=Number(options.timeoutMs)||((options.method==='POST'&&options.body instanceof FormData)?6*60*60*1000:options.method==='PUT'?10*60*1000:options.binary?120000:30000),timer=setTimeout(abort,timeout);
 try{const {binary,maxBytes=PC_VIDEO_PREVIEW_LIMIT,timeoutMs,...request}=options,response=await projectRequest(pcVideoPath(n,suffix),{...request,signal:controller.signal});if(!response.ok||!binary)return await projectResponse(response);
  if(Number(response.headers.get('Content-Length'))>maxBytes)throw new Error('FUPCJ Server file exceeds its download size limit.');
  const reader=response.body?.getReader();if(!reader){const blob=await response.blob();if(blob.size>maxBytes)throw new Error('FUPCJ Server file exceeds its download size limit.');return blob;}
  const parts=[];let bytes=0;try{while(true){const{done,value}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>maxBytes)throw new Error('FUPCJ Server file exceeds its download size limit.');parts.push(value);}return new Blob(parts,{type:response.headers.get('Content-Type')||'application/octet-stream'});}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
 }
 finally{clearTimeout(timer);parent?.removeEventListener('abort',abort);}
}
function pcVideoSetPoster(n,data,project){
 if(!/^data:image\/jpeg;base64,[A-Za-z0-9+/]+=*$/.test(data||'')||data.length>2*1024*1024||n.src===data)return;
 n.src=data;void R.loadImage(data).then(img=>{if(state!==project||!state.nodes.includes(n)||n.src!==data)return;n.width=img.naturalWidth;n.height=img.naturalHeight;void renderNode(n);markDirty();}).catch(()=>{});
}
async function pcVideoFirstFrame(n,file,project){
 const video=document.createElement('video'),url=URL.createObjectURL(file);video.muted=true;video.playsInline=true;video.preload='metadata';
 try{
  const data=await new Promise(resolve=>{let done=false;const finish=value=>{if(done)return;done=true;clearTimeout(timer);resolve(value);};const capture=()=>{try{if(!video.videoWidth||!video.videoHeight)return finish(null);const canvas=document.createElement('canvas'),ratio=Math.min(480/video.videoWidth,480/video.videoHeight,1);canvas.width=Math.max(1,Math.round(video.videoWidth*ratio));canvas.height=Math.max(1,Math.round(video.videoHeight*ratio));canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);finish(canvas.toDataURL('image/jpeg',.65));canvas.width=canvas.height=1;}catch{finish(null);}};const timer=setTimeout(()=>finish(null),4000);video.onloadeddata=capture;video.onseeked=capture;video.onerror=()=>finish(null);video.onloadedmetadata=()=>{try{video.currentTime=0;}catch{}};video.src=url;video.load();});
  if(data&&state===project&&state.nodes.includes(n)&&!n.pcVideo?.id)pcVideoSetPoster(n,data,project);
 }finally{video.removeAttribute('src');video.load();URL.revokeObjectURL(url);}
}
function pcVideoUploadPhase(sent,total,started,initial){
 const percent=total>0?Math.max(0,Math.min(100,100*sent/total)):0,elapsed=Math.max(.001,(performance.now()-started)/1000),rate=Math.max(0,(sent-initial)/elapsed);
 return 'Uploading to FUPCJ Server · '+percent.toFixed(percent<10?1:0)+'% · '+readableBytes(sent)+' of '+readableBytes(total)+(rate>0?' · '+readableBytes(rate)+'/s':'')+' · keep this page open';
}
async function pcVideoBeginResumable(n,file,signal){
 return pcVideoRequest(n,'/upload/'+encodeURIComponent(n.pcVideo.requestId),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:file.name,size:file.size}),signal,timeoutMs:30000});
}
async function pcVideoUploadResumable(n,file,health,signal,check){
 let state=await pcVideoBeginResumable(n,file,signal);check();
 if(state.accepted&&/^[-\w]{1,120}$/.test(state.id||''))return state;
 let offset=Number(state.receivedBytes),total=Number(state.totalBytes),chunkBytes=Number(state.chunkBytes)||Number(health.videoMedia?.uploadChunkBytes)||PC_VIDEO_UPLOAD_CHUNK;
 if(!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(total)||total!==file.size||offset>total)throw new Error('FUPCJ Server returned an invalid resumable upload state.');
 chunkBytes=Math.max(1024*1024,Math.min(64*1024*1024,Math.floor(chunkBytes)));
 const started=performance.now(),initial=offset,show=()=>pcVideoUpdate(n,{status:'uploading',error:'',needsFile:false,progress:total?100*offset/total:0,phase:pcVideoUploadPhase(offset,total,started,initial)},{save:false});
 show();
 while(offset<total){
  check();const end=Math.min(total,offset+chunkBytes),chunk=file.slice(offset,end);let result;
  try{
   result=await pcVideoRequest(n,'/upload/'+encodeURIComponent(n.pcVideo.requestId)+'?offset='+offset,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:chunk,signal,timeoutMs:10*60*1000});
  }catch(error){
   if(error.status===409&&error.data?.code==='VISION_UPLOAD_OFFSET'){
    state=await pcVideoBeginResumable(n,file,signal);check();
    if(state.accepted&&/^[-\w]{1,120}$/.test(state.id||''))return state;
    const resumed=Number(state.receivedBytes);if(!Number.isSafeInteger(resumed)||resumed<0||resumed>total)throw error;offset=resumed;show();continue;
   }
   throw error;
  }
  check();
  if(result.accepted&&/^[-\w]{1,120}$/.test(result.id||'')){offset=total;show();return result;}
  const next=Number(result.receivedBytes);
  if(!Number.isSafeInteger(next)||next<=offset||next>end)throw new Error('FUPCJ Server returned an invalid upload offset.');
  offset=next;show();
 }
 const accepted=await pcVideoRequest(n,'/request/'+encodeURIComponent(n.pcVideo.requestId),{signal,timeoutMs:30000});check();
 if(!/^[-\w]{1,120}$/.test(accepted.id||''))throw new Error('FUPCJ Server did not confirm the completed upload.');
 return accepted;
}
async function importPCVideoFiles(files,location,options={}){
 const provider=options.provider==='gemini'?'gemini':options.provider==='local'?'local':transcriptionProvider(),includeSoundEvents=typeof options.includeSoundEvents==='boolean'?options.includeSoundEvents:soundEventsSelected(provider);
 const context=boardAsyncContext(),project=context.project,created=[];
 for(const [index,file]of Array.from(files).entries()){
  if(!boardAsyncCurrent(context))break;
  const point=location?{x:location.x+index*40,y:location.y+index*35}:undefined;
  const n=await createModuleNode('video',file.name||'Video',point);assertBoardAsyncContext(context);
  const meta=ensureProjectIdentity(),source={id:uid(),name:file.name||'video.mp4',mime:file.type||mimeFromName(file.name),size:file.size,metadataOnly:true,role:'video',generated:false,status:'pending',snapshotsStatus:'pending',transcriptionStatus:'pending',createdAt:new Date().toISOString()};
  n.attachments.push(source);n.videoSourceId=source.id;n.fileType='Video';
  n.pcVideo={requestId:'media-'+uid(),id:'',projectId:meta.id,backendUrl:meta.backendUrl||cloudConfig?.backendUrl||'',sourceId:source.id,sourceName:source.name,sourceSize:file.size,provider,includeSoundEvents,status:'waiting',phase:'Waiting to upload · keep this page open',progress:0,error:'',preview:null};
  created.push(n);if(file.size>PC_VIDEO_UPLOAD_LIMIT)pcVideoUpdate(n,{status:'error',error:'This video exceeds FUPCJ Server’s 5 GB upload limit. Choose a smaller copy.'});else{pcVideoUploads.set(n.pcVideo.requestId,file);void pcVideoFirstFrame(n,file,project);}
  refreshNodeAttachments(n);installPCVideoControls(n);markDirty();
 }
 if(boardAsyncCurrent(context))schedulePCVideos();return created;
}
function schedulePCVideos(delay=0){clearTimeout(pcVideoTimer);pcVideoTimer=setTimeout(()=>void pumpPCVideos(),Math.max(0,delay));}
function pcVideoResume(){
 for(const [key,runtime]of pcVideoWorkers)if(!pcVideoCurrent(runtime.project,runtime.n,key)){runtime.controller.abort();pcVideoWorkers.delete(key);}
 for(const [key]of pcVideoUploads)if(!state.nodes.some(n=>n.pcVideo?.requestId===key))pcVideoUploads.delete(key);
 for(const [key]of pcVideoRetryAt)if(!state.nodes.some(n=>n.pcVideo?.requestId===key))pcVideoRetryAt.delete(key);
 cleanupPCVideoPlayers();schedulePCVideos();
}
async function pumpPCVideos(){
 for(const [key,runtime]of pcVideoWorkers)if(!pcVideoCurrent(runtime.project,runtime.n,key)){runtime.controller.abort();pcVideoWorkers.delete(key);}
 const project=state;
 for(const n of state.nodes){
  const media=n.pcVideo;if(!media||media.status==='complete'||pcVideoWorkers.has(media.requestId)||pcVideoWorkers.size>=2)continue;
  if(media.status==='error'||(pcVideoRetryAt.get(media.requestId)||0)>Date.now())continue;
  pcVideoRetryAt.delete(media.requestId);
  const runtime={project,n,controller:new AbortController()};pcVideoWorkers.set(media.requestId,runtime);void workPCVideo(runtime);
 }
 if(state.nodes.some(n=>n.pcVideo&&!['error','complete'].includes(n.pcVideo.status)))schedulePCVideos(1500);
}
async function workPCVideo(runtime){
 const{project,n,controller}=runtime,signal=controller.signal,requestId=n.pcVideo.requestId,authEpoch=typeof accountAuthEpoch==='undefined'?0:accountAuthEpoch;
 const sameAccount=()=>authEpoch===(typeof accountAuthEpoch==='undefined'?0:accountAuthEpoch);
 const check=()=>{if(signal.aborted||!sameAccount()||!pcVideoCurrent(project,n,requestId))throw new DOMException('The destination project or account changed.','AbortError');};
 try{
  check();
  if(!n.pcVideo.id){
   pcVideoUpdate(n,{status:'waiting',error:'',phase:'Connecting to FUPCJ Server · video not uploaded yet'});
   const health=await projectCapabilities();check();const caps=health.capabilities,ready=Array.isArray(caps)?caps.includes('uploadedMedia'):caps?.uploadedMedia===true;
   if(!ready)throw new Error('Update FUPCJ Server to enable video uploads and compressed previews.');
   let recovered=null;
   try{recovered=await pcVideoRequest(n,'/request/'+encodeURIComponent(requestId),{signal});}catch(error){if(error.status!==404)throw error;}check();
   if(recovered&&/^[-\w]{1,120}$/.test(recovered.id||'')){
    pcVideoUpdate(n,{id:recovered.id,status:'working',phase:'Recovered accepted upload',error:''});pcVideoUploads.delete(requestId);
   }else{
    const file=pcVideoUploads.get(requestId),limit=Number(health.videoMedia?.maxUploadBytes)||PC_VIDEO_UPLOAD_LIMIT;
    if(!file){const missing=new Error('FUPCJ Server has no accepted upload for this video. Select the same video again to upload it.');missing.code='VISION_VIDEO_FILE_REQUIRED';throw missing;}if(file.size>limit)throw new Error('This video exceeds FUPCJ Server upload limit of '+Math.round(limit/1048576)+' MB. Choose a smaller copy.');
    await ensureRemoteProject();check();let accepted;
    if(health.videoMedia?.resumableUpload===true){
     accepted=await pcVideoUploadResumable(n,file,health,signal,check);
    }else{
     if(file.size>PC_VIDEO_LEGACY_UPLOAD_LIMIT)throw new Error('Update FUPCJ Server to enable reliable resumable uploads for videos over 100 MB.');
     const form=new FormData();form.append('file',file,file.name);form.append('requestId',requestId);
     pcVideoUpdate(n,{status:'uploading',phase:'Uploading video · keep this page open until accepted',progress:0});
     accepted=await pcVideoRequest(n,'',{method:'POST',body:form,signal});check();
    }
    if(!/^[-\w]{1,120}$/.test(accepted.id||''))throw new Error('FUPCJ Server did not confirm the upload. Retry with the same video.');
    pcVideoUpdate(n,{id:accepted.id,status:'working',phase:'Accepted by FUPCJ Server · preparing preview',progress:0});pcVideoUploads.delete(requestId);
   }
  }
  const status=await pcVideoRequest(n,'',{signal});check();
  if(status.thumbnail)pcVideoSetPoster(n,status.thumbnail,project);
  if(['error','failed','cancelled'].includes(status.status))throw new Error(status.error||'FUPCJ Server could not prepare this video. Select the video again to start another attempt.');
  pcVideoUpdate(n,{status:'working',error:'',phase:String(status.phase||'Processing on FUPCJ Server'),progress:Math.min(99,Number(status.progress)||0)},{save:false});
  if(status.status==='complete'){
   const result=await pcVideoRequest(n,'/result',{signal});check();await applyPCVideoResult(runtime,result);check();
   pcVideoUpdate(n,{status:'complete',phase:'FUPCJ Server preview ready',progress:100,error:''});
   refreshNodeAttachments(n);updateSequence();recordActivity(n.title,'Video preview and snapshots ready');
   if(!(n.transcriptionJobs||[]).some(job=>job.sourceId===n.pcVideo.sourceId))toast('Video ready: '+n.title);
  }
 }catch(error){
  if(!pcVideoCurrent(project,n,requestId)||signal.aborted||!sameAccount())return;
  if(!error.status&&(error.name==='AbortError'||error.code==='VISION_SERVER_UNAVAILABLE'||error.retryable===true)){
   // Preserve the request identity: the next attempt checks for an accepted
   // receipt before resending an upload whose response may have been lost.
   pcVideoRetryAt.set(requestId,Date.now()+15000);
   pcVideoUpdate(n,{status:'waiting',error:'',needsFile:false,phase:n.pcVideo.id?'Connection interrupted · reconnecting automatically in 15 seconds':'Connection interrupted · retrying in 15 seconds · keep this page open'});
   return;
  }
  const message=error.name==='AbortError'?(n.pcVideo.id?'FUPCJ Server did not reply. Its video work may still be running. Retry to reconnect.':'The upload was not confirmed. Keep this page open and retry.'):String(error.message||'Video processing could not finish.');
  pcVideoUpdate(n,{status:'error',error:message,needsFile:error.code==='VISION_VIDEO_FILE_REQUIRED'});
 }finally{if(pcVideoWorkers.get(requestId)===runtime)pcVideoWorkers.delete(requestId);schedulePCVideos(1500);}
}
function pcVideoArtifactSuffix(n,value,type,index){
 const expected='/api'+pcVideoPath(n,type==='audio'?'/audio/'+index:'/preview');
 if(value!==expected)throw new Error('FUPCJ Server returned an invalid '+type+' address.');
 return type==='audio'?'/audio/'+index:'/preview';
}
async function applyPCVideoResult(runtime,result){
 const{project,n,controller}=runtime,requestId=n.pcVideo.requestId,source=n.attachments.find(a=>a.id===n.pcVideo.sourceId);
 const check=()=>{if(controller.signal.aborted||!pcVideoCurrent(project,n,requestId)||!n.attachments.includes(source))throw new DOMException('The destination module changed.','AbortError');};check();
 if(!source||!Array.isArray(result.frames)||!result.frames.length||result.frames.length>120||!Array.isArray(result.audioSections)||result.audioSections.length>16)throw new Error('FUPCJ Server returned incomplete video results.');
 let frameBytes=0;const frames=result.frames.map((frame,index)=>{if(!/^data:image\/jpeg;base64,[A-Za-z0-9+/]+=*$/.test(frame.data||'')||!Number.isFinite(frame.timestamp)||frame.timestamp<0)throw new Error('A video snapshot could not be read.');frameBytes+=frame.data.length;if(frameBytes>28*1024*1024)throw new Error('The video snapshots exceed the project size limit.');return{id:source.id+'-frame-'+index,name:String(frame.name||'frame-'+index+'.jpg').slice(0,180),mime:'image/jpeg',size:bytesFromDataURL(frame.data).length,data:frame.data,timestamp:frame.timestamp,requestedTimestamp:frame.timestamp,role:'video-frame',videoOf:source.id,generated:true,status:'complete',createdAt:new Date().toISOString()};});
 pcVideoArtifactSuffix(n,result.preview?.url,'preview');
 const preview=normalizePCVideo({...n.pcVideo,preview:result.preview})?.preview;if(!preview)throw new Error('FUPCJ Server returned an invalid video preview.');
 pcVideoUpdate(n,{preview,phase:'Receiving snapshots and prepared audio'});
 const hasJob=n.transcriptionJobs?.some(job=>job.sourceId===source.id),sections=[];
 if(!hasJob&&source.transcriptionStatus!=='complete')for(const [index,part]of result.audioSections.entries()){
  check();if(!Number.isFinite(part.start)||!Number.isFinite(part.end)||part.start<0||part.end<=part.start||part.end-part.start>901||part.mime!=='audio/mpeg')throw new Error('An audio section has invalid timing.');
  const suffix=pcVideoArtifactSuffix(n,part.url,'audio',index),blob=await pcVideoRequest(n,suffix,{binary:true,maxBytes:LOCAL_ASR_SECTION_MAX_BYTES,signal:controller.signal});check();
  if(blob.size>LOCAL_ASR_SECTION_MAX_BYTES||!blob.size)throw new Error('An audio section exceeds the transcription size limit.');
  const audioData=await readFile(new Blob([blob],{type:part.mime}));check();sections.push({start:part.start,end:part.end,mimeType:part.mime,audioData,text:null,done:false});
 }
 check();n.attachments=n.attachments.filter(a=>!(a.videoOf===source.id&&a.role==='video-frame'));n.attachments.push(...frames);
 source.videoDuration=Number(result.duration)||0;source.snapshotInterval=Number(result.snapshotInterval)||0;source.videoHasAudio=!!result.audioSections.length;source.snapshotsStatus='complete';
 pcVideoSetPoster(n,result.thumbnail||frames[0].data,project);
 if(sections.length){const job={id:uid(),provider:n.pcVideo.provider,includeSoundEvents:n.pcVideo.includeSoundEvents===true,sourceId:source.id,sourceName:source.name,sourceKind:'video',offsetSeconds:0,createdAt:new Date().toISOString(),status:'waiting',error:null,sections};n.transcriptionJobs=n.transcriptionJobs||[];n.transcriptionJobs.push(job);source.status='queued';source.transcriptionStatus='waiting';queueChanged(n);scheduleTranscriptionQueue();}
 else if(!result.audioSections.length)storeVideoTranscript(n,source,'No audio track was detected. Review the timestamped snapshots.','no-audio');
 markDirty();
}
function retryPCVideo(n){
 if(!state.nodes.includes(n)||!n.pcVideo)return;
 pcVideoRetryAt.delete(n.pcVideo.requestId);
 if(n.pcVideo.needsFile&&!n.pcVideo.id&&!pcVideoUploads.has(n.pcVideo.requestId)){choosePCVideoAgain(n);return;}
 pcVideoUpdate(n,{status:'waiting',error:'',needsFile:false,phase:n.pcVideo.id?'Reconnecting to FUPCJ Server':'Waiting to upload · keep this page open'});schedulePCVideos();
}
function choosePCVideoAgain(n){
 const project=state,input=document.createElement('input');input.type='file';input.accept='video/*,.mp4,.mov,.m4v,.webm,.mkv';
 input.onchange=()=>{const file=input.files?.[0];if(!file||state!==project||!state.nodes.includes(n))return;if(file.size>PC_VIDEO_UPLOAD_LIMIT){toast('Choose a video no larger than 5 GB.',true);return;}
  if(file.name!==n.pcVideo.sourceName||file.size!==n.pcVideo.sourceSize){toast('Choose the same video, or drop a different video onto the board to make a new module.',true);return;}
  const oldId=n.pcVideo.requestId;
  // A terminal FUPCJ Server failure can be restarted using a fresh receipt, while an unconfirmed upload keeps its identity.
  if(n.pcVideo.id)pcVideoUpdate(n,{requestId:'media-'+uid(),id:'',preview:null});
  pcVideoUploads.delete(oldId);pcVideoUploads.set(n.pcVideo.requestId,file);retryPCVideo(n);
 };input.click();
}
function stopPCVideoPlayer(id){const player=pcVideoPlayers.get(id);if(!player)return;player.controller?.abort();player.video?.pause();player.video?.removeAttribute('src');player.video?.load();player.video?.remove();player.close?.remove();if(player.url)URL.revokeObjectURL(player.url);pcVideoPlayers.delete(id);}
function cleanupPCVideoPlayers(){for(const [id,player]of pcVideoPlayers)if(!player.element?.isConnected||!state.nodes.some(n=>n.id===id&&n.pcVideo?.requestId===player.requestId))stopPCVideoPlayer(id);}
async function playPCVideo(n,button){
 const project=state,requestId=n.pcVideo.requestId,wrap=button.closest('.canvas-wrap');if(!wrap)return;stopPCVideoPlayer(n.id);
 const controller=new AbortController(),entry={controller,element:wrap,requestId,url:null,video:null};pcVideoPlayers.set(n.id,entry);button.disabled=true;button.textContent='Loading preview…';
 try{const blob=await pcVideoRequest(n,'/preview',{binary:true,signal:controller.signal});if(!pcVideoCurrent(project,n,requestId)||!wrap.isConnected||controller.signal.aborted)return;
  entry.url=URL.createObjectURL(new Blob([blob],{type:'video/mp4'}));const player=document.createElement('video');entry.video=player;player.className='pc-video-player';player.controls=true;player.playsInline=true;player.preload='metadata';player.poster=n.src;player.src=entry.url;player.setAttribute('aria-label',n.title+' compressed video preview');for(const type of ['pointerdown','click','dblclick','touchstart','wheel'])player.addEventListener(type,e=>e.stopPropagation());wrap.appendChild(player);const close=document.createElement('button');entry.close=close;close.type='button';close.className='pc-video-close';close.textContent='×';close.setAttribute('aria-label','Close video preview');close.onpointerdown=e=>e.stopPropagation();close.onclick=e=>{e.stopPropagation();stopPCVideoPlayer(n.id);installPCVideoControls(n);};wrap.appendChild(close);button.hidden=true;await player.play().catch(()=>{});
 }catch(error){if(controller.signal.aborted)return;toast(error.message||'Could not load FUPCJ Server preview.',true);stopPCVideoPlayer(n.id);}
 finally{if(button.isConnected){button.disabled=false;button.textContent='▶ Play preview';}}
}
function installPCVideoControls(n){
 const el=document.getElementById('node-'+n.id),media=n.pcVideo;if(!el||!media)return;const wrap=el.querySelector('.canvas-wrap');if(!wrap)return;
 let panel=el.querySelector('.pc-video-status');if(!panel){panel=document.createElement('div');panel.className='pc-video-status';panel.setAttribute('aria-live','polite');el.querySelector('.node-files').before(panel);}
 panel.replaceChildren();const text=document.createElement('span');text.textContent=media.status==='error'?media.error||'Video processing stopped.':media.phase||'Waiting for FUPCJ Server';panel.appendChild(text);
 if(!['complete','error'].includes(media.status)){const progress=document.createElement('progress');progress.className='pc-video-progress';progress.max=100;progress.value=Math.max(0,Math.min(100,Number(media.progress)||0));progress.setAttribute('aria-label',Math.round(progress.value)+'% complete');panel.appendChild(progress);}
 let overlay=wrap.querySelector('.pc-video-overlay');if(!overlay){overlay=document.createElement('div');overlay.className='pc-video-overlay';wrap.appendChild(overlay);}overlay.replaceChildren();
 if(!['complete','error'].includes(media.status)){const spinner=document.createElement('span');spinner.className='pc-video-spinner';spinner.setAttribute('aria-label',media.id?'FUPCJ Server processing video':'Video upload pending');overlay.appendChild(spinner);}
 if(media.preview){const play=document.createElement('button');play.type='button';play.className='pc-video-play';play.textContent='▶ Play preview';play.setAttribute('aria-label','Play '+n.title+' preview');play.hidden=!!pcVideoPlayers.get(n.id)?.video;play.onclick=e=>{e.stopPropagation();void playPCVideo(n,play);};play.onpointerdown=e=>e.stopPropagation();overlay.appendChild(play);}
 if(media.status==='error'){const retry=document.createElement('button');retry.type='button';retry.className='ghost';retry.textContent=media.needsFile&&!media.id&&!pcVideoUploads.has(media.requestId)?'Select video again':'Retry';retry.onclick=()=>retryPCVideo(n);panel.appendChild(retry);if(media.id){const reselect=document.createElement('button');reselect.type='button';reselect.className='ghost';reselect.textContent='Reprocess video';reselect.onclick=()=>choosePCVideoAgain(n);panel.appendChild(reselect);}if(/Update FUPCJ Server/.test(media.error)){const help=document.createElement('a');help.textContent='FUPCJ Server update instructions';help.href='https://github.com/JRDN-R/vision/tree/main/vision-pc#updating';help.target='_blank';help.rel='noopener noreferrer';panel.appendChild(help);}}
 const foot=el.querySelector('.node-foot span');if(foot)foot.textContent=media.preview?'Video · small FUPCJ Server preview':media.previewDeleted?'Video · snapshots saved':'Video · FUPCJ Server processing';
}
function pcVideoMarkDeleted(project,id){
 if(state!==project)return;
 for(const n of state.nodes)if(n.pcVideo?.id===id){const key=n.pcVideo.requestId;pcVideoWorkers.get(key)?.controller.abort();pcVideoWorkers.delete(key);pcVideoUploads.delete(key);stopPCVideoPlayer(n.id);pcVideoUpdate(n,{preview:null,previewDeleted:true,status:'complete',phase:'FUPCJ Server video files removed · saved snapshots and transcript kept',error:''});}
 for(const saved of [...history,...future])for(const n of saved.nodes||[])if(n.pcVideo?.id===id)n.pcVideo={...n.pcVideo,preview:null,previewDeleted:true,status:'complete',phase:'FUPCJ Server video files removed',error:''};
}
let pcVideoStorageContext=null;
const pcVideoStorageDialog=document.createElement('dialog');pcVideoStorageDialog.id='pcVideoStorageDialog';pcVideoStorageDialog.innerHTML='<div class="row spread"><h2>FUPCJ Server video storage</h2><button class="dialog-close" aria-label="Close FUPCJ Server video storage">×</button></div><p class="mini-note">Video files retained by this project’s FUPCJ Server, including videos whose modules were removed. Saved snapshots and transcripts stay in your project when these FUPCJ Server files are deleted.</p><p class="pc-video-storage-message" role="status"></p><div class="pc-video-storage-list"></div><div class="row"><button class="pc-video-storage-refresh">Refresh</button><button class="pc-video-storage-more" hidden>Show more</button></div>';
document.body.appendChild(pcVideoStorageDialog);pcVideoStorageDialog.querySelector('.dialog-close').onclick=()=>pcVideoStorageDialog.close();
const pcVideoStorageButton=document.createElement('button');pcVideoStorageButton.id='openPCVideoStorage';pcVideoStorageButton.textContent='FUPCJ Server video storage';pcVideoStorageButton.onclick=()=>void openPCVideoStorage();$('projectsDialog')?.querySelector('.project-actions')?.appendChild(pcVideoStorageButton);
async function openPCVideoStorage(){
 $('projectsDialog')?.close();pcVideoStorageContext={project:state,cursor:null};pcVideoStorageDialog.querySelector('.pc-video-storage-list').replaceChildren();if(!pcVideoStorageDialog.open)pcVideoStorageDialog.showModal();await loadPCVideoStorage(true);
}
async function loadPCVideoStorage(reset=false){
 const context=pcVideoStorageContext,message=pcVideoStorageDialog.querySelector('.pc-video-storage-message'),list=pcVideoStorageDialog.querySelector('.pc-video-storage-list'),more=pcVideoStorageDialog.querySelector('.pc-video-storage-more');if(!context)return;
 const check=()=>{if(state!==context.project||context!==pcVideoStorageContext)throw new Error('The project changed. Reopen FUPCJ Server video storage from the intended project.');};
 try{check();message.textContent='Loading FUPCJ Server video files…';more.disabled=true;const health=await projectCapabilities();check();if(!(Array.isArray(health.capabilities)?health.capabilities.includes('uploadedMedia'):health.capabilities?.uploadedMedia))throw new Error('Update FUPCJ Server to manage stored videos.');
  const meta=await ensureRemoteProject();check();const anchor={pcVideo:{projectId:meta.id,backendUrl:meta.backendUrl,id:''}},cursor=reset?'':context.cursor;const result=await pcVideoRequest(anchor,cursor?'?cursor='+encodeURIComponent(cursor):'');check();
  if(!Array.isArray(result.items))throw new Error('FUPCJ Server did not return its video inventory.');if(reset)list.replaceChildren();
  for(const item of result.items){if(!/^[-\w]{1,120}$/.test(item.id||''))continue;const row=document.createElement('div');row.className='pc-video-storage-row';const description=document.createElement('span'),name=document.createElement('strong'),detail=document.createElement('small');name.textContent=String(item.sourceName||'Video');const referenced=state.nodes.some(n=>n.pcVideo?.id===item.id&&!n.pcVideo.previewDeleted);detail.textContent=[referenced?'On this board':'Not on this board',String(item.status||''),readableBytes(Math.max(0,Number(item.bytes)||0))].join(' · ');description.append(name,detail);row.appendChild(description);
   const remove=document.createElement('button');remove.textContent='Delete FUPCJ Server files';remove.className='ghost';remove.onclick=async()=>{try{check();if(!confirm('Delete FUPCJ Server video files for “'+name.textContent+'”? This also cancels pending video processing. Existing snapshots and transcripts stay in your project, but saved copies and undo will no longer be able to play this FUPCJ Server preview.'))return;remove.disabled=true;const target={pcVideo:{projectId:meta.id,backendUrl:meta.backendUrl,id:item.id}};await pcVideoRequest(target,'',{method:'DELETE'});check();pcVideoMarkDeleted(context.project,item.id);row.remove();message.textContent='Removal requested. Refresh to check remaining FUPCJ Server files.';}catch(error){message.textContent=error.message;remove.disabled=false;}};row.appendChild(remove);list.appendChild(row);
  }
  context.cursor=typeof result.nextCursor==='string'?result.nextCursor:null;more.hidden=!context.cursor;message.textContent=list.children.length?'Only delete videos you no longer need to play.':'No retained FUPCJ Server video files for this project.';
 }catch(error){message.textContent=error.message;}finally{more.disabled=false;}
}
pcVideoStorageDialog.querySelector('.pc-video-storage-refresh').onclick=()=>void loadPCVideoStorage(true);pcVideoStorageDialog.querySelector('.pc-video-storage-more').onclick=()=>void loadPCVideoStorage(false);
const pcVideoOriginalValidate=validateProject;
validateProject=async function(raw){const loaded=await pcVideoOriginalValidate(raw),byId=new Map((raw?.nodes||[]).map(n=>[n.id,n]));for(const n of loaded.nodes){const media=normalizePCVideo(byId.get(n.id)?.pcVideo);if(media&&n.attachments.some(a=>a.id===media.sourceId)){n.pcVideo=media;if(media.status!=='complete')n.pcVideo={...media,status:'waiting',phase:'Checking saved video upload',error:''};}}return loaded;};
const pcVideoOriginalRemoveAttachment=removeAttachment;
removeAttachment=function(n,a){const receipt=n.pcVideo;if(receipt?.sourceId===a.id&&!receipt.id){const anchor={pcVideo:{...receipt,id:''}};void pcVideoRequest(anchor,'/upload/'+encodeURIComponent(receipt.requestId),{method:'DELETE',timeoutMs:30000}).catch(()=>{});}pcVideoOriginalRemoveAttachment(n,a);if(receipt?.sourceId===a.id&&!n.attachments.some(item=>item.id===a.id)){delete n.pcVideo;pcVideoUploads.delete(receipt.requestId);pcVideoWorkers.get(receipt.requestId)?.controller.abort();pcVideoWorkers.delete(receipt.requestId);stopPCVideoPlayer(n.id);const el=document.getElementById('node-'+n.id);el?.querySelector('.pc-video-status')?.remove();el?.querySelector('.pc-video-overlay')?.remove();markDirty();}};
const pcVideoOriginalRenderNode=renderNode;
renderNode=async function(n){await pcVideoOriginalRenderNode(n);installPCVideoControls(n);};
const pcVideoOriginalRenderAll=renderAll;
renderAll=function(){for(const id of pcVideoPlayers.keys())stopPCVideoPlayer(id);pcVideoOriginalRenderAll();pcVideoResume();};
new MutationObserver(cleanupPCVideoPlayers).observe(world,{childList:true,subtree:true});
window.addEventListener('online',()=>{for(const n of state.nodes){const media=n.pcVideo;if(media&&(pcVideoRetryAt.has(media.requestId)||(media.id||pcVideoUploads.has(media.requestId))&&media.status==='error'&&/^(This device cannot reach FUPCJ Server\.|FUPCJ Server did not reply\.|The upload was not confirmed\.)/.test(media.error)))retryPCVideo(n);}schedulePCVideos();});
window.addEventListener('beforeunload',event=>{if(pcVideoUploads.size){event.preventDefault();event.returnValue='';}});
window.addEventListener('pagehide',()=>{for(const id of pcVideoPlayers.keys())stopPCVideoPlayer(id);});
schedulePCVideos();
