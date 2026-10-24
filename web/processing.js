// Project-owned video preferences and authenticated, lazily fetched frame sets.
function normalizeProcessingSettings(value={}){
 return {screenshotDetail:['maximum','medium','balanced','minimal'].includes(value.screenshotDetail)?value.screenshotDetail:'balanced',describeFrames:value.describeFrames!==false,frameDescriptionEngine:['auto','luna','local'].includes(value.frameDescriptionEngine)?value.frameDescriptionEngine:'auto'};
}
function processingSettings(){return normalizeProcessingSettings(state.settings);}
function processingInterval(duration,detail){const [seconds,limit]=({maximum:[.25,1200],medium:[1,600],balanced:[3,300],minimal:[10,120]})[detail]||[3,300];return Math.max(seconds,duration/limit);}
function normalizeFrameRef(value){return value&&/^[a-f0-9]{24}$/.test(value.collectionId||'')&&/^[-\w]{16,120}$/.test(value.projectId||'')&&Number.isInteger(value.index)&&value.index>=0&&value.index<1200?{collectionId:value.collectionId,projectId:value.projectId,index:value.index}:null;}
function frameAttachment(frame,source,index){
 const ref=normalizeFrameRef(frame.frameRef),inline=/^data:image\/jpeg;base64,[A-Za-z0-9+/]+=*$/.test(frame.data||'');
 if((!ref&&!inline)||!Number.isFinite(frame.timestamp)||frame.timestamp<0)throw new Error('A screenshot could not be read.');
 if(ref&&ref.projectId!==ensureProjectIdentity().id)throw new Error('A screenshot belongs to a different project.');
 return {id:source.id+'-frame-'+index,name:String(frame.name||'frame.jpg').slice(0,180),mime:'image/jpeg',size:ref?Number(frame.size)||0:bytesFromDataURL(frame.data).length,...(ref?{frameRef:ref,metadataOnly:true}:{data:frame.data}),timestamp:frame.timestamp,requestedTimestamp:frame.timestamp,role:'video-frame',videoOf:source.id,generated:true,status:'complete',createdAt:new Date().toISOString()};
}
function frameCollectionPath(ref){
 const meta=ensureProjectIdentity();if(ref.projectId!==meta.id||meta.backendUrl!==cloudConfig?.backendUrl)throw new Error('Connect this project’s original FUPCJ Server to retrieve its screenshots.');
 return '/projects/'+encodeURIComponent(ref.projectId)+'/frames/'+encodeURIComponent(ref.collectionId);
}
async function frameData(attachment){
 if(attachment.data)return attachment.data;
 const ref=normalizeFrameRef(attachment.frameRef);if(!ref)throw new Error('The screenshot is missing.');
 const context=boardAsyncContext(),controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
 try{const response=await projectRequest(frameCollectionPath(ref)+'/'+ref.index,{signal:controller.signal});assertBoardAsyncContext(context);
  if(!response.ok)await projectResponse(response);
  if(Number(response.headers.get('Content-Length'))>512*1024)throw new Error('Screenshot exceeds its download limit.');
  const blob=await response.blob();assertBoardAsyncContext(context);if(blob.size>512*1024||!blob.type.startsWith('image/jpeg'))throw new Error('The screenshot response is invalid.');
  const data=await readFile(blob);assertBoardAsyncContext(context);return data;
 }finally{clearTimeout(timer);}
}
function frameReportText(report){
 const breakdown=report.providerCostsUSD||{};
 const lines=['VISUAL OBSERVATIONS · '+report.model,
   ...(report.providerRequests?['Estimated cloud token cost USD: '+Number(report.estimatedProviderCostUSD||0).toFixed(5)+', Luna '+Number(breakdown.luna||0).toFixed(5)+', Gemini '+Number(breakdown.gemini||0).toFixed(5)+' (interrupted requests may have extra charges)']:[]),
   'Status: '+report.status+' · '+report.completed+' / '+report.count+' saved screenshots processed',
  'Machine-generated descriptions may be incorrect. Verify against screenshots. '+report.grouping,''];
 lines.push('Described screenshots: '+(report.analyzed??report.frames?.filter(f=>f.analysisStatus!=='skipped').length??0)+' / '+(report.planned??report.count)+' planned. All screenshots remain saved.');
 for(const frame of report.frames||[]){lines.push('['+videoTimestamp(frame.timestamp)+'] Frame '+(frame.index+1)+' · '+frame.width+'×'+frame.height+' · '+frame.aspectRatio+' · '+(frame.prominentColors||[]).join(', '));
  if(frame.analysisStatus==='skipped')lines.push('Not analyzed (every-other-screenshot setting). Screenshot retained.','');
  else lines.push('Model: '+(frame.model||report.model),frame.independentlyInspected?'Independently inspected.':'Description reused from frame '+(frame.reusedFrom+1)+' (identical decoded pixels).',frame.description,'');
 }
 for(const t of report.fallbackHistory||[])lines.push('AUTO FALLBACK: '+t.from+' -> '+t.to+'; '+t.reason);
 lines.push('GROUPED TIMELINE');for(const group of report.timeline||[])lines.push('['+videoTimestamp(group.start)+' – '+videoTimestamp(group.end)+'] Frames '+(group.startFrame+1)+'–'+(group.endFrame+1)+': '+(group.observations||[{description:group.description}]).map(o=>(o.timestamp!==undefined?'['+videoTimestamp(o.timestamp)+'] ':'')+o.description).join(' '));
 if(report.error)lines.push('Analysis needs attention: '+report.error);
 return lines.join('\n');
}
function attachFrameCollection(source,result){
 if(!/^[a-f0-9]{24}$/.test(result.frameCollection||''))return;
 source.frameCollection={collectionId:result.frameCollection,projectId:ensureProjectIdentity().id};
 source.visualAnalysis={status:'queued',completed:0,count:result.frames.length};
 void refreshFrameAnalysis();
}
let framePollBusy=false;
async function refreshFrameAnalysis(){
 if(framePollBusy||!cloudAuth)return;
 const context=boardAsyncContext(),project=context.project;framePollBusy=true;
 try{
  for(const n of project.nodes)for(const source of n.attachments||[]){
   if(!source.frameCollection||['complete','disabled'].includes(source.visualAnalysis?.status))continue;
   try{
    let report=await projectResponse(await projectRequest(frameCollectionPath(source.frameCollection)+'?summary=1'));
    if(!boardAsyncCurrent(context)||!n.attachments.includes(source))return;
    const prior=source.visualAnalysis;
    if(prior?.completed!==report.completed||prior?.status!==report.status){
     report=await projectResponse(await projectRequest(frameCollectionPath(source.frameCollection)));if(!boardAsyncCurrent(context)||!n.attachments.includes(source))return;
     const id=source.id+'-visual',name=source.name.replace(/\.[^.]+$/,'')+'.visual-timeline.txt';
     n.attachments=n.attachments.filter(a=>a.id!==id);
     n.attachments.push({id,name,mime:'text/plain',data:textDataURL(frameReportText(report)),size:0,generated:true,status:report.status==='complete'?'complete':'partial',videoOf:source.id,createdAt:new Date().toISOString()});
     markDirty();
    }
    source.visualAnalysis={status:report.status,completed:report.completed,count:report.count,error:report.error};renderActivity();
   }catch(error){if(boardAsyncCurrent(context)){source.visualAnalysis={...source.visualAnalysis,connectionError:true};renderActivity();}}
  }
 }finally{framePollBusy=false;}
}
async function controlFrameAnalysis(source,action){
 try{await projectResponse(await projectRequest(frameCollectionPath(source.frameCollection),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})}));source.visualAnalysis.status=action==='pause'?'paused':'queued';markDirty();await refreshFrameAnalysis();}catch(error){toast(error.message,true);}
}
setInterval(()=>void refreshFrameAnalysis(),5000);
window.addEventListener('online',()=>void refreshFrameAnalysis());
document.addEventListener('visibilitychange',()=>{if(!document.hidden)void refreshFrameAnalysis();});

