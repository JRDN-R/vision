// Record a voice note, then hand the file to the same durable ASR queue as an upload.
// The microphone never uses browser speech recognition or changes the chosen provider.
const boardCaptureDialog=document.createElement('dialog');
boardCaptureDialog.id='boardCaptureDialog';boardCaptureDialog.setAttribute('aria-labelledby','boardCaptureTitle');
boardCaptureDialog.innerHTML='<div class="capture-heading"><h2 id="boardCaptureTitle">Voice note</h2><button type="button" id="boardCaptureClose" class="dialog-close" aria-label="Close voice note">×</button></div><p class="intro">Record your instructions. Stop to add a module; its text fills in when transcription finishes.</p><label for="boardCaptureProvider">Transcription for new recordings and files</label><select id="boardCaptureProvider" class="full"><option value="local">Local PC · Whisper · no API charge</option><option value="gemini">Gemini · API billing may apply</option></select><p id="boardCaptureProviderNote" class="mini-note"></p><div class="capture-clock" aria-hidden="true"><span class="capture-light"></span><span id="boardCaptureClock">00:00</span></div><p id="boardCaptureStatus" role="status" aria-live="polite"></p><div class="capture-actions"><button type="button" id="boardCaptureCancel" class="ghost">Close</button><button type="button" id="boardCaptureStart" class="primary">Start recording</button><button type="button" id="boardCaptureStop" class="primary" hidden>Stop and add module</button></div>';
document.body.appendChild(boardCaptureDialog);
const boardCaptureButton=document.createElement('button');boardCaptureButton.type='button';boardCaptureButton.id='boardCaptureButton';boardCaptureButton.title='Voice note and transcription settings';boardCaptureButton.setAttribute('aria-label','Voice note and transcription settings');
boardCaptureButton.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><rect x="9" y="2.5" width="6" height="12" rx="3"/><path d="M5.5 11.5v1a6.5 6.5 0 0 0 13 0v-1M12 19v3M8.5 22h7"/></svg>';
document.querySelector('.tools').appendChild(boardCaptureButton);
let boardCaptureSession=null,boardCaptureSerial=0;
const BOARD_CAPTURE_MAX_MS=15*60*1000,BOARD_CAPTURE_MAX_BYTES=24*1024*1024;
function boardCaptureAvailable(){return !!navigator.mediaDevices?.getUserMedia&&typeof MediaRecorder==='function';}
function boardCaptureMessage(message,error=false){$('boardCaptureStatus').textContent=message;boardCaptureDialog.classList.toggle('capture-error',error);}
function syncBoardCaptureProvider(){
 const provider=transcriptionProvider();$('boardCaptureProvider').value=provider;
 $('boardCaptureProviderNote').textContent=provider==='gemini'?'Gemini may incur API charges. This preference is saved with the project. Keep Vision open while Gemini transcribes.':'Whisper runs on your PC without an API charge. Keep Vision open until the PC accepts the audio.';
}
function boardCaptureControls(mode='idle'){
 const active=mode!=='idle';$('boardCaptureProvider').disabled=active;$('boardCaptureStart').hidden=mode==='recording'||mode==='stopping';$('boardCaptureStart').disabled=active||!boardCaptureAvailable();$('boardCaptureStop').hidden=mode!=='recording';$('boardCaptureCancel').textContent=active?'Cancel recording':'Close';boardCaptureDialog.classList.toggle('capture-recording',mode==='recording');
 boardCaptureButton.setAttribute('aria-pressed',String(mode==='recording'));
}
function releaseBoardCapture(session){
 clearInterval(session.timer);session.timer=null;
 for(const [track,listener]of session.trackListeners||[])track.removeEventListener('ended',listener);
 session.trackListeners=[];
 if(session.recorder){session.recorder.ondataavailable=null;session.recorder.onstop=null;session.recorder.onerror=null;try{if(session.recorder.state!=='inactive')session.recorder.stop();}catch{}}
 for(const track of session.stream?.getTracks()||[])track.stop();session.stream=null;session.chunks=[];session.bytes=0;
}
function cancelBoardCapture(message='Recording canceled. No audio was added.'){
 boardCaptureSerial++;const session=boardCaptureSession;boardCaptureSession=null;if(session)releaseBoardCapture(session);boardCaptureControls();$('boardCaptureClock').textContent='00:00';if(message)boardCaptureMessage(message);
}
function openBoardCapture(){
 if(busy||ioBusy){toast('Please wait for the current operation to finish.');return;}
 syncBoardCaptureProvider();boardCaptureControls();$('boardCaptureClock').textContent='00:00';
 boardCaptureMessage(boardCaptureAvailable()?'Ready when you are. Microphone access is requested only when you start.':'Microphone recording is unavailable here. Open Vision over HTTPS in a browser that supports recording, or import an audio file.',!boardCaptureAvailable());
 if(!boardCaptureDialog.open)boardCaptureDialog.showModal();
}
function boardCaptureFailure(error){
 const name=error?.name;
 if(name==='NotAllowedError'||name==='SecurityError')return'Microphone permission was denied. Allow microphone access in this browser’s site settings, then try again.';
 if(name==='NotFoundError')return'No microphone was found. Connect one, or import an audio recording.';
 if(name==='NotReadableError'||name==='AbortError')return'The microphone could not start. Close another app using it, then try again.';
 return error?.message||'Recording failed. Try again or import an audio file.';
}
function failBoardCapture(session,error){if(boardCaptureSession!==session)return;cancelBoardCapture('');boardCaptureMessage(boardCaptureFailure(error),true);}
async function finishBoardCapture(session){
 if(boardCaptureSession!==session)return;
 const type=session.recorder.mimeType||session.chunks[0]?.type||'audio/webm',audio=new Blob(session.chunks,{type});
 boardCaptureSession=null;releaseBoardCapture(session);boardCaptureControls();
 if(!audio.size){boardCaptureMessage('No audio was captured. Try recording again.',true);return;}
 if(state!==session.project){boardCaptureMessage('The project changed while recording. Record again in the current project.',true);return;}
 const extension=/mp4|aac/.test(type)?'m4a':/ogg/.test(type)?'ogg':'webm';
 const file=new File([audio],'Voice note '+new Date().toISOString().replace(/[:.]/g,'-')+'.'+extension,{type});
 boardCaptureDialog.close();
 try{await importBoardFiles([file],session.location,{dictation:true,title:'Voice note',provider:session.provider});}
 catch(error){toast('Could not add the recording: '+error.message,true);}
}
function stopBoardCapture(message='Adding your voice note…'){
 const session=boardCaptureSession;if(!session||session.recorder?.state!=='recording')return;
 clearInterval(session.timer);session.timer=null;boardCaptureControls('stopping');boardCaptureMessage(message);
 try{session.recorder.stop();for(const [track,listener]of session.trackListeners)track.removeEventListener('ended',listener);session.trackListeners=[];for(const track of session.stream.getTracks())track.stop();}
 catch(error){failBoardCapture(session,error);}
}
async function startBoardCapture(){
 if(boardCaptureSession||busy||ioBusy)return;
 if(!boardCaptureAvailable()){boardCaptureMessage('This browser cannot record audio. Import a recording instead.',true);return;}
 const r=board.getBoundingClientRect(),session={serial:++boardCaptureSerial,project:state,provider:transcriptionProvider(),location:{x:r.left+r.width/2,y:r.top+r.height/2},chunks:[],bytes:0,stream:null,recorder:null,trackListeners:[]};
 boardCaptureSession=session;boardCaptureControls('requesting');boardCaptureMessage('Waiting for microphone permission…');
 try{
  const stream=await navigator.mediaDevices.getUserMedia({audio:true});
  // A permission prompt can resolve after Cancel or after the page has been hidden.
  if(boardCaptureSession!==session||session.serial!==boardCaptureSerial){for(const track of stream.getTracks())track.stop();return;}
  session.stream=stream;
  const mime=['audio/webm;codecs=opus','audio/mp4','audio/ogg;codecs=opus'].find(type=>MediaRecorder.isTypeSupported?.(type));
  session.recorder=new MediaRecorder(stream,{...(mime?{mimeType:mime}:{}),audioBitsPerSecond:64000});
  session.recorder.ondataavailable=event=>{if(boardCaptureSession!==session||!event.data?.size)return;session.chunks.push(event.data);session.bytes+=event.data.size;if(session.bytes>=BOARD_CAPTURE_MAX_BYTES)stopBoardCapture('Recording limit reached. Adding your voice note…');};
  session.recorder.onerror=event=>failBoardCapture(session,event.error||new Error('The microphone recording stopped unexpectedly.'));
  session.recorder.onstop=()=>{void finishBoardCapture(session);};
  for(const track of stream.getTracks()){const ended=()=>failBoardCapture(session,new Error('The microphone disconnected. Recording was canceled.'));track.addEventListener('ended',ended);session.trackListeners.push([track,ended]);}
  session.recorder.start(1000);session.startedAt=Date.now();boardCaptureControls('recording');boardCaptureMessage('Recording. Stop to create a module, or cancel to discard.');
  session.timer=setInterval(()=>{if(boardCaptureSession!==session)return;const elapsed=Date.now()-session.startedAt,seconds=Math.floor(elapsed/1000);$('boardCaptureClock').textContent=String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');if(elapsed>=BOARD_CAPTURE_MAX_MS)stopBoardCapture('15-minute recording limit reached. Adding your voice note…');},500);
 }catch(error){failBoardCapture(session,error);}
}
boardCaptureButton.onclick=openBoardCapture;
$('boardCaptureStart').onclick=()=>{void startBoardCapture();};$('boardCaptureStop').onclick=()=>stopBoardCapture();
$('boardCaptureProvider').onchange=()=>{setTranscriptionProvider($('boardCaptureProvider').value);syncBoardCaptureProvider();};
const captureSyncTranscriptionProviderUI=syncTranscriptionProviderUI;
syncTranscriptionProviderUI=function(){captureSyncTranscriptionProviderUI();if(!boardCaptureSession)syncBoardCaptureProvider();};
for(const id of ['boardCaptureClose','boardCaptureCancel'])$(id).onclick=()=>{cancelBoardCapture('');boardCaptureDialog.close();};
boardCaptureDialog.addEventListener('cancel',()=>cancelBoardCapture(''));
boardCaptureDialog.addEventListener('close',()=>{if(boardCaptureSession)cancelBoardCapture('');});
window.addEventListener('pagehide',()=>cancelBoardCapture(''));
document.addEventListener('visibilitychange',()=>{if(document.hidden&&boardCaptureSession)cancelBoardCapture('Recording canceled because Vision moved to the background. No audio was added.');});
