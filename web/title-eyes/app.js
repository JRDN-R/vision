import {initAuth, user, accountEpoch, authError, signOut} from '../vortex/auth.js?v=1.0.4&rev=moises.1';
import {createTitleEyesClient} from './client.js?v=2';
const $ = id => document.getElementById(id);
let file = null, previewURL = '', generation = 0, jobId = '', timer, pending = false, copyTimer, uploadId = '', controller;
const api = createTitleEyesClient({getToken:async () => {
  const identity = user(), epoch = accountEpoch();
  if (!identity) throw new Error('Sign in with your Vision account first.');
  const token = await identity.getIdToken();
  if (epoch !== accountEpoch()) throw new Error('The signed-in account changed.');
  return token;
}});
const storageKey = () => 'title-eyes-job:' + user()?.uid;
function remember(id) { try { id ? sessionStorage.setItem(storageKey(), id) : sessionStorage.removeItem(storageKey()); } catch {} }
function remembered() { try { return sessionStorage.getItem(storageKey()) || ''; } catch { return ''; } }
function status(text, error = false) { $('status').textContent = text; $('status').dataset.error = String(error); }
function copyState() { $('copy').disabled = !$('titleText').value.trim(); }
function busy(value) { pending = value; $('cancel').hidden = !value; $('retry').hidden = true; $('choose').setAttribute('aria-busy', String(value)); }
function resetCopy() { clearTimeout(copyTimer); $('copyIcon').textContent='▣'; $('copyLabel').textContent='Copy'; }
function invalidate() { generation++; clearTimeout(timer); controller?.abort(); controller = new AbortController(); }
function clearPreview() { if (previewURL) URL.revokeObjectURL(previewURL); previewURL=''; $('preview').removeAttribute('src'); $('preview').hidden=true; $('empty').hidden=false; $('replace').hidden=true; $('filename').textContent=''; }
function auth() { return window.VisionFirebaseSDK.auth.getAuth(window.VisionFirebaseSDK.app.getApp('vision-account-login')); }
function openLogin() {
  if (!$('login').open) $('login').showModal();
  $('loginStatus').textContent='';
  accountGoogleLogin.prepare().catch(error => { $('loginStatus').textContent=authError(error); });
}
async function poll(id, version) {
  if (version !== generation) return;
  try {
    const job = await api.get(id, {signal:controller.signal});
    if (version !== generation) return;
    if (job.status === 'queued' || job.status === 'processing') {
      busy(true); status(job.phase || 'Reading the title…');
      timer=setTimeout(() => poll(id,version),2000); return;
    }
    busy(false); jobId=''; uploadId=''; remember('');
    if (job.status !== 'complete') throw new Error(job.error || 'The title could not be read.');
    $('titleText').value=job.title || ''; copyState();
    status(job.title ? (job.uncertain ? 'Please check the title. Some lettering or its placement was unclear.' : 'Ready to copy. You can make any corrections here.') :
      job.uncertain ? 'The title was unclear. Try a sharper image or crop around the headline.' : 'No distinct title found. Try a slide with a title headline.');
    $('retry').hidden=!file;
  } catch(error) {
    if (version !== generation) return;
    busy(false); status(error.message,true); $('retry').hidden=false; $('cancel').hidden=!jobId;
    if (error.status===404) {jobId='';remember('');}
  }
}
async function start() {
  if (!file || pending) return;
  if (!user()) { status('Sign in to read this slide.');openLogin();return; }
  invalidate(); const version=generation; busy(true); resetCopy(); $('titleText').value='';copyState();status('Uploading your slide…');
  try {
    uploadId ||= crypto.randomUUID();
    const job=await api.submit(file,{requestId:uploadId,signal:controller.signal});
    if (version!==generation) return;
    jobId=job.id;remember(jobId); await poll(jobId,version);
  } catch(error) { if(version===generation) {busy(false);status(error.message,true);$('retry').hidden=false;} }
}
async function accept(files) {
  if (pending || jobId) {status('Finish or cancel the current slide before choosing another.',true);return;}
  if (files.length!==1) {status('Choose one slide at a time.',true);return;}
  const chosen=files[0];
  if (!/^(image\/(jpeg|png|webp|gif))$/.test(chosen.type) || !chosen.size || chosen.size>10*1024*1024) {
    status('Choose a JPG, PNG, WebP or still GIF smaller than 10 MB.',true);return;
  }
  invalidate();clearPreview();file=chosen;uploadId=crypto.randomUUID();$('titleText').value='';copyState();
  previewURL=URL.createObjectURL(file);$('preview').src=previewURL;$('preview').hidden=false;$('empty').hidden=true;$('replace').hidden=false;$('filename').textContent=file.name;
  await start();
}
$('choose').onclick=()=>{if(pending||jobId){status('Finish or cancel this slide first.');return;}$('file').click();};
$('file').onchange=()=>{void accept([...$('file').files]);$('file').value='';};
$('titleText').oninput=()=>{resetCopy();copyState();};
$('copy').onclick=async()=>{
  const text=$('titleText').value.trim();if(!text)return;
  try { await navigator.clipboard.writeText(text);$('copyIcon').textContent='✓';$('copyLabel').textContent='Copied';clearTimeout(copyTimer);copyTimer=setTimeout(resetCopy,2000); }
  catch { $('titleText').focus();$('titleText').select();status('Text selected. Use Copy from your device’s menu.'); }
};
$('retry').onclick=()=>{
  if(jobId){invalidate();busy(true);void poll(jobId,generation);}
  else if(file){void start();}
  else status('Choose a slide to try again.');
};
$('cancel').onclick=async()=>{
  const id=jobId, version=generation;
  if(!id){status('The upload is still finishing. You can cancel once it is accepted.');return;}
  $('cancel').disabled=true;
  try { await api.remove(id);if(version!==generation)return;invalidate();jobId='';uploadId='';remember('');busy(false);status('Cancelled. Choose another slide or try again.');$('retry').hidden=!file; }
  catch(error){if(version===generation)status(error.message,true);}
  finally{$('cancel').disabled=false;}
};
let dragDepth=0;
window.addEventListener('dragenter',event=>{if(event.dataTransfer?.types.includes('Files')){event.preventDefault();dragDepth++;document.body.classList.add('dragging');}});
window.addEventListener('dragover',event=>{if(event.dataTransfer?.types.includes('Files')){event.preventDefault();event.dataTransfer.dropEffect='copy';}});
window.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;document.body.classList.remove('dragging');}});
window.addEventListener('drop',event=>{event.preventDefault();dragDepth=0;document.body.classList.remove('dragging');if(event.dataTransfer?.files.length)void accept([...event.dataTransfer.files]);});
window.addEventListener('paste',event=>{if(event.clipboardData?.files.length){event.preventDefault();void accept([...event.clipboardData.files]);}});
$('account').onclick=async()=>{if(!user()){openLogin();return;}try{await signOut();}catch(error){status(authError(error),true);}};
$('closeLogin').onclick=()=>$('login').close();
$('google').onclick=async()=>{
  try {
    if(!accountGoogleLogin.ready()){await accountGoogleLogin.prepare();$('loginStatus').textContent='Google is ready. Press Continue with Google again.';return;}
    const request=accountGoogleLogin.request();$('google').disabled=true;
    const accessToken=await request;
    const sdk=window.VisionFirebaseSDK.auth;
    await sdk.signInWithCredential(auth(),sdk.GoogleAuthProvider.credential(null,accessToken));
  }catch(error){$('loginStatus').textContent=authError(error);}finally{$('google').disabled=false;}
};
$('loginForm').onsubmit=async event=>{event.preventDefault();$('emailLogin').disabled=true;try{await window.VisionFirebaseSDK.auth.signInWithEmailAndPassword(auth(),$('email').value.trim(),$('password').value);$('password').value='';}catch(error){$('loginStatus').textContent=authError(error);}finally{$('emailLogin').disabled=false;}};
let standbyTimer;
async function refreshStandby() {
  clearTimeout(standbyTimer);
  if (!user()) return;
  const epoch=accountEpoch();
  try {
    const info=await api.standby();
    if(epoch!==accountEpoch() || !user())return;
    const memory=Number.isFinite(info.residentBytes) && info.loaded ? ' · '+(info.residentBytes/1e9).toFixed(2)+' GB RAM' : '';
    const ready=info.loaded && info.phase==='Ready' ? (info.scheduled ? 'Model ready' : 'Model ready (on demand)') : info.phase;
    $('standbyStatus').textContent=ready+memory+' · Warm standby Sundays & Wednesdays';
  } catch(error) {
    if(epoch!==accountEpoch() || !user())return;
    $('standbyStatus').textContent=error.status===404 ? 'Daily title worker needs the latest server update.' : 'Title worker status unavailable. The server may be offline.';
  }
  standbyTimer=setTimeout(refreshStandby,10000);
}
let priorUid=null;
void initAuth(identity=>{
  clearTimeout(standbyTimer);
  $('standbyStatus').textContent='Available every day · warm standby Sundays & Wednesdays';
  const switched=priorUid && priorUid!==identity?.uid;priorUid=identity?.uid||null;
  invalidate();jobId='';busy(false);$('titleText').value='';copyState();
  if(switched){file=null;clearPreview();}
  $('account').disabled=false;$('account').textContent=identity?'Sign out':'Sign in';
  if(identity){void refreshStandby();$('login').close();jobId=remembered();if(jobId){busy(true);void poll(jobId,generation);}else if(file){void start();}else status('Ready for your slide.');}
  else status('Choose a slide, then sign in with your Vision account.');
}).catch(error=>{status(authError(error),true);$('account').textContent='Refresh to connect';});
window.addEventListener('pagehide',()=>{clearTimeout(standbyTimer);clearTimeout(timer);controller?.abort();});
window.addEventListener('pageshow',event=>{if(event.persisted&&user())void refreshStandby();if(event.persisted&&jobId&&user()){invalidate();void poll(jobId,generation);}});
