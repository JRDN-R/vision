// A local document owns the workspace. Only identity, service calls and update checks are remote.
let localBridgeCancel=null;
function localGoogleConnect(mode='signin',email=''){
 if(location.protocol!=='file:')return Promise.reject(new Error('This connection is for a local Vision file.'));
 if(localBridgeCancel)localBridgeCancel();
 const stateToken=Array.from(crypto.getRandomValues(new Uint8Array(32)),x=>x.toString(16).padStart(2,'0')).join(''),origin='https://jrdn-r.github.io';
 const params=new URLSearchParams({state:stateToken,mode});if(email)params.set('email',email);
 const popup=window.open(origin+'/vision/local-signin.html#'+params,'_blank','popup,width=560,height=760');
 if(!popup)return Promise.reject(new Error('Allow popups for local Vision, then press Continue with Google again.'));
 return new Promise((resolve,reject)=>{
  let channel=null,done=false;
  const cleanup=()=>{window.removeEventListener('message',ready);clearTimeout(deadline);clearInterval(closed);channel?.port1.close();if(localBridgeCancel===cancel)localBridgeCancel=null;};
  const finish=(error,result)=>{if(done)return;done=true;cleanup();try{popup.close();}catch{}if(error)reject(error);else resolve(result);};
  const cancel=()=>finish(new Error('Google connection cancelled.'));localBridgeCancel=cancel;
  const ready=event=>{
   if(done||channel||event.origin!==origin||event.source!==popup||event.data?.type!=='vision-local-ready'||event.data.state!==stateToken)return;
   channel=new MessageChannel();channel.port1.onmessage=event=>{
    const data=event.data;if(data?.type!=='vision-local-credential'||data.state!==stateToken)return;
    if(typeof data.idToken!=='string'||typeof data.accessToken!=='string'||data.idToken.length>20000||data.accessToken.length>20000)return finish(new Error('Invalid Google sign-in response.'));
    if(email&&String(data.email).toLowerCase()!==email.toLowerCase())return finish(new Error('Choose the same Google account used in local Vision.'));
    finish(null,data);
   };channel.port1.start();popup.postMessage({type:'vision-local-connect',state:stateToken},origin,[channel.port2]);
  };
  window.addEventListener('message',ready);
  const deadline=setTimeout(()=>finish(new Error('Google sign-in expired. Press Continue with Google to retry.')),300000);
  const closed=setInterval(()=>{if(popup.closed)finish(new Error('Google sign-in closed before connecting.'));},500);
 });
}
const LOCAL_UPDATE_ROOT='https://raw.githubusercontent.com/JRDN-R/vision/main/';
let localUpdateManifest=null,localUpdateBusy=false,localUpdateBlob=null,localUpdateHandle=null,localUpdateLastCheck=0;
function localUpdateStatus(text){if($('localUpdateStatus'))$('localUpdateStatus').textContent=text;}
function localVersion(){return document.querySelector('meta[name="vision-version"]')?.content||'0.0.0.0';}
function localVersionNewer(a,b){const aa=a.split('.').map(Number),bb=b.split('.').map(Number);for(let i=0;i<4;i++){if(aa[i]!==bb[i])return aa[i]>bb[i];}return false;}
async function localUpdateFetch(path){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),90000);
 try{const response=await fetch(LOCAL_UPDATE_ROOT+path,{cache:'no-store',credentials:'omit',signal:controller.signal});if(!response.ok)throw new Error('Update server returned '+response.status+'.');return await response.blob();}finally{clearTimeout(timer);}
}
async function localCheckUpdates(manual=false){
 if(localUpdateBusy||(!manual&&Date.now()-localUpdateLastCheck<6*60*60*1000))return;
 localUpdateBusy=true;localUpdateLastCheck=Date.now();localUpdateStatus('Checking for a newer local app…');
 try{
  const manifest=JSON.parse(await (await localUpdateFetch('portable-manifest.json')).text());
  if(!/^\d+\.\d+\.\d+\.\d+$/.test(manifest.version)||!/^[a-f0-9]{64}$/.test(manifest.sha256)||!Number.isSafeInteger(manifest.size)||manifest.size<100000||manifest.size>150*1024*1024)throw new Error('The update manifest is invalid. Your local file was not changed.');
  if(!localVersionNewer(manifest.version,localVersion())){localUpdateStatus('This local copy is current ('+localVersion()+').');return;}
  localUpdateManifest=manifest;localUpdateBlob=null;$('localUpdateButton').textContent='Update available';$('localUpdateDownload').disabled=false;localUpdateStatus('Version '+manifest.version+' is available. Download it or update the HTML file you authorized. Your current workspace stays open.');
  if(localUpdateHandle&&$('localUpdateAuto').checked&&await localUpdateHandle.queryPermission({mode:'readwrite'})==='granted'&&!consoleIsRunning()&&!busy&&!ioBusy)await localInstallUpdate();
 }catch(error){localUpdateLastCheck=0;localUpdateStatus((navigator.onLine===false?'Offline. The bundled app remains available.':'Update check unavailable: '+error.message)+' Existing app and projects were not changed.');}
 finally{localUpdateBusy=false;}
}
async function localVerifiedUpdate(){
 if(localUpdateBlob)return localUpdateBlob;if(!localUpdateManifest)throw new Error('Check for updates first.');
 localUpdateStatus('Downloading and verifying the complete app…');const blob=await localUpdateFetch('Vision.html');
 const bytes=await blob.arrayBuffer(),digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
 if(bytes.byteLength!==localUpdateManifest.size||digest!==localUpdateManifest.sha256)throw new Error('The release changed during download or failed verification. Check for updates again; no file was replaced.');
 localUpdateBlob=new Blob([bytes],{type:'text/html;charset=utf-8'});return localUpdateBlob;
}
async function localAuthorizeUpdate(){
 if(!window.showOpenFilePicker){localUpdateStatus('This browser cannot replace a local HTML file. Use Download update, then replace your saved copy.');return;}
 try{
  const [handle]=await window.showOpenFilePicker({multiple:false,types:[{description:'Your local Vision HTML',accept:{'text/html':['.html']}}]});
  const file=await handle.getFile(),text=await file.text(),id=document.querySelector('meta[name="vision-source-id"]')?.content;
  if(!id||!text.includes('name="vision-source-id" content="'+id+'"'))throw new Error('Choose the saved HTML for this version of Vision. No file was changed.');
  if(await handle.requestPermission({mode:'readwrite'})!=='granted')throw new Error('File update permission was not granted.');
  localUpdateHandle=handle;$('localUpdateAuto').disabled=false;localUpdateStatus('Authorized '+file.name+'. Enable automatic file updates to apply verified releases while this page is open. Reopen the HTML after an update to run the new version.');
 }catch(error){if(error.name!=='AbortError')localUpdateStatus(error.message);}
}
async function localInstallUpdate(){
 if(!localUpdateHandle)return;const blob=await localVerifiedUpdate();
 if(await localUpdateHandle.queryPermission({mode:'readwrite'})!=='granted')throw new Error('Choose your HTML again to renew update permission.');
 const file=await localUpdateHandle.getFile(),text=await file.text();
 if(!text.includes('name="vision-version"')||!text.includes('name="vision-source-id"'))throw new Error('The selected file is no longer a Vision app. It was not overwritten.');
 const writable=await localUpdateHandle.createWritable();try{await writable.write(blob);await writable.close();}catch(error){await writable.abort().catch(()=>{});throw error;}
 $('localUpdateAuto').checked=false;localUpdateStatus('Updated the authorized HTML to '+localUpdateManifest.version+'. Your current workspace was not reloaded. Save your project and reopen that file to use the new version.');
}
function localInstallControls(){
 const description=$('downloadAppDialog').querySelector('.intro');description.textContent='Download one self-contained HTML file with the interface, artwork, media runtimes, and Firebase login code included. Sign in from the local file to use the same remote PC and account projects. Internet is needed for authentication and cloud services, not to load the interface.';
 if(location.protocol!=='file:')return;
 const button=document.createElement('button');button.id='localUpdateButton';button.className='ghost';button.type='button';button.textContent='Local updates';$('downloadAppBtn').before(button);
 const dialog=document.createElement('dialog');dialog.id='localUpdateDialog';dialog.innerHTML='<div class="row spread"><h2>Local app updates</h2><button type="button" id="localUpdateClose">Close</button></div><p id="localUpdateStatus" role="status">Current version: '+localVersion()+'.</p><p class="mini-note">Checks automatically while this page is open. Downloading never navigates this page or includes your credentials. Browsers require explicit file permission before an HTML file can replace itself.</p><div class="dialog-actions"><button type="button" id="localUpdateCheck">Check now</button><button type="button" id="localUpdateDownload" disabled>Download update</button><button type="button" id="localUpdateAuthorize">Choose this HTML for updates</button></div><label><input id="localUpdateAuto" type="checkbox" disabled> Automatically update the authorized file during this session</label>';
 document.body.appendChild(dialog);button.onclick=()=>dialog.showModal();$('localUpdateClose').onclick=()=>dialog.close();$('localUpdateCheck').onclick=()=>void localCheckUpdates(true);$('localUpdateAuthorize').onclick=()=>void localAuthorizeUpdate();
 $('localUpdateAuto').onchange=()=>{if($('localUpdateAuto').checked&&localUpdateManifest)void localInstallUpdate().catch(e=>localUpdateStatus(e.message));};
 $('localUpdateDownload').onclick=async()=>{try{download(await localVerifiedUpdate(),'Vision-Local.html');localUpdateStatus('Update downloaded. Replace your saved HTML and reopen it after saving your project.');}catch(error){localUpdateStatus(error.message);}};
 window.addEventListener('online',()=>void localCheckUpdates());setInterval(()=>void localCheckUpdates(),6*60*60*1000);void localCheckUpdates();
}
localInstallControls();
