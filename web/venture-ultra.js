/* Controls for the installed 27B profile. Hardware tuning remains on the PC. */
const VENTURE_ULTRA_ID='qwen-ultra-local';
const VENTURE_ULTRA_DEFAULTS={contextTokens:8192,thinking:true,longResponses:true,performance:'auto',temperature:.6,topP:.95,topK:20,minP:0,repeatPenalty:1,presencePenalty:0,seed:-1};
const VENTURE_ULTRA_DIALS=[['temperature','Temperature',0,2,.05],['topP','Top P',.01,1,.01],['topK','Top K',0,100,1],['minP','Min P',0,1,.01],['repeatPenalty','Repeat penalty',.8,1.5,.01],['presencePenalty','Presence penalty',-2,2,.1]];
function ventureUltraOptions(value){const result=Object.fromEntries(Object.entries(VENTURE_ULTRA_DEFAULTS).map(([k,v])=>[k,value?.[k]??v])),cap=ventureUltraCapability();if(cap&&cap.capabilities?.longResponses!==true)delete result.longResponses;return result;}
function ventureUltraCapability(){return venture.localCapability?.ultra||null;}
function ventureUltraControls(){return '<section id="ventureUltraControls" class="venture-ultra-controls" hidden><p class="venture-ultra-label">27B · Ultra-Uncensored Heretic · Q4_K_M</p><label class="venture-ultra-check"><input id="ventureUltraThinking" type="checkbox">Multistep thinking</label><p class="venture-fine-print">Thinking can take longer. “Uncensored” is the publisher’s description; refusals and mistakes can still occur.</p><label class="venture-ultra-check"><input id="ventureUltraLongResponses" type="checkbox">Long responses · keep working and save progress</label><p id="ventureUltraLongHelp" class="venture-fine-print">Uses the tested context, reserves answer space, and continues long replies automatically. May take hours.</p><label for="ventureUltraContext">Context window</label><select id="ventureUltraContext"><option value="4096">4,096 tokens</option><option value="8192">8,192 tokens</option></select><label for="ventureUltraPerformance">Performance</label><select id="ventureUltraPerformance"><option value="auto">Fastest measured profile</option><option value="cpu">Measured CPU profile</option></select><p id="ventureUltraHardware" class="venture-fine-print"></p><details><summary>Sampling controls</summary>'+VENTURE_ULTRA_DIALS.map(([k,label,min,max,step])=>'<label class="venture-dial-label" for="ventureUltra_'+k+'">'+label+'<output id="ventureUltraValue_'+k+'"></output></label><input id="ventureUltra_'+k+'" type="range" min="'+min+'" max="'+max+'" step="'+step+'">').join('')+'<label for="ventureUltraSeed">Seed (−1 = random)</label><input type="number" id="ventureUltraSeed" min="-1" max="2147483647" step="1"></details><p class="venture-fine-print">File reading, code writing and the installed image worker are available through Venture. Web search, code execution and image generation are not integrated with this model.</p></section>';}
function venturePaintUltra(){
 const host=$('ventureUltraControls');if(!host)return;
 host.hidden=venture.settings.model!==VENTURE_ULTRA_ID;if(host.hidden)return;
 const o=ventureUltraOptions(venture.settings.qwenUltra),cap=ventureUltraCapability();
 const contexts=cap?.contexts||[4096,8192];
 $('ventureUltraContext').innerHTML=[...new Set([...contexts,o.contextTokens])].sort((a,b)=>a-b).map(n=>'<option value="'+n+'"'+(!contexts.includes(n)?' disabled':'')+'>'+n.toLocaleString()+' tokens'+(!contexts.includes(n)?' · needs tuning':'')+'</option>').join('');
 $('ventureUltraContext').value=o.contextTokens;$('ventureUltraThinking').checked=o.thinking;$('ventureUltraPerformance').value=o.performance;
 const longAvailable=cap?.capabilities?.longResponses===true;
 // Unknown capability is not an old server: do not erase a saved off choice during initial loading.
 if(cap&&!longAvailable&&venture.settings.qwenUltra)delete venture.settings.qwenUltra.longResponses;
 $('ventureUltraLongResponses').disabled=!longAvailable;$('ventureUltraLongResponses').checked=longAvailable&&o.longResponses!==false;
 $('ventureUltraLongHelp').textContent=!longAvailable?'Update FUPCJ Server to enable long responses.':o.longResponses!==false?'Uses the available tested context and continues long replies automatically. The output slider reserves answer space per pass; it is not a whole-reply cap. May take hours. Stop remains available.':'Single pass: thinking and the answer share the selected output-token limit.';
 $('ventureUltraSeed').value=o.seed;
 for(const [k] of VENTURE_ULTRA_DIALS){$('ventureUltra_'+k).value=o[k];$('ventureUltraValue_'+k).textContent=o[k];}
 const p=cap?.performance?.[o.performance];
 $('ventureUltraHardware').textContent=p?p.threads+' CPU threads · '+p.gpuLayers+' GPU layers · '+p.benchmark.generationTokensPerSecond.toFixed(2)+' measured tokens/s. Larger contexts require a successful Tune on the PC.':'Install and tune Ultra on FUPCJ to measure performance and enable it.';
}
function ventureReadUltra(){
 const value={contextTokens:Number($('ventureUltraContext').value),thinking:$('ventureUltraThinking').checked,performance:$('ventureUltraPerformance').value,seed:Number($('ventureUltraSeed').value)};
 const cap=ventureUltraCapability();
 if(cap?.capabilities?.longResponses===true)value.longResponses=$('ventureUltraLongResponses').checked;
 else if(!cap&&typeof venture.settings.qwenUltra?.longResponses==='boolean')value.longResponses=venture.settings.qwenUltra.longResponses;
 for(const [k] of VENTURE_ULTRA_DIALS){value[k]=Number($('ventureUltra_'+k).value);$('ventureUltraValue_'+k).textContent=value[k];}
 return value;
}

