/* Hosted Venture controls: microphone -> authenticated dictation endpoint only.
 * No Gemini credential or general-purpose model permission is present in browser code.
 */
const VENTURE_DICTATION_LIMIT_MS = 180000;
let ventureRecording = null;
let ventureAvatarObjectURL = null;

function ventureBrandIcon() {
 const source = document.querySelector('.vision-header-head');
 if (!source) return '<span class="venture-brand-fallback" aria-hidden="true">V</span>';
 const svg = source.cloneNode(true);
 svg.removeAttribute('hidden'); svg.removeAttribute('id'); svg.removeAttribute('role'); svg.removeAttribute('aria-label');
 svg.classList.remove('vision-header-head'); svg.classList.add('venture-monocle');
 svg.setAttribute('aria-hidden', 'true');
 return svg.outerHTML;
}
function ventureDictationBusy() { return !!ventureRecording && !['failed','awaiting-space'].includes(ventureRecording.phase); }
function ventureDictationCurrent(recording) {
 return ventureRecording === recording && !recording.cancelled && venture.open &&
   recording.epoch === venture.epoch && recording.selection === venture.selection;
}
function ventureRecordingUI(recording, phase) {
 const live = phase === 'recording';
 const provider = recording?.provider === 'whisper' ? 'PC Whisper' : 'Gemini';
 $('ventureMic').onclick = ventureDictate;
 $('ventureMic').setAttribute('aria-pressed', String(live));
 $('ventureMic').setAttribute('aria-label', live ? 'Stop dictation' : phase === 'processing' ? 'Transcribing with ' + provider : 'Dictate a message');
 $('ventureMic').innerHTML = ventureIcon(live ? 'stop' : 'mic');
 $('ventureMic').disabled = !!phase && !live;
 $('ventureDictation').hidden = !phase;
 $('ventureDictation').classList.toggle('transcribing', phase === 'processing');
 $('ventureDictationStatus').textContent = live ? 'Listening' : phase === 'processing' ? 'Transcribing with ' + provider + '…' : 'Waiting for microphone…';
 $('ventureDictationTime').hidden = !live;
 $('ventureDictation').classList.remove('failed');
 for (const id of ['ventureDictationSave','ventureDictationOriginal','ventureDictationSaveText']) if ($(id)) $(id).hidden = true;
 const discard = $('ventureDictationDiscard');
 if (discard) {
  discard.hidden = !phase;
  discard.textContent = 'Cancel dictation';
  discard.onclick = () => { if (ventureDictationCurrent(recording)) ventureStopDictation(); };
 }
 venturePaintStatus();
}
function ventureReleaseMic(recording) {
 clearTimeout(recording.deadline); clearInterval(recording.watchdog);
 cancelAnimationFrame(recording.frame);
 for (const track of recording.stream?.getTracks() || []) track.stop();
 try { recording.source?.disconnect(); } catch {}
 try { recording.analyser?.disconnect(); } catch {}
 if (recording.audioContext) void recording.audioContext.close().catch(() => {});
}
function ventureStopDictation(discard = true) {
 const recording = ventureRecording;
 if (!recording) return;
 if (!discard) { ventureFinishRecording(recording); return; }
 recording.cancelled = true;
 clearTimeout(recording.retryTimer); recording.retryResolve?.(false); recording.retryResolve = null;
 recording.controller?.abort(); recording.exportController?.abort(); recording.decoder?.stop();
 recording.audio = null; recording.mp3 = null; recording.form = null; recording.chunks = [];
 if (recording.recorder?.state !== 'inactive') { try { recording.recorder?.stop(); } catch {} }
 ventureReleaseMic(recording);
 ventureRecording = null;
 ventureRecordingUI(null, '');
}
function ventureWaveform(recording) {
 if (!ventureDictationCurrent(recording) || recording.phase !== 'recording') return;
 const elapsed = Math.min(VENTURE_DICTATION_LIMIT_MS, performance.now() - recording.started);
 const seconds = Math.floor(elapsed / 1000);
 $('ventureDictationTime').textContent = Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
 const canvas = $('ventureWaveform'), ctx = canvas.getContext('2d');
 if (ctx) {
  const width = canvas.width, height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = getComputedStyle($('visionVenture')).getPropertyValue('--accent').trim() || '#C6F68B';
  const samples = recording.samples;
  recording.analyser?.getByteTimeDomainData(samples);
  const bars = 48, step = Math.max(1, Math.floor(samples.length / bars));
  for (let i = 0; i < bars; i++) {
   let peak = 0;
   for (let j = i * step; j < (i + 1) * step; j++) peak = Math.max(peak, Math.abs(samples[j] - 128) / 128);
   const h = Math.max(2, Math.min(height, peak * height * 2.4));
   ctx.fillRect(i * width / bars, (height - h) / 2, Math.max(2, width / bars - 3), h);
  }
 }
 if (elapsed >= VENTURE_DICTATION_LIMIT_MS) { ventureFinishRecording(recording); return; }
 recording.frame = requestAnimationFrame(() => ventureWaveform(recording));
}
async function ventureDictate() {
 if (ventureRecording) {
  if (ventureRecording.phase === 'recording') ventureFinishRecording(ventureRecording);
  return;
 }
 if (!venture.ready || venture.sending || venture.pending) return;
 if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
  ventureSetNotice('Microphone dictation needs the hosted HTTPS page and a browser that supports audio recording.');
  return;
 }
 const recording = {epoch: venture.epoch, selection: venture.selection, phase: 'permission',
  cancelled: false, chunks: [], size: 0, samples: new Uint8Array(1024).fill(128), requestId: ventureId()};
 ventureRecording = recording;
 ventureSetNotice(''); ventureRecordingUI(recording, 'permission');
 try {
  // Unlock Web Audio synchronously in the click gesture (especially Safari).
  // Waiting until after a permission dialog can leave the waveform suspended.
  const Audio = window.AudioContext || window.webkitAudioContext;
  if (Audio) {
   try { recording.audioContext = new Audio(); void recording.audioContext.resume().catch(() => {}); }
   catch { /* Audio recording can work without a Web Audio analyser. */ }
  }
  // Request from the user gesture; never silently acquire microphone access.
  recording.stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, channelCount: 1}});
  if (!ventureDictationCurrent(recording)) { ventureReleaseMic(recording); return; }
  const mime = ['audio/mp4;codecs=mp4a.40.2', 'audio/mp4', 'audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/webm'].find(t => MediaRecorder.isTypeSupported(t));
  recording.recorder = new MediaRecorder(recording.stream, {...(mime ? {mimeType: mime} : {}), audioBitsPerSecond: 64000});
  recording.recorder.ondataavailable = event => {
   if (recording.cancelled) return;
   if (event.data.size) { recording.chunks.push(event.data); recording.size += event.data.size; }
   if (recording.size >= 11 * 1024 * 1024 || performance.now() - recording.started >= VENTURE_DICTATION_LIMIT_MS) ventureFinishRecording(recording);
  };
  recording.recorder.onerror = () => {
   if (ventureDictationCurrent(recording)) { ventureStopDictation(); ventureSetNotice('The microphone stopped unexpectedly. Try recording again.'); }
  };
  recording.recorder.onstop = () => { if (ventureDictationCurrent(recording)) void ventureUploadDictation(recording); };
  for (const track of recording.stream.getAudioTracks()) track.onended = () => ventureFinishRecording(recording);
  // The waveform uses actual microphone samples, not simulated speech activity.
  if (recording.audioContext) {
   try {
    recording.analyser = recording.audioContext.createAnalyser(); recording.analyser.fftSize = 1024;
    recording.source = recording.audioContext.createMediaStreamSource(recording.stream);
    recording.source.connect(recording.analyser);
    void recording.audioContext.resume().catch(() => {});
   } catch { /* Recording still works on a browser without an analyser. */ }
  }
  recording.started = performance.now(); recording.phase = 'recording';
  recording.recorder.start(1000);
  recording.deadline = setTimeout(() => ventureFinishRecording(recording), VENTURE_DICTATION_LIMIT_MS);
  // Wall-clock guard handles delayed recording events and throttled timers.
  recording.watchdog = setInterval(() => {
   if (performance.now() - recording.started >= VENTURE_DICTATION_LIMIT_MS) ventureFinishRecording(recording);
  }, 250);
  ventureRecordingUI(recording, 'recording'); ventureWaveform(recording);
 } catch (error) {
  if (!ventureDictationCurrent(recording)) { ventureReleaseMic(recording); return; }
  ventureStopDictation();
  const message = error.name === 'NotAllowedError' ? 'Allow microphone access for this site in your browser settings, then tap the microphone again.' :
   error.name === 'NotFoundError' ? 'No microphone was found. Connect one and try again.' : 'The microphone could not start. Check whether another app is using it.';
  ventureSetNotice(message);
 }
}
function ventureFinishRecording(recording) {
 if (!ventureDictationCurrent(recording) || recording.phase !== 'recording') return;
 recording.phase = 'processing';
 // No warning, sound, confirmation or auto-send at three minutes.
 try { if (recording.recorder.state !== 'inactive') recording.recorder.stop(); }
 finally { ventureReleaseMic(recording); ventureRecordingUI(recording, 'processing'); }
}
// One paid attempt, then three local attempts. Waiting never resubmits Gemini.
const VENTURE_WHISPER_RETRY_MS = Object.freeze([0, 30000, 60000]);
function ventureDictationPause(recording, milliseconds) {
 return new Promise(resolve => {
  recording.retryResolve = resolve;
  recording.retryTimer = setTimeout(() => {
   recording.retryResolve = null; recording.retryTimer = null;
   resolve(ventureDictationCurrent(recording));
  }, milliseconds);
 });
}
function ventureDictationAudio(recording) {
 return recording.audio || recording.form?.get('audio');
}
function ventureDictationAudioExtension(blob) {
 const mime = (blob?.type || '').split(';')[0].toLowerCase();
 return ({'audio/mp4':'m4a','audio/m4a':'m4a','audio/x-m4a':'m4a',
  'audio/mpeg':'mp3','audio/mp3':'mp3','audio/ogg':'ogg','audio/wav':'wav','audio/x-wav':'wav','audio/webm':'webm'})[mime] || 'bin';
}
function ventureDictationAudioName(recording, extension) {
 recording.savedName ||= 'Venture-dictation-' + new Date().toISOString().replace(/[:.]/g, '-');
 return recording.savedName + '.' + extension;
}
function ventureDictationShowRecovery(recording, message, failed = true) {
 if (!ventureDictationCurrent(recording)) return;
 recording.phase = failed ? 'failed' : 'awaiting-space';
 ventureRecordingUI(recording, recording.phase);
 $('ventureDictationStatus').textContent = message;
 $('ventureDictation').classList.toggle('failed', failed);
 const blob = ventureDictationAudio(recording), extension = ventureDictationAudioExtension(blob);
 const direct = extension === 'm4a' || extension === 'mp3';
 const save = $('ventureDictationSave'), original = $('ventureDictationOriginal');
 save.hidden = !blob; save.disabled = false; save.textContent = direct ? 'Save audio (.' + extension + ')' : 'Save audio as MP3';
 save.onclick = () => void ventureSaveDictationAudio(recording);
 original.hidden = !blob || direct;
 original.textContent = 'Save original (.' + extension + ')';
 original.onclick = () => { if (ventureDictationCurrent(recording)) download(blob, ventureDictationAudioName(recording, extension)); };
 const transcript = $('ventureDictationSaveText');
 transcript.hidden = !recording.result;
 transcript.onclick = () => { if (ventureDictationCurrent(recording)) download(new Blob([recording.result.text], {type:'text/plain'}), ventureDictationAudioName(recording,'txt')); };
 $('ventureDictationDiscard').textContent = 'Discard recording';
 venturePaintStatus();
}
async function ventureSaveDictationAudio(recording) {
 if (!ventureDictationCurrent(recording) || recording.exporting) return;
 const blob = ventureDictationAudio(recording); if (!blob) return;
 const extension = ventureDictationAudioExtension(blob);
 if (extension === 'm4a' || extension === 'mp3') {
  download(blob, ventureDictationAudioName(recording, extension)); return;
 }
 if (recording.mp3) { download(recording.mp3, ventureDictationAudioName(recording,'mp3')); return; }
 // Reuse the already-bundled, offline FFmpeg worker. No PC or cloud request.
 recording.exporting = true; recording.exportController = new AbortController();
 const button = $('ventureDictationSave'), signal = recording.exportController.signal;
 const message = $('ventureDictationStatus').textContent;
 button.disabled = true; button.textContent = 'Preparing MP3 on this device…';
 const timeout = setTimeout(() => { recording.exportController.abort(); recording.decoder?.stop(); }, 120000);
 try {
  const wasmBinary = await embeddedBytes('ffmpeg-wasm-source', signal);
  if (!ventureDictationCurrent(recording) || signal.aborted) return;
  recording.decoder = decoderClient();
  await recording.decoder.request('init', {wasmBinary}, [wasmBinary.buffer]);
  const converted = await recording.decoder.request('transcription_chunk', {file:blob, start:0, end:VENTURE_DICTATION_LIMIT_MS/1000});
  if (!ventureDictationCurrent(recording) || signal.aborted) return;
  if (!converted.audio?.byteLength || converted.mimeType !== 'audio/mpeg') throw new Error('Invalid MP3 export');
  recording.mp3 = new Blob([converted.audio], {type:'audio/mpeg'});
  // Require a fresh click after asynchronous preparation for iOS download rules.
  button.textContent = 'Save audio (.mp3)';
  $('ventureDictationStatus').textContent = message + ' MP3 ready. Tap Save audio to download it.';
 } catch {
  if (ventureDictationCurrent(recording)) {
   button.textContent = 'Prepare MP3 again';
   $('ventureDictationStatus').textContent = message + ' MP3 conversion was unavailable. Save the original audio instead; it has not been changed.';
  }
 } finally {
  clearTimeout(timeout); recording.decoder?.stop(); recording.decoder = null; recording.exporting = false;
  if (ventureDictationCurrent(recording)) button.disabled = false;
 }
}
function ventureInsertDictation(recording) {
 if (!ventureDictationCurrent(recording) || !recording.result) return false;
 const field = $('ventureMessage'), text = recording.result.text.trim();
 const updated = field.value + (field.value && text ? ' ' : '') + text;
 if (field.maxLength > 0 && updated.length > field.maxLength) {
  ventureDictationShowRecovery(recording, 'Transcription completed. Shorten your draft to insert the retained transcript, or save the transcript below. No further transcription will run.', false);
  return false;
 }
 // Only the currently selected account/conversation may receive a result.
 field.value = updated;
 if (venture.current) venture.drafts.set(venture.current.id, updated);
 ventureStopDictation(); ventureResizeComposer();
 if (!text) ventureSetNotice('No speech was detected.');
 field.focus({preventScroll:true});
 return true;
}
async function ventureDictationAttempt(recording) {
 const provider = recording.provider;
 recording.controller = new AbortController();
 const signal = recording.controller.signal;
 const limit = provider === 'whisper' ? 700000 : 240000;
 const deadline = Date.now() + limit;
 const receiptPath = '/venture/dictation/' + encodeURIComponent(recording.requestId);
 const receipt = async () => (await ventureFetch(receiptPath, {signal})).json();
 let result;
 try {
  const response = await ventureFetch('/venture/dictation', {method:'POST', body:recording.form, signal, timeoutMs:limit});
  result = await response.json();
 } catch (error) {
  if (!ventureDictationCurrent(recording)) throw error;
  // Read once after a lost response. Never repeat the paid POST.
  try { result = await receipt(); } catch { throw error; }
 }
 while (result?.status === 'processing' && Date.now() < deadline) {
  if (!await ventureDictationPause(recording, 1500)) throw new DOMException('Canceled','AbortError');
  result = await receipt();
 }
 if (!ventureDictationCurrent(recording)) throw new DOMException('Canceled','AbortError');
 if (result?.status !== 'completed' || typeof result.text !== 'string') {
  throw new Error(result?.error || (provider === 'whisper' ? 'PC Whisper did not return a transcript.' : 'Gemini did not return a transcript.'));
 }
 return result;
}
async function ventureUploadDictation(recording) {
 if (!ventureDictationCurrent(recording) || recording.uploading || recording.sequenceStarted) return;
 recording.sequenceStarted = true;
 if (!recording.form) {
  const mime = (recording.recorder?.mimeType || recording.chunks?.[0]?.type || 'audio/webm').split(';')[0];
  recording.audio = new Blob(recording.chunks || [], {type:mime}); recording.chunks = [];
  if (!recording.audio.size) { ventureStopDictation(); ventureSetNotice('No microphone audio was recorded.'); return; }
  recording.form = new FormData();
  recording.form.append('audio', recording.audio, 'dictation.' + ventureDictationAudioExtension(recording.audio));
 }
 recording.audio ||= recording.form.get('audio');
 recording.uploading = true;
 try {
  for (let attempt = 0; attempt < 4; attempt++) {
   if (!ventureDictationCurrent(recording)) return;
   recording.provider = attempt === 0 ? 'gemini' : 'whisper';
   recording.localAttempt = attempt;
   const delay = attempt > 0 ? VENTURE_WHISPER_RETRY_MS[attempt-1] : 0;
   if (delay) {
    recording.phase = 'waiting'; ventureRecordingUI(recording, 'waiting');
    $('ventureDictationStatus').textContent = 'PC Whisper failed. Retrying in ' + (delay/1000) + ' seconds (attempt ' + attempt + ' of 3).';
    if (!await ventureDictationPause(recording, delay)) return;
   }
   if (!ventureDictationCurrent(recording)) return;
   recording.requestId = attempt === 0 ? recording.requestId || ventureId() : ventureId();
   recording.form.set('requestId', recording.requestId); recording.form.set('provider', recording.provider);
   recording.phase = 'processing'; ventureRecordingUI(recording, 'processing');
   if (attempt) $('ventureDictationStatus').textContent = 'Transcribing with PC Whisper (attempt ' + attempt + ' of 3)…';
   try {
    recording.result = await ventureDictationAttempt(recording);
    if (ventureDictationCurrent(recording)) ventureInsertDictation(recording);
    return;
   } catch (error) {
    if (!ventureDictationCurrent(recording)) return;
    recording.lastError = ventureError(error);
   }
  }
  ventureDictationShowRecovery(recording, 'Transcription failed. Gemini and all 3 PC Whisper attempts were unsuccessful. Save the audio clip below to transcribe it elsewhere. Keep this tab open until it is saved.');
 } finally { recording.uploading = false; }
}
function ventureResetAvatar() {
 if (ventureAvatarObjectURL) URL.revokeObjectURL(ventureAvatarObjectURL);
 ventureAvatarObjectURL = null;
 venture.profile = null;
}
async function ventureLoadProfile() {
 const epoch = venture.epoch;
 try {
  const profile = await ventureJSON('/venture/profile');
  if (epoch !== venture.epoch) return;
  if (profile.hasAvatar && (profile.avatarVersion !== venture.profile?.avatarVersion || !ventureAvatarObjectURL)) {
   const response = await ventureFetch('/venture/profile/avatar');
   const blob = await response.blob();
   if (epoch !== venture.epoch) return;
   if (ventureAvatarObjectURL) URL.revokeObjectURL(ventureAvatarObjectURL);
   ventureAvatarObjectURL = URL.createObjectURL(blob);
  } else if (!profile.hasAvatar && ventureAvatarObjectURL) {
   URL.revokeObjectURL(ventureAvatarObjectURL); ventureAvatarObjectURL = null;
  }
  venture.profile = profile; venturePaintAccount();
  $('ventureRemoveAvatar').hidden = !profile.hasAvatar;
 } catch (error) { if (epoch === venture.epoch) $('ventureAvatarStatus').textContent = ventureError(error); }
}
async function venturePrepareAvatar(file) {
 if (!file || !/^image\//.test(file.type) || /svg/i.test(file.type) || file.size > 20*1024*1024) throw new Error('Choose a JPEG, PNG or WebP photo under 20 MB.');
 const url = URL.createObjectURL(file);
 try {
  const image = new Image(); image.src = url; await image.decode();
  if (image.naturalWidth * image.naturalHeight > 64_000_000) throw new Error('Choose a photo no larger than 64 megapixels.');
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 256;
  const ctx = canvas.getContext('2d'), side = Math.min(image.naturalWidth, image.naturalHeight);
  ctx.drawImage(image, (image.naturalWidth-side)/2, (image.naturalHeight-side)/2, side, side, 0, 0, 256, 256);
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/webp', .82));
  if (!blob) throw new Error('This photo could not be processed. Use a JPEG or PNG.');
  return blob;
 } finally { URL.revokeObjectURL(url); }
}
async function ventureUploadAvatar(file) {
 if (!file) return;
 const epoch = venture.epoch;
 $('ventureAvatarStatus').textContent = 'Cropping and compressing…'; $('ventureChooseAvatar').disabled = true;
 try {
  const blob = await venturePrepareAvatar(file);
  if (epoch !== venture.epoch) return;
  const form = new FormData(); form.append('avatar', blob, 'avatar.webp');
  await ventureFetch('/venture/profile', {method:'PUT', body:form});
  if (epoch !== venture.epoch) return;
  await ventureLoadProfile(); $('ventureAvatarStatus').textContent = 'Profile picture saved for this account.';
 } catch(error) { if (epoch === venture.epoch) $('ventureAvatarStatus').textContent = error.message || 'This photo could not be processed.'; }
 finally { if (epoch === venture.epoch) $('ventureChooseAvatar').disabled = false; }
}
async function ventureRemoveAvatar() {
 const epoch = venture.epoch;
 try {
  await ventureJSON('/venture/profile', 'DELETE');
  if (epoch !== venture.epoch) return;
  ventureResetAvatar(); await ventureLoadProfile(); $('ventureAvatarStatus').textContent = 'Using your account picture again.';
 } catch(error) { if (epoch === venture.epoch) $('ventureAvatarStatus').textContent = ventureError(error); }
}
async function ventureShowStorage() {
 const epoch = venture.epoch;
 try {
  const info = await ventureJSON('/venture/storage');
  if (epoch !== venture.epoch) return;
  $('ventureStoragePath').textContent = info.conversationsDirectory;
  $('ventureStorageDetails').hidden = false;
 } catch(error) { if (epoch === venture.epoch) $('ventureAvatarStatus').textContent = ventureError(error); }
}
async function ventureDeleteConversation() {
 const item = ventureRenameTarget, epoch = venture.epoch;
 if (!item || !confirm('Permanently delete “'+item.title+'” and its saved conversation files? It will no longer be used for past-conversation memory. This cannot be undone.')) return;
 $('ventureDeleteConversation').disabled = true;
 try {
  const result = await ventureJSON(venturePath(item.id), 'DELETE', {});
  if (epoch !== venture.epoch) return;
  venture.drafts.delete(item.id); ventureRemember('pending-'+item.id, null);
  if (venture.current?.id === item.id) {
   venture.selection++; ventureStopDictation(); clearTimeout(venture.poll);
   ventureRemember('selection', null); consoleClosePreview(); ventureBlank();
  }
  $('ventureRenameDialog').close(); await ventureLoadHistory();
  if (result.storageWarning) ventureSetNotice(result.storageWarning);
 } catch(error) { if (epoch === venture.epoch) $('ventureRenameStatus').textContent = ventureError(error); }
 finally { $('ventureDeleteConversation').disabled = false; }
}
