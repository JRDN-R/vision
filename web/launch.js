// Guest leases are issued and timed by FUPCJ Server, not Firebase anonymous auth.
// Only a random device receipt is persisted, never guest projects, files or API keys.
const TRIAL_RECEIPT='vision-guest-receipt-v1';
let trialSession=null,trialStarting=false,trialTimer=0,trialFinishing=null;
function trialRemaining(){return trialSession?Math.max(0,Math.min(trialSession.until-performance.now(),trialSession.wallUntil-Date.now())):0;}
const trialRequests=new Set();
function trialRead(){try{const v=JSON.parse(localStorage.getItem(TRIAL_RECEIPT)||'null');return v&&/^[a-f0-9]{64}$/.test(v.deviceId)?v:null;}catch{return null;}}
function trialWrite(value){try{localStorage.setItem(TRIAL_RECEIPT,JSON.stringify(value));return true;}catch{return false;}}
function trialActive(){return !!(trialSession&&trialRemaining()>0&&cloudAuth?.kind==='trial');}
function accountCanUseApp(){return !accountGateLocked&&(accountSignedIn()||trialActive());}
function trialPaint(){
 const button=$('accountGateTrial'),receipt=trialRead();
 const gateway=!!accountContinueURL;
 button.hidden=location.protocol==='file:'||gateway;button.disabled=accountBusy||trialStarting||!!receipt?.consumed;
 $('accountTrialWarning').hidden=gateway;
 for(const details of document.querySelectorAll('#accountGate .trial-details'))details.hidden=gateway;
 button.textContent=trialStarting?'Starting your trial…':receipt?.consumed?'Trial used · sign in to continue':'Or try for five minutes';
 $('trialBanner').hidden=!trialActive();
 if(trialActive()){
  const seconds=Math.max(0,Math.ceil(trialRemaining()/1000));
  const text=Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0')+' left';
  if($('trialCountdown').textContent!==text)$('trialCountdown').textContent=text;
 }
}
async function trialStart({resume=false}={}){
 await accountReady;
 if(accountContinueURL||accountRestoring||accountRestoreFailed||accountSignedIn()||accountBusy||trialStarting||trialSession)return;
 trialStarting=true;accountError='';accountPaint();
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
 try{
  const receipt=trialRead()||{deviceId:projectRandom(32)};
  if(receipt.consumed)throw new Error('Your trial has been used. Sign in to continue.');
  if(!trialWrite({...receipt,started:true}))throw new Error('Allow site storage to use the one-time trial, or sign in.');
  const response=await fetch(ACCOUNT_PC+'/api/trial/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({deviceId:receipt.deviceId}),credentials:'omit',signal:controller.signal});
  let value;try{value=await response.json();}catch{throw new Error('Update FUPCJ Server to enable five-minute trials. Account sign-in is still available.');}
  if(!response.ok){if(['trial-used','trial-expired'].includes(value.code))trialWrite({...receipt,started:true,consumed:true});throw new Error(response.status===404?'Update FUPCJ Server to enable five-minute trials. Account sign-in is still available.':value.error||'The trial could not start.');}
  if(!/^[a-f0-9]{24}$/.test(value.id||'')||!/^trial_[a-f0-9]{24}_[a-f0-9]{64}$/.test(value.token||'')||!Number.isFinite(value.expiresAt)||!Number.isFinite(value.serverNow)||!validProjectIdentity(value.project)||value.project.id!=='trial-'+value.id)throw new Error('The server returned an invalid trial session.');
  if(accountSignedIn())return;
  const remaining=Math.max(0,Math.min(300,value.expiresAt-value.serverNow));
  if(!remaining){trialWrite({...receipt,consumed:true});throw new Error('Your five-minute trial has ended. Sign in.');}
  accountGateLocked=true;accountAuthEpoch++;accountClearRunKey();
  cloudConfig={kind:'private-pc',backendUrl:ACCOUNT_PC,publicAccess:true};
  cloudAuth={kind:'trial',backendUrl:ACCOUNT_PC,accessToken:value.token};
  trialSession={...value,project:{...value.project,backendUrl:ACCOUNT_PC},until:performance.now()+remaining*1000,wallUntil:Date.now()+remaining*1000};
  trialWrite({...receipt,started:true,consumed:false});projectHealthCache=null;
  await projectSwitchAccountScope('trial:'+value.id);
  state.projectCloud={...trialSession.project};accountGateLocked=false;accountError='';
  projectStatus('Temporary trial · projects are not saved','local');
  clearInterval(trialTimer);trialTimer=setInterval(()=>{if(trialSession&&!trialActive())void trialFinish();else trialPaint();},250);
  accountPaint();$('accountGate').querySelector('dialog[open]')?.close();
  toast(resume?'Trial resumed. The original timer is still running.':'Your five-minute trial has started. Nothing is saved.');
 }catch(error){if(trialSession)await trialFinish({message:''});accountError=error.name==='AbortError'?'FUPCJ Server did not respond. Try again or sign in.':error.message;}
 finally{clearTimeout(timer);trialStarting=false;accountPaint();}
}
function trialFinish({notify=true,message='Your five-minute trial has ended. Sign in to continue.'}={}){
 if(trialFinishing)return trialFinishing;
 if(!trialSession&&cloudAuth?.kind!=='trial')return Promise.resolve();
 const old=trialSession;trialSession=null;clearInterval(trialTimer);
 const receipt=trialRead();if(receipt)trialWrite({...receipt,started:true,consumed:true});
 accountGateLocked=true;accountAuthEpoch++;accountError=message;accountUpdateGate();
 for(const controller of trialRequests)controller.abort();trialRequests.clear();
 accountClearRunKey();
 if(notify&&old)void fetch(ACCOUNT_PC+'/api/trial/end',{method:'POST',headers:{Authorization:'Bearer '+old.token},credentials:'omit',keepalive:true}).catch(()=>{});
 trialFinishing=(async()=>{
  await projectSwitchAccountScope('signed-out');
  if(cloudAuth?.kind==='trial')cloudAuth=null;
  projectHealthCache=null;accountGateLocked=false;accountPaint();
 })().finally(()=>{trialFinishing=null;});return trialFinishing;
}
async function trialFetch(path,options={}){
 if(!trialActive()){void trialFinish();throw new Error('Your trial has ended. Sign in.');}
 const session=trialSession,epoch=accountAuthEpoch,controller=new AbortController();trialRequests.add(controller);
 const abort=()=>controller.abort();if(options.signal?.aborted)abort();else options.signal?.addEventListener('abort',abort,{once:true});
 const deadline=setTimeout(()=>{abort();options.signal?.removeEventListener('abort',abort);trialRequests.delete(controller);},Math.max(0,session.until-performance.now()));
 const headers=new Headers(options.headers||{});headers.set('Authorization','Bearer '+session.token);
 try{
  const response=await fetch(ACCOUNT_PC+'/api'+path,{...options,headers,signal:controller.signal,credentials:'omit'});
  if(session!==trialSession||epoch!==accountAuthEpoch||!trialActive())throw new Error('The trial or account changed.');
  if(response.status===401||response.status===403){const body=await response.clone().json().catch(()=>({}));if(['trial-expired','trial-invalid'].includes(body.code)){void trialFinish();throw new Error(body.error);}}
  return response;
 }finally{
  if(controller.signal.aborted){clearTimeout(deadline);trialRequests.delete(controller);options.signal?.removeEventListener('abort',abort);}
  else setTimeout(()=>trialRequests.delete(controller),Math.max(0,session.until-performance.now())+1000);
 }
}
$('accountGateTrial').onclick=()=>void trialStart();
$('trialSignIn').onclick=()=>openAccountDialog('Sign in to keep using your saved Vision account.');
$('whyVisionButton').onclick=()=>{const modal=$('whyVisionDialog');if(!modal.open)modal.showModal();};
$('whyVisionClose').onclick=()=>$('whyVisionDialog').close();
$('whyVisionDone').onclick=()=>$('whyVisionDialog').close();
$('whyVisionDialog').addEventListener('click',event=>{if(event.target===$('whyVisionDialog')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
window.addEventListener('storage',event=>{if(event.key===TRIAL_RECEIPT&&trialSession&&trialRead()?.consumed)void trialFinish();});
document.addEventListener('visibilitychange',()=>{if(trialSession&&!trialActive())void trialFinish();});
Promise.resolve().then(async()=>{await accountReady;const receipt=trialRead();if(!accountContinueURL&&!accountSignedIn()&&receipt?.started&&!receipt.consumed)await trialStart({resume:true});else trialPaint();});
