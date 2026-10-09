// Selected Drive files and public YouTube search. OAuth tokens stay in memory.
const GOOGLE_SOURCE_CONFIG={apiKey:VISION_FIREBASE.apiKey,appId:VISION_FIREBASE.appId.split(':')[1]};
const DRIVE_SCOPE='https://www.googleapis.com/auth/drive.file';
const DRIVE_MAX_BYTES=100*1024*1024,DRIVE_MAX_FILES=20;
const DRIVE_EXPORTS={
 'application/vnd.google-apps.document':{mime:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',ext:'.docx'},
 'application/vnd.google-apps.spreadsheet':{mime:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',ext:'.xlsx'},
 'application/vnd.google-apps.presentation':{mime:'application/vnd.openxmlformats-officedocument.presentationml.presentation',ext:'.pptx'},
 'application/vnd.google-apps.drawing':{mime:'application/pdf',ext:'.pdf'}
};
let driveCredential=null,driveRuntime=null,drivePicker=null,drivePickerLoading=null,driveDestination=null;
let youtubeSearchRuntime=null,youtubeSearchQuery='',youtubeSearchNext='',youtubeSearchCache=new Map();
function googleAccountLinked(){return !!accountFirebase?.currentUser?.providerData?.some(item=>item.providerId==='google.com');}

function googleSourceError(data,service,status){
 const reasons=[...(data?.error?.errors||[]).map(e=>e.reason),...(data?.error?.details||[]).map(e=>e.reason)].join(' ');
 if(/accessNotConfigured|SERVICE_DISABLED|API_KEY_SERVICE_BLOCKED|API_KEY_HTTP_REFERRER_BLOCKED/.test(reasons))return service==='YouTube'?'YouTube search is not enabled for Vision yet. You can still paste a video link.':'Google Drive access is not enabled for Vision yet. You can still add downloaded files from your device.';
 if(/quota|rateLimit|RATE_LIMIT/i.test(reasons)||status===429)return service+' has reached its request limit. Please try later.';
 if(/exportSizeLimitExceeded/.test(reasons))return 'Google limits document exports to 10 MB. Download a smaller copy from Drive, then add it from your device.';
 if(/ACCESS_TOKEN_SCOPE_INSUFFICIENT|insufficientPermissions/.test(reasons))return 'Google Drive permission was not granted. Choose files again and approve access to selected files.';
 if(status===401)return 'Google access expired. Choose files from Drive again to reconnect.';
 if(status===403)return service==='Drive'?'Google did not allow this file to be downloaded. Check the file’s sharing/download permissions or reconnect Drive.':'YouTube search is unavailable for this Google project. You can still paste a video link.';
 if(status===404)return 'This Google file or video is no longer available.';
 return service+' could not complete the request. Please try again.';
}

async function googleSourceResponse(url,{signal,headers={},limit=1024*1024,timeout=20000,service='Google',json=true}={}){
 const controller=new AbortController(),abort=()=>controller.abort();
 if(signal?.aborted)throw new DOMException('Canceled','AbortError');
 signal?.addEventListener('abort',abort,{once:true});
 const timer=setTimeout(abort,timeout);
 try{
  const response=await fetch(url,{headers,signal:controller.signal,credentials:'omit',cache:'no-store'});
  const maximum=response.ok?limit:64*1024,declared=Number(response.headers.get('content-length'));
  if(declared>maximum){await response.body?.cancel();throw new Error(json?service+' returned too much data. Try a narrower search.':'The selected files exceed the 100 MB import limit. Choose fewer or smaller files.');}
  const reader=response.body?.getReader();if(!reader)throw new Error(service+' returned an unreadable response.');
  const chunks=[];let size=0;
  try{while(true){const{value,done}=await reader.read();if(done)break;size+=value.byteLength;if(size>maximum){await reader.cancel();throw new Error('This download exceeds the import size limit. Choose fewer or smaller files.');}chunks.push(value);}}
  finally{reader.releaseLock();}
  if(controller.signal.aborted)throw new DOMException('Canceled','AbortError');
  const blob=new Blob(chunks,{type:response.headers.get('content-type')||'application/octet-stream'});
  if(!response.ok){let body={};try{body=JSON.parse(await blob.text());}catch{}const error=new Error(googleSourceError(body,service,response.status));error.status=response.status;throw error;}
  return json?JSON.parse(await blob.text()):blob;
 }catch(error){
  if(controller.signal.aborted&&!signal?.aborted)throw new Error(service+' took too long to respond. Please try again.');
  throw error;
 }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}

function driveCurrent(run){return driveRuntime===run&&!run.controller.signal.aborted&&accountSignedIn()&&run.uid===accountFirebase.currentUser?.uid&&run.epoch===accountAuthEpoch&&boardImportCurrent(run.context)&&(!run.target||nodeById(run.target.id)===run.target);}
function assertDriveCurrent(run){if(!driveCurrent(run))throw new DOMException('The project or Google account changed. Import canceled.','AbortError');}
function disposeDrivePicker(){const picker=drivePicker;drivePicker=null;picker?.dispose();}
function cancelGoogleSourceImports(){
 const run=driveRuntime;driveRuntime=null;driveDestination=null;run?.controller.abort();
 disposeDrivePicker();
 $('driveDialog')?.close();
}
function cancelGoogleSources(){
 driveCredential=null;cancelGoogleSourceImports();cancelYouTubeSearch();youtubeSearchCache.clear();
 youtubeSearchQuery='';youtubeSearchNext='';$('youtubeResults')?.replaceChildren();
 if($('youtubeURL'))$('youtubeURL').value='';
 if($('youtubeMore'))$('youtubeMore').hidden=true;
}

async function driveAccessToken(run){
 assertDriveCurrent(run);
 if(!googleAccountLinked())throw new Error('Google Drive requires Google sign-in. Sign out, then sign in with Google to use Drive.');
 if(location.protocol!=='file:'&&driveCredential?.uid===run.uid&&driveCredential.epoch===run.epoch&&driveCredential.expiresAt>Date.now())return driveCredential.token;
 // Reauthenticate the current user: selecting a different Google account must
 // never switch the Vision account or import another person's Drive files.
 const user=accountFirebase.currentUser;
 if(location.protocol==='file:'){
  const credential=await localGoogleConnect('drive',user.email);assertDriveCurrent(run);
  await accountSDK.reauthenticateWithCredential(user,accountSDK.GoogleAuthProvider.credential(credential.idToken,credential.accessToken));assertDriveCurrent(run);
  run.localDriveDocs=credential.docs||[];return credential.accessToken;
 }
 const provider=new accountSDK.GoogleAuthProvider();
 provider.addScope(DRIVE_SCOPE);provider.setCustomParameters({login_hint:user.email||'',prompt:'consent'});
 const result=await accountSDK.reauthenticateWithPopup(user,provider,accountSDK.browserPopupRedirectResolver);
 assertDriveCurrent(run);
 if(result.user.uid!==run.uid)throw new Error('Choose the same Google account you used to sign in to Vision.');
 const credential=accountSDK.GoogleAuthProvider.credentialFromResult(result);
 if(!credential?.accessToken)throw new Error('Google did not grant Drive access. Choose files again to retry.');
 driveCredential={uid:run.uid,epoch:run.epoch,token:credential.accessToken,expiresAt:Date.now()+50*60*1000};
 return driveCredential.token;
}

function loadDrivePicker(){
 if(window.google?.picker)return Promise.resolve();if(drivePickerLoading)return drivePickerLoading;
 drivePickerLoading=new Promise((resolve,reject)=>{
  const fail=()=>reject(new Error('Google’s file picker could not load. Check your connection and try again.'));
  const load=()=>{if(!window.gapi?.load){fail();return;}gapi.load('picker',{callback:resolve,onerror:fail,timeout:15000,ontimeout:fail});};
  if(window.gapi?.load){load();return;}
  const script=document.createElement('script');script.src='https://apis.google.com/js/api.js';script.async=true;
  const timer=setTimeout(fail,15000);script.onload=()=>{clearTimeout(timer);load();};script.onerror=()=>{clearTimeout(timer);script.remove();fail();};document.head.appendChild(script);
 }).catch(error=>{drivePickerLoading=null;throw error;});
 return drivePickerLoading;
}

function chooseDriveDocuments(run,token){
 if(location.protocol==='file:')return Promise.resolve(run.localDriveDocs||[]);
 return new Promise((resolve,reject)=>{
  const abort=()=>{disposeDrivePicker();reject(new DOMException('Canceled','AbortError'));};
  run.controller.signal.addEventListener('abort',abort,{once:true});
  const finish=value=>{run.controller.signal.removeEventListener('abort',abort);disposeDrivePicker();resolve(value);};
  const view=new google.picker.DocsView(google.picker.ViewId.DOCS).setIncludeFolders(true).setSelectFolderEnabled(false);
  drivePicker=new google.picker.PickerBuilder().setTitle('Choose files for Vision').setAppId(GOOGLE_SOURCE_CONFIG.appId)
   .setDeveloperKey(GOOGLE_SOURCE_CONFIG.apiKey).setOAuthToken(token).setOrigin(location.origin)
   .addView(view).enableFeature(google.picker.Feature.MULTISELECT_ENABLED).setMaxItems(DRIVE_MAX_FILES)
   .setCallback(data=>{if(data.action===google.picker.Action.PICKED)finish(data.docs||[]);else if(data.action===google.picker.Action.CANCEL)finish([]);}).build();
  $('driveDialog').close();drivePicker.setVisible(true);
 });
}

async function downloadDriveFile(doc,token,run,remaining){
 assertDriveCurrent(run);
 const id=String(doc.id||'');if(!/^[A-Za-z0-9_-]{1,200}$/.test(id))throw new Error('Google returned an invalid file selection.');
 const headers={Authorization:'Bearer '+token};
 const resourceKey=String(doc.resourceKey||'');if(/^[A-Za-z0-9_-]{1,200}$/.test(resourceKey))headers['X-Goog-Drive-Resource-Keys']=id+'/'+resourceKey;
 const base='https://www.googleapis.com/drive/v3/files/'+encodeURIComponent(id);
 const fields='id,name,mimeType,size,capabilities(canDownload)';
 const meta=await googleSourceResponse(base+'?supportsAllDrives=true&fields='+encodeURIComponent(fields),{headers,signal:run.controller.signal,service:'Drive'});
 assertDriveCurrent(run);
 if(meta.capabilities?.canDownload===false)throw new Error('The owner has disabled downloads for '+(meta.name||'this file')+'.');
 if(Number(meta.size)>remaining)throw new Error('The selected files exceed the 100 MB import limit. Choose fewer or smaller files.');
 const format=DRIVE_EXPORTS[meta.mimeType];
 if(meta.mimeType?.startsWith('application/vnd.google-apps.')&&!format)throw new Error('Choose the original file instead of a folder or shortcut. This Google file type cannot be imported.');
 const url=format?base+'/export?mimeType='+encodeURIComponent(format.mime):base+'?alt=media&supportsAllDrives=true';
 const blob=await googleSourceResponse(url,{headers,signal:run.controller.signal,service:'Drive',limit:remaining,timeout:120000,json:false});
 assertDriveCurrent(run);
 if(!blob.size)throw new Error('The selected Drive file is empty.');
 const name=String(meta.name||'Drive file').replace(/[\u0000-\u001f\u007f]/g,'').slice(0,230);
 return new File([blob],name+(format&&!name.toLowerCase().endsWith(format.ext)?format.ext:''),{type:format?.mime||meta.mimeType||blob.type});
}

function openDriveDialog(targetId=''){
 if(!accountSignedIn()){openAccountDialog('Sign in to choose Drive files.');return;}
 if(!googleAccountLinked()){toast('Google Drive requires Google sign-in. Sign out, then sign in with Google to use Drive.');return;}
 if(busy||ioBusy||boardImportRunning||driveRuntime){toast('Finish the current import first.');return;}
 const target=targetId?nodeById(targetId):null,context=boardImportContext();
 if(!context)return;if(targetId&&!target){toast('Select a module to attach Drive files.');return;}
 driveDestination={context,target};
 boardAddDialog.close();$('driveIdentity').textContent=accountFirebase.currentUser.email||'Your signed-in Google account';
 $('driveStatus').textContent=target?'Choose files to attach to '+(target.title||'the selected module')+'.':'Choose files from your Google Drive. Each file is added as a module in this project.';
 $('driveChoose').disabled=false;$('driveCancel').disabled=false;$('driveProgress').hidden=true;$('driveDialog').showModal();
}

async function importDriveFiles(){
 if(driveRuntime||busy||ioBusy||boardImportRunning||!accountSignedIn())return;
 const context=driveDestination?.context||boardImportContext(),target=driveDestination?.target||null;if(!boardImportCurrent(context))return;
 if(target&&nodeById(target.id)!==target){closeDriveImport();toast('The destination module was removed. Choose another module.');return;}
 const run={context,target,uid:accountFirebase.currentUser.uid,epoch:accountAuthEpoch,controller:new AbortController(),provider:transcriptionProvider(),phase:'choose'};
 run.includeSoundEvents=soundEventsSelected(run.provider);driveRuntime=run;
 $('driveChoose').disabled=true;$('driveStatus').textContent='Connecting to your Google Drive…';
 try{
  const token=await driveAccessToken(run);assertDriveCurrent(run);
  await googleSourceResponse('https://www.googleapis.com/drive/v3/about?fields=user(displayName)',{headers:{Authorization:'Bearer '+token},signal:run.controller.signal,service:'Drive'});
  assertDriveCurrent(run);await loadDrivePicker();assertDriveCurrent(run);
  const documents=await chooseDriveDocuments(run,token);assertDriveCurrent(run);
  if(!documents.length){$('driveStatus').textContent='No files selected.';return;}
  if(documents.length>DRIVE_MAX_FILES)throw new Error('Choose up to '+DRIVE_MAX_FILES+' files at a time.');
  $('driveDialog').showModal();$('driveProgress').hidden=false;$('driveProgress').value=0;run.phase='download';
  const files=[];let size=0;
  for(let i=0;i<documents.length;i++){
   $('driveStatus').textContent='Downloading '+(i+1)+' of '+documents.length+': '+String(documents[i].name||'Drive file');
   const file=await downloadDriveFile(documents[i],token,run,DRIVE_MAX_BYTES-size);assertDriveCurrent(run);files.push(file);size+=file.size;$('driveProgress').value=(i+1)/documents.length;
  }
  if(busy||ioBusy||boardImportRunning)throw new Error('Another import is still running. Let it finish, then choose your Drive files again.');
  run.phase='import';$('driveCancel').disabled=true;$('driveStatus').textContent=target?'Attaching files to your module…':'Adding files to your board…';
  if(target)await attachFiles(target.id,files);
  else await importBoardFiles(files,undefined,{importContext:context,provider:run.provider,includeSoundEvents:run.includeSoundEvents});
  assertDriveCurrent(run);driveDestination=null;$('driveDialog').close();
 }catch(error){
  if(error.status===401||error.status===403)driveCredential=null;
  if(driveCurrent(run)&&error.name!=='AbortError'){
   const message=error.code==='auth/user-mismatch'?'Choose the same Google account you used to sign in to Vision.':error.code?accountErrorText(error):error.message;
   $('driveStatus').textContent=message;if(!$('driveDialog').open)$('driveDialog').showModal();
  }
 }finally{
  if(driveRuntime===run){driveRuntime=null;$('driveChoose').disabled=false;$('driveCancel').disabled=false;$('driveProgress').hidden=true;}
 }
}

function youtubeInputIsURL(value){try{return !!normalizeYouTubeURL(value);}catch{return false;}}
function youtubeLooksLikeURL(value){return /^(?:https?:\/\/|www\.|(?:m\.)?youtube\.com(?:\/|$)|youtu\.be(?:\/|$))/i.test(value.trim());}
function cancelYouTubeSearch(){youtubeSearchRuntime?.controller.abort();youtubeSearchRuntime=null;if($('youtubeSearch'))syncYouTubeSearchUI();}
function syncYouTubeSearchUI(){
 const value=$('youtubeURL').value.trim(),url=youtubeInputIsURL(value),running=!!youtubeSearchRuntime;
 $('youtubeSearch').disabled=running||!value||url;$('youtubeImport').disabled=youtubeImportRunning||!url;
 $('youtubeMore').disabled=running;$('youtubeSearch').textContent=running?'Searching…':'Search';
 const chosen=Array.from($('youtubeResults').children).find(row=>url&&value==='https://www.youtube.com/watch?v='+row.dataset.videoId);
 $('youtubeRunSummary').textContent=chosen?.dataset.title||(url?'Video ready to run':'Choose a video');
 $('youtubeRunSummary').title=$('youtubeRunSummary').textContent;
}
function setYouTubeControls(open){$('youtubeControlsPanel').hidden=!open;$('youtubeControls').setAttribute('aria-expanded',String(open));if(open)$('youtubeBody').scrollTop=0;}
function youtubePlainText(value){const node=document.createElement('textarea');node.innerHTML=String(value||'');return node.value;}
function youtubeDuration(value){const m=String(value||'').match(/^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$/);if(!m)return'';const seconds=Math.round(Number(m[1]||0)*86400+Number(m[2]||0)*3600+Number(m[3]||0)*60+Number(m[4]||0));return seconds>=3600?Math.floor(seconds/3600)+':'+String(Math.floor(seconds/60)%60).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0'):Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');}
function youtubeViewCount(value){const n=Number(value);return Number.isFinite(n)&&n>=0?new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1}).format(n)+' views':'';}
function youtubePublished(value){const date=new Date(value);return Number.isFinite(date.getTime())?date.toLocaleDateString(undefined,{year:'numeric',month:'short',day:'numeric'}):'';}
function youtubeApiURL(resource,params){return 'https://www.googleapis.com/youtube/v3/'+resource+'?'+new URLSearchParams({...params,key:GOOGLE_SOURCE_CONFIG.apiKey});}
async function fetchYouTubeResults(query,pageToken,signal){
 const cacheKey=query+'\0'+pageToken,cached=youtubeSearchCache.get(cacheKey);
 if(cached&&Date.now()-cached.time<5*60*1000)return cached.value;
 const data=await googleSourceResponse(youtubeApiURL('search',{part:'snippet',type:'video',maxResults:'10',q:query,...(pageToken?{pageToken}:{})}),{signal,service:'YouTube'});
 const items=(data.items||[]).filter(item=>/^[A-Za-z0-9_-]{11}$/.test(item.id?.videoId)).slice(0,10);
 let details=new Map();
 if(items.length){
  try{const extra=await googleSourceResponse(youtubeApiURL('videos',{part:'contentDetails,statistics',id:items.map(item=>item.id.videoId).join(',')}),{signal,service:'YouTube'});details=new Map((extra.items||[]).map(item=>[item.id,item]));}
  catch(error){if(signal.aborted)throw error;}
 }
 if(signal.aborted)throw new DOMException('Canceled','AbortError');
 const value={items:items.map(item=>{const extra=details.get(item.id.videoId),snippet=item.snippet||{};return{id:item.id.videoId,title:youtubePlainText(snippet.title),channel:youtubePlainText(snippet.channelTitle),description:youtubePlainText(snippet.description),published:youtubePublished(snippet.publishedAt),duration:snippet.liveBroadcastContent==='live'?'LIVE':snippet.liveBroadcastContent==='upcoming'?'UPCOMING':youtubeDuration(extra?.contentDetails?.duration),views:extra?.statistics?.viewCount===undefined?'':youtubeViewCount(extra.statistics.viewCount)};}),next:typeof data.nextPageToken==='string'?data.nextPageToken.slice(0,1000):''};
 if(youtubeSearchCache.size>=12)youtubeSearchCache.delete(youtubeSearchCache.keys().next().value);
 youtubeSearchCache.set(cacheKey,{time:Date.now(),value});return value;
}
function renderYouTubeResults(items,append=false){
 const list=$('youtubeResults');if(!append)list.replaceChildren();const existing=new Set(Array.from(list.children).map(el=>el.dataset.videoId));
 for(const item of items){
  if(existing.has(item.id))continue;const row=document.createElement('article');row.className='youtube-result';row.dataset.videoId=item.id;row.dataset.title=item.title;
  const select=document.createElement('button');select.type='button';select.className='youtube-result-select';select.setAttribute('aria-pressed','false');select.setAttribute('aria-label','Select '+item.title);
  const thumb=document.createElement('span');thumb.className='youtube-result-thumbnail';
  const image=document.createElement('img');image.src='https://i.ytimg.com/vi/'+item.id+'/mqdefault.jpg';image.alt='';image.loading='lazy';image.referrerPolicy='no-referrer';thumb.appendChild(image);
  if(item.duration){const duration=document.createElement('span');duration.className='youtube-result-duration';duration.textContent=item.duration;thumb.appendChild(duration);}
  const copy=document.createElement('span');copy.className='youtube-result-copy';
  const title=document.createElement('strong');title.textContent=item.title;copy.appendChild(title);
  const meta=document.createElement('span');meta.className='youtube-result-meta';meta.textContent=[item.views,item.published].filter(Boolean).join(' · ');copy.appendChild(meta);
  const channel=document.createElement('span');channel.className='youtube-result-channel';channel.textContent=item.channel;copy.appendChild(channel);
  const description=document.createElement('span');description.className='youtube-result-description';description.textContent=item.description;copy.appendChild(description);select.append(thumb,copy);
  const url='https://www.youtube.com/watch?v='+item.id;
  select.onclick=()=>{for(const el of list.querySelectorAll('[aria-pressed]'))el.setAttribute('aria-pressed','false');select.setAttribute('aria-pressed','true');$('youtubeURL').value=url;$('youtubeNotice').textContent='Selected: '+item.title+'. Press Run above to add it.';syncYouTubeSearchUI();};
  const watch=document.createElement('a');watch.href=url;watch.target='_blank';watch.rel='noopener noreferrer';watch.className='youtube-result-watch';watch.textContent='Watch on YouTube ↗';row.append(select,watch);list.appendChild(row);
 }
}
async function searchYouTubeVideos(more=false){
 if(!accountSignedIn())return;
 const query=more?youtubeSearchQuery:$('youtubeURL').value.trim();
 if(!query){$('youtubeNotice').textContent='Type a search or paste a YouTube video link.';return;}
 if(youtubeInputIsURL(query)){syncYouTubeSearchUI();return;}
 if(youtubeLooksLikeURL(query)){try{normalizeYouTubeURL(query);}catch(error){$('youtubeNotice').textContent=error.message;}return;}
 if(query.length>200){$('youtubeNotice').textContent='Keep your search under 200 characters.';return;}
 if(more&&!youtubeSearchNext)return;
 cancelYouTubeSearch();const run={controller:new AbortController(),context:boardAsyncContext()};youtubeSearchRuntime=run;
 const current=()=>youtubeSearchRuntime===run&&boardAsyncCurrent(run.context)&&accountSignedIn()&&$('youtubeDialog').open;
 if(!more){youtubeSearchQuery=query;youtubeSearchNext='';$('youtubeResults').replaceChildren();$('youtubeMore').hidden=true;}
 $('youtubeNotice').textContent='Searching YouTube…';syncYouTubeSearchUI();
 try{
  const result=await fetchYouTubeResults(query,more?youtubeSearchNext:'',run.controller.signal);if(!current())return;
  renderYouTubeResults(result.items,more);youtubeSearchNext=result.next;$('youtubeMore').hidden=!result.next;
  $('youtubeNotice').textContent=result.items.length?'Choose a video, then press Run above.':'No videos found. Try a different search.';
 }catch(error){if(current()&&error.name!=='AbortError')$('youtubeNotice').textContent=error.message;}
 finally{if(youtubeSearchRuntime===run){youtubeSearchRuntime=null;syncYouTubeSearchUI();}}
}

