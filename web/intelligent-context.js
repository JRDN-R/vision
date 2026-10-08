/* A context reference identifies an immutable server revision. Editable project
 * exports and autosave remain the authority; this module never rewrites nodes,
 * attachments or instructions. No project key is placed in a model request. */
const ventureContext={capable:false,scope:'',mode:'adaptive',status:null,error:'',timer:null,serial:0,rebuilding:false};
function ventureContextScope(){return [venture.uid,typeof projectEpoch==='number'?projectEpoch:0,state.projectCloud?.id||''].join(':');}
function ventureContextReset(){
 clearTimeout(ventureContext.timer);ventureContext.serial++;ventureContext.scope='';ventureContext.capable=false;ventureContext.mode='adaptive';ventureContext.status=null;ventureContext.error='';ventureContext.rebuilding=false;
 if($('ventureContextDialog')?.open)$('ventureContextDialog').close();
}
function ventureContextSetHealth(health){ventureContext.capable=health?.capabilities?.intelligentContextV1===true;ventureContext.status=null;ventureContext.error='';ventureContextPaint();ventureContextSchedule(0);}
function ventureContextInstall(){
 const dialog=document.createElement('dialog');dialog.id='ventureContextDialog';dialog.className='venture-modal venture-context-dialog';
 dialog.innerHTML='<h2>Board context</h2><p id="ventureContextDetails" class="venture-fine-print" role="status"></p><p class="venture-fine-print">Your editable project stays complete. Venture selects evidence from the saved revision on FUPCJ Server. This reference is not an offline project export.</p><label class="venture-context-full"><input id="ventureContextFull" type="checkbox">Include all extracted source text</label><p class="venture-fine-print">For comparisons or detailed tasks. Larger contexts can cost more. Files that cannot be fully extracted are reported; visual evidence and Code &amp; files may still be needed.</p><div class="dialog-actions"><button type="button" id="ventureContextRefresh">Refresh status</button><button type="button" id="ventureContextRebuild">Rebuild index</button><button type="button" id="ventureContextClose" class="primary">Done</button></div>';
 document.body.appendChild(dialog);
 $('ventureContextStatus').onclick=()=>{ventureContextPaint();dialog.showModal();ventureContextSchedule(0);};
 $('ventureContextClose').onclick=()=>dialog.close();$('ventureContextRefresh').onclick=()=>ventureContextSchedule(0);$('ventureContextRebuild').onclick=()=>void ventureContextRebuild();
 $('ventureContextFull').onchange=()=>{ventureContext.mode=$('ventureContextFull').checked?'full':'adaptive';ventureContextPaint();};
 $('ventureIncludeBoard').onchange=()=>{ventureContextPaint();ventureContextSchedule(0);};
}
function ventureContextPaint(){
 const button=$('ventureContextStatus');if(!button)return;
 const scope=ventureContextScope();if(scope!==ventureContext.scope){ventureContext.scope=scope;ventureContext.mode='adaptive';ventureContext.status=null;ventureContext.error='';ventureContext.rebuilding=false;ventureContext.serial++;}
 const enabled=$('ventureIncludeBoard').checked,meta=state.projectCloud,stale=ventureContext.status?.revision!==meta?.revision;
 const status=!ventureContext.capable?'legacy':ventureContext.error?'unavailable':(typeof projectPending!=='undefined'&&projectPending)||!meta?.revision||stale?'updating':ventureContext.status?.status||'updating';
 const label={ready:'Context ready',updating:'Context updating',attention:'Check context',unavailable:'Context unavailable',legacy:'Full board'}[status]||'Check context';
 button.hidden=!enabled;button.textContent=ventureContext.mode==='full'&&ventureContext.capable?'Full context':label;button.dataset.status=status;button.title='Board context status and options';
 $('ventureToolsLabel').hidden=enabled;
 const full=$('ventureContextFull');if(full){full.checked=ventureContext.mode==='full';full.disabled=!ventureContext.capable||venture.sending;}
 const rebuild=$('ventureContextRebuild');if(rebuild){rebuild.disabled=!ventureContext.capable||!meta?.revision||(typeof projectPending!=='undefined'&&projectPending)||venture.sending||ventureContext.rebuilding;rebuild.textContent=ventureContext.rebuilding?'Queuing…':'Rebuild index';}
 const details=$('ventureContextDetails');if(details){
  const data=ventureContext.status;
  details.textContent=!ventureContext.capable?'This server uses the existing complete board ZIP pathway. Sending the board may use more API tokens. Update FUPCJ Server to enable intelligent context.':ventureContext.error||label+(meta?.revision?' · saved revision '+meta.revision:' · waiting for autosave')+(data?.warnings?.length?' · '+data.warnings.filter(w=>typeof w==='string').join(' '):'');
 }
}
function ventureContextSchedule(delay=800){
 // The portable bundle hoists functions before Venture installs its controls.
 if(!$('ventureContextStatus'))return;
 clearTimeout(ventureContext.timer);ventureContextPaint();
 if(!venture.open||!ventureContext.capable||ventureContext.rebuilding||!$('ventureIncludeBoard')?.checked)return;
 ventureContext.timer=setTimeout(()=>void ventureContextRefresh(),delay);
}
async function ventureContextRefresh(){
 const meta=state.projectCloud;if(!venture.open||!ventureContext.capable||!$('ventureIncludeBoard')?.checked)return;
 if(!meta?.revision||(typeof projectPending!=='undefined'&&projectPending)){ventureContextSchedule(2500);return;}
 const scope=ventureContextScope(),revision=meta.revision,serial=++ventureContext.serial,epoch=venture.epoch;
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);
 try{
  const data=await projectResponse(await projectRequest('/projects/'+encodeURIComponent(meta.id)+'/context?revision='+revision,{signal:controller.signal}));
  if(scope!==ventureContextScope()||epoch!==venture.epoch||serial!==ventureContext.serial||revision!==state.projectCloud?.revision)return;
  if(data.revision!==revision)throw new Error('The context index is for a different revision. Refresh status before sending.');
  ventureContext.status=data;ventureContext.error='';
 }catch(error){if(scope===ventureContextScope()&&epoch===venture.epoch&&serial===ventureContext.serial){ventureContext.status=null;ventureContext.error=error.name==='AbortError'?'Context status timed out. Check FUPCJ Server.':error.message;}}
 finally{clearTimeout(timer);if(scope===ventureContextScope()&&epoch===venture.epoch&&serial===ventureContext.serial)ventureContextSchedule(ventureContext.status?.status==='ready'?15000:4000);}
}
async function venturePrepareBoardContext(){
 // Saving to the existing account-owned project endpoint also queues local
 // indexing. Never duplicate attachment bytes in Venture's multipart request.
 const project=state,epoch=projectEpoch,accountEpoch=venture.epoch;
 for(let attempt=0;attempt<3;attempt++){
  const meta=await ensureRemoteProject();
  if(state!==project||projectEpoch!==epoch||venture.epoch!==accountEpoch)throw new Error('The board or account changed. Review the intended project and send again.');
  if(projectPending)continue;
  if(!Number.isSafeInteger(meta.revision)||meta.revision<1)throw new Error('Save the board to FUPCJ Server before including its context.');
  return {projectId:meta.id,revision:meta.revision,mode:ventureContext.mode};
 }
 throw new Error('The board is still changing. Wait for autosave to finish, then send again.');
}
async function ventureContextRebuild(){
 const meta=state.projectCloud;if(!ventureContext.capable||ventureContext.rebuilding||!meta?.revision||projectPending)return;
 const scope=ventureContextScope(),epoch=venture.epoch,revision=meta.revision,serial=++ventureContext.serial;
 clearTimeout(ventureContext.timer);ventureContext.rebuilding=true;ventureContext.error='';ventureContextPaint();
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
 try{
  const data=await projectResponse(await projectRequest('/projects/'+encodeURIComponent(meta.id)+'/context',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'rebuild',revision}),signal:controller.signal}));
  if(scope!==ventureContextScope()||epoch!==venture.epoch||serial!==ventureContext.serial||revision!==state.projectCloud?.revision)return;
  if(data.revision!==revision)throw new Error('The board revision changed. Refresh status and try again.');
  ventureContext.status=data;
 }catch(error){if(scope===ventureContextScope()&&epoch===venture.epoch&&serial===ventureContext.serial)ventureContext.error=error.name==='AbortError'?'Rebuild status timed out. Refresh status to check the server.':error.message;}
 finally{clearTimeout(timer);if(scope===ventureContextScope()&&epoch===venture.epoch&&serial===ventureContext.serial){ventureContext.rebuilding=false;ventureContextSchedule(1000);}}
}
function ventureContextRunDetails(run){
 const metrics=run.contextMetrics,reference=run.boardContext;if(!metrics&&!reference)return '';
 const lines=[],count=value=>Number.isSafeInteger(value)&&value>=0?value.toLocaleString():null;
 if(reference?.revision)lines.push('Saved board revision '+reference.revision+' · '+(reference.mode==='full'?'all extracted source text':'adaptive retrieval'));
 if(metrics){
  if(count(metrics.retrievedCharacters)!==null)lines.push('Retrieved context: '+count(metrics.retrievedCharacters)+' characters.');
  if(count(metrics.retrievalOperations)!==null)lines.push('Retrieval operations: '+count(metrics.retrievalOperations)+'.');
  if(metrics.complete===false)lines.push('Retrieval coverage is incomplete. Review the source limitations below.');
  const usage=metrics.providerUsage;
  if(usage){
   const values=[['input',usage.input_tokens],['cached input',usage.cached_tokens],['output',usage.output_tokens],['reasoning (within output)',usage.reasoning_tokens]].filter(([,v])=>count(v)!==null).map(([label,v])=>label+' '+count(v));
   if(values.length)lines.push('Provider-reported tokens: '+values.join(' · ')+'.');
   if(usage.complete===false)lines.push('Usage is partial; '+(count(usage.missingResponses)||'some')+' responses did not report usage.');
  }
  if(Array.isArray(metrics.warnings))lines.push(...metrics.warnings.filter(value=>typeof value==='string'));
 }
 return '<details class="venture-preparation venture-context-usage"><summary>Board context and usage</summary><p>'+escapeHTML(lines.join('\n'))+'</p></details>';
}
