/* Local provider UI. All model access still goes through the account backend. */
VENTURE_MODEL_SYMBOLS.qwen = "<svg role=\"img\" viewBox=\"0 0 24 24\" xmlns=\"http://www.w3.org/2000/svg\"><title>QWen</title><path d=\"M23.919 14.545 20.817 9.17l1.47-2.544a.56.56 0 0 0 0-.566l-1.633-2.83a.57.57 0 0 0-.49-.283h-6.207L12.487.402a.57.57 0 0 0-.49-.284H8.732a.56.56 0 0 0-.49.284L5.139 5.775h-2.94a.56.56 0 0 0-.49.284L.077 8.887a.56.56 0 0 0 0 .567L3.18 14.83l-1.47 2.545a.56.56 0 0 0 0 .566l1.634 2.83a.57.57 0 0 0 .49.283h6.205l1.47 2.545a.57.57 0 0 0 .49.284h3.266a.57.57 0 0 0 .49-.284l3.104-5.375h2.94a.57.57 0 0 0 .49-.283l1.634-2.828a.55.55 0 0 0-.004-.568M8.733.686l1.634 2.828-1.634 2.828H21.8L20.164 9.17H7.425L5.63 6.06Zm1.306 19.801-6.205-.002 1.634-2.83h3.265L2.201 6.344h3.267q3.182 5.517 6.367 11.032zm10.124-5.66L18.53 12l-6.532 11.315-1.634-2.83c2.129-3.673 4.25-7.351 6.373-11.028h3.592l3.102 5.374z\"/></svg>";
function ventureIsLocal(model=venture.settings.model){return model==='qwen3.8-local';}
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
  dialog.innerHTML='<h2 id="qwenNoticeTitle">Qwen3.8 Local</h2><p>Qwen runs on the FUPCJ server. Expect longer responses. Requests are processed one at a time; your queue position and elapsed time appear with the response. Estimated completion will be shown when measured data is available.</p><p>Local means FUPCJ, not this phone. Authorized server administrators may access server-held data. This is not an end-to-end encryption guarantee.</p><p>This initial profile supports text, extracted files, and project retrieval. Photo pixel analysis, web search, and code execution are unavailable. Local dictation uses PC Whisper. No API token charge.</p><p class="qwen-notice-error" role="status"></p><div class="dialog-actions"><button type="button" data-close>Cancel</button><button type="button" data-accept>OK</button></div>';
  document.body.appendChild(dialog);
  let accepted=false;
  dialog.querySelector('[data-close]').onclick=()=>dialog.close();
  dialog.querySelector('[data-accept]').onclick=async()=>{const button=dialog.querySelector('[data-accept]');button.disabled=true;
   try{const prefs=await ventureJSON('/venture/local/disclosure','POST',{version:venture.disclosureVersion||'qwen-local-v1'});if(epoch!==venture.epoch){dialog.close();return;}ventureAcceptPreferences(prefs);accepted=true;dialog.close();}
   catch(error){dialog.querySelector('.qwen-notice-error').textContent=ventureError(error);button.disabled=false;}
  };
  dialog.addEventListener('close',()=>{dialog.remove();resolve(accepted&&epoch===venture.epoch);},{once:true});dialog.showModal();
 });
 try{return await venture.localNoticePromise;}finally{venture.localNoticePromise=null;}
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
