// Vortex uses the saved media-import queue; closing a dialog never cancels Apply.
let vortexLookup=null,vortexChoice=null,vortexDestination=null,vortexApplying=false;
function normalizeVortexURL(value){
 const raw=String(value||'').trim();let url;
 try{url=new URL(/^https?:\/\//i.test(raw)?raw:'https://'+raw);}catch{throw new Error('Paste a video or audio link.');}
 if(!['http:','https:'].includes(url.protocol)||url.username||url.password||raw.length>2000||!url.hostname.includes('.'))throw new Error('Paste a public video or audio link.');
 return url.href;
}
const vortexDialog=document.createElement('dialog');vortexDialog.id='vortexImportDialog';vortexDialog.setAttribute('aria-labelledby','vortexImportTitle');
vortexDialog.innerHTML='<div class="row spread"><h2 id="vortexImportTitle">Vortex</h2><button id="closeVortexImport" class="dialog-close" type="button" aria-label="Close Vortex import">×</button></div><p class="intro">Bring a video or audio link straight onto your board.</p><label for="vortexImportURL">Video or audio URL</label><div class="vortex-import-orbit"><form id="vortexImportFindForm"><input id="vortexImportURL" type="url" inputmode="url" placeholder="Paste a media link…" autocomplete="off" maxlength="2000"><button id="vortexImportFind" type="submit">Find</button></form></div><p id="vortexImportStatus" class="mini-note" role="status" aria-live="polite">Video when available. Audio for music and audio-only links.</p><div id="vortexImportSelection" hidden><strong id="vortexImportName"></strong><span id="vortexImportType"></span></div><details id="vortexImportControls"><summary>Controls</summary><label class="youtube-target"><input id="vortexImportNewModule" type="checkbox"> Add as a new module</label><div id="vortexImportProviderAnchor"></div></details><div class="dialog-actions"><button id="vortexImportApply" class="primary" type="button" disabled>Apply</button></div>';
document.body.appendChild(vortexDialog);
const vortexProvider=providerChoice(transcriptionProvider(),setTranscriptionProvider,'New transcriptions');vortexProvider.id='vortexImportProvider';$('vortexImportProviderAnchor').after(vortexProvider);vortexProvider.after(soundEventsChoice('vortexImportProviderSounds'));
const vortexBoardButton=document.createElement('button');vortexBoardButton.type='button';vortexBoardButton.id='boardAddVortex';vortexBoardButton.innerHTML='<strong>Vortex</strong><span>Import a video or audio link</span>';$('boardAddAudio').after(vortexBoardButton);
const vortexFilesButton=document.createElement('button');vortexFilesButton.type='button';vortexFilesButton.id='addAttachmentsVortex';vortexFilesButton.className='ghost';vortexFilesButton.textContent='+ Vortex';$('youtubeButton').after(vortexFilesButton);
function syncVortexImport(){
 $('vortexImportApply').disabled=vortexApplying||!!vortexLookup||!vortexChoice;
 $('vortexImportFind').disabled=vortexApplying||!!vortexLookup||!$('vortexImportURL').value.trim();
 $('vortexImportFind').textContent=vortexLookup?'Finding…':'Find';
 $('vortexImportApply').textContent=vortexApplying?'Applying…':'Apply';
 $('vortexImportURL').disabled=vortexApplying;
 vortexDialog.setAttribute('aria-busy',String(!!vortexLookup||vortexApplying));
}
function cancelVortexLookup(){vortexLookup?.controller.abort();vortexLookup=null;syncVortexImport();}
function cancelVortexImportDialog(){cancelVortexLookup();vortexChoice=null;vortexDestination=null;$('vortexImportURL').value='';$('vortexImportSelection').hidden=true;vortexDialog.close();}
function openVortexImport(newModule=false){
 if(!accountSignedIn()||cloudAuth?.kind!=='firebase-google'){openAccountDialog('Sign in to use Vortex.');return;}
 cancelVortexLookup();vortexChoice=null;
 vortexDestination={context:boardAsyncContext(),targetId:newModule?null:selected};
 $('vortexImportNewModule').checked=newModule||!nodeById(selected);$('vortexImportNewModule').disabled=newModule||!nodeById(selected);
 $('vortexImportSelection').hidden=true;$('vortexImportControls').open=false;
 $('vortexImportStatus').textContent='Video when available. Audio for music and audio-only links.';
 syncTranscriptionProviderUI();syncVortexImport();vortexDialog.showModal();$('vortexImportURL').focus();
}
function vortexDestinationCurrent(destination){return destination&&boardAsyncCurrent(destination.context)&&accountSignedIn()&&cloudAuth?.kind==='firebase-google';}
async function findVortexMedia(){
 if(vortexLookup||vortexApplying)return;const destination=vortexDestination;let url;
 try{url=normalizeVortexURL($('vortexImportURL').value);}catch(error){$('vortexImportStatus').textContent=error.message;return;}
 if(!vortexDestinationCurrent(destination))return;
 const run={controller:new AbortController(),destination};vortexLookup=run;vortexChoice=null;$('vortexImportSelection').hidden=true;
 const current=()=>vortexLookup===run&&vortexDestinationCurrent(destination)&&vortexDialog.open;
 syncVortexImport();$('vortexImportStatus').textContent='Finding media…';let remoteId;
 try{
  const caps=await youtubeAPI('vortex/capabilities',{signal:run.controller.signal});if(!current())return;
  if(!caps.visionImport)throw new Error('Update FUPCJ Server to use Vortex imports in Vision.');
  const accepted=await youtubeAPI('vortex/jobs',{method:'POST',signal:run.controller.signal,body:JSON.stringify({input:url,kind:'inspect',quality:'small',requestId:'vision-find-'+uid()})});remoteId=accepted.id;
  const deadline=Date.now()+190000;
  while(current()){
   const job=await youtubeAPI('vortex/jobs/'+encodeURIComponent(remoteId),{signal:run.controller.signal});if(!current())return;
   if(['error','cancelled','expired'].includes(job.status))throw new Error(job.error||'This media could not be found. Try another link.');
   if(job.status==='complete'){
    const media=job.media;if(!media||(!['video','audio','unknown'].includes(media.mediaType)||Number(media.itemCount||1)>1))throw new Error('Paste a single video or audio link.');
    vortexChoice={url,title:String(media.title||'Vortex media').slice(0,150),mediaType:media.mediaType};
    $('vortexImportName').textContent=vortexChoice.title;$('vortexImportType').textContent=media.mediaType==='audio'?'Audio · transcript':'Video · snapshots and transcript';
    $('vortexImportSelection').hidden=false;$('vortexImportStatus').textContent='Ready. Apply to add this media and process it in the background.';return;
   }
   if(Date.now()>deadline)throw new Error('Finding this media took too long. Try again.');
   $('vortexImportStatus').textContent=job.phase||'Finding media…';await new Promise(resolve=>setTimeout(resolve,1200));
  }
 }catch(error){if(current()&&error.name!=='AbortError')$('vortexImportStatus').textContent=error.message;}
 finally{
  // Never clean up a receipt using a different account's session.
  if(remoteId&&vortexDestinationCurrent(destination))void youtubeAPI('vortex/jobs/'+encodeURIComponent(remoteId),{method:'DELETE'}).catch(()=>{});
  if(vortexLookup===run){vortexLookup=null;syncVortexImport();}
 }
}
async function applyVortexMedia(){
 if(vortexApplying||!vortexChoice||busy||ioBusy)return;
 const destination=vortexDestination,choice={...vortexChoice},newModule=$('vortexImportNewModule').checked;
 const provider=transcriptionProvider(),includeSoundEvents=soundEventsSelected(provider);
 if(!vortexDestinationCurrent(destination))return;
 let staged=false;vortexApplying=true;syncVortexImport();
 try{
  const meta=await ensureRemoteProject();if(!vortexDestinationCurrent(destination))return;
  if(!newModule&&!nodeById(destination.targetId))throw new Error('The selected module was removed. Open Vortex again.');
  const n=newModule?await createModuleNode('video',choice.title):nodeById(destination.targetId);if(!vortexDestinationCurrent(destination)||!n)return;
  // Pending sources use Vision's resumable video placeholder; audio-only sources
  // become audio metadata when the complete transcript has arrived.
  const source={id:uid(),name:choice.title+'.mp4',mime:'video/mp4',size:0,metadataOnly:true,role:'video',status:'pending',snapshotsStatus:'pending',transcriptionStatus:'pending',createdAt:new Date().toISOString()};
  checkpoint();n.attachments.push(source);if(newModule)n.videoSourceId=source.id;
  const job={sourceKind:'vortex',url:choice.url,provider,includeSoundEvents,projectId:meta.id,clientRequestId:uid(),backendUrl:cloudConfig.backendUrl,targetId:n.id,sourceId:source.id,remoteId:'',createdAt:source.createdAt,status:'queued'};
  state.youtubeImports=state.youtubeImports||[];state.youtubeImports.push(job);staged=true;vortexChoice=null;youtubeTask(job);markDirty();updateVideoAttachments(n);renderActivity();
  // Persist the target/receipt before polling so reopening resumes this import.
  await projectBackup();if(!vortexDestinationCurrent(destination))return;
  await projectPerformSave();if(!vortexDestinationCurrent(destination))return;
  const accepted=await youtubeAPI('vortex/import',{method:'POST',body:JSON.stringify({url:job.url,projectId:job.projectId,provider,includeSoundEvents,clientRequestId:job.clientRequestId})});if(!vortexDestinationCurrent(destination))return;
  if(!accepted.id)throw new Error('The server did not return an import ID.');
  job.remoteId=String(accepted.id);markDirty();await projectBackup();
  vortexDialog.close();scheduleYouTubeImports();toast('Vortex import queued. Processing will continue on FUPCJ Server.');
 }catch(error){if(vortexDestinationCurrent(destination)){if(staged)toast('Vortex import saved. It will retry when the server is available.');else $('vortexImportStatus').textContent=error.message;}}
 finally{if(staged&&vortexDestinationCurrent(destination)){vortexDialog.close();scheduleYouTubeImports();}vortexApplying=false;syncVortexImport();}
}
async function applyVortexImportResult(n,source,job,result,check){
 if(!['video','audio'].includes(result.mediaType)||!Number.isFinite(result.duration)||result.duration<=0||result.duration>7200||!Array.isArray(result.frames)||result.frames.length>120)throw new Error('FUPCJ Server returned incomplete media results.');
 const audio=result.mediaType==='audio',title=String(result.title||'Vortex media').slice(0,150);
 if(!audio&&!result.frames.length)throw new Error('No video snapshots were returned.');
 let total=0;const frames=result.frames.map((frame,index)=>{
  total+=String(frame.data||'').length;
  if(total>25*1024*1024||!/^data:image\/jpeg;base64,[A-Za-z0-9+/]+=*$/.test(frame.data||'')||!Number.isFinite(frame.timestamp)||frame.timestamp<0||frame.timestamp>result.duration)throw new Error('A video snapshot could not be read.');
  return{id:source.id+'-frame-'+index,name:String(frame.name||'frame.jpg'),mime:'image/jpeg',size:bytesFromDataURL(frame.data).length,data:frame.data,timestamp:frame.timestamp,requestedTimestamp:frame.timestamp,role:'video-frame',videoOf:source.id,generated:true,status:'complete',createdAt:new Date().toISOString()};
 });
 const text=result.hasAudio?soundResultText(result.transcription?.text):'';
 const soundJob={includeSoundEvents:job.includeSoundEvents,sourceKind:audio?'audio':'video',offsetSeconds:0,sections:[{start:0,end:result.duration}]};
 if(result.hasAudio&&job.includeSoundEvents)soundJob.soundResult=validateSoundResult(result.transcription,soundJob);
 check();source.name=title+(audio?'.m4a':'.mp4');source.mime=audio?'audio/mp4':'video/mp4';source.role=audio?'audio':'video';source.transcriptionProvider=job.provider;source.videoDuration=result.duration;source.videoHasAudio=!!result.hasAudio;source.snapshotsStatus='complete';if(!audio)source.snapshotInterval=result.snapshotInterval;
 n.attachments=n.attachments.filter(a=>!(a.videoOf===source.id&&a.role==='video-frame'));n.attachments.push(...frames);
 if(n.videoSourceId===source.id){
  n.title=title;
  if(audio){n.videoSourceId=null;n.kind='node';n.fileType='Audio';}
  else{n.src=frames[0].data;const img=await R.loadImage(n.src);check();n.width=img.naturalWidth;n.height=img.naturalHeight;}
  await renderNode(n);check();
 }
 if(result.hasAudio){if(audio){saveTranscript(n,source,text,'complete');source.transcriptionStatus='complete';}else storeVideoTranscript(n,source,text,'complete');if(soundJob.soundResult)saveSoundSubtitles(n,source,soundJob);}
 else storeVideoTranscript(n,source,'No audio track was detected. Review the timestamped snapshots.','no-audio');
 const warning=soundJob.soundResult?.warnings?.join(' ');recordActivity(title,warning||'Vortex import complete');toast(warning||'Vortex import complete.',!!warning);
}
vortexBoardButton.onclick=()=>{boardAddDialog.close();openVortexImport(true);};vortexFilesButton.onclick=()=>openVortexImport(false);
$('closeVortexImport').onclick=()=>vortexDialog.close();vortexDialog.addEventListener('close',cancelVortexLookup);
$('vortexImportURL').addEventListener('input',()=>{cancelVortexLookup();vortexChoice=null;$('vortexImportSelection').hidden=true;syncVortexImport();});
$('vortexImportFindForm').onsubmit=event=>{event.preventDefault();void findVortexMedia();};$('vortexImportApply').onclick=()=>void applyVortexMedia();
