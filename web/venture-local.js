/* Local provider UI. All model access still goes through the account backend. */
VENTURE_MODEL_SYMBOLS.qwen = "<svg role=\"img\" viewBox=\"0 0 24 24\" xmlns=\"http://www.w3.org/2000/svg\"><title>QWen</title><path d=\"M23.919 14.545 20.817 9.17l1.47-2.544a.56.56 0 0 0 0-.566l-1.633-2.83a.57.57 0 0 0-.49-.283h-6.207L12.487.402a.57.57 0 0 0-.49-.284H8.732a.56.56 0 0 0-.49.284L5.139 5.775h-2.94a.56.56 0 0 0-.49.284L.077 8.887a.56.56 0 0 0 0 .567L3.18 14.83l-1.47 2.545a.56.56 0 0 0 0 .566l1.634 2.83a.57.57 0 0 0 .49.283h6.205l1.47 2.545a.57.57 0 0 0 .49.284h3.266a.57.57 0 0 0 .49-.284l3.104-5.375h2.94a.57.57 0 0 0 .49-.283l1.634-2.828a.55.55 0 0 0-.004-.568M8.733.686l1.634 2.828-1.634 2.828H21.8L20.164 9.17H7.425L5.63 6.06Zm1.306 19.801-6.205-.002 1.634-2.83h3.265L2.201 6.344h3.267q3.182 5.517 6.367 11.032zm10.124-5.66L18.53 12l-6.532 11.315-1.634-2.83c2.129-3.673 4.25-7.351 6.373-11.028h3.592l3.102 5.374z\"/></svg>";
function ventureIsLocal(model=venture.settings?.model){return model==='qwen3.8-local'||model==='qwen-ultra-local';}
function ventureLocalVisionReady(model=venture.settings?.model){return model==='qwen-ultra-local'?venture.localCapability?.ultra?.capabilities?.vision===true:venture.settings?.qwenUncensored===true?venture.localCapability?.variants?.heretic?.vision===true:venture.localCapability?.capabilities?.vision===true;}
function ventureLocalSubtitle(model='qwen3.8-local'){if(model==='qwen-ultra-local')return '27B Heretic'+(ventureLocalVisionReady(model)?' + Vision':venture.localCapability?.ultra?.enabled?' · local':' · install to enable');return ventureLocalVisionReady(model)?'9B Distill + Vision':venture.localCapability?.enabled===true&&venture.localCapability?.visual?.status==='awaiting-integrated-check'?'9B Distill':'9B Distill · vision not ready';}
function ventureAcceptLocalCapability(data){
 if(!Object.prototype.hasOwnProperty.call(data,'localModel'))return;
 const before=ventureLocalSubtitle(venture.settings.model);venture.localCapability=data.localModel||null;
 if(typeof $==='function')venturePaintUncensored();
 if(typeof venturePaintUltra==='function'){venturePaintUltra();}
 if(before!==ventureLocalSubtitle(venture.settings.model)){venture.rendered.clear();venturePaintModelPicker();venturePaintTurns();}
}
function ventureLocalSourceLink(label,url,run){
 const index=run?.localVisualEvidence?.findIndex(e=>e.sourceURL===url)??-1;
 return index<0?label+' (source unavailable)':'<button type="button" data-v-action="local-visual" data-index="'+index+'">'+label+'</button>';
}
function ventureLocalDisclosure(model=venture.settings?.model){
 if(model==='qwen-ultra-local')return '<h2 id="qwenNoticeTitle">Qwen Ultra</h2><p>The 27B Ultra-Uncensored Heretic model runs on FUPCJ, one local request at a time. It reads extracted file evidence, writes code and can use the existing visual worker for source-linked image findings. The worker and answering model run in sequence.</p><p>This larger model can take several minutes or longer. Venture estimates time from the installed benchmark and comparable completed requests, including the output budget used for thinking. Queueing, files, images and other PC work may add time. Accepted work continues when you leave.</p><p>Web search, code execution and image generation are not integrated. There is no API token charge and no automatic cloud fallback. Authorized server administrators may access server-held data. “Uncensored” describes the publisher’s modification, not a guarantee of accuracy or zero refusals.</p>';
 return '<h2 id="qwenNoticeTitle">Qwen Local</h2><p>9B Distill + Vision combines a local visual worker with the 9B answering model. For images and selected video frames, the visual worker provides source-linked findings and timestamps, then the 9B model writes the answer. They run in sequence. A new question can trigger a focused inspection when saved findings do not cover it.</p><p>'+ (ventureLocalVisionReady()?'This backend has confirmed the integrated image analysis path.':'This backend has not yet confirmed image analysis. Visual requests may be unavailable until the integrated path is checked.')+'</p><p>Qwen runs on the FUPCJ server, not this phone. Requests run one at a time, with queue position and elapsed time shown. Response time is not yet calibrated. Authorized server administrators may access server-held data; this is not an end-to-end encryption guarantee.</p><p>Visual findings can miss details or misread small text. Only the identified frames and crops are inspected. Web search and code execution are unavailable. Local dictation uses PC Whisper. No API token charge and no automatic cloud fallback.</p>';
}
function ventureAcceptPreferences(prefs){
 venture.preferenceRevision=prefs.preferenceRevision;venture.defaultEpoch=prefs.defaultEpoch||'';
 venture.disclosureVersion=prefs.disclosureVersion;venture.localAcknowledged=prefs.disclosureAcknowledged===true;
 venture.profiles=prefs.profiles||{};
}
async function ventureLocalNotice(model=venture.settings?.model){
 if(model==='qwen-ultra-local')return ventureUltraNotice();
 if(venture.localAcknowledged)return true;
 if(venture.localNoticePromise)return venture.localNoticePromise;
 const epoch=venture.epoch;
 venture.localNoticePromise=new Promise(resolve=>{
  const dialog=document.createElement('dialog');dialog.className='venture-local-notice';dialog.setAttribute('aria-labelledby','qwenNoticeTitle');
  dialog.innerHTML=ventureLocalDisclosure(model)+'<p class="qwen-notice-error" role="status"></p><div class="dialog-actions"><button type="button" data-close>Cancel</button><button type="button" data-accept>OK</button></div>';
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
// Only describe observed stages. Rotating wording is motion, not simulated tool activity.
function ventureLoadingLabels(run){
 const q=run.localQueue,phase=String(run.phase||'').toLowerCase();
 if(q?.requestsAhead)return [q.requestsAhead+' request'+(q.requestsAhead===1?'':'s')+' ahead','Waiting for your turn'];
 if(run.status==='queued')return ['You’re next in line','Getting ready'];
 if(run.status==='saving')return ['Saving your answer','Finishing up'];
 if(run.text)return ['Writing your answer','Putting it into words'];
 if(/inspect|image|frame|visual/.test(phase))return ['Looking at your images','Taking a closer look'];
 if(/search/.test(phase))return ['Searching for information','Looking for useful sources'];
 if(/file|upload|prepar/.test(phase))return ['Reading your request','Getting ready'];
 if(/load|model|connect/.test(phase))return ['Getting ready','Your answer is on its way'];
 return ['Thinking it through','Working on your answer'];
}
function ventureLocalProgress(run,details=false){
 const q=run.localQueue,active=ventureActive(run);
 if(details){
  if(!q)return '';
  return '<div class="venture-local-progress is-complete"><strong>Local inference · no API token charge</strong><small>Waited '+ventureLocalDuration(q.waitingSeconds||0)+' · Processing '+ventureLocalDuration(q.processingSeconds||0)+'</small></div>';
 }
 if(run.model==='qwen-ultra-local')return ventureUltraProgress(run);
 if(!active)return '';
 const labels=ventureLoadingLabels(run),range=q?.estimatedRemainingRange;
 // Conservative time estimate, not a token-completion measurement. Queue and
 // overruns stay indeterminate; neither can honestly imply near completion.
 const estimated=q?.estimateStatus==='estimated'&&!q.requestsAhead&&run.status!=='queued'&&Array.isArray(range)&&range.length===2&&range.every(n=>Number.isFinite(n)&&n>=0)&&range[1]>=range[0]&&Number.isFinite(q.processingSeconds)&&q.processingSeconds>0;
 const percent=estimated?Math.min(95,Math.max(1,Math.round(100*q.processingSeconds/(q.processingSeconds+Math.max(1,range[1]))))):null;
 const caption=estimated?('About '+ventureLocalDuration(range[0])+'–'+ventureLocalDuration(range[1])+' left'):q?.estimateStatus==='overrun'?'Still working on it':run.model==='qwen-ultra-local'?'Ultra can take a while. You can leave and return later.':'';
 return '<div class="venture-loading"'+(estimated?' data-estimated':'')+'><div class="venture-loading-heading"><venture-loading-symbol aria-hidden="true"></venture-loading-symbol><span class="venture-loading-label" role="status" aria-label="'+escapeHTML(labels[0])+'"><span aria-hidden="true">'+labels.map(t=>'<span>'+escapeHTML(t)+'</span>').join('')+'</span></span></div><div class="venture-progress-track'+(estimated?' is-estimated':'')+'" role="progressbar" aria-label="'+(estimated?'Estimated response progress':'Answer in progress')+'"'+(estimated?' aria-valuemin="0" aria-valuemax="100" aria-valuenow="'+percent+'" aria-valuetext="'+escapeHTML(caption)+' (estimated)"':'')+'><span'+(estimated?' style="width:'+percent+'%"':'')+'></span></div><span class="venture-progress-estimate">'+escapeHTML(caption)+'</span></div>';
}
function ventureLocalDuration(seconds){seconds=Math.max(0,Math.round(seconds));return seconds<60?seconds+'s':Math.floor(seconds/60)+'m'+(seconds%60?' '+seconds%60+'s':'');}
function ventureLocalDetailsButton(run){
 return run.localQueue?'<button type="button" class="venture-response-info" data-v-action="local-info" aria-label="Response information" aria-haspopup="dialog" aria-expanded="false" title="Response information"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="12" r="6.5"/><path d="M9 11v5m0-9v.5m9 3 3 2.5-3 2.5"/></svg></button>':'';
}
let ventureLocalInfo=null;
function ventureCloseLocalInfo(){if(ventureLocalInfo){ventureLocalInfo.close();ventureLocalInfo=null;}}
function ventureOpenLocalInfo(run,button){
 const same=ventureLocalInfo?.runId===run.runId;ventureCloseLocalInfo();if(same)return;
 const controller=new AbortController(),popover=document.createElement('section');popover.className='venture-response-popover';popover.setAttribute('role','dialog');popover.setAttribute('aria-label','Local response information');popover.setAttribute('popover','auto');popover.tabIndex=-1;popover.innerHTML='<p>'+(run.model==='qwen-ultra-local'?'Qwen3.5-27B Ultra-Uncensored Heretic':(run.localQueue?.modelVariant||'standard')==='heretic'?'Qwen3.8-9B Heretic · Uncensored':'Qwen3.8-9B Distill · Standard')+'</p>'+ventureLocalProgress(run,true);
 const close=()=>{controller.abort();button.setAttribute('aria-expanded','false');if(popover.matches(':popover-open'))popover.hidePopover();popover.remove();if(ventureLocalInfo?.element===popover)ventureLocalInfo=null;};
 ventureLocalInfo={runId:run.runId,element:popover,close};document.body.appendChild(popover);button.setAttribute('aria-expanded','true');
 if(popover.showPopover)popover.showPopover();
 const anchor=button.getBoundingClientRect(),rect=popover.getBoundingClientRect();
 popover.style.left=Math.max(12,Math.min(innerWidth-rect.width-12,anchor.right-rect.width))+'px';
 popover.style.top=(anchor.top>rect.height+20?anchor.top-rect.height-10:Math.min(innerHeight-rect.height-12,anchor.bottom+10))+'px';
 document.addEventListener('pointerdown',event=>{if(!popover.contains(event.target)&&!button.contains(event.target))close();},{signal:controller.signal});
 document.addEventListener('keydown',event=>{if(event.key==='Escape'){close();if(button.isConnected)button.focus();}},{signal:controller.signal});
 window.addEventListener('resize',close,{signal:controller.signal});$('ventureTranscript').addEventListener('scroll',close,{signal:controller.signal});
 popover.addEventListener('toggle',event=>{if(event.newState==='closed')close();},{signal:controller.signal});
}
async function ventureFlushLocalSettings(){
 clearTimeout(venture.settingsSaving);
 if(venture.savedSettingsVersion===venture.settingsVersion)return true;
 await venturePersistSettings();
 const until=Date.now()+5000;
 while(venture.persisting&&Date.now()<until)await new Promise(resolve=>setTimeout(resolve,50));
 return venture.savedSettingsVersion===venture.settingsVersion;
}

function ventureHereticAvailable(){return venture.localCapability?.variants?.heretic?.available===true;}
function venturePaintUncensored(){
 const row=$('ventureUncensoredRow'),toggle=$('ventureUncensored'),help=$('ventureUncensoredHelp');
 if(!row||!toggle||!help)return;
 const local=venture.settings.model==='qwen3.8-local',on=venture.settings.qwenUncensored===true,ready=ventureHereticAvailable();
 row.hidden=help.hidden=!local;toggle.checked=local&&on;
 // A missing variant can always be switched OFF; never silently reset saved intent.
 toggle.disabled=!local||(!ready&&!on);
 help.textContent=!venture.localCapability?.variants?'Update FUPCJ Server to enable model switching.':
  !ready?(on?'Heretic unavailable. Turn off to use Standard.':'Install and enable Heretic on FUPCJ Server first.'):
  on?'Heretic model selected. Refusals may still occur.':'Off uses the original Distill model.';
}

// Compatibility for replies saved before in-band progress markers were removed
// by the processor. Restore valid notes to the same disclosure, but never
// render control tags or their contents as answer Markdown.
function ventureLocalResponseParts(value){
 const original=String(value||''),open='<progress_summary>',close='</progress_summary>';
 if(!original.includes('<'))return {text:original,notes:[]};
 const notes=[],pattern=/<progress_summary>([\s\S]*?)<\/progress_summary>/g;
 let text='',offset=0,removed=false;
 for(const match of original.matchAll(pattern)){
  text+=original.slice(offset,match.index);
  const note=match[1].trim().replace(/\s+/gu,' ');
  const count=note?note.split(/\s+/u).length:0;
  if(count>=3&&count<=120&&note.length<=1600&&!/[<>\x00-\x08]/u.test(note))notes.push(note);
  offset=match.index+match[0].length;
  removed=true;
 }
 let rest=original.slice(offset),unfinished=rest.indexOf(open);
 if(unfinished!==-1){rest=rest.slice(0,unfinished);removed=true;}
 // Avoid brief flashes of a partially streamed marker from an older server.
 for(const marker of [open,close]){
  for(let length=Math.min(marker.length-1,rest.length);length>=4;length--){
   if(rest.endsWith(marker.slice(0,length))){rest=rest.slice(0,-length);removed=true;break;}
  }
 }
 if(rest.includes(close)){rest=rest.replaceAll(close,'');removed=true;}
 text+=rest;
 if(removed)text=text.replace(/^\n+/u,'');
 return {text,notes};
}

// Public model summaries and measured processor activity, never a raw thought transcript.
// Keep account-scoped progress from the backend as text rather than HTML.
function ventureStreamActivity(run){
 const active=ventureActive(run),metrics=run.localQueue?.metrics||{},a=metrics.activity;
 const details=[];
 if(a?.version!==1)return {details,notice:active&&Number.isFinite(metrics.inputTokens)?'Live output measurements are unavailable for this run. Input tokens are a fixed request size, not progress. Update FUPCJ and start a new request to enable live measurements.':''};
 if(Number.isInteger(a.generatedTokens)&&a.generatedTokens>=0)
  details.push(a.generatedTokens.toLocaleString()+' generated tokens this pass (including thinking)');
 else if(Number.isFinite(a.receivedCharacters)&&a.receivedCharacters>0)
  details.push(a.receivedCharacters.toLocaleString()+' output characters received this pass');
 if(Number.isFinite(a.tokensPerSecond)&&a.tokensPerSecond>0)
  details.push(a.tokensPerSecond.toFixed(2)+' measured tokens/s');
 if(!active)return {details,notice:''};
 const age=Number.isFinite(a.secondsSinceOutput)?a.secondsSinceOutput:a.elapsedSeconds;
 const received=Number.isFinite(a.secondsSinceOutput);
 if(Number.isFinite(age))details.push(received?'Last model output '+ventureLocalDuration(age)+' ago':'Waiting for first model output · '+ventureLocalDuration(age));
 if(Number.isFinite(a.secondsSinceEvent))details.push('Last runtime contact '+ventureLocalDuration(a.secondsSinceEvent)+' ago');
 return {details,notice:Number.isFinite(age)&&age>=120?'No new model output for at least two minutes. Processing may be slow or stalled; a runtime heartbeat does not confirm generation. You can Stop this request and retry.':''};
}
function ventureUpdateProgressNotes(host,run){
 if(!host)return;
 const data=run.localQueue?.progress;
 const valid=s=>Number.isInteger(s.id)&&typeof s.text==='string'&&s.text.trim().split(/\s+/u).length>=3&&s.text.trim().split(/\s+/u).length<=120&&s.text.length<=1600;
 const steps=data?.version===1&&Array.isArray(data.steps)?data.steps.filter(valid).slice(-128):[];
 if(ventureIsLocal(run.model)){
  // Earlier server versions sometimes stored in-band notes in the answer.
  const recovered=ventureLocalResponseParts(run.text).notes;
  const seen=new Set(steps.map(s=>s.text.trim().toLocaleLowerCase()));
  for(let i=0;i<recovered.length;i++){
   const note=recovered[i],key=note.toLocaleLowerCase();
   if(!seen.has(key)){steps.push({id:1000000+i,text:note,source:'model',legacy:true});seen.add(key);}
  }
  if(steps.length>128)steps.splice(0,steps.length-128);
 }
 if(!steps.length){host.replaceChildren();delete host.progressState;return;}
 const active=ventureActive(run),latest=steps[steps.length-1];
 const latestModel=steps.slice().reverse().find(s=>s.source==='model');
 const featured=latestModel||latest;
 const q=run.localQueue||{},metrics=q.metrics||{};
 const native=metrics.nativeReasoning||{},deep=metrics.reasoning||{};
 const activity=ventureStreamActivity(run);
 const details=[];
 if(metrics.computeRoute==='mac-rpc')details.push('Mac Metal processor');
 else if(metrics.computeRoute==='fupcj')details.push('FUPCJ processor');
 if(Number.isFinite(metrics.inputTokens)&&Number.isFinite(metrics.contextTokens))
  details.push(metrics.inputTokens.toLocaleString()+' input tokens · '+metrics.contextTokens.toLocaleString()+' context capacity');
 if(Number.isInteger(deep.stageNumber)&&deep.stageNumber>=1&&deep.stageNumber<=3)
  details.push('Deep stage '+deep.stageNumber+' of 3');
 details.push(...activity.details);
 if(metrics.activity?.version!==1&&Number.isFinite(native.receivedCharacters)&&native.receivedCharacters>0)
  details.push(native.receivedCharacters.toLocaleString()+' native reasoning characters observed'+(Number.isFinite(native.observedSeconds)?' over '+ventureLocalDuration(native.observedSeconds):''));
 if(Number.isFinite(q.processingSeconds)&&q.processingSeconds>0)details.push(ventureLocalDuration(q.processingSeconds)+' processing');
 const signature=JSON.stringify([active,run.status,run.phase,steps,details,activity.notice]);
 if(host.progressState?.signature===signature)return;
 let state=host.progressState;
 if(!state){
  const panel=document.createElement('details');panel.className='venture-progress-notes';panel.open=true;
  const title=document.createElement('summary');
  const phase=document.createElement('div');phase.className='venture-progress-phase';
  const label=document.createElement('strong');label.className='venture-progress-label';
  const current=document.createElement('p');current.className='venture-progress-current';current.setAttribute('role','status');current.setAttribute('aria-live','polite');current.setAttribute('aria-atomic','true');
  const telemetry=document.createElement('p');telemetry.className='venture-progress-telemetry';
  const health=document.createElement('p');health.className='venture-progress-health';health.setAttribute('role','status');
  const help=document.createElement('p');help.className='venture-progress-help';
  const history=document.createElement('details');history.className='venture-progress-history';history.open=true;
  const historyTitle=document.createElement('summary'),list=document.createElement('div');list.className='venture-progress-steps';
  history.append(historyTitle,list);panel.append(title,phase,label,current,telemetry,health,help,history);host.appendChild(panel);
  state=host.progressState={panel,title,phase,label,current,telemetry,health,help,history,historyTitle,list,rows:new Map()};
 }
 state.title.textContent=active?'Qwen · live activity':run.status==='cancelled'?'Stopped · Qwen activity':'Qwen activity history';
 state.phase.textContent=active?'Current processor phase · '+(run.phase||'Working'):'Response status · '+(run.status||'finished');
 state.label.textContent=latestModel?'Qwen’s latest public explanation':'Latest processor event';
 if(state.current.textContent!==featured.text)state.current.textContent=featured.text;
 state.telemetry.hidden=!details.length;
 state.telemetry.textContent=details.join(' · ');
 state.health.hidden=!activity.notice;
 if(state.health.textContent!==activity.notice)state.health.textContent=activity.notice;
 state.help.textContent=latestModel
  ?'Model-written explanations describe high-level work, not private chain-of-thought. Processor events and measurements are reported separately.'
  :'No model-written explanation has arrived. The history below contains observed processor events, not a reconstruction of Qwen’s thoughts.';
 const historySteps=steps.filter(s=>!active||s.id!==featured.id);
 state.history.hidden=!historySteps.length;
 state.historyTitle.textContent='Activity timeline · '+historySteps.length+' '+(historySteps.length===1?'entry':'entries');
 const keep=new Set(historySteps.map(step=>step.id));
 for(const [id,row] of state.rows)if(!keep.has(id)){row.remove();state.rows.delete(id);}
 let previousRow=null;
 for(const step of historySteps){
  let row=state.rows.get(step.id);
  if(!row){
   row=document.createElement('div');row.className='venture-progress-entry'+(step.source==='model'?' is-model':'');
   const heading=document.createElement('div');heading.className='venture-progress-entry-heading';
   const type=document.createElement('strong');type.textContent=step.source==='model'?'Qwen public summary':'Processor event';
   const index=document.createElement('span');index.textContent='Step '+step.id;
   heading.append(type,index);
   if(Number.isFinite(step.atSeconds)&&step.atSeconds>=0){
    const clock=document.createElement('span');clock.textContent='+'+ventureLocalDuration(step.atSeconds);heading.append(clock);
   }
   const copy=document.createElement('p');copy.textContent=step.text;
   row.append(heading,copy);state.rows.set(step.id,row);
  }
  // Move only when necessary: a normal status poll preserves scroll and selection.
  // A formerly featured summary can rejoin history before an existing event.
  const before=previousRow?previousRow.nextSibling:state.list.firstChild;
  if(row!==before)state.list.insertBefore(row,before);
  previousRow=row;
 }
 state.active=active;state.signature=signature;
}