const driveDialog=document.createElement('dialog');driveDialog.id='driveDialog';driveDialog.setAttribute('aria-labelledby','driveTitle');
driveDialog.innerHTML='<div class="row spread"><h2 id="driveTitle">Add from Google Drive</h2><button type="button" id="closeDrive" class="dialog-close" aria-label="Close Google Drive import">×</button></div><p id="driveIdentity" class="drive-identity"></p><p id="driveStatus" class="intro" role="status"></p><progress id="driveProgress" max="1" value="0" hidden></progress><p class="mini-note">Choose up to 20 files, 100 MB total. Google Docs, Sheets, and Slides import as Word, Excel, and PowerPoint files. Google may ask you to approve access to selected files.</p><div class="dialog-actions"><button id="driveCancel" type="button">Cancel</button><button id="driveChoose" type="button" class="primary">Choose files from Drive</button></div>';
document.body.appendChild(driveDialog);
const driveButton=document.createElement('button');driveButton.id='boardAddDrive';driveButton.type='button';driveButton.innerHTML='<strong>Google Drive</strong><span>Files from your Google account</span>';$('boardAddFiles').after(driveButton);
driveButton.onclick=()=>openDriveDialog();$('addAttachmentsDrive').onclick=()=>{if(selected)openDriveDialog(selected);};$('driveChoose').onclick=()=>void importDriveFiles();
function closeDriveImport(){if(driveRuntime?.phase==='import')return;cancelGoogleSourceImports();}
$('driveCancel').onclick=$('closeDrive').onclick=closeDriveImport;driveDialog.addEventListener('cancel',event=>{event.preventDefault();closeDriveImport();});
$('youtubeSearch').onclick=()=>void searchYouTubeVideos();$('youtubeMore').onclick=()=>void searchYouTubeVideos(true);
$('youtubeControls').onclick=()=>setYouTubeControls($('youtubeControlsPanel').hidden);
$('youtubeNewModule').onchange=()=>{if(typeof setYouTubeNewModulePreference==='function')setYouTubeNewModulePreference($('youtubeNewModule').checked);};
$('youtubeURL').addEventListener('input',()=>{cancelYouTubeSearch();$('youtubeMore').hidden=!youtubeSearchNext||$('youtubeURL').value.trim()!==youtubeSearchQuery;for(const el of $('youtubeResults').querySelectorAll('[aria-pressed]'))el.setAttribute('aria-pressed','false');});
$('youtubeDialog').addEventListener('close',()=>{cancelYouTubeSearch();syncYouTubeSearchUI();});
