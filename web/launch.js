// Guest leases are issued and timed by FUPCJ Server, not Firebase anonymous auth.
// Only a random device receipt is persisted, never guest projects, files or API keys.
const TRIAL_RECEIPT='vision-guest-receipt-v1';
let trialSession=null,trialStarting=false,trialTimer=0,trialFinishing=null;
function trialRemaining(){return trialSession?Math.max(0,Math.min(trialSession.until-performance.now(),trialSession.wallUntil-Date.now())):0;}
const trialRequests=new Set();
function trialRead(){try{const v=JSON.parse(localStorage.getItem(TRIAL_RECEIPT)||'null');return v&&/^[a-f0-9]{64}$/.test(v.deviceId)?v:null;}catch{return null;}}
function trialWrite(value){try{localStorage.setItem(TRIAL_RECEIPT,JSON.stringify(value));return true;}catch{return false;}}
function trialActive(){return !!(trialSession&&trialRemaining()>0&&cloudAuth?.kind==='trial');}
function accountCanUseApp(){return !accountGateLocked&&accountSignedIn();}
function trialPaint(){
 const button=$('accountGateTrial'),receipt=trialRead();
 const gateway=!!accountContinueURL;
 button.hidden=true;button.disabled=accountBusy||trialStarting||!!receipt?.consumed;
 $('accountTrialWarning').hidden=true;
 for(const details of document.querySelectorAll('#accountGate .trial-details'))details.hidden=true;
 button.textContent=trialStarting?'Starting your trial…':receipt?.consumed?'Trial used · sign in to continue':'Or try for five minutes';
 $('trialBanner').hidden=!trialActive();
 if(trialActive()){
  const seconds=Math.max(0,Math.ceil(trialRemaining()/1000));
  const text=Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0')+' left';
  if($('trialCountdown').textContent!==text)$('trialCountdown').textContent=text;
 }
}
async function trialStart(){accountError='Create an account and wait for approval to use Vision.';accountPaint();}
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