const processingDialog=document.createElement('dialog');processingDialog.id='processingDialog';
processingDialog.innerHTML='<form method="dialog"><div class="row spread"><h2>Project Settings</h2><button aria-label="Close settings">×</button></div><p class="mini-note">Video options apply to new imports. Existing jobs keep their settings.</p><label>Transcription<select id="projectTranscription"><option value="local">FUPCJ Server · Whisper</option><option value="gemini">Gemini</option></select></label><label class="check-row"><input type="checkbox" id="projectSounds"> Include sound effects</label><label>Screenshot detail<select id="projectDetail"><option value="maximum">Maximum · every 0.25 seconds, up to 1,200</option><option value="medium">Medium · every second, up to 600</option><option value="balanced">Balanced · every 3 seconds, up to 300</option><option value="minimal">Minimal · every 10 seconds, up to 120</option></select></label><p id="projectFrameEstimate" class="mini-note"></p><label class="check-row"><input type="checkbox" id="projectDescribe"> Describe screenshots in the background</label><label>Description engine<select id="projectFrameEngine"><option value="auto">Automatic (default) · Luna → Gemini 3.8 Flash → Qwen</option><option value="local">Qwen 0.8B · local, no API fees</option><option value="luna">GPT-6 Luna only · OpenAI API, billed</option></select></label><p class="mini-note">Automatic mode tries GPT-6 Luna with your saved OpenAI key, then Gemini 3.8 Flash with the protected server credential (when your Gemini access is approved), then local Qwen. Cloud requests send screenshots and nearby narration. The combined cloud spending guard is approximately $0.90 per video, not a guaranteed billing cap.</p><label class="check-row"><input type="checkbox" id="projectAutoName"> Automatically name this project after its first file</label><p class="mini-note">Uses the local Qwen worker to review node titles, captions, extracted text and up to six image previews. Runs once for an untitled saved project. A name you type is always kept.</p><button type="button" id="frameStorageButton">Manage saved screenshots</button><div id="frameStorageList"></div><div class="row"><button id="saveProcessing" class="primary" type="button">Save settings</button></div></form>';
document.body.append(processingDialog);
function openProcessingSettings(){
 processingDialog.project=state; $('projectTranscription').value=transcriptionProvider();$('projectSounds').checked=soundEventsSelected();$('projectDetail').value=processingSettings().screenshotDetail;$('projectDescribe').checked=processingSettings().describeFrames;$('projectFrameEngine').value=processingSettings().frameDescriptionEngine;$('projectAutoName').checked=state.settings.autoNameProject===true;
 $('frameStorageList').replaceChildren();updateFrameEstimate();processingDialog.showModal();
}
function updateFrameEstimate(){const detail=$('projectDetail').value; $('projectFrameEstimate').textContent='5-minute video: '+Math.ceil(300/processingInterval(300,detail))+' screenshots; '+Math.ceil(Math.ceil(300/processingInterval(300,detail))/(['maximum','medium'].includes(detail)&&$('projectFrameEngine').value==='local'?2:1))+' selected for descriptions. '+(['maximum','medium'].includes(detail)&&$('projectFrameEngine').value==='local'?'Every other screenshot is analyzed locally. ':'Every screenshot is analyzed. ')+'Longer videos spread screenshots farther apart to stay within this preset’s limit. Fast movement between screenshots can still be missed.';}
$('projectDetail').onchange=updateFrameEstimate;
$('projectFrameEngine').onchange=updateFrameEstimate;
$('saveProcessing').onclick=()=>{if(processingDialog.project!==state){processingDialog.close();return;}if($('projectDescribe').checked&&processingSettings().frameDescriptionEngine==='local'&&$('projectFrameEngine').value!=='local'&&!confirm('Cloud descriptions send screenshots and short transcript excerpts to OpenAI and possibly Gemini. API charges apply; the combined per-video guard is approximately $0.90, not a guaranteed billing limit. Continue?'))return;checkpoint();Object.assign(state.settings,{processingVersion:1,transcriptionProvider:$('projectTranscription').value,includeSoundEvents:$('projectSounds').checked,screenshotDetail:$('projectDetail').value,describeFrames:$('projectDescribe').checked,frameDescriptionEngine:$('projectFrameEngine').value,autoNameProject:$('projectAutoName').checked});if(!state.settings.autoNameProject)void cancelProjectNaming();else if(state.settings.autoNaming?.status==='cancelled')delete state.settings.autoNaming;markDirty();void refreshProjectNaming();syncTranscriptionProviderUI();processingDialog.close();toast('Project processing settings saved.');};
$('frameStorageButton').onclick=async()=>{
 const host=$('frameStorageList');host.textContent='Loading…';const project=state;
 try{const meta=ensureProjectIdentity(),result=await projectResponse(await projectRequest('/projects/'+meta.id+'/frames'));if(state!==project)return;host.replaceChildren();
  for(const item of result.items){const row=document.createElement('div'),label=document.createElement('span'),button=document.createElement('button');label.textContent=item.count+' screenshots · '+item.status+' · '+readableBytes(item.bytes)+' ';button.type='button';button.textContent='Delete';button.onclick=async()=>{if(state!==project||!confirm('Delete these server screenshots and descriptions? Projects referring to them will no longer be able to export those screenshots.'))return;try{await projectResponse(await projectRequest('/projects/'+meta.id+'/frames/'+item.id,{method:'DELETE'}));row.remove();}catch(error){toast(error.message,true);}};row.append(label,button);host.append(row);}if(!result.items.length)host.textContent='No saved screenshot collections.';
 }catch(error){host.textContent=error.message;}
};

function positionActivityForKeyboard(){const view=window.visualViewport;document.documentElement.style.setProperty('--activity-keyboard-offset',Math.max(0,window.innerHeight-(view?.height||window.innerHeight)-(view?.offsetTop||0))+'px');}
window.visualViewport?.addEventListener('resize',positionActivityForKeyboard);window.visualViewport?.addEventListener('scroll',positionActivityForKeyboard);positionActivityForKeyboard();