const VENTURE_ULTRA_COFFEE_IMAGE=/* VISION_ULTRA_COFFEE_IMAGE */ './qwen-ultra-coffee.png';
const ventureUltraProgressState=new Map();
async function ventureUltraNotice(){
 if(venture.ultraNoticePromise)return venture.ultraNoticePromise;
 const epoch=venture.epoch;
 const task=(async()=>{
  let notice;
  try{notice=await ventureJSON('/venture/ultra/notice');}
  catch(error){if(epoch===venture.epoch)ventureSetNotice('Unable to check the Ultra notice. Update FUPCJ Server and try again. '+ventureError(error));return false;}
  if(epoch!==venture.epoch)return false;
  if(notice.due===false){venture.localAcknowledged=true;return true;}
  return new Promise(resolve=>{
   const dialog=document.createElement('dialog');dialog.className='venture-ultra-notice';dialog.setAttribute('aria-labelledby','ventureUltraNoticeTitle');dialog.setAttribute('aria-describedby','ventureUltraNoticeCopy');
   dialog.innerHTML='<img class="venture-ultra-coffee" src="'+VENTURE_ULTRA_COFFEE_IMAGE+'" alt="A smiling character relaxing with a cup of coffee"><span class="venture-ultra-eyebrow">QWEN ULTRA</span><h2 id="ventureUltraNoticeTitle">Good things take a little time.</h2><p id="ventureUltraNoticeCopy">Qwen Ultra runs locally on the FUPCJ server. Responses can take significantly longer, and long responses may take hours, so grab yourself a coffee and settle in. Your accepted request keeps running if you leave, and visible progress is saved.</p><p class="venture-ultra-notice-error" role="status"></p><button type="button" data-accept>Acknowledge</button>';
   let accepted=false;document.body.appendChild(dialog);
   dialog.querySelector('[data-accept]').onclick=async()=>{
    const button=dialog.querySelector('[data-accept]');button.disabled=true;
    try{await ventureJSON('/venture/ultra/notice','POST',{});if(epoch!==venture.epoch){dialog.close();return;}venture.localAcknowledged=true;accepted=true;dialog.close();}
    catch(error){if(epoch===venture.epoch){dialog.querySelector('[role="status"]').textContent=ventureError(error);button.disabled=false;}}
   };
   dialog.addEventListener('close',()=>{dialog.remove();resolve(accepted&&epoch===venture.epoch);},{once:true});dialog.showModal();
  });
 })();
 venture.ultraNoticePromise=task;
 try{return await task;}finally{if(venture.ultraNoticePromise===task)venture.ultraNoticePromise=null;}
}
function ventureUltraProgress(run){
 const active=ventureActive(run),q=run.localQueue||{};
 if(!active&&q.longResponse){
  ventureUltraProgressState.delete(run.runId);
  return q.metrics?.generation?.continuationContextTruncated?'<p class="venture-fine-print">Long-answer note: later sections used the ending of the saved answer rather than all earlier sections. The original request and source evidence were kept. Review continuity across sections.</p>':'';
 }
 if(active&&q.longResponse){
  const generation=q.metrics?.generation||{},parts=Math.max(0,Math.min(64,Number(generation.completedPasses)||0));
  const waiting=q.requestsAhead||run.status==='queued';
  const title=run.phase==='Stopping'?'Stopping your response…':waiting?'Waiting for your turn':generation.passNumber>1?'Continuing your answer':'Working on your answer';
  const caption=waiting?'Your request is saved.':parts?parts+' processing '+(parts===1?'pass':'passes')+' finished · visible progress is saved':'You can leave and return later. Visible progress is saved.';
  const note=generation.continuationContextTruncated?'<small class="venture-fine-print">Earlier sections remain saved. This continuation uses the ending of the answer.</small>':'';
  return '<div class="venture-loading venture-ultra-loading"><div class="venture-loading-heading"><venture-loading-symbol aria-hidden="true"></venture-loading-symbol><span role="status">'+escapeHTML(title)+'</span></div><span class="venture-progress-estimate">'+escapeHTML(caption)+'</span>'+note+'</div>';
 }
 let state=ventureUltraProgressState.get(run.runId);
 if(!active){
  if(!state)return '';
  if(run.status!=='completed'){ventureUltraProgressState.delete(run.runId);return '';}
  state.percent=100;
  return '<div class="venture-loading venture-ultra-loading is-finished"><div class="venture-progress-track is-ultra" role="progressbar" aria-label="Response complete" aria-valuemin="0" aria-valuemax="100" aria-valuenow="100"><span style="width:100%"></span></div></div>';
 }
 if(!state){state={percent:0};ventureUltraProgressState.set(run.runId,state);if(ventureUltraProgressState.size>100)ventureUltraProgressState.delete(ventureUltraProgressState.keys().next().value);}
 const total=q.estimatedTotalSeconds,elapsed=q.processingSeconds||0,hasETA=Number.isFinite(total)&&total>0&&Number.isFinite(q.estimatedCompletionAt);
 if(hasETA)state.percent=Math.max(state.percent,Math.min(97,100*elapsed/total));
 const labels=ventureLoadingLabels(run),clock=hasETA?new Date(q.estimatedCompletionAt*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):'';
 const caption=q.requestsAhead||run.status==='queued'?'ETA available when your turn begins':hasETA?(q.estimateStatus==='overrun'?'Taking longer than estimated · originally expected ':'Estimated completion · ')+clock:q.estimateStatus==='preparing'?'Preparing your files · ETA follows':'Measuring response time…';
 const basis=hasETA?(q.estimateBasis==='calibrated'?'Based on the saved server calibration':'Initial estimate · learning from completed responses'):'';
 return '<div class="venture-loading venture-ultra-loading"><div class="venture-loading-heading"><venture-loading-symbol aria-hidden="true"></venture-loading-symbol><span role="status">'+escapeHTML(run.phase==='Stopping'?'Stopping your response…':labels[0])+'</span></div><div class="venture-progress-track is-ultra" role="progressbar" aria-label="Estimated response progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="'+Math.round(state.percent)+'" aria-valuetext="'+escapeHTML(caption)+'"><span style="width:'+state.percent.toFixed(3)+'%"></span></div><span class="venture-progress-estimate">'+escapeHTML(caption)+'</span>'+(basis?'<small class="venture-ultra-estimate-basis">'+escapeHTML(basis)+'</small>':'')+'</div>';
}
