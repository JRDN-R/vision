// Portable connection settings. A bundled private-PC token connects automatically.
function validCloudConfig(value){
 if(!value||typeof value!=='object')return null;
 try{const u=new URL(value.backendUrl);if(u.protocol!=='https:'||u.username||u.password||u.search||u.hash||!['','/'].includes(u.pathname))return null;
  if(value.kind==='private-pc'){if(!/^[a-z0-9.-]+\.ts\.net$/i.test(u.hostname)||u.port)return null;return{kind:'private-pc',backendUrl:u.origin,...(value.publicAccess===true?{publicAccess:true}:{})};}
  if(!(/\.(run\.app|web\.app)$/.test(u.hostname)))return null;const key=String(value.firebaseApiKey||value.apiKey||'');if(!/^AIza[\w-]{20,100}$/.test(key))return null;return{kind:'firebase',backendUrl:u.origin,firebaseApiKey:key,projectId:String(value.projectId||'').slice(0,150)};
 }catch{return null;}
}
function parseConnectionInput(text){const start=text.indexOf('{'),end=text.lastIndexOf('}');if(start<0||end<start)throw new Error('Import the connection text file created by setup, or paste its configuration.');return JSON.parse(text.slice(start,end+1));}
function validPrivateConnection(value){const config=validCloudConfig(value),accessToken=String(value?.accessToken||'');return config?.kind==='private-pc'&&/^[A-Za-z0-9_-]{32,256}$/.test(accessToken)?{...config,accessToken,remember:true}:null;}
const bundledCloudConnection=(()=>{try{return JSON.parse($('visionCloudConfig').textContent);}catch{return null;}})();
let cloudConfig=(()=>{const built=validCloudConfig(bundledCloudConnection);if(validPrivateConnection(bundledCloudConnection))return built;try{return validCloudConfig(JSON.parse(localStorage.getItem('vision-cloud-config')||'null'))||built;}catch{return built;}})();
let cloudAuth=(()=>{const built=validPrivateConnection(bundledCloudConnection);if(built&&built.backendUrl===cloudConfig?.backendUrl)return built;try{const saved=JSON.parse(sessionStorage.getItem('vision-cloud-session')||localStorage.getItem('vision-cloud-session')||'null');if(!saved||!cloudConfig||saved.backendUrl!==cloudConfig.backendUrl)return null;return cloudConfig.kind==='private-pc'?validPrivateConnection({...saved,kind:'private-pc'}):saved;}catch{return null;}})();
function saveCloudSession(){try{sessionStorage.removeItem('vision-cloud-session');localStorage.removeItem('vision-cloud-session');if(cloudAuth&&cloudAuth.kind!=='firebase-google')(cloudAuth.remember?localStorage:sessionStorage).setItem('vision-cloud-session',JSON.stringify(cloudAuth));}catch{}}
function pcConnectionHelp(config=cloudConfig){return config?.publicAccess===true?'Keep FUPCJ Server awake and connected to the internet.':'Keep FUPCJ Server awake, with Tailscale connected on this device and FUPCJ Server.';}
function pcConnectionFailure(){return 'This device cannot reach FUPCJ Server. It may be offline, or this browser or network may be blocking the connection. '+(cloudConfig?.publicAccess===true?'Use Processor connection to check this device.':pcConnectionHelp());}
function updatePCConnectionCheck(value){const config=validCloudConfig(value),box=$('cloudReachabilityCheck');if(!box)return;box.hidden=config?.kind!=='private-pc';const link=$('cloudReachabilityLink');if(config?.kind==='private-pc')link.href=config.backendUrl+'/api/status';else link.removeAttribute('href');}
function updateConnectionFields(){let value;try{value=parseConnectionInput($('cloudConfigInput').value);}catch{}const privatePC=value?.kind==='private-pc'||(!value&&cloudConfig?.kind==='private-pc');$('cloudFirebaseFields').hidden=privatePC;$('cloudPCFields').hidden=!privatePC;$('cloudPCNote').textContent=pcConnectionHelp(value||cloudConfig);$('cloudPortableNote').textContent=privatePC?'FUPCJ Server connection settings are included in HTML downloads so they connect automatically. '+pcConnectionHelp(value||cloudConfig):'Cloud configuration is included in HTML downloads. Sign in on each device to connect.';if(value?.accessToken){$('cloudAccessToken').value=String(value.accessToken);const publicPart=validCloudConfig(value);if(publicPart)$('cloudConfigInput').value=JSON.stringify(publicPart,null,2);}updatePCConnectionCheck(value||cloudConfig);}
function openCloudSettings(message=''){
 $('cloudConfigInput').value=cloudConfig?JSON.stringify(cloudConfig,null,2):'';$('cloudStatus').textContent=message||(cloudAuth?(cloudConfig?.kind==='private-pc'?'FUPCJ Server configured.':'Signed in as '+cloudAuth.email):'Connect Vision to FUPCJ Server or cloud service.');$('cloudEmail').value=cloudAuth?.email||'';$('cloudPassword').value='';$('cloudAccessToken').value=cloudAuth?.accessToken||'';$('cloudRemember').checked=cloudAuth?.remember!==false;$('cloudConfigFields').open=!cloudConfig;$('cloudSignOut').hidden=!cloudAuth;updateConnectionFields();if(!$('cloudDialog').open)$('cloudDialog').showModal();
}
function saveCloudConfig(){
 if(cloudAuth?.kind==='firebase-google'||typeof accountFirebase!=='undefined'&&accountFirebase?.currentUser)throw new Error('Sign out of your Google account before changing the processor connection.');
 let value,next;try{value=parseConnectionInput($('cloudConfigInput').value);next=validCloudConfig(value);}catch{}if(!next)throw new Error('Import a valid Vision connection file or cloud configuration.');
 if(value.accessToken)$('cloudAccessToken').value=String(value.accessToken);
 if(cloudConfig?.backendUrl!==next.backendUrl||cloudConfig?.kind!==next.kind||cloudConfig?.firebaseApiKey!==next.firebaseApiKey){cloudAuth=null;saveCloudSession();}cloudConfig=next;try{localStorage.setItem('vision-cloud-config',JSON.stringify(next));}catch{}$('cloudConfigInput').value=JSON.stringify(next,null,2);updateConnectionFields();return next;
}
async function cloudSignIn(){
 const button=$('cloudSignIn');button.disabled=true;
 try{saveCloudConfig();
  if(cloudConfig.kind==='private-pc'){
   const accessToken=$('cloudAccessToken').value.trim();if(!/^[A-Za-z0-9_-]{32,256}$/.test(accessToken))throw new Error('Import Vision-Connection.txt or enter its access token.');
   const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);let response;try{response=await fetch(cloudConfig.backendUrl+'/api/health',{headers:{Authorization:'Bearer '+accessToken},credentials:'omit',signal:controller.signal});}catch{throw new Error(pcConnectionFailure());}finally{clearTimeout(timer);}
   if(!response.ok)throw new Error(response.status===401?'FUPCJ Server connection token was not accepted. Import the latest connection file.':'FUPCJ Server service could not be reached.');const health=await response.json();if(health.service!=='vision-pc')throw new Error('This address is not a Vision FUPCJ Server.');
   cloudAuth={kind:'private-pc',backendUrl:cloudConfig.backendUrl,accessToken,remember:$('cloudRemember').checked};
  }else{
   const email=$('cloudEmail').value.trim(),password=$('cloudPassword').value;if(!email||!password)throw new Error('Enter the email and password created for Vision in Firebase Authentication.');
   const response=await fetch('https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key='+encodeURIComponent(cloudConfig.firebaseApiKey),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password,returnSecureToken:true})});const data=await response.json();if(!response.ok)throw new Error(data.error?.message==='INVALID_LOGIN_CREDENTIALS'?'Email or password was not accepted.':data.error?.message||'Sign-in failed.');
   cloudAuth={kind:'firebase',backendUrl:cloudConfig.backendUrl,idToken:data.idToken,refreshToken:data.refreshToken,email:data.email,remember:$('cloudRemember').checked,expiresAt:Date.now()+Number(data.expiresIn)*1000};
  }
  saveCloudSession();$('cloudPassword').value='';$('cloudDialog').close();toast('Connected. Queued work will resume.');if(typeof projectHealthCache!=='undefined')projectHealthCache=null;if(typeof resumePCTranscriptionQueue==='function')resumePCTranscriptionQueue();if(typeof resumeYouTubeImports==='function')resumeYouTubeImports();
 }catch(error){$('cloudStatus').textContent=error.message;if(cloudConfig?.kind==='private-pc')$('cloudReachabilityCheck').open=true;}finally{button.disabled=false;}
}
async function ensureCloudSession(){
 if(typeof accountReady!=='undefined')await accountReady;
 if(typeof accountTransition!=='undefined')await accountTransition;
 if(typeof accountSignedIn==='function'&&!accountSignedIn()){openAccountDialog('Sign in with Google to use Vision.');throw new Error('Google sign-in is required.');}
 if(cloudAuth?.kind==='firebase-google')return accountIdToken();
 if(typeof accountUsesGoogle==='function'&&accountUsesGoogle()){openAccountDialog('Sign in with Google to reconnect your projects.');throw new Error('Sign in with Google to reconnect.');}
 if(!cloudConfig||!cloudAuth){openCloudSettings();throw new Error('Connect your processor, then try again.');}
 if(cloudConfig.kind==='private-pc'){if(!cloudAuth.accessToken){openCloudSettings();throw new Error('Import FUPCJ Server connection file.');}return cloudAuth.accessToken;}
 if(cloudAuth.expiresAt>Date.now()+60000)return cloudAuth.idToken;
 const response=await fetch('https://securetoken.googleapis.com/v1/token?key='+encodeURIComponent(cloudConfig.firebaseApiKey),{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({grant_type:'refresh_token',refresh_token:cloudAuth.refreshToken})});
 const data=await response.json();if(!response.ok){cloudAuth=null;saveCloudSession();openCloudSettings('Sign in again to reconnect.');throw new Error('Cloud sign-in expired.');}
 Object.assign(cloudAuth,{idToken:data.id_token,refreshToken:data.refresh_token,expiresAt:Date.now()+Number(data.expires_in)*1000});saveCloudSession();return cloudAuth.idToken;
}
async function cloudFetch(path,options={}){
 if(!/^\/[a-z]/i.test(path)||path.includes('..'))throw new Error('Invalid service request.');const authAtStart=typeof accountAuthEpoch==='undefined'?0:accountAuthEpoch;const token=await ensureCloudSession();
 if(typeof accountAuthEpoch!=='undefined'&&authAtStart!==accountAuthEpoch)throw new Error('The signed-in account changed. Try again.');
 const backendUrl=cloudConfig.backendUrl;
 const headers=new Headers(options.headers||{});headers.set('Authorization','Bearer '+token);
 try{const response=await fetch(backendUrl+'/api'+path,{...options,headers,credentials:'omit'});if(typeof accountAuthEpoch!=='undefined'&&authAtStart!==accountAuthEpoch)throw new Error('The signed-in account changed. Try again.');return response;}catch(error){if(error.name==='AbortError'||/account changed/.test(error.message))throw error;const failure=new Error(cloudConfig.kind==='private-pc'?pcConnectionFailure():'Processing server unavailable. Check your internet connection.');failure.code='VISION_SERVER_UNAVAILABLE';failure.retryable=true;throw failure;}
}
function downloadedAppSource(){
 const privateConnection=cloudConfig?.kind==='private-pc'&&cloudAuth?.backendUrl===cloudConfig.backendUrl?validPrivateConnection({...cloudConfig,accessToken:cloudAuth.accessToken}):null;
 const portable=privateConnection?{kind:privateConnection.kind,backendUrl:privateConnection.backendUrl,accessToken:privateConnection.accessToken,...(privateConnection.publicAccess===true?{publicAccess:true}:{})}:cloudConfig||{};
 const safe=JSON.stringify(portable).replace(/</g,'\\u003c');
 return APP_SOURCE.replace(/(<script id="visionCloudConfig" type="application\/json">)[\s\S]*?(<\/script>)/,(_match,start,end)=>start+safe+end);
}
const cloudReachabilityCheck=document.createElement('details');cloudReachabilityCheck.id='cloudReachabilityCheck';cloudReachabilityCheck.hidden=true;cloudReachabilityCheck.innerHTML='<summary>Check this device’s connection</summary><p><a id="cloudReachabilityLink" target="_blank" rel="noopener noreferrer">Open processor connection check ↗</a></p><p class="mini-note">A server status response means the address is reachable. A blocked page or connection error means this device cannot reach it. If FUPCJ Server works on another device, compare the networks and browser settings.</p>';$('cloudStatus').after(cloudReachabilityCheck);
$('openCloudSettings').onclick=()=>openCloudSettings();$('youtubeCloudSettings').onclick=()=>openCloudSettings();
$('closeCloudSettings').onclick=()=>$('cloudDialog').close();$('cloudSignIn').onclick=cloudSignIn;
$('cloudSaveConfig').onclick=()=>{try{saveCloudConfig();$('cloudStatus').textContent='Settings saved. Connect below to verify the processor.';}catch(error){$('cloudStatus').textContent=error.message;}};
$('cloudSignOut').onclick=()=>{cloudAuth=null;saveCloudSession();$('cloudAccessToken').value='';$('cloudSignOut').hidden=true;$('cloudStatus').textContent='Disconnected.';};
$('cloudPassword').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();void cloudSignIn();}});
$('cloudConfigInput').addEventListener('change',updateConnectionFields);
$('cloudImportFile').onclick=()=>$('cloudConnectionFile').click();
$('cloudConnectionFile').onchange=async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>65536)throw new Error('Choose the connection text file created by setup.');$('cloudConfigInput').value=await file.text();saveCloudConfig();$('cloudStatus').textContent='Connection imported. Click Connect to check it.';}catch(error){$('cloudStatus').textContent=error.message;}finally{e.target.value='';}};

