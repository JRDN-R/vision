/* Local provider UI. All model access still goes through the account backend. */
VENTURE_MODEL_SYMBOLS.qwen = "<svg role=\"img\" viewBox=\"0 0 24 24\" xmlns=\"http://www.w3.org/2000/svg\"><title>QWen</title><path d=\"M23.919 14.545 20.817 9.17l1.47-2.544a.56.56 0 0 0 0-.566l-1.633-2.83a.57.57 0 0 0-.49-.283h-6.207L12.487.402a.57.57 0 0 0-.49-.284H8.732a.56.56 0 0 0-.49.284L5.139 5.775h-2.94a.56.56 0 0 0-.49.284L.077 8.887a.56.56 0 0 0 0 .567L3.18 14.83l-1.47 2.545a.56.56 0 0 0 0 .566l1.634 2.83a.57.57 0 0 0 .49.283h6.205l1.47 2.545a.57.57 0 0 0 .49.284h3.266a.57.57 0 0 0 .49-.284l3.104-5.375h2.94a.57.57 0 0 0 .49-.283l1.634-2.828a.55.55 0 0 0-.004-.568M8.733.686l1.634 2.828-1.634 2.828H21.8L20.164 9.17H7.425L5.63 6.06Zm1.306 19.801-6.205-.002 1.634-2.83h3.265L2.201 6.344h3.267q3.182 5.517 6.367 11.032zm10.124-5.66L18.53 12l-6.532 11.315-1.634-2.83c2.129-3.673 4.25-7.351 6.373-11.028h3.592l3.102 5.374z\"/></svg>";
function ventureIsLocal(model=venture.settings.model){return model==='qwen3.8-local';}
function ventureLocalVisionReady(){return venture.localCapability?.capabilities?.vision===true;}
function ventureLocalSubtitle(){return ventureLocalVisionReady()?'9B Distill + Vision':'9B Distill · vision not ready';}
function ventureAcceptLocalCapability(data){
 if(!Object.prototype.hasOwnProperty.call(data,'localModel'))return;
 const before=ventureLocalVisionReady();venture.localCapability=data.localModel||null;
 if(before!==ventureLocalVisionReady()){venture.rendered.clear();venturePaintModelPicker();venturePaintTurns();}
}
function ventureLocalSourceLink(label,url,run){
 const index=run?.localVisualEvidence?.findIndex(e=>e.sourceURL===url)??-1;
 return index<0?label+' (source unavailable)':'<button type="button" data-v-action="local-visual" data-index="'+index+'">'+label+'</button>';
}
function ventureLocalDisclosure(){
 return '<h2 id="qwenNoticeTitle">Qwen Local</h2><p>9B Distill + Vision combines a local visual worker with the 9B answering model. For images and selected video frames, the visual worker provides source-linked findings and timestamps, then the 9B model writes the answer. They run in sequence. A new question can trigger a focused inspection when saved findings do not cover it.</p><p>'+ (ventureLocalVisionReady()?'This backend has confirmed the integrated image analysis path.':'This backend has not yet confirmed image analysis. Visual requests may be unavailable until the integrated path is checked.')+'</p><p>Qwen runs on the FUPCJ server, not this phone. Requests run one at a time, with queue position and elapsed time shown. Response time is not yet calibrated. Authorized server administrators may access server-held data; this is not an end-to-end encryption guarantee.</p><p>Visual findings can miss details or misread small text. Only the identified frames and crops are inspected. Web search and code execution are unavailable. Local dictation uses PC Whisper. No API token charge and no automatic cloud fallback.</p>';
}
function ventureAcceptPreferences(prefs){
 venture.preferenceRevision=prefs.preferenceRevision;venture.defaultEpoch=prefs.defaultEpoch||'';
 venture.disclosureVersion=prefs.disclosureVersion;venture.localAcknowledged=prefs.disclosureAcknowledged===true;
 venture.profiles=prefs.profiles||{};
}
async function ventureLocalNotice(){
 if(venture.localAcknowledged)return true;
 if(venture.localNoticePromise)return venture.localNoticePromise;
 const epoch=venture.epoch;
 venture.localNoticePromise=new Promise(resolve=>{
  const dialog=document.createElement('dialog');dialog.className='venture-local-notice';dialog.setAttribute('aria-labelledby','qwenNoticeTitle');
  dialog.innerHTML=ventureLocalDisclosure()+'<p class="qwen-notice-error" role="status"></p><div class="dialog-actions"><button type="button" data-close>Cancel</button><button type="button" data-accept>OK</button></div>';
  document.body.appendChild(dialog);
  let accepted=false;
  dialog.querySelector('[data-close]').onclick=()=>dialog.close();
  dialog.querySelector('[data-accept]').onclick=async()=>{const button=dialog.querySelector('[data-accept]');button.disabled=true;
   try{const prefs=await ventureJSON('/venture/local/disclosure','POST',{version:venture.disclosureVersion||'qwen-local-vision-v2'});if(epoch!==venture.epoch){dialog.close();return;}ventureAcceptPreferences(prefs);accepted=true;dialog.close();}
   catch(error){dialog.querySelector('.qwen-notice-error').textContent=ventureError(error);button.disabled=false;}
  };
  dialog.addEventListener('close',()=>{dialog.remove();resolve(accepted&&epoch===venture.epoch);},{once:true});dialog.showModal();
 });
 try{return await venture.localNoticePromise;}finally{venture.localNoticePromise=null;}
}
function ventureLocalEvidence(run){
 if(!Array.isArray(run.localVisualEvidence)||!run.localVisualEvidence.length)return '';
 return '<details class="venture-preparation"><summary>Inspected images and frames</summary>'+run.localVisualEvidence.map((e,i)=>'<p><button type="button" data-v-action="local-visual" data-index="'+i+'">'+escapeHTML(e.name+' · '+e.location+' · '+e.crop)+'</button><br>'+escapeHTML(e.finding)+'</p>').join('')+'</details>';
}
async function ventureOpenLocalVisual(run,index){
 const record=run.localVisualEvidence?.[index];if(!record||!Number.isInteger(index))return;
 const epoch=venture.epoch;
 const response=await ventureFetch(ventureRunPath(run.ventureId||run.projectId)+'/'+encodeURIComponent(run.runId)+'/local-visual/'+index);
 const blob=await response.blob();if(epoch!==venture.epoch)return;
 const url=URL.createObjectURL(blob),dialog=document.createElement('dialog');dialog.className='venture-local-image';
 const caption=document.createElement('p');caption.textContent=record.name+' · '+record.location+' · '+record.crop;
 const image=document.createElement('img');image.src=url;image.alt=caption.textContent;
 const close=document.createElement('button');close.textContent='Close';close.onclick=()=>dialog.close();
 dialog.append(caption,image,close);dialog.addEventListener('close',()=>{URL.revokeObjectURL(url);dialog.remove();},{once:true});document.body.appendChild(dialog);dialog.showModal();
}
function ventureLocalProgress(run){
 const q=run.localQueue;if(!q)return '';
 const active=ventureActive(run),wait=Math.round(q.waitingSeconds||0),processing=Math.round(q.processingSeconds||0);
 return '<div class="venture-local-progress" role="status"><strong>'+(active?(q.requestsAhead?q.requestsAhead+' request'+(q.requestsAhead===1?'':'s')+' ahead':run.status==='queued'?'Queued · next in line':'Your request is running'):'Local inference · no API token charge')+'</strong>'+
 (active?'<progress aria-label="Local answer in progress"></progress><span>'+escapeHTML(run.phase||'Queued')+'</span>':'')+
 '<small>Waited '+wait+'s · Processing '+processing+'s'+(active?' · Completion estimate not calibrated':'')+'</small></div>';
}
async function ventureFlushLocalSettings(){
 clearTimeout(venture.settingsSaving);
 if(venture.savedSettingsVersion===venture.settingsVersion)return true;
 await venturePersistSettings();
 const until=Date.now()+5000;
 while(venture.persisting&&Date.now()<until)await new Promise(resolve=>setTimeout(resolve,50));
 return venture.savedSettingsVersion===venture.settingsVersion;
}
