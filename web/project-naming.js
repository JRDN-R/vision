// Automatic naming uses one durable server request and applies its suggestion
// only to the same still-untitled project. Typed names always win.
function normalizeProjectNamingSettings(value={}){
 const raw=value.autoNaming,job=raw&&/^[\w-]{1,100}$/.test(raw.requestId||'')?{requestId:raw.requestId,nodeId:String(raw.nodeId||'').slice(0,100),status:['awaiting','queued','processing','paused','complete','cancelled'].includes(raw.status)?raw.status:'awaiting',error:String(raw.error||'').slice(0,300)}:null;
 return {autoNameProject:value.autoNameProject===true,autoNameManual:value.autoNameManual===true,autoNameDone:value.autoNameDone===true,...(job?{autoNaming:job}:{})};
}
function projectNamingEligible(project=state){return project.settings?.autoNameProject===true&&!project.settings.autoNameManual&&!project.settings.autoNameDone&&['','Untitled timeline','Untitled project'].includes(project.title);}
function projectNamingSource(project=state){return project.nodes.find(n=>['image','video'].includes(n.kind)||(n.attachments||[]).some(a=>!a.generated));}
function projectNamingReady(project=state){
 // Wait for accepted source preparation, so a video or document can contribute
 // real content instead of receiving a name based only on its extension.
 return !project.youtubeImports?.length&&!project.nodes.some(n=>n.pcVideo&&!['complete','error'].includes(n.pcVideo.status)||(n.transcriptionJobs||[]).some(j=>!['error','paused'].includes(j.status))||(n.attachments||[]).some(a=>a.documentJob&&!['complete','error','paused'].includes(a.documentJob.status)));
}
let projectNamingBusy=false;
async function cancelProjectNaming(){
 const job=state.settings?.autoNaming;if(!job||['complete','cancelled'].includes(job.status))return;
 job.status='cancelled';renderActivity();
 try{await projectResponse(await projectRequest('/projects/'+ensureProjectIdentity().id+'/auto-name?requestId='+encodeURIComponent(job.requestId),{method:'DELETE'}));}catch{}
}
function retryProjectNaming(){if(!projectNamingEligible())return;delete state.settings.autoNaming;markDirty();void refreshProjectNaming();}
async function refreshProjectNaming(){
 if(projectNamingBusy||!projectNamingEligible()||projectIsTemporary()||projectLoading||projectAccountSwitching)return;
 const source=projectNamingSource();if(!source)return;
 const settings=state.settings;
 if(settings.autoNaming&&['complete','cancelled','paused'].includes(settings.autoNaming.status))return;
 if(!settings.autoNaming){settings.autoNaming={requestId:'name-'+uid(),nodeId:source.id,status:'awaiting',error:''};markDirty();}
 const job=settings.autoNaming;
 if(!projectNamingReady()||!cloudAuth){job.status='awaiting';renderActivity();return;}
 const context=boardAsyncContext(),requestId=job.requestId;
 const current=()=>boardAsyncCurrent(context)&&projectNamingEligible()&&state.settings.autoNaming===job&&job.requestId===requestId&&job.status!=='cancelled';
 projectNamingBusy=true;
 try{
  const health=await projectCapabilities();if(!current())return;
  if(health.capabilities?.projectAutoNamingV1!==true)throw new Error('Update FUPCJ Server to enable automatic project naming.');
  await flushProjectSave();if(!current())return;
  const path='/projects/'+ensureProjectIdentity().id+'/auto-name';
  let result=await projectResponse(await projectRequest(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({requestId})}));
  if(!current()||result.requestId!==requestId)return;
  const changed=job.status!==result.status||job.error!==(result.error||'');
  job.status=result.status;job.error=result.error||'';
  if(result.status==='complete'){
   if(typeof result.title!=='string'||!result.title.trim()||result.title.length>80)throw new Error('The local model returned an invalid project name.');
   checkpoint();state.title=result.title;state.settings.autoNameDone=true;$('projectTitle').value=state.title;
   markDirty();renderProjectMenu();toast('Project named “'+state.title+'”.');
  }else if(changed)markDirty();
  renderActivity();
 }catch(error){if(current()){job.status=error.status===409||error.code==='VISION_SERVER_UNAVAILABLE'||error.name==='AbortError'?'awaiting':'paused';job.error=error.message;markDirty();renderActivity();}}
 finally{projectNamingBusy=false;}
}
setInterval(()=>void refreshProjectNaming(),4000);
