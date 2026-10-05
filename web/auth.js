// Firebase handles Google and email/password identity. Project data and account keys stay on FUPCJ Server.
const VISION_FIREBASE={apiKey:'AIzaSyDh1AHhi41cAXcSFnvFkfeZWmxc8gI0zSg',authDomain:'visionboard-api.firebaseapp.com',projectId:'visionboard-api',appId:'1:150865729216:web:554423d0c7602d3a47bf25'};
const ACCOUNT_PC='https://desktop-vjt2br2.tail385c9d.ts.net',ACCOUNT_MODE='vision-account-mode-v1';
let accountSDK=null,accountFirebase=null,accountLoadPromise=null,accountAuthEpoch=0,accountTransition=Promise.resolve(),accountLastUID=null,accountBusy=false,accountError='',accountKeyTimer=0,accountKeyEpoch=0,accountKeyWrites=Promise.resolve(),accountGateLocked=true;
function accountSignedIn(){return !!(!accountGateLocked&&accountFirebase?.currentUser?.uid&&cloudAuth?.kind==='firebase-google'&&cloudAuth.uid===accountFirebase.currentUser.uid);}
function accountUpdateGate(){
 const gate=$('accountGate');if(!gate)return;const locked=!accountCanUseApp();
 document.documentElement.classList.toggle('account-locked',locked);gate.hidden=!locked;
 for(const el of document.querySelectorAll('body > :not(script):not(#accountGate)')){
  if(locked){if(!el.hasAttribute('data-account-inert'))el.setAttribute('data-account-inert',el.inert?'true':'false');el.inert=true;}
  else if(el.hasAttribute('data-account-inert')){el.inert=el.getAttribute('data-account-inert')==='true';el.removeAttribute('data-account-inert');}
 }
 if(locked)for(const dialog of document.querySelectorAll('dialog[open]:not(#whyVisionDialog)'))dialog.close();
 $('accountGateStatus').textContent=accountError||(trialStarting?'Starting your five-minute trial…':accountBusy?'Signing in…':'Sign in to save your work, or explore without an account.');
 for(const id of ['accountGateSignIn','accountGateEmailSignIn','accountGateEmailCreate','accountGatePasswordReset']){const el=$(id);if(el)el.disabled=accountBusy||trialStarting;}
 trialPaint();$('accountGateSignIn').hidden=location.protocol==='file:';$('accountGateEmailArea').hidden=location.protocol==='file:';
 $('accountGateOnline').hidden=location.protocol!=='file:';
 $('accountGateDetail').textContent=location.protocol==='file:'?'Open the online Vision page to sign in. Your projects and files are stored on FUPCJ Server.':'Your projects, files, and saved settings are stored on FUPCJ Server. Sign in with Google or email/password.';
 if(locked&&!gate.contains(document.activeElement)){gate.tabIndex=-1;const target=location.protocol==='file:'?$('accountGateOnline'):accountBusy?gate:$('accountGateSignIn');target.focus({preventScroll:true});}
}
function accountUsesGoogle(){try{return localStorage.getItem(ACCOUNT_MODE)==='google';}catch{return cloudAuth?.kind==='firebase-google';}}
function accountMode(value){try{if(value)localStorage.setItem(ACCOUNT_MODE,value);else localStorage.removeItem(ACCOUNT_MODE);}catch{}}
function accountErrorText(error){const code=error?.code||'';return ({'auth/unauthorized-domain':'Add jrdn-r.github.io to Firebase Authentication → Settings → Authorized domains.','auth/operation-not-allowed':'Enable the requested sign-in provider in Firebase Authentication → Sign-in method.','auth/popup-blocked':'Allow the Google sign-in popup for this site, then press Sign in again.','auth/popup-closed-by-user':'Google sign-in was closed. Press Sign in when ready.','auth/cancelled-popup-request':'A sign-in window is already open.','auth/network-request-failed':'Sign-in is unavailable on this connection. Check your internet connection and try again.','auth/invalid-credential':'Email or password is incorrect.','auth/wrong-password':'Email or password is incorrect.','auth/user-not-found':'Email or password is incorrect.','auth/invalid-email':'Enter a valid email address.','auth/email-already-in-use':'An account already uses that email. Sign in instead, or use Google if that is your existing Vision account.','auth/weak-password':'Use a password with at least 6 characters.','auth/too-many-requests':'Too many sign-in attempts. Wait a little and try again.'})[code]||error?.message||'Sign-in could not finish.';}
function accountPaint(){
 const user=cloudAuth?.kind==='firebase-google'?cloudAuth:null;
 $('accountButton').textContent=user?'Account':'Sign in';$('accountButton').title=user?'Signed in as '+user.email:'Sign in';
 $('accountIdentity').textContent=user?(user.email||'Signed in'):'Sign in on your phone or computer to open the same projects.';
 $('accountGoogleSignIn').hidden=!!user;$('accountGoogleSignIn').disabled=accountBusy;
 $('accountGoogleSignOut').hidden=!user;$('accountGoogleSignOut').disabled=accountBusy;
 $('accountOpenProjects').hidden=!user;$('accountStatus').textContent=accountError;
 $('accountWebLink').hidden=location.protocol!=='file:';$('accountGoogleSignIn').hidden=!!user||location.protocol==='file:';
 if($('accountEmailArea'))$('accountEmailArea').hidden=!!user||location.protocol==='file:';
 for(const id of ['accountEmailSignIn','accountEmailCreate','accountPasswordReset']){const el=$(id);if(el)el.disabled=accountBusy||trialStarting;}
 $('consoleRememberKey').closest('label').hidden=!!user||accountUsesGoogle();
 $('consoleAccountKey').hidden=!user;
 accountUpdateGate();
 renderAccountProjectList();
}
function openAccountDialog(message=''){accountError=message;accountPaint();if(accountCanUseApp()&&!$('accountDialog').open)$('accountDialog').showModal();}
function accountClearRunKey(){
 clearTimeout(accountKeyTimer);accountKeyEpoch++;$('consoleKey').value='';$('consoleKey').type='password';$('consoleRememberKey').checked=false;
 if(typeof setKeyVisibility==='function')setKeyVisibility($('consoleKey'),false);
 try{sessionStorage.removeItem(CONSOLE_KEY_KEY);}catch{}
 $('accountKeyStatus').textContent='';
}
async function accountSetUser(user){
 const uid=user?.uid||'';
 if(accountLastUID===uid)return;
 if(user&&(trialSession||cloudAuth?.kind==='trial'))await trialFinish({message:''});
 accountLastUID=uid;accountAuthEpoch++;accountGateLocked=true;accountUpdateGate();
 if(typeof cancelGoogleSources==='function')cancelGoogleSources();
 accountClearRunKey();
 if(user){accountMode('google');cloudConfig={kind:'private-pc',backendUrl:ACCOUNT_PC,publicAccess:true};cloudAuth={kind:'firebase-google',uid,email:user.email||'',provider:user.providerData?.some(item=>item.providerId==='google.com')?'google.com':'password',backendUrl:ACCOUNT_PC,remember:true};try{localStorage.setItem('vision-cloud-config',JSON.stringify(cloudConfig));}catch{}}
 else cloudAuth=null;
 projectHealthCache=null;
 // Change the local recovery scope before any account requests resume.
 await projectSwitchAccountScope(user?uid:'signed-out');
 accountGateLocked=false;
 accountPaint();
}
async function accountLoad(){
 if(location.protocol==='file:')return null;
 if(accountLoadPromise)return accountLoadPromise;
 accountLoadPromise=(async()=>{
  const [appSDK,authSDK]=await Promise.all([import('https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js'),import('https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js')]);
  accountSDK=authSDK;accountFirebase=authSDK.getAuth(appSDK.initializeApp(VISION_FIREBASE,'vision-account-login'));
  authSDK.useDeviceLanguage(accountFirebase);
  await accountFirebase.authStateReady();
  await accountSetUser(accountFirebase.currentUser);
  authSDK.onAuthStateChanged(accountFirebase,user=>{
   if(accountLastUID===(user?.uid||''))return;
   accountGateLocked=true;accountUpdateGate();accountTransition=accountTransition.catch(()=>{}).then(()=>accountSetUser(user));
   void accountTransition.then(()=>{if(user){void projectRefreshAccountList();void accountRestoreRunKey();if(state.projectCloud)void projectReconcile();}}).catch(error=>{accountError=accountErrorText(error);accountPaint();});
  });
  return accountFirebase;
 })().catch(error=>{accountLoadPromise=null;throw error;});
 return accountLoadPromise;
}
async function accountIdToken(){
 const user=accountFirebase?.currentUser,uid=cloudAuth?.uid,epoch=accountAuthEpoch;
 if(!user||!uid||user.uid!==uid)throw new Error('Sign in again to reconnect.');
 const token=await user.getIdToken();
 if(epoch!==accountAuthEpoch||cloudAuth?.uid!==uid)throw new Error('The signed-in account changed. Try again.');
 return token;
}
async function accountGoogleSignIn(){
 if(location.protocol==='file:'){openAccountDialog('Open Vision online to sign in with Google.');return;}
 if(accountBusy||trialStarting)return;accountBusy=true;accountError='';accountPaint();
 try{const auth=await accountLoad(),provider=new accountSDK.GoogleAuthProvider();provider.setCustomParameters({prompt:'select_account'});
  await accountSDK.setPersistence(auth,accountSDK.browserLocalPersistence);
  const result=await accountSDK.signInWithPopup(auth,provider);
  await accountTransition;await accountSetUser(result.user);
  $('accountDialog').close();openProjectsMenu();void accountRestoreRunKey();
 }catch(error){accountError=accountErrorText(error);}finally{accountBusy=false;accountPaint();}
}
function accountEmailFields(prefix){
 const email=$(prefix+'Email')?.value.trim()||'',password=$(prefix+'Password')?.value||'';
 if(!email)throw new Error('Enter your email.');
 return {email,password};
}
async function accountEmailAction(action,prefix){
 if(location.protocol==='file:'){openAccountDialog('Open Vision online to sign in with email/password.');return;}
 if(accountBusy||trialStarting)return;accountBusy=true;accountError='';accountPaint();
 try{
  const auth=await accountLoad(),{email,password}=accountEmailFields(prefix);
  await accountSDK.setPersistence(auth,accountSDK.browserLocalPersistence);
  if(action==='reset'){await accountSDK.sendPasswordResetEmail(auth,email);accountError='Password reset email sent to '+email+'.';return;}
  if(password.length<6)throw new Error('Enter a password with at least 6 characters.');
  const result=action==='create'?await accountSDK.createUserWithEmailAndPassword(auth,email,password):await accountSDK.signInWithEmailAndPassword(auth,email,password);
  await accountTransition;await accountSetUser(result.user);
  $('accountDialog').close();openProjectsMenu();void accountRestoreRunKey();
 }catch(error){accountError=accountErrorText(error);}finally{accountBusy=false;accountPaint();}
}
async function accountGoogleSignOut(){
 if(accountBusy)return;accountBusy=true;accountGateLocked=true;accountPaint();
 try{accountClearRunKey();await projectBackup();await accountSDK.signOut(accountFirebase);await accountTransition;await accountSetUser(null);accountError='Signed out. Your projects remain on FUPCJ Server and in this account’s device recovery.';}
 catch(error){accountError=accountErrorText(error);}finally{accountBusy=false;accountGateLocked=false;accountPaint();}
}
async function accountRestoreRunKey(){
 const uid=projectAccountUID(),epoch=++accountKeyEpoch;if(!uid)return;
 $('accountKeyStatus').textContent='Loading your saved API key…';
 try{const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);let value;
  try{value=await projectResponse(await cloudFetch('/account/openai-key',{signal:controller.signal}));}finally{clearTimeout(timer);}
  if(uid!==projectAccountUID()||epoch!==accountKeyEpoch)return;
  $('consoleKey').value=typeof value.apiKey==='string'?value.apiKey:'';
  $('accountKeyStatus').textContent=value.saved?'Saved to your account on FUPCJ Server':'Enter your API key once. It saves to your account automatically.';
 }catch(error){if(uid!==projectAccountUID()||epoch!==accountKeyEpoch)return;$('accountKeyStatus').textContent=error.code==='VISION_PC_UPDATE_REQUIRED'||error.status===404?'Run EnableGoogleSignIn on FUPCJ Server to load and save account API keys.':'Could not load the saved key. Your saved key has not been changed.';}
}
function accountRunKeyChanged(){
 if(!projectAccountUID())return;clearTimeout(accountKeyTimer);const epoch=++accountKeyEpoch,uid=projectAccountUID(),apiKey=$('consoleKey').value.trim();
 if(!apiKey){$('accountKeyStatus').textContent='The field is empty. Use Remove saved key to delete your account copy.';return;}
 $('accountKeyStatus').textContent='Saving key to your account…';
 accountKeyTimer=setTimeout(()=>void accountSaveRunKey(uid,epoch,apiKey),800);
}
function accountSaveRunKey(uid=projectAccountUID(),epoch=accountKeyEpoch,apiKey=$('consoleKey').value.trim()){
 accountKeyWrites=accountKeyWrites.catch(()=>{}).then(async()=>{
 if(!uid||uid!==projectAccountUID()||epoch!==accountKeyEpoch||!apiKey)return;
 try{await projectResponse(await cloudFetch('/account/openai-key',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({apiKey})}));if(uid===projectAccountUID()&&epoch===accountKeyEpoch)$('accountKeyStatus').textContent='Saved to your account on FUPCJ Server';}
 catch(error){if(uid===projectAccountUID()&&epoch===accountKeyEpoch)$('accountKeyStatus').textContent='Could not save the key. You can still use it in this tab; press Save key to retry.';}
 });return accountKeyWrites;
}
async function accountRemoveRunKey(){
 const uid=projectAccountUID();if(!uid)return;clearTimeout(accountKeyTimer);const epoch=++accountKeyEpoch;
 accountKeyWrites=accountKeyWrites.catch(()=>{}).then(async()=>{
 if(uid!==projectAccountUID()||epoch!==accountKeyEpoch)return;
 try{await projectResponse(await cloudFetch('/account/openai-key',{method:'DELETE'}));if(uid!==projectAccountUID()||epoch!==accountKeyEpoch)return;$('consoleKey').value='';$('accountKeyStatus').textContent='Saved API key removed.';}
 catch(error){if(uid===projectAccountUID())$('accountKeyStatus').textContent='Could not remove the saved key. Please retry when FUPCJ Server is online.';}
 });return accountKeyWrites;
}
const accountDialog=document.createElement('dialog');accountDialog.id='accountDialog';accountDialog.innerHTML=`<div class="row spread"><h2>Your account</h2><button id="closeAccount" class="dialog-close" type="button" aria-label="Close account">×</button></div><p id="accountIdentity" class="intro"></p><p class="mini-note">Firebase handles sign-in. FUPCJ Server stores projects, files, and background work. Keep it awake and online for saving and processing.</p><a id="accountWebLink" class="account-web-link" href="https://jrdn-r.github.io/vision/" target="_blank" rel="noopener noreferrer" hidden>Open Vision online to sign in ↗</a><p id="accountStatus" class="mini-note" role="status"></p><div class="dialog-actions"><button id="accountGoogleSignIn" class="primary" type="button">Continue with Google</button><div id="accountEmailArea" class="account-email-area"><div class="account-login-divider"><span>or</span></div><form id="accountEmailForm" class="account-email-form"><label>Email<input id="accountEmail" type="email" autocomplete="email" inputmode="email" required></label><label>Password<input id="accountPassword" type="password" autocomplete="current-password" minlength="6" required></label><button id="accountEmailSignIn" class="primary account-email-submit" type="submit">Sign in with email</button><div class="account-email-links"><button id="accountEmailCreate" class="account-email-link" type="button">Create account</button><button id="accountPasswordReset" class="account-email-link" type="button">Forgot password?</button></div><p class="account-email-note">Use a Vision password here, not your Google password.</p></form></div><button id="accountOpenProjects" class="primary" type="button" hidden>My projects</button><button id="accountGoogleSignOut" class="ghost" type="button" hidden>Sign out</button></div>`;
document.body.appendChild(accountDialog);
$('accountButton').onclick=()=>openAccountDialog();$('closeAccount').onclick=()=>accountDialog.close();$('accountGoogleSignIn').onclick=()=>void accountGoogleSignIn();$('accountGoogleSignOut').onclick=()=>void accountGoogleSignOut();$('accountOpenProjects').onclick=()=>{accountDialog.close();openProjectsMenu();};
$('accountEmailForm').onsubmit=event=>{event.preventDefault();void accountEmailAction('signin','account');};$('accountEmailCreate').onclick=()=>void accountEmailAction('create','account');$('accountPasswordReset').onclick=()=>void accountEmailAction('reset','account');
$('accountGateEmailForm').onsubmit=event=>{event.preventDefault();void accountEmailAction('signin','accountGate');};$('accountGateEmailCreate').onclick=()=>void accountEmailAction('create','accountGate');$('accountGatePasswordReset').onclick=()=>void accountEmailAction('reset','accountGate');
$('accountSaveKey').onclick=()=>{clearTimeout(accountKeyTimer);void accountSaveRunKey();};$('accountRemoveKey').onclick=()=>void accountRemoveRunKey();$('consoleKey').addEventListener('input',accountRunKeyChanged);
// Existing setup-token connections are administrative only; the app requires a signed-in account.
const originalConnectionDialog=openCloudSettings;
openCloudSettings=function(message=''){
 if(!accountSignedIn()){openAccountDialog('Sign in to use Vision.');return;}
 originalConnectionDialog(message||'Connected as '+(cloudAuth.email||'your account')+'. Your projects and processing use FUPCJ Server.');
 for(const id of ['cloudImportFile','cloudConfigFields','cloudFirebaseFields','cloudPCFields','cloudSignIn','cloudSignOut'])$(id).hidden=true;
 $('cloudRemember').closest('label').hidden=true;
};
$('cloudSignIn').onclick=()=>openAccountDialog('Vision requires account sign-in.');
$('cloudSignOut').onclick=()=>void accountGoogleSignOut();
$('accountGateSignIn').onclick=()=>void accountGoogleSignIn();
function accountGateEvent(event){
 if(accountCanUseApp())return;
 const inGate=!!event.target?.nodeType&&$('accountGate').contains(event.target);
 if(inGate&&['click','pointerdown','pointerup','touchstart','touchend'].includes(event.type))return;
 if(inGate&&event.type==='keydown'&&!event.ctrlKey&&!event.metaKey){event.stopImmediatePropagation();return;}
 event.preventDefault();event.stopImmediatePropagation();
}
for(const type of ['keydown','paste','drop','dragover','click','pointerdown','pointerup','touchstart','touchend'])window.addEventListener(type,accountGateEvent,{capture:true,passive:false});
new MutationObserver(()=>{if(!accountCanUseApp())accountUpdateGate();}).observe(document.body,{childList:true});
accountUpdateGate();
// Restore Firebase identity before opening any application controls or project recovery.
const accountReady=Promise.resolve().then(async()=>{
 accountMode('google');cloudAuth=null;saveCloudSession();accountClearRunKey();await projectSwitchAccountScope('signed-out');
 if(location.protocol==='file:'){accountPaint();return;}
 let timer;
 try{await Promise.race([accountLoad(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Sign-in could not load. Check your connection, then press Sign in.')),12000);})]);}
 catch(error){accountError=accountErrorText(error);}finally{clearTimeout(timer);accountPaint();}
});
void accountReady.then(()=>{if(projectAccountUID()){void projectRefreshAccountList();void accountRestoreRunKey();}});