// Imports retain their request and server job IDs so reconnecting does not create duplicate work.
let youtubeImportRunning=false,youtubeImportRuntime=null,youtubeImportTimer=null;
const youtubeImportTasks=new Map();
function normalizeYouTubeURL(value){
 let u;try{u=new URL(/^https?:\/\//i.test(value.trim())?value.trim():'https://'+value.trim());}catch{throw new Error('Enter a YouTube video link.');}
 const host=u.hostname.toLowerCase(),parts=u.pathname.split('/').filter(Boolean);let id;
 if(host==='youtu.be')id=parts[0];else if(['youtube.com','www.youtube.com','m.youtube.com','music.youtube.com'].includes(host)){id=u.searchParams.get('v');if(['shorts','live','embed'].includes(parts[0]))id=parts[1];}
 if(!/^[\w-]{11}$/.test(id||''))throw new Error('Use a YouTube video, Shorts, or youtu.be link.');return'https://www.youtube.com/watch?v='+id;
}
function normalizeYouTubeImports(raw){
 if(!Array.isArray(raw))return[];const ids=new Set();return raw.slice(0,100).flatMap(value=>{
  if(!value||typeof value!=='object')return[];try{
   const text=(x,max=180)=>String(x||'').slice(0,max),clientRequestId=text(value.clientRequestId),targetId=text(value.targetId),sourceId=text(value.sourceId),u=new URL(value.backendUrl);
   if(!/^[\w-]{8,180}$/.test(clientRequestId)||!targetId||!sourceId||u.protocol!=='https:'||u.username||u.password||u.search||u.hash||ids.has(clientRequestId))return[];
   ids.add(clientRequestId);return[{provider:value.provider==='gemini'?'gemini':'local',includeSoundEvents:value.provider!=='gemini'&&value.includeSoundEvents===true,url:normalizeYouTubeURL(String(value.url||'')),clientRequestId,backendUrl:u.origin+u.pathname.replace(/\/+$/,''),targetId,sourceId,remoteId:text(value.remoteId),createdAt:text(value.createdAt,40),status:value.status==='failed'?'failed':value.status==='auth'?'auth':'waiting'}];
  }catch{return[];}
 });
}
async function youtubeAPI(path,options={}){
 const controller=new AbortController(),parentSignal=options.signal;let timedOut=false;
 const abort=()=>controller.abort();if(parentSignal?.aborted)abort();else parentSignal?.addEventListener('abort',abort,{once:true});
 const timer=setTimeout(()=>{timedOut=true;controller.abort();},path.endsWith('/result')?120000:20000);
 try{
  const response=await cloudFetch('/'+path,{...options,signal:controller.signal,headers:{'Accept':'application/json',...(options.body?{'Content-Type':'application/json'}:{}),...options.headers}});
  let data;try{data=await response.json();}catch{const error=new Error('The processing server did not return a valid response.');error.retryable=response.status>=500;error.status=response.status;throw error;}
  if(!response.ok){const error=new Error(data.error||data.detail||'The processing server could not finish this request.');error.status=response.status;error.retryable=response.status>=500||response.status===429;throw error;}return data;
 }catch(error){if(timedOut){const timeout=new Error('Processing server unavailable.');timeout.retryable=true;throw timeout;}throw error;}
 finally{clearTimeout(timer);parentSignal?.removeEventListener('abort',abort);}
}
function youtubeTask(job){
 let task=youtubeImportTasks.get(job.clientRequestId);if(!task){const n=nodeById(job.targetId);task={title:n?.title||'YouTube video',detail:'Queued',progress:0};youtubeImportTasks.set(job.clientRequestId,task);mediaActivity.push(task);}return task;
}
function removeYouTubeTask(job){const task=youtubeImportTasks.get(job.clientRequestId);if(task){task.finished=true;const index=mediaActivity.indexOf(task);if(index>=0)mediaActivity.splice(index,1);youtubeImportTasks.delete(job.clientRequestId);}}
function youtubeConnected(){if(typeof accountSignedIn==='function'&&!accountSignedIn())return false;return !!(cloudConfig&&cloudAuth&&cloudAuth.backendUrl===cloudConfig.backendUrl&&(cloudAuth.kind==='firebase-google'?cloudAuth.uid:cloudAuth.kind==='private-pc'?cloudAuth.accessToken:cloudAuth.refreshToken||cloudAuth.idToken));}
function scheduleYouTubeImports(delay=0){
 clearTimeout(youtubeImportTimer);youtubeImportTimer=setTimeout(()=>void pumpYouTubeImports(),Math.max(0,delay));
}
function resumeYouTubeImports(){
 if(youtubeImportRuntime)youtubeImportRuntime.controller.abort();
 for(const task of youtubeImportTasks.values()){const index=mediaActivity.indexOf(task);if(index>=0)mediaActivity.splice(index,1);}youtubeImportTasks.clear();
 state.youtubeImports=normalizeYouTubeImports(state.youtubeImports);
 for(const job of state.youtubeImports){if(job.status==='auth'&&youtubeConnected())job.status='waiting';if(job.status!=='failed')youtubeTask(job).detail=job.backendUrl!==cloudConfig?.backendUrl?'Waiting for original server':youtubeConnected()?'Waiting for server':'Connect to resume';}
 renderActivity();scheduleYouTubeImports();
}
function openYouTubeDialog(){
 syncTranscriptionProviderUI();$('youtubeNewModule').checked=!nodeById(selected);$('youtubeNotice').textContent='Uses available captions first. Include sound effects sends the full audio to FUPCJ Server Whisper and sound detection. Save your project to keep pending imports; reopen it to resume.';
 $('youtubeImport').disabled=youtubeImportRunning;$('youtubeDialog').showModal();$('youtubeURL').focus();
}
async function importYouTube(){
 if(youtubeImportRunning||busy||ioBusy)return;let url;
 try{url=normalizeYouTubeURL($('youtubeURL').value);}catch(e){$('youtubeNotice').textContent=e.message;return;}
 const createNew=$('youtubeNewModule').checked||!nodeById(selected),targetId=selected,project=state,provider=transcriptionProvider(),includeSoundEvents=soundEventsSelected(provider);
 try{
  $('youtubeImport').disabled=true;await ensureCloudSession();if(state!==project)return;
  const n=createNew?await createModuleNode('video','YouTube video'):nodeById(targetId);if(state!==project||!n||!state.nodes.includes(n))return;
  const source={id:uid(),name:'YouTube video.mp4',mime:'video/mp4',size:0,metadataOnly:true,role:'video',status:'pending',snapshotsStatus:'pending',transcriptionStatus:'pending',createdAt:new Date().toISOString()};
  checkpoint();n.attachments.push(source);if(createNew)n.videoSourceId=source.id;
  const job={url,provider,includeSoundEvents,clientRequestId:uid(),backendUrl:cloudConfig.backendUrl,targetId:n.id,sourceId:source.id,remoteId:'',createdAt:source.createdAt,status:'queued'};
  state.youtubeImports=state.youtubeImports||[];state.youtubeImports.push(job);youtubeTask(job);markDirty();updateVideoAttachments(n);renderActivity();$('youtubeDialog').close();
  toast('YouTube import queued. Save your project to keep pending work.');scheduleYouTubeImports();
 }catch(error){$('youtubeNotice').textContent=error.message;toast(error.message,true);}finally{$('youtubeImport').disabled=youtubeImportRunning;}
}
async function pumpYouTubeImports(){
 if(youtubeImportRuntime)return;
 const jobs=state.youtubeImports||[];let job;
 for(const candidate of jobs){
  if(candidate.status==='failed')continue;const task=youtubeTask(candidate);
  if(candidate.backendUrl!==cloudConfig?.backendUrl){task.detail='Waiting for original server';continue;}
  if(!youtubeConnected()||candidate.status==='auth'){task.detail='Connect to resume';continue;}
  if(navigator.onLine===false){task.detail='Waiting for internet';continue;}
  if(!job)job=candidate;
 }
 renderActivity();if(!job){if(jobs.some(j=>j.status!=='failed'))scheduleYouTubeImports(20000);return;}
 if(busy||ioBusy){scheduleYouTubeImports(1500);return;}
 const project=state,n=nodeById(job.targetId),source=n?.attachments?.find(a=>a.id===job.sourceId),task=youtubeTask(job);
 if(!n||!source){state.youtubeImports=jobs.filter(j=>j!==job);removeYouTubeTask(job);markDirty();scheduleYouTubeImports();return;}
 const controller=new AbortController(),signal=controller.signal;let decoder,ownsIO=false,completed=false,terminal=false;
 const runtime={project,controller,job};youtubeImportRuntime=runtime;youtubeImportRunning=true;$('youtubeImport').disabled=true;
 const check=()=>{if(signal.aborted||state!==project||!state.nodes.includes(n)||!n.attachments.includes(source)||!state.youtubeImports?.includes(job))throw new DOMException('The destination module is no longer open.','AbortError');if(job.backendUrl!==cloudConfig?.backendUrl)throw new DOMException('The connection changed.','AbortError');};
 try{
  check();job.status='working';task.detail=job.remoteId?'Reconnecting':'Connecting';renderActivity();
  if(!job.remoteId){const accepted=await youtubeAPI('youtube',{method:'POST',body:JSON.stringify({url:job.url,clientRequestId:job.clientRequestId,includeSoundEvents:job.includeSoundEvents===true}),signal});check();if(!accepted.id)throw new Error('The server did not return an import ID.');job.remoteId=String(accepted.id);markDirty();}
  let result;
  while(true){check();const status=await youtubeAPI('jobs/'+encodeURIComponent(job.remoteId),{signal});check();task.detail=status.phase||status.status;task.progress=Math.min(90,Number(status.progress||0)*.9);renderActivity();if(['error','failed','cancelled'].includes(status.status)){terminal=true;throw new Error(status.error||'The video could not be imported.');}if(status.status==='complete'){result=await youtubeAPI('jobs/'+encodeURIComponent(job.remoteId)+'/result',{signal});check();break;}await new Promise(resolve=>setTimeout(resolve,1200));}
  while(busy||ioBusy){check();await new Promise(resolve=>setTimeout(resolve,250));}check();ioBusy=true;ownsIO=true;
  if(!Array.isArray(result.frames)||!result.frames.length)throw new Error('No screenshots were returned.');
  const title=String(result.title||'YouTube video').slice(0,150),frames=result.frames.map((frame,index)=>{
   if(!/^data:image\/jpeg;base64,[A-Za-z0-9+/]+=*$/.test(frame.data||''))throw new Error('A screenshot could not be read.');
   return{id:job.sourceId+'-frame-'+index,name:String(frame.name||'frame.jpg'),mime:'image/jpeg',size:bytesFromDataURL(frame.data).length,data:frame.data,timestamp:frame.timestamp,requestedTimestamp:frame.timestamp,role:'video-frame',videoOf:source.id,generated:true,status:'complete',createdAt:new Date().toISOString()};
  });
  source.name=title+'.mp4';task.title=title;source.videoDuration=result.duration;source.snapshotInterval=result.snapshotInterval;
  // Replace only generated frames belonging to this import when recovering an interrupted application.
  n.attachments=n.attachments.filter(a=>!(a.videoOf===source.id&&a.role==='video-frame'));n.attachments.push(...frames);source.snapshotsStatus='complete';
  if(n.videoSourceId===source.id){n.title=title;n.src=result.frames[0].data;const img=await R.loadImage(n.src);check();n.width=img.naturalWidth;n.height=img.naturalHeight;await renderNode(n);}
  if(job.includeSoundEvents&&!result.audio?.data)throw new Error('Sound effects require the full YouTube audio. Update FUPCJ Server and retry this import. Captions alone cannot detect sounds.');
  if(result.transcript?.text&&!job.includeSoundEvents){source.transcriptionProvider='captions';storeVideoTranscript(n,source,String(result.transcript.text),'complete');recordActivity(title,'Screenshots and captions ready');toast('YouTube import complete: screenshots and captions ready.');}
  else if(result.audio?.data){
   if(!(n.transcriptionJobs||[]).some(j=>j.sourceId===source.id)&&source.transcriptionStatus!=='complete'){
    task.detail='Preparing audio for transcription';task.progress=90;renderActivity();
    const file=new File([bytesFromDataURL(result.audio.data)],result.audio.name||'youtube-audio.m4a',{type:result.audio.mime||'audio/mp4'});
    const wasmBinary=await embeddedBytes('ffmpeg-wasm-source',signal),fvadBinary=await embeddedBytes('fvad-wasm-source',signal);check();decoder=decoderClient();await decoder.request('init',{wasmBinary,fvadBinary},[wasmBinary.buffer,fvadBinary.buffer]);check();
    await prepareTranscriptionQueue(n,source,file,decoder,signal,(message,fraction)=>{task.detail=message;task.progress=90+10*fraction;renderActivity();},job.provider,job.includeSoundEvents);check();
   }
   recordActivity(title,'Screenshots ready · transcription queued');toast('Screenshots ready. Audio queued for transcription.');
  }else{storeVideoTranscript(n,source,'No captions or audio track were available.','no-audio');recordActivity(title,'Screenshots ready');toast('YouTube screenshots ready.');}
  check();completed=true;state.youtubeImports=state.youtubeImports.filter(j=>j!==job);removeYouTubeTask(job);updateVideoAttachments(n);updateSequence();markDirty();
 }catch(error){
  if(state!==project||signal.aborted)return;
  if(error.name==='AbortError'){job.status='waiting';task.detail='Waiting for original server';}
  else if(error.status===401||error.status===403||!youtubeConnected()){job.status='auth';task.detail='Reconnect to continue';openCloudSettings('Reconnect to resume your saved YouTube import.');}
  else if(!terminal&&(error.retryable||error instanceof TypeError||error.code==='VISION_SERVER_UNAVAILABLE')){job.status='waiting';task.detail='Waiting for server';}
  else{job.status='failed';source.status='failed';source.transcriptionStatus='failed';task.detail=error.message;removeYouTubeTask(job);recordActivity(task.title,error.message+' · import the link again to retry');updateVideoAttachments(n);toast(error.message,true);}
  markDirty();
 }finally{
  decoder?.stop();if(ownsIO)ioBusy=false;updateRefreshNotice();Promise.resolve().then(()=>scheduleTranscriptionQueue());
  if(completed&&job.remoteId&&job.backendUrl===cloudConfig?.backendUrl)void youtubeAPI('jobs/'+encodeURIComponent(job.remoteId),{method:'DELETE'}).catch(()=>{});
  if(youtubeImportRuntime===runtime)youtubeImportRuntime=null;youtubeImportRunning=false;$('youtubeImport').disabled=false;renderActivity();scheduleTranscriptionQueue();scheduleYouTubeImports(completed?100:20000);
 }
}
$('youtubeButton').onclick=openYouTubeDialog;
$('closeYouTube').onclick=()=>$('youtubeDialog').close();
$('youtubeImport').onclick=importYouTube;
$('youtubeURL').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();void importYouTube();}});
window.addEventListener('online',()=>scheduleYouTubeImports());
