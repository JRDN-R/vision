import {BACKEND, accountEpoch, authError, initAuth, request, signOut, user} from './auth.js?v=1.0.2';

const $ = id => document.getElementById(id);
const ACTIVE = new Set(['queued', 'processing']);
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const state = {jobs:[], olderJobs:new Map(), cursor:null, olderLoaded:false, revision:0, selected:null, inspectId:'', inspectInput:'', appliedInspect:'', quality:'balanced', pending:false, menu:false, timer:0, polling:false, pollEpoch:0, failures:0, actionId:'', actionEpoch:0, share:null, ticket:null, terminal:'', terminalTimer:0, animation:null, requestIds:new Map()};
Object.assign(state, {profile:null, avatarURL:'', profileLoading:false, selectedQuality:'balanced', qualityTimer:0, quietInspection:false, qualityRefreshing:false});
Object.assign(state, {downloadMode:'video', videoFormat:'mp4', audioFormat:'m4a', searchQuery:'', searchNextPage:null, searchSeen:new Set(), searchBusy:false, appendSearch:false, searchFailed:false, lookupGeneration:0});
Object.assign(state, {queuedDownload:null, submittingDownload:null, autoSaveJobs:new Map(), readyDownload:null, downloadError:''});
Object.assign(state, {sessionJobs:new Set(), deletedIds:new Set(), historyLoaded:false, visitEpoch:0});
const searchObserver = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
  if (entries.some(entry => entry.isIntersecting) && !state.searchFailed) void loadMoreResults();
}, {rootMargin:'240px'}) : null;
const icons = {
  media:'<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="m10 8 6 4-6 4Z"/></svg>',
  more:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>'
};
function safeURL(value) { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : ''; } catch { return ''; } }
function jobPath(id) { if (!/^[\w-]{1,120}$/.test(id || '')) throw new Error('Invalid media job.'); return '/vortex/jobs/' + encodeURIComponent(id); }
function getJob(id) { return state.jobs.find(job => job.id === id); }
function seconds(value) { return Number.isFinite(Number(value)) ? Number(value) * 1000 : 0; }
function sizeLabel(value) { const n = Number(value); if (!(n > 0)) return ''; if (n < 1024) return Math.round(n) + ' B'; const unit = n >= 1073741824 ? 1073741824 : n >= 1048576 ? 1048576 : 1024; return (n / unit).toFixed(n / unit < 10 ? 1 : 0) + ' ' + (unit === 1073741824 ? 'GB' : unit === 1048576 ? 'MB' : 'KB'); }
function durationLabel(value) { const n = Math.round(Number(value)); if (!(n > 0)) return ''; return n >= 3600 ? Math.floor(n / 3600) + ':' + String(Math.floor(n % 3600 / 60)).padStart(2, '0') + ':' + String(n % 60).padStart(2, '0') : Math.floor(n / 60) + ':' + String(n % 60).padStart(2, '0'); }
function expiryLabel(value) { const remaining = seconds(value) - Date.now(); if (!(remaining > 0)) return 'Retention ended'; const minutes = Math.ceil(remaining / 60000), days = Math.floor(minutes / 1440), hours = Math.floor(minutes % 1440 / 60); return 'Deletes in ' + (days ? days + 'd ' + hours + 'h' : hours ? hours + 'h ' + minutes % 60 + 'm' : minutes + 'm'); }
function sourceLabel(media) { if (media?.source) return String(media.source); try { return new URL(media?.url).hostname.replace(/^www\./, ''); } catch { return ''; } }
function titleFor(job) { return job.media?.title || job.title || (job.kind === 'inspect' ? 'Finding media' : job.input) || 'Media download'; }
function notice(message = '', error = false) { $('notice').textContent = message; $('notice').hidden = !message; $('notice').classList.toggle('error', error); }
// Web and Windows deployments are independent; never infer the server release
// from the menu label or keep a previously connected server's version on error.
function showServerVersion(value, connected = false) {
  const valid = typeof value === 'string' && /^\d{1,4}\.\d{1,4}\.\d{1,4}$/.test(value);
  $('serverVersion').textContent = valid ? 'Vortex server: v' + value : connected ? 'Vortex server version not reported.' : 'Vortex server version unavailable.';
}
function rememberInspection(id, input = '') { state.inspectId = id; state.inspectInput = id ? input : ''; try { if (user()) sessionStorage.setItem('vortex-inspection:' + user().uid, id); } catch {} }
function upsert(job) { if (!job?.id) throw new Error('FUPCJ Server did not confirm this media job.'); const i = state.jobs.findIndex(item => item.id === job.id); if (i < 0) state.jobs.unshift(job); else state.jobs[i] = job; }
function imageSource(image, value) { const url = safeURL(value); image.hidden = !url; if (url && image.getAttribute('src') !== url) { image.src = url; image.referrerPolicy = 'no-referrer'; } if (!url) image.removeAttribute('src'); image.onerror = () => { image.hidden = true; }; }

// The shell is safe to paint immediately, but remains inert until Firebase
// finishes restoring the shared Vision session. Never show sign-in while pending.
function settleAuth(identity) {
  $('authRestoring').hidden = true;
  $('authGate').hidden = !!identity;
  $('application').hidden = !identity;
  $('application').inert = !identity;
  $('application').removeAttribute('aria-busy');
}
function accountChanged(identity) {
  state.lookupGeneration++; resetSearch(); state.downloadMode = 'video'; state.videoFormat = 'mp4'; state.audioFormat = 'm4a';
  clearTimeout(state.timer); clearTimeout(state.terminalTimer); state.pollEpoch++; state.polling = false;
  state.jobs = []; state.selected = null; state.inspectId = ''; state.inspectInput = ''; state.appliedInspect = ''; state.actionId = ''; state.actionEpoch++; state.share = null; state.ticket = null; state.terminal = ''; state.pending = false; state.failures = 0; state.requestIds.clear();
  // A click-to-save intent belongs only to the account and tab that created it.
  state.queuedDownload = null; state.submittingDownload = null; state.autoSaveJobs.clear(); state.readyDownload = null; state.downloadError = '';
  state.olderJobs.clear(); state.cursor = null; state.olderLoaded = false; state.sessionJobs.clear(); state.deletedIds.clear(); state.historyLoaded = false; state.visitEpoch++; $('loadOlder').hidden = true; $('loadOlder').disabled = false;
  clearTimeout(state.qualityTimer); state.quietInspection = false; state.qualityRefreshing = false; $('selection').removeAttribute('aria-busy');
  if (state.avatarURL) URL.revokeObjectURL(state.avatarURL);
  state.avatarURL = ''; state.profile = null; state.profileLoading = false;
  closeMenu(false);
  for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
  $('sourceInput').value = ''; $('activityList').replaceChildren(); $('historyList').replaceChildren(); $('resultList').replaceChildren(); $('historyCount').textContent = ''; $('historyEmpty').hidden = false; $('historyEmpty').textContent = 'Loading history…'; $('historyStatus').textContent = '';
  $('accountName').textContent = ''; $('accountEmail').textContent = ''; $('profileImage').removeAttribute('src'); $('profileImage').hidden = true;
  $('selection').hidden = true; $('searchResults').hidden = true; $('processing').hidden = true; $('emptyActivity').hidden = false; $('connectionStatus').textContent = ''; $('downloadButton').disabled = false; $('sourceSubmit').disabled = false;
  renderDownloadStatus();
  showServerVersion();
  notice();
  settleAuth(identity);
  if (!identity) {
    $('authGate').hidden = true;
    $('authRestoring').hidden = false;
    $('authRestoring').textContent = 'Opening Vision sign-in…';
    location.replace($('visionSignIn').href);
    return;
  }
  $('profileInitial').textContent = (identity.displayName || identity.email || 'V').slice(0, 1).toUpperCase();
  $('accountName').textContent = identity.displayName || 'Your Vision account'; $('accountEmail').textContent = identity.email || '';
  try { state.inspectId = sessionStorage.getItem('vortex-inspection:' + identity.uid) || ''; } catch {}
  void refresh(); void loadProfile();
}
async function loadProfile() {
  if (!user() || state.profileLoading) return;
  const epoch = accountEpoch(); state.profileLoading = true;
  try {
    const profile = await request('/venture/profile');
    if (epoch !== accountEpoch()) return;
    if (profile.hasAvatar && (profile.avatarVersion !== state.profile?.avatarVersion || !state.avatarURL)) {
      const blob = await request('/venture/profile/avatar', {binary:true});
      if (epoch !== accountEpoch()) return;
      if (state.avatarURL) URL.revokeObjectURL(state.avatarURL);
      state.avatarURL = URL.createObjectURL(blob);
    } else if (!profile.hasAvatar && state.avatarURL) {
      URL.revokeObjectURL(state.avatarURL); state.avatarURL = '';
    }
    state.profile = profile;
    if (state.avatarURL) { $('profileImage').src = state.avatarURL; $('profileImage').hidden = false; }
    else imageSource($('profileImage'), user()?.photoURL);
  } catch { /* Keep this account's initials or saved photo when the PC is offline. */ }
  finally { if (epoch === accountEpoch()) state.profileLoading = false; }
}
async function loadMenuAnimation() {
  try {
    const response = await fetch('../web/animations/menu-in-out.json');
    if (!response.ok || !window.lottie) return;
    const data = await response.json(), host = $('menuGlyph');
    // Preserve the supplied movement and paths, changing only its Vortex accent.
    const tint = value => { if (!value || typeof value !== 'object') return; if (['st', 'fl'].includes(value.ty) && value.c?.a === 0) value.c.k = [209 / 255, 186 / 255, 162 / 255, 1]; for (const child of Object.values(value)) tint(child); };
    tint(data);
    host.replaceChildren(); host.dataset.animated = 'true';
    const animation = window.lottie.loadAnimation({container:host, renderer:'svg', loop:false, autoplay:false, animationData:data, rendererSettings:{preserveAspectRatio:'xMidYMid meet', hideOnTransparent:false}});
    animation.setSpeed(1.6);
    const settle = () => { animation.playSegments([0, 120], true); animation.goToAndStop(state.menu ? 119 : 60, true); host.style.opacity = state.menu ? '0' : '1'; };
    animation.addEventListener('DOMLoaded', settle); animation.addEventListener('complete', settle);
    state.animation = {setOpen(open) { if (reducedMotion.matches) { settle(); return; } host.style.opacity = '1'; animation.playSegments(open ? [80, 120] : [0, 51], true); }};
    reducedMotion.addEventListener('change', settle);
  } catch { /* The line-only hamburger remains usable without animation. */ }
}
function setMenu(open, focus = true) {
  state.menu = !!open && !!user(); $('navigation').hidden = !state.menu; $('navigation').inert = !state.menu; $('menuScrim').hidden = !state.menu;
  $('mainContent').inert = state.menu; document.querySelector('.topbar').inert = state.menu;
  $('menuButton').setAttribute('aria-expanded', String(state.menu)); document.body.classList.toggle('menu-open', state.menu); state.animation?.setOpen(state.menu);
  if (focus) (state.menu ? $('closeMenu') : $('menuButton')).focus({preventScroll:true});
}
function closeMenu(focus = true) { setMenu(false, focus); }
function openAccount() { closeMenu(false); $('accountDialog').showModal(); void loadProfile(); }
function confirmAction(description, title = 'Are you sure?') {
  const dialog = $('confirmDialog'); $('confirmTitle').textContent = title; $('confirmDescription').textContent = description;
  return new Promise(resolve => {
    let accepted = false;
    const cleanup = () => { $('confirmYes').onclick = null; $('confirmNo').onclick = null; resolve(accepted); };
    dialog.addEventListener('close', cleanup, {once:true});
    $('confirmYes').onclick = () => { accepted = true; dialog.close(); }; $('confirmNo').onclick = () => dialog.close(); dialog.showModal(); $('confirmNo').focus();
  });
}
async function signOutAction() { closeMenu(false); if (!await confirmAction('Sign out of your Vision account?')) return; try { await signOut(); } catch (error) { notice(authError(error), true); } }

function scheduleRefresh(delay) { clearTimeout(state.timer); if (user() && !document.hidden) state.timer = setTimeout(() => void refresh(), delay); }
async function refresh() {
  if (!user() || state.polling) return;
  state.polling = true; const epoch = accountEpoch(), generation = state.pollEpoch, revision = state.revision;
  $('refreshButton').disabled = true;
  try {
    const inspectId = state.inspectId;
    const [history, inspection] = await Promise.allSettled([
      request('/vortex/jobs?kind=download'),
      inspectId ? request(jobPath(inspectId)) : Promise.resolve(null)
    ]);
    if (epoch !== accountEpoch() || generation !== state.pollEpoch) return;
    if (history.status === 'rejected') throw history.reason;
    const data = history.value;
    if (!Array.isArray(data.jobs)) throw new Error('FUPCJ Server returned an incomplete media history.');
    const merged = new Map([...state.olderJobs].filter(([id]) => !state.deletedIds.has(id)));
    for (const job of data.jobs) if (!state.deletedIds.has(job.id)) merged.set(job.id, job);
    // A job accepted while this GET was in flight must not disappear until the
    // next poll merely because the earlier snapshot did not contain it yet.
    if (revision !== state.revision) for (const job of state.jobs) if (!state.deletedIds.has(job.id) && !merged.has(job.id)) merged.set(job.id, job);
    if (state.inspectId !== inspectId) { const selectedInspection = getJob(state.inspectId); if (selectedInspection) merged.set(selectedInspection.id, selectedInspection); }
    state.jobs = [...merged.values()]; state.failures = 0; state.historyLoaded = true; $('historyStatus').textContent = '';
    if (inspection.status === 'fulfilled' && inspection.value && state.inspectId === inspectId) upsert(inspection.value);
    else if (inspection.status === 'rejected' && state.inspectId === inspectId) {
      if (inspection.reason?.status === 404) {
        rememberInspection(''); finishQualityRefresh(false);
        if (state.appendSearch) searchFailure('This search page is unavailable. Try loading it again.');
        else notice('This media lookup is no longer available. Find the media again.', true);
      } else if (!state.appliedInspect) notice(inspection.reason.message, true);
    }
    if (!state.olderLoaded) state.cursor = data.nextCursor || null;
    $('loadOlder').hidden = !state.cursor;
    $('connectionStatus').textContent = 'Connected to FUPCJ Server'; $('connectionStatus').classList.remove('offline');
    showServerVersion(data.vortexVersion, true);
    applyInspection(); renderJobs(); renderProcessing(); checkAutoSaves(); renderDownloadStatus();
  } catch (error) {
    if (epoch !== accountEpoch() || generation !== state.pollEpoch) return;
    showServerVersion();
    state.failures++; $('connectionStatus').textContent = error.message; $('connectionStatus').classList.add('offline'); $('historyStatus').textContent = 'Could not refresh history: ' + error.message; renderHistory();
  } finally {
    if (epoch === accountEpoch() && generation === state.pollEpoch) { state.polling = false; $('refreshButton').disabled = false; scheduleRefresh(state.failures ? Math.min(30000, 5000 * state.failures) : state.jobs.some(job => ACTIVE.has(job.status)) ? 2000 : 15000); }
  }
}
async function loadOlder() {
  if (!state.cursor || !user()) return;
  const epoch = accountEpoch(), cursor = state.cursor; $('loadOlder').disabled = true;
  try {
    const data = await request('/vortex/jobs?kind=download&cursor=' + encodeURIComponent(cursor));
    if (epoch !== accountEpoch()) return;
    if (!Array.isArray(data.jobs)) throw new Error('FUPCJ Server returned an incomplete media history.');
    for (const job of data.jobs) { if (state.deletedIds.has(job.id)) continue; state.olderJobs.set(job.id, job); upsert(job); }
    state.olderLoaded = true; state.cursor = data.nextCursor || null; $('loadOlder').hidden = !state.cursor; $('historyStatus').textContent = ''; renderJobs();
  } catch (error) { if (epoch === accountEpoch()) { $('historyStatus').textContent = error.message; notice(error.message, true); } }
  finally { if (epoch === accountEpoch()) $('loadOlder').disabled = false; }
}
async function submitJob(kind, input, quality = state.quality, options = {}) {
  const key = JSON.stringify([kind, input, quality, options]);
  let requestId = state.requestIds.get(key);
  if (!requestId) { requestId = crypto.randomUUID ? crypto.randomUUID() : 'vx-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2); state.requestIds.set(key, requestId); }
  const visit = state.visitEpoch;
  const job = await request('/vortex/jobs', {method:'POST', body:{kind, input, quality, ...options, requestId}});
  state.requestIds.delete(key); state.revision++; upsert(job); if (kind === 'download' && visit === state.visitEpoch) state.sessionJobs.add(job.id); return job;
}
async function inspect(input = $('sourceInput').value.trim(), {background = false, append = false, searchPage = 0} = {}) {
  if (!input || state.pending || !user()) return;
  const epoch = accountEpoch(), previous = getJob(state.inspectId), quality = state.quality, generation = ++state.lookupGeneration;
  state.pending = true; state.appliedInspect = ''; state.inspectId = ''; state.inspectInput = ''; state.quietInspection = background;
  if (!append) resetSearch();
  state.appendSearch = append;
  $('sourceSubmit').disabled = true; $('searchResults').hidden = !append;
  if (background) { state.qualityRefreshing = true; $('selection').setAttribute('aria-busy', 'true'); renderDownloadStatus(); }
  else if (!append) { clearTimeout(state.qualityTimer); state.qualityRefreshing = false; state.selected = null; $('selection').removeAttribute('aria-busy'); $('selection').hidden = true; notice('Sending to FUPCJ Server…'); }
  if (append) { state.searchBusy = true; state.searchFailed = false; renderSearchFooter(); }
  try {
    // Replaced lookups no longer need a queue slot. Download jobs are untouched.
    if (previous?.kind === 'inspect' && ACTIVE.has(previous.status)) {
      try { await request(jobPath(previous.id) + '/cancel', {method:'POST'}); } catch { /* The lookup may already have finished. */ }
      if (epoch !== accountEpoch() || generation !== state.lookupGeneration) return;
    }
    const job = await submitJob('inspect', input, quality, {searchPage});
    if (epoch !== accountEpoch() || generation !== state.lookupGeneration) return;
    rememberInspection(job.id, input); notice(); applyInspection(); renderJobs(); renderProcessing(); scheduleRefresh(500);
  } catch (error) { if (epoch === accountEpoch() && generation === state.lookupGeneration) { finishQualityRefresh(false); if (append) searchFailure(error.message); else notice(error.message, true); scheduleRefresh(1000); } }
  finally { if (epoch === accountEpoch() && generation === state.lookupGeneration) { state.pending = false; $('sourceSubmit').disabled = false; void flushDownloadQueue(); } }
}
function finishQualityRefresh(success = true) {
  if (!state.qualityRefreshing) return;
  state.qualityRefreshing = false; $('selection').removeAttribute('aria-busy');
  if (!success && state.selected) {
    // Never silently submit Balanced after the user explicitly queued Max.
    if (state.queuedDownload && !state.queuedDownload.fromRetry && state.queuedDownload.input === state.selected.url) {
      state.queuedDownload = null;
      state.downloadError = 'Quality lookup failed. Your queued download was not started. Please choose the quality and try again.';
    }
    state.quality = state.selectedQuality;
    for (const radio of document.querySelectorAll('input[name=quality]')) radio.checked = radio.value === state.quality;
  }
  renderDownloadStatus();
}
function queueQualityRefresh() {
  clearTimeout(state.qualityTimer);
  state.downloadError = '';
  state.qualityRefreshing = true; $('selection').setAttribute('aria-busy', 'true');
  renderDownloadStatus();
  state.qualityTimer = setTimeout(() => {
    if (!state.selected?.url || !user()) return;
    if (state.pending) { queueQualityRefresh(); return; }
    $('sourceInput').value = state.selected.url;
    void inspect(state.selected.url, {background:true});
  }, 150);
}
function applyInspection() {
  if (!state.inspectId) return;
  const job = getJob(state.inspectId); if (!job) return;
  const receipt = job.id + ':' + job.status;
  if (receipt === state.appliedInspect) return;
  if (state.quietInspection && job.quality !== state.quality) return;
  if (job.status === 'complete') {
    // The server canonicalizes shared/short URLs before storing job.input.
    // Match this accepted job to its submitted text as well as its canonical
    // input, while keeping late results from replacing a genuinely edited link.
    const input = $('sourceInput').value.trim();
    if (input && input !== state.inspectInput && input !== job.input) return;
    if (!$('sourceInput').value.trim()) $('sourceInput').value = job.input || '';
    if (['small', 'balanced', 'max'].includes(job.quality)) { state.quality = job.quality; for (const radio of document.querySelectorAll('input[name=quality]')) radio.checked = radio.value === job.quality; }
    state.appliedInspect = receipt;
    // A direct lookup also includes results:[media]. Prefer its full metadata;
    // search-only results have no selected media and require another lookup.
    if (job.media && safeURL(job.media.url)) selectMedia(job.media);
    else if (Array.isArray(job.results) && (job.results.length || state.appendSearch)) renderResults(job.results, job);
    else { finishQualityRefresh(false); notice('No downloadable media was found for that input. Try another link.', true); }
  } else if (job.status === 'error' || job.status === 'cancelled' || job.status === 'expired') {
    finishQualityRefresh(false);
    state.appliedInspect = receipt;
    const message = job.error || (job.status === 'cancelled' ? 'Media lookup cancelled.' : 'This lookup is no longer available. Search again.');
    if (state.appendSearch) searchFailure(message); else notice(message, job.status === 'error');
  }
}
function resetSearch() {
  searchObserver?.disconnect(); state.searchQuery = ''; state.searchNextPage = null; state.searchSeen.clear(); state.searchBusy = false; state.appendSearch = false; state.searchFailed = false;
  $('loadMoreResults').hidden = true; $('searchStatus').textContent = '';
}
function renderSearchFooter() {
  searchObserver?.disconnect();
  $('searchStatus').textContent = state.searchBusy ? 'Loading more results…' : state.searchFailed ? $('searchStatus').textContent : state.searchNextPage === null ? 'No more results for this search.' : '';
  $('loadMoreResults').hidden = state.searchNextPage === null;
  $('loadMoreResults').disabled = state.searchBusy;
  $('loadMoreResults').textContent = state.searchFailed ? 'Try loading more again' : 'Load more results';
  if (!state.searchBusy && !state.searchFailed && state.searchNextPage !== null) searchObserver?.observe($('loadMoreResults'));
}
function searchFailure(message) {
  state.searchBusy = false; state.searchFailed = true; $('searchStatus').textContent = message; renderSearchFooter();
}
async function loadMoreResults() {
  if (!user() || state.pending || state.searchBusy || state.searchNextPage === null || $('searchResults').hidden || !state.searchQuery) return;
  await inspect(state.searchQuery, {append:true, searchPage:state.searchNextPage});
}
function renderResults(results, job) {
  const list = $('resultList');
  if (!state.appendSearch) { list.replaceChildren(); state.searchSeen.clear(); }
  state.searchQuery = job.input; state.searchNextPage = Number.isInteger(job.searchNextPage) && job.searchNextPage > (job.searchPage || 0) ? job.searchNextPage : null;
  state.searchBusy = false; state.searchFailed = false; $('searchResults').hidden = false;
  for (const result of results) {
    const url = safeURL(result.url || result.webpage_url); if (!url || state.searchSeen.has(url)) continue;
    state.searchSeen.add(url);
    const row = document.createElement('button'); row.type = 'button'; row.className = 'search-result';
    row.append(thumbnail(result.thumbnail));
    const detail = document.createElement('span'); detail.className = 'item-detail';
    const title = document.createElement('span'); title.className = 'item-title'; title.textContent = result.title || url;
    const meta = document.createElement('span'); meta.className = 'item-meta'; meta.style.display = 'block'; meta.textContent = [sourceLabel(result), result.uploader, durationLabel(result.duration)].filter(Boolean).join(' · ');
    detail.append(title, meta); row.append(detail); row.onclick = () => { $('sourceInput').value = url; void inspect(url); }; list.append(row);
  }
  renderSearchFooter();
  if (!list.children.length) { $('searchResults').hidden = true; notice('No matching media was found. Try another search.', true); }
}
function gcd(a, b) { return b ? gcd(b, a % b) : a; }
function codec(value) { if (!value || value === 'none' || value === 'unknown') return ''; const text = String(value); return /^avc[13]/i.test(text) ? 'H.264' : /^(hevc|hev1|hvc1)/i.test(text) ? 'H.265' : /^av01/i.test(text) ? 'AV1' : /^mp4a/i.test(text) ? 'AAC' : /^vp0?9/i.test(text) ? 'VP9' : text; }
function selectMedia(media) {
  finishQualityRefresh(); state.selectedQuality = state.quality;
  const sameMedia = state.selected?.url === media.url;
  if (!sameMedia) state.downloadMode = media.mediaType === 'audio' ? 'audio' : 'video';
  state.selected = media; $('selection').hidden = false; $('searchResults').hidden = true;
  $('selectionTitle').textContent = media.title || 'Selected media'; $('selectionSource').textContent = [sourceLabel(media), durationLabel(media.duration)].filter(Boolean).join(' · '); imageSource($('selectionImage'), media.thumbnail);
  sourceLink($('selectionSourceLink'), media.url, media.title);
  const info = $('mediaInfo'); info.replaceChildren(); const entries = [];
  const audio = media.mediaType === 'audio' || (media.vcodec === 'none' && media.acodec && media.acodec !== 'none');
  if (!audio && Number(media.width) > 0 && Number(media.height) > 0) { const w = Math.round(media.width), h = Math.round(media.height), factor = gcd(w, h); entries.push(['Aspect ratio', w / factor + ':' + h / factor], ['Resolution', w + ' × ' + h]); }
  if (!audio && Number(media.fps) > 0) entries.push(['Frame rate', Number(media.fps).toFixed(2).replace(/\.00$/, '') + ' fps']);
  if (!audio && codec(media.vcodec)) entries.push(['Video codec', codec(media.vcodec)]);
  if (codec(media.acodec)) entries.push(['Audio codec', codec(media.acodec)]);
  if (audio && Number(media.abr) > 0) entries.push(['Bitrate', Math.round(Number(media.abr)) + ' kbps']);
  if (audio && Number(media.asr) > 0) entries.push(['Sample rate', Number(media.asr) / 1000 + ' kHz']);
  if (audio && Number(media.audioChannels) > 0) entries.push(['Channels', Number(media.audioChannels) === 2 ? 'Stereo' : Number(media.audioChannels) === 1 ? 'Mono' : String(media.audioChannels)]);
  if (!entries.length) { const empty = document.createElement('div'); empty.className = 'metadata-empty'; empty.textContent = 'This source did not report technical details.'; info.append(empty); }
  for (const [label, value] of entries) { const cell = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = label; dd.textContent = value; cell.append(dt, dd); info.append(cell); }
  renderOutputControls();
  $('mediaNote').hidden = !media.note; $('mediaNote').textContent = media.note || ''; notice();
  renderDownloadStatus(); void flushDownloadQueue();
}
function renderOutputControls() {
  const media = state.selected; if (!media) return;
  const original = ['image', 'gallery'].includes(media.mediaType), audio = media.mediaType === 'audio' || state.downloadMode === 'audio';
  if (media.mediaType === 'audio') state.downloadMode = 'audio';
  $('downloadModeControls').hidden = original || media.mediaType === 'audio';
  for (const radio of document.querySelectorAll('input[name=downloadMode]')) radio.checked = radio.value === state.downloadMode;
  $('outputFormatControl').hidden = original;
  const formats = audio ? ['m4a', 'mp3', 'wav'] : ['mp4', 'mov'];
  $('outputFormat').replaceChildren(...formats.map(value => { const option = document.createElement('option'); option.value = value; option.textContent = value.toUpperCase(); return option; }));
  $('outputFormat').value = audio ? state.audioFormat : state.videoFormat;
  $('qualityControls').hidden = audio || original; $('originalAudio').hidden = !audio && !original;
  $('originalAudio').textContent = original ? media.mediaType === 'image' ? 'Original image' : 'Images stay original · videos use MP4' : 'Highest available audio source';
  renderDownloadStatus();
}
// A user's click is a durable, idempotent intent for the chosen format. The
// quality lookup may still be running, but it must never disable this button.
function downloadOptions() {
  return {downloadMode:state.selected?.mediaType === 'audio' ? 'audio' : state.downloadMode,
    videoFormat:state.selected?.mediaType === 'gallery' ? 'mp4' : state.videoFormat,
    audioFormat:state.audioFormat};
}
function downloadKey(input, quality, options) {
  return JSON.stringify([input, options.downloadMode === 'audio' ? 'max' : quality, options]);
}
function currentDownloadKey() {
  return state.selected?.url ? downloadKey(state.selected.url, state.quality, downloadOptions()) : '';
}
function qualityLabel(value) { return {small:'Small', balanced:'Balanced', max:'Max'}[value] || value; }
function discardQueuedDownload() {
  if (state.queuedDownload && !state.queuedDownload.fromRetry) state.queuedDownload = null;
  state.downloadError = '';
}
function renderDownloadStatus() {
  const button = $('downloadButton'), status = $('downloadStatus'), fallback = $('downloadFallback');
  const key = currentDownloadKey(), input = state.selected?.url;
  const queued = key && state.queuedDownload?.key === key, submitting = key && state.submittingDownload?.key === key;
  const running = key && [...state.autoSaveJobs.entries()].find(([, intent]) => intent.key === key);
  const ready = key && state.readyDownload?.key === key && seconds(state.readyDownload.expiresAt) > Date.now() + 5000 ? state.readyDownload : null;
  let message = '', working = false;
  if (queued) {
    message = state.qualityRefreshing ? 'Download queued · Checking ' + qualityLabel(state.quality) + ' quality…' : 'Download queued · Preparing your request…';
    working = true;
  } else if (submitting) {
    message = 'Submitting your download to FUPCJ Server…'; working = true;
  } else if (running) {
    const job = getJob(running[0]);
    const progress = Number.isFinite(Number(job?.progress)) && job?.progress != null ? ' · ' + Math.round(job.progress) + '%' : '';
    message = running[1].delivering ? 'File ready · Preparing secure download…' : (job?.phase || (job?.status === 'queued' ? 'Queued on FUPCJ Server' : 'Processing on FUPCJ Server')) + progress + ' · Saving automatically when ready';
    working = true;
  } else if (ready) {
    message = 'File ready. Download started automatically where supported. You can also save it below.';
  } else if (state.downloadError && input) {
    message = state.downloadError;
  } else if (state.qualityRefreshing && input) {
    message = 'Checking ' + qualityLabel(state.quality) + ' quality… You can tap Download now.';
    working = true;
  }
  button.disabled = false;
  button.dataset.working = String(working);
  status.textContent = message; status.hidden = !message;
  if (message) button.setAttribute('aria-describedby', 'downloadStatus');
  else button.removeAttribute('aria-describedby');
  fallback.hidden = !ready;
  if (ready) { fallback.href = ready.url; fallback.download = ready.filename; }
  else { fallback.removeAttribute('href'); fallback.removeAttribute('download'); }
}
function triggerBrowserSave(url, filename) {
  // The backend sets Content-Disposition: attachment. Cross-origin download
  // attributes alone are insufficient, especially on mobile Safari.
  const link = document.createElement('a');
  link.href = url; link.download = filename || 'media'; link.rel = 'noreferrer'; link.hidden = true;
  document.body.append(link);
  try { link.click(); } finally { link.remove(); }
}
async function flushDownloadQueue() {
  const intent = state.queuedDownload;
  if (!intent || !user() || state.pending || state.submittingDownload) return;
  if (!intent.fromRetry) {
    if (state.selected?.url !== intent.input || currentDownloadKey() !== intent.key) {
      state.queuedDownload = null; renderDownloadStatus(); return;
    }
    if (state.qualityRefreshing) return;
  }
  state.queuedDownload = null; state.submittingDownload = intent;
  const epoch = accountEpoch(); state.pending = true; renderDownloadStatus();
  try {
    // An older processor may ignore export settings and return MKV or Opus.
    const capability = await request('/vortex/capabilities');
    if (epoch !== accountEpoch()) return;
    if (!capability.videoFormats?.includes(intent.options.videoFormat) || !capability.audioFormats?.includes(intent.options.audioFormat)) {
      throw new Error('Update Vision PC on FUPCJ Server to enable MP4/MOV video and M4A/MP3/WAV audio downloads, then try again.');
    }
    const job = await submitJob('download', intent.input, intent.quality, intent.options);
    if (epoch !== accountEpoch()) return;
    state.autoSaveJobs.set(job.id, {...intent, delivering:false});
    notice('Download accepted. Keep Vortex open to save automatically when the file is ready; it will also remain in Download history.');
    renderJobs(); renderProcessing(); checkAutoSaves(); renderDownloadStatus(); scheduleRefresh(500);
  } catch (error) {
    if (epoch === accountEpoch()) { state.downloadError = error.message; notice(error.message, true); renderDownloadStatus(); scheduleRefresh(1000); }
  } finally {
    if (epoch === accountEpoch()) {
      state.pending = false;
      if (state.submittingDownload === intent) state.submittingDownload = null;
      renderDownloadStatus(); void flushDownloadQueue();
    }
  }
}
function downloadSelected(input = state.selected?.url, quality = state.quality, options = null) {
  if (!input || !user()) return;
  const fromRetry = options !== null;
  const chosen = options || downloadOptions(), effectiveQuality = chosen.downloadMode === 'audio' ? 'max' : quality;
  const key = downloadKey(input, effectiveQuality, chosen);
  if (!fromRetry && state.readyDownload?.key === key && seconds(state.readyDownload.expiresAt) > Date.now() + 5000) {
    triggerBrowserSave(state.readyDownload.url, state.readyDownload.filename);
    return;
  }
  // Repeated taps must not create duplicate server jobs or duplicate downloads.
  if (state.queuedDownload?.key === key || state.submittingDownload?.key === key ||
      [...state.autoSaveJobs.values()].some(intent => intent.key === key)) { renderDownloadStatus(); return; }
  state.downloadError = '';
  state.queuedDownload = {key, input, quality:effectiveQuality, options:chosen, fromRetry};
  renderDownloadStatus(); void flushDownloadQueue();
}
function validateTicketURL(job, ticket) {
  const url = new URL(ticket.url, BACKEND);
  if (url.origin !== new URL(BACKEND).origin || url.pathname !== '/api' + jobPath(job.id) + '/file' || url.username || url.password) {
    throw new Error('FUPCJ Server returned an invalid download address.');
  }
  return url;
}
function checkAutoSaves() {
  if (document.hidden) return; // Browser downloads should not fire from hidden tabs.
  for (const [id, intent] of state.autoSaveJobs) {
    if (intent.delivering) continue;
    const job = getJob(id);
    if (!job) continue;
    if (job.status === 'complete' && job.resultReady) {
      intent.delivering = true; renderDownloadStatus();
      void (async () => {
        const epoch = accountEpoch();
        try {
          const ticket = await request(jobPath(id) + '/ticket', {method:'POST'});
          if (epoch !== accountEpoch() || state.autoSaveJobs.get(id) !== intent) return;
          const url = validateTicketURL(job, ticket);
          state.readyDownload = {key:intent.key, input:intent.input, filename:job.filename || 'media', url:url.href, expiresAt:ticket.expiresAt};
          state.autoSaveJobs.delete(id); renderDownloadStatus();
          triggerBrowserSave(url.href, job.filename || 'media');
          notice('Your file is ready. The download should start automatically. If your browser blocks it, tap Save file below or use Download history.');
        } catch (error) {
          if (epoch === accountEpoch() && state.autoSaveJobs.get(id) === intent) {
            state.autoSaveJobs.delete(id);
            state.downloadError = 'File is ready, but automatic saving failed. Open Download history to save it.';
            notice('Automatic download could not start: ' + error.message + '. Use Download history to save the file.', true);
            renderDownloadStatus();
          }
        }
      })();
    } else if (['complete', 'error', 'cancelled', 'expired'].includes(job.status)) {
      state.autoSaveJobs.delete(id);
      state.downloadError = job.error || (job.status === 'complete' ? 'File was not available to save.' : 'Download ' + job.status + '.');
      notice('Automatic download was not started: ' + state.downloadError, true);
      renderDownloadStatus();
    }
  }
}

function thumbnail(value) {
  const wrap = document.createElement('span'); wrap.className = 'thumbnail'; wrap.innerHTML = icons.media;
  const url = safeURL(value);
  if (url) { const img = document.createElement('img'); img.className = 'thumbnail'; img.alt = ''; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; img.src = url; img.onerror = () => img.replaceWith(wrap); return img; }
  return wrap;
}
function sourceLink(link, value, title) {
  const url = safeURL(value); link.className = 'source-thumbnail'; link.target = '_blank'; link.rel = 'noopener noreferrer';
  if (url) { link.href = url; link.setAttribute('aria-label', 'Open ' + (title || 'original media') + ' at its source (new tab)'); }
  else { link.removeAttribute('href'); link.removeAttribute('aria-label'); }
}
function renderJobs() {
  const jobs = state.jobs.filter(job => job.kind === 'download' && state.sessionJobs.has(job.id)).sort((a, b) => Number(b.createdAt || 0) - Number(a.createdAt || 0));
  $('emptyActivity').hidden = jobs.length > 0;
  const list = $('activityList'), existing = new Map([...list.children].map(row => [row.dataset.id, row]));
  for (const [position, job] of jobs.entries()) {
    let row = existing.get(job.id);
    if (!row) {
      row = document.createElement('article'); row.className = 'activity-item'; row.dataset.id = job.id;
      row.append(document.createElement('a'));
      const detail = document.createElement('div'); detail.className = 'item-detail';
      for (const cls of ['item-title', 'item-meta', 'item-status', 'item-expiry']) { const line = document.createElement('p'); line.className = cls; detail.append(line); }
      const more = document.createElement('button'); more.className = 'icon-button'; more.type = 'button'; more.innerHTML = icons.more; more.setAttribute('aria-haspopup', 'dialog'); more.onclick = () => void openActions(job.id);
      row.append(detail, more);
      let timer = 0, origin = null;
      row.addEventListener('pointerdown', event => { if (event.pointerType === 'mouse' || event.target.closest('button,a')) return; origin = [event.clientX, event.clientY]; timer = setTimeout(() => { timer = 0; void openActions(job.id); }, 600); });
      const stop = () => { clearTimeout(timer); timer = 0; };
      row.addEventListener('pointermove', event => { if (origin && Math.hypot(event.clientX - origin[0], event.clientY - origin[1]) > 8) stop(); });
      row.addEventListener('pointerup', stop); row.addEventListener('pointercancel', stop);
      row.addEventListener('contextmenu', event => { if (event.target.closest('button,a')) return; event.preventDefault(); stop(); void openActions(job.id); });
    }
    existing.delete(job.id);
    const thumbURL = safeURL(job.media?.thumbnail || job.thumbnail);
    sourceLink(row.firstElementChild, job.media?.url || job.input, titleFor(job));
    if (row.dataset.thumbnail !== thumbURL) { row.firstElementChild.replaceChildren(thumbnail(thumbURL)); row.dataset.thumbnail = thumbURL; }
    row.classList.toggle('active', ACTIVE.has(job.status));
    row.querySelector('.item-title').textContent = titleFor(job);
    const audio = job.downloadMode === 'audio' || job.media?.mediaType === 'audio';
    row.querySelector('.item-meta').textContent = [sourceLabel(job.media) || job.source || job.engine, (job.format || job.filename?.split('.').pop() || (audio ? job.audioFormat : job.videoFormat) || '').toUpperCase(), audio ? 'Audio' : job.quality ? {small:'Small', balanced:'Balanced', max:'Max'}[job.quality] || job.quality : '', sizeLabel(job.size)].filter(Boolean).join(' · ');
    const status = row.querySelector('.item-status'); status.classList.toggle('error', job.status === 'error');
    const progress = typeof job.progress === 'number' && Number.isFinite(job.progress) ? ' · ' + Math.round(job.progress) + '%' : '';
    status.textContent = job.status === 'error' ? job.error || 'Download failed' : job.status === 'complete' ? 'Ready to save' : job.status === 'expired' ? 'File deleted after five days' : job.status === 'cancelled' ? 'Cancelled' : (job.phase || (job.status === 'queued' ? 'Queued on FUPCJ Server' : 'Processing on FUPCJ Server')) + progress;
    const expiry = row.querySelector('.item-expiry'); expiry.textContent = job.status === 'complete' && job.expiresAt ? expiryLabel(job.expiresAt) : '';
    row.querySelector('button').setAttribute('aria-label', 'Actions for ' + titleFor(job));
    // Keep existing nodes in place so polling never steals keyboard focus.
    if (list.children[position] !== row) list.insertBefore(row, list.children[position] || null);
  }
  for (const row of existing.values()) row.remove();
  renderHistory();
  if (state.actionId) { const job = getJob(state.actionId); if (!job && $('actionDialog').open) $('actionDialog').close(); }
}
function historyStatus(job) {
  if (job.status === 'complete') return job.resultReady ? 'Ready' : 'Finished';
  if (job.status === 'expired') return 'Expired';
  if (job.status === 'error') return 'Failed';
  if (job.status === 'cancelled') return 'Cancelled';
  if (job.status === 'queued') return 'Queued';
  const progress = job.progress == null ? NaN : Number(job.progress);
  return job.status === 'processing' ? (Number.isFinite(progress) && progress >= 0 ? Math.round(progress) + '%' : 'Processing') : 'Pending';
}
function historyDate(job) {
  const value = Number(job.completedAt || job.createdAt);
  return Number.isFinite(value) && value > 0 ? new Date(value * 1000).toLocaleDateString(undefined, {month:'short', day:'numeric'}) : '';
}
function renderHistory() {
  const jobs = state.jobs.filter(job => job.kind === 'download')
    .sort((a, b) => Number(b.createdAt || 0) - Number(a.createdAt || 0) || String(b.id).localeCompare(String(a.id)));
  $('historyEmpty').hidden = jobs.length > 0;
  $('historyEmpty').textContent = state.historyLoaded ? 'No downloads yet.' : 'Loading history…';
  $('historyCount').textContent = state.historyLoaded ? jobs.length + (state.cursor ? '+' : '') : '';
  const list = $('historyList'), existing = new Map([...list.children].map(row => [row.dataset.id, row]));
  for (const [position, job] of jobs.entries()) {
    let row = existing.get(job.id);
    if (!row) {
      row = document.createElement('div'); row.className = 'history-item'; row.dataset.id = job.id;
      row.setAttribute('role', 'listitem');
      const source = document.createElement('a'); source.className = 'history-source';
      const info = document.createElement('button'); info.className = 'history-info'; info.type = 'button';
      const title = document.createElement('span'); title.className = 'history-item-title';
      const meta = document.createElement('span'); meta.className = 'history-item-meta';
      info.append(title, meta); info.onclick = () => void openActions(job.id);
      const more = document.createElement('button'); more.className = 'icon-button history-actions'; more.type = 'button';
      more.innerHTML = icons.more; more.setAttribute('aria-haspopup', 'dialog'); more.onclick = () => void openActions(job.id);
      row.append(source, info, more);
    }
    existing.delete(job.id);
    const source = row.firstElementChild;
    sourceLink(source, job.media?.url || job.input, titleFor(job)); source.classList.add('history-source');
    const thumbURL = safeURL(job.media?.thumbnail || job.thumbnail);
    if (row.dataset.thumbnail !== thumbURL) { source.replaceChildren(thumbnail(thumbURL)); row.dataset.thumbnail = thumbURL; }
    row.querySelector('.history-item-title').textContent = titleFor(job);
    const format = (job.format || job.filename?.split('.').pop() || (job.downloadMode === 'audio' ? job.audioFormat : job.videoFormat) || '').toUpperCase();
    row.querySelector('.history-item-meta').textContent = [historyStatus(job), format, historyDate(job)].filter(Boolean).join(' · ');
    row.classList.toggle('active', ACTIVE.has(job.status));
    row.querySelector('.history-info').setAttribute('aria-label', 'Open actions for ' + titleFor(job));
    row.querySelector('.history-actions').setAttribute('aria-label', 'More actions for ' + titleFor(job));
    if (list.children[position] !== row) list.insertBefore(row, list.children[position] || null);
  }
  for (const row of existing.values()) row.remove();
}
function terminalText(text) {
  if (state.terminal === text) return;
  state.terminal = text; clearTimeout(state.terminalTimer); const line = $('terminalLine');
  if (reducedMotion.matches || !line.textContent) { line.textContent = text; line.className = ''; return; }
  line.className = 'depart';
  state.terminalTimer = setTimeout(() => { line.textContent = state.terminal; line.className = 'arrive'; }, 180);
}
function renderProcessing() {
  const inspectJob = getJob(state.inspectId), job = (!state.quietInspection && !state.appendSearch && inspectJob && ACTIVE.has(inspectJob.status) ? inspectJob : null) || state.jobs.find(item => item.kind === 'download' && state.sessionJobs.has(item.id) && ACTIVE.has(item.status));
  $('processing').hidden = !job;
  if (!job) return;
  terminalText(job.phase || (job.status === 'queued' ? 'Queued on FUPCJ Server' : 'Processing media on FUPCJ Server'));
  const progress = typeof job.progress === 'number' && Number.isFinite(job.progress) ? Math.max(0, Math.min(100, job.progress)) : null;
  $('progressPercent').textContent = progress === null ? '' : Math.round(progress) + '%';
  $('progressTrack').classList.toggle('indeterminate', progress === null);
  if (progress === null) { $('progressTrack').removeAttribute('aria-valuenow'); $('progressTrack').setAttribute('aria-valuetext', job.phase || 'Working; percentage not available'); $('progressFill').style.width = ''; }
  else { $('progressTrack').setAttribute('aria-valuenow', String(progress)); $('progressTrack').removeAttribute('aria-valuetext'); $('progressFill').style.width = progress + '%'; }
}

function updateReexportOptions(job) {
  const original = ['image', 'gallery'].includes(job.media?.mediaType);
  const sourceAudio = job.media?.mediaType === 'audio';
  if (sourceAudio) $('reexportMode').value = 'audio';
  $('reexportMode').disabled = sourceAudio;
  const audio = $('reexportMode').value === 'audio';
  $('reexportModeRow').hidden = original || sourceAudio;
  $('reexportFormatRow').hidden = original;
  $('reexportQualityRow').hidden = original || audio;
  $('reexportSubmit').textContent = original ? 'Download again' : 'Queue re-export';
  const formats = audio ? ['m4a', 'mp3', 'wav'] : ['mp4', 'mov'];
  const previouslyChosen = $('reexportFormat').value;
  const preferred = audio ? job.audioFormat || 'm4a' : job.videoFormat || 'mp4';
  $('reexportFormat').replaceChildren(...formats.map(value => {
    const option = document.createElement('option'); option.value = value; option.textContent = value.toUpperCase(); return option;
  }));
  $('reexportFormat').value = formats.includes(previouslyChosen) ? previouslyChosen : formats.includes(preferred) ? preferred : formats[0];
}
function toggleReexport() {
  const job = getJob(state.actionId);
  if (!job) return;
  if (!safeURL(job.media?.url || job.input)) { $('actionStatus').textContent = 'The original source is unavailable for re-export.'; return; }
  const open = $('reexportPanel').hidden;
  $('reexportPanel').hidden = !open;
  $('retryJob').setAttribute('aria-expanded', String(open));
  if (open) {
    updateReexportOptions(job);
    ($('reexportModeRow').hidden ? $('reexportFormatRow').hidden ? $('reexportSubmit') : $('reexportFormat') : $('reexportMode')).focus();
  }
}
function submitReexport() {
  const id = state.actionId, job = getJob(id), epoch = state.actionEpoch;
  if (!job || !user()) return;
  const input = safeURL(job.media?.url || job.input);
  if (!input) { $('actionStatus').textContent = 'The original source is unavailable for re-export.'; return; }
  const original = ['image', 'gallery'].includes(job.media?.mediaType);
  const mode = original ? (job.downloadMode || 'video') : job.media?.mediaType === 'audio' ? 'audio' : $('reexportMode').value;
  const format = $('reexportFormat').value;
  const options = {downloadMode:mode, videoFormat:mode === 'video' && !original ? format : job.videoFormat || 'mp4',
                   audioFormat:mode === 'audio' && !original ? format : job.audioFormat || 'm4a'};
  const quality = mode === 'audio' ? 'max' : original ? job.quality || 'balanced' : $('reexportQuality').value;
  const key = downloadKey(input, quality, options);
  $('reexportSubmit').disabled = true;
  $('actionStatus').textContent = 'Queuing your new export…';
  // The shared Vortex queue accepts the click now and submits on the processor's
  // schedule. Do not bypass its duplicate protection or auto-save handling.
  downloadSelected(input, quality, options);
  const queued = state.queuedDownload?.key === key || state.submittingDownload?.key === key ||
    [...state.autoSaveJobs.values()].some(intent => intent.key === key);
  if (!actionCurrent(id, epoch)) return;
  if (queued) { $('actionDialog').close(); if (state.menu) closeMenu(false); }
  else { $('actionStatus').textContent = state.downloadError || 'Unable to queue re-export. Please try again.'; $('reexportSubmit').disabled = false; }
}

function actionCurrent(id, epoch) { return !!user() && state.actionId === id && state.actionEpoch === epoch && $('actionDialog').open; }
async function prepareTicket(job, epoch = state.actionEpoch) {
  const ticket = await request(jobPath(job.id) + '/ticket', {method:'POST'});
  if (!actionCurrent(job.id, epoch)) return;
  const url = validateTicketURL(job, ticket);
  state.ticket = {url:url.href, expiresAt:ticket.expiresAt};
  const save = $('saveFile'); save.href = url.href; save.download = job.filename || 'media'; save.rel = 'noreferrer'; save.hidden = false;
  $('actionStatus').textContent = (job.expiresAt ? expiryLabel(job.expiresAt) + '. ' : '') + 'Download opens your file. On iPhone, use the browser’s Share menu to save to Files.';
}
async function openActions(id) {
  const job = getJob(id); if (!job || !user()) return;
  state.actionId = id; const epoch = ++state.actionEpoch; state.share = null; state.ticket = null;
  $('reexportPanel').hidden = true; $('reexportSubmit').disabled = false; $('retryJob').setAttribute('aria-expanded', 'false');
  $('reexportMode').value = job.media?.mediaType === 'audio' || job.downloadMode === 'audio' ? 'audio' : 'video';
  $('reexportQuality').value = ['small', 'balanced', 'max'].includes(job.quality) ? job.quality : 'balanced';
  updateReexportOptions(job);
  $('actionTitle').textContent = titleFor(job); $('actionStatus').textContent = job.error || job.phase || '';
  $('saveFile').hidden = true; $('saveFile').removeAttribute('href'); $('shareFile').hidden = true; $('shareFile').disabled = false; $('shareFile').textContent = 'Prepare to share';
  $('retryJob').hidden = ACTIVE.has(job.status) || job.kind === 'inspect' || !safeURL(job.media?.url || job.input); $('cancelJob').hidden = !ACTIVE.has(job.status); $('cancelJob').textContent = job.kind === 'inspect' ? 'Cancel lookup' : 'Cancel download';
  $('deleteJob').hidden = job.kind === 'inspect'; $('deleteJob').disabled = false; $('cancelJob').disabled = false;
  if (!$('actionDialog').open) $('actionDialog').showModal();
  if (job.status === 'complete' && job.resultReady) {
    $('actionStatus').textContent = 'Preparing your secure download…';
    // Avoid loading large videos into mobile memory. File sharing is opt-in and
    // capped; direct attachment delivery streams files of any accepted size.
    $('shareFile').hidden = !(navigator.share && navigator.canShare && Number(job.size) > 0 && Number(job.size) <= 32 * 1024 * 1024);
    try { await prepareTicket(job, epoch); } catch (error) { if (actionCurrent(id, epoch)) $('actionStatus').textContent = error.message; }
  }
}
async function shareAction() {
  const id = state.actionId, job = getJob(id), epoch = state.actionEpoch; if (!job) return;
  if (state.share) {
    // This call is made directly from the second tap, preserving iOS activation.
    try { await navigator.share({files:[state.share], title:titleFor(job)}); } catch (error) { if (error.name !== 'AbortError' && actionCurrent(id, epoch)) $('actionStatus').textContent = 'Sharing is unavailable here. Use Save file instead.'; }
    return;
  }
  $('shareFile').disabled = true; $('actionStatus').textContent = 'Preparing the file for your share sheet…';
  try {
    const blob = await request(jobPath(id) + '/file', {binary:true, timeout:90000});
    if (!actionCurrent(id, epoch)) return;
    if (blob.size > 32 * 1024 * 1024) throw new Error('Use Save file for this larger download.');
    const file = new File([blob], job.filename || 'media', {type:blob.type || 'application/octet-stream'});
    if (!navigator.canShare({files:[file]})) throw new Error('This file cannot be shared by your browser. Use Save file instead.');
    state.share = file; $('shareFile').textContent = 'Share file'; $('actionStatus').textContent = 'Ready. Tap Share file to open your share sheet.';
  } catch (error) { if (actionCurrent(id, epoch)) $('actionStatus').textContent = error.message; }
  finally { if (actionCurrent(id, epoch)) $('shareFile').disabled = false; }
}
async function cancelAction() {
  const id = state.actionId, epoch = accountEpoch(); $('cancelJob').disabled = true;
  try { await request(jobPath(id) + '/cancel', {method:'POST'}); if (epoch !== accountEpoch()) return; $('actionDialog').close(); await refresh(); }
  catch (error) { if (epoch === accountEpoch()) { $('actionStatus').textContent = error.message; $('cancelJob').disabled = false; } }
}
async function deleteAction() {
  const id = state.actionId, job = getJob(id), epoch = accountEpoch(); if (!job) return;
  if (!await confirmAction(ACTIVE.has(job.status) ? 'Remove “' + titleFor(job) + '” from the queue and delete its history?' : 'Delete “' + titleFor(job) + '” and its saved file and history?')) return;
  if (epoch !== accountEpoch()) return;
  $('deleteJob').disabled = true;
  try {
    await request(jobPath(id), {method:'DELETE'});
    if (epoch !== accountEpoch()) return;
    state.deletedIds.add(id); state.revision++; state.olderJobs.delete(id);
    state.sessionJobs.delete(id); state.autoSaveJobs.delete(id);
    if (state.readyDownload?.url && new URL(state.readyDownload.url, BACKEND).pathname === '/api' + jobPath(id) + '/file') state.readyDownload = null;
    state.jobs = state.jobs.filter(item => item.id !== id);
    if (state.cursor === id) { state.cursor = null; state.olderLoaded = false; state.olderJobs.clear(); scheduleRefresh(500); }
    $('loadOlder').hidden = !state.cursor; $('actionDialog').close();
    renderJobs(); renderProcessing(); renderDownloadStatus();
  }
  catch (error) { if (epoch === accountEpoch()) { $('actionStatus').textContent = error.message; $('deleteJob').disabled = false; } }
}

$('menuButton').onclick = () => setMenu(!state.menu); $('closeMenu').onclick = () => closeMenu(); $('menuScrim').onclick = () => closeMenu();
$('profileButton').onclick = openAccount; $('menuAccount').onclick = openAccount; $('menuSignOut').onclick = () => void signOutAction(); $('accountSignOut').onclick = () => void signOutAction();
for (const button of document.querySelectorAll('.close-dialog')) button.onclick = () => button.closest('dialog').close();
for (const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('click', event => { if (event.target !== dialog) return; const box = dialog.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close(); });
$('actionDialog').addEventListener('close', () => { state.actionId = ''; state.actionEpoch++; state.share = null; state.ticket = null; $('reexportPanel').hidden = true; $('saveFile').removeAttribute('href'); });
document.addEventListener('keydown', event => {
  if (!state.menu) return;
  if (event.key === 'Escape') { event.preventDefault(); closeMenu(); }
  if (event.key === 'Tab') { const elements = [...$('navigation').querySelectorAll('a,button')], first = elements[0], last = elements.at(-1); if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }
});
$('sourceForm').onsubmit = event => { event.preventDefault(); void inspect(); };
$('sourceInput').addEventListener('paste', () => { setTimeout(() => { const value = $('sourceInput').value.trim(); if (safeURL(value)) void inspect(value); }, 0); });
$('sourceInput').addEventListener('input', () => { if (state.searchQuery && $('sourceInput').value.trim() !== state.searchQuery) { resetSearch(); $('searchResults').hidden = true; } if (state.selected && $('sourceInput').value.trim() !== state.selected.url) { clearTimeout(state.qualityTimer); discardQueuedDownload(); finishQualityRefresh(); state.selected = null; $('selection').hidden = true; renderDownloadStatus(); } });
$('clearResults').onclick = () => { state.lookupGeneration++; state.pending = false; $('sourceSubmit').disabled = false; resetSearch(); rememberInspection(''); $('searchResults').hidden = true; };
$('loadMoreResults').onclick = () => void loadMoreResults();
for (const radio of document.querySelectorAll('input[name=quality]')) radio.onchange = () => { discardQueuedDownload(); state.quality = radio.value; if (state.selected?.url) queueQualityRefresh(); };
for (const radio of document.querySelectorAll('input[name=downloadMode]')) radio.onchange = () => { discardQueuedDownload(); state.downloadMode = radio.value; renderOutputControls(); };
$('outputFormat').onchange = () => { discardQueuedDownload(); state[state.downloadMode === 'audio' ? 'audioFormat' : 'videoFormat'] = $('outputFormat').value; renderDownloadStatus(); };
$('downloadButton').onclick = () => downloadSelected(); $('refreshButton').onclick = () => void refresh();
$('downloadFallback').onclick = event => { if (!state.readyDownload || seconds(state.readyDownload.expiresAt) <= Date.now() + 5000) { event.preventDefault(); notice('This secure save link expired. Open Download history and choose Download to get a new link.', true); renderDownloadStatus(); } };
$('loadOlder').onclick = () => void loadOlder();
$('shareFile').onclick = () => void shareAction(); $('cancelJob').onclick = () => void cancelAction(); $('deleteJob').onclick = () => void deleteAction();
$('retryJob').onclick = toggleReexport;
$('reexportMode').onchange = () => { const job = getJob(state.actionId); if (job) updateReexportOptions(job); };
$('reexportSubmit').onclick = submitReexport;
$('saveFile').onclick = event => { if (!state.ticket || seconds(state.ticket.expiresAt) <= Date.now() + 5000) { event.preventDefault(); const job = getJob(state.actionId), epoch = state.actionEpoch; if (job) void prepareTicket(job, epoch).then(() => { if (actionCurrent(job.id, epoch)) $('actionStatus').textContent = 'Download refreshed. Tap Save file again.'; }).catch(error => { if (actionCurrent(job.id, epoch)) $('actionStatus').textContent = error.message; }); } };
window.addEventListener('pagehide', () => { state.visitEpoch++; state.sessionJobs.clear(); renderJobs(); renderProcessing(); });
window.addEventListener('online', () => void refresh()); window.addEventListener('offline', () => { showServerVersion(); $('connectionStatus').textContent = 'You are offline. Accepted jobs continue on FUPCJ Server.'; $('connectionStatus').classList.add('offline'); });
document.addEventListener('visibilitychange', () => { if (document.hidden) clearTimeout(state.timer); else { checkAutoSaves(); void refresh(); void loadProfile(); } }); window.addEventListener('pageshow', () => { if (user()) { checkAutoSaves(); void refresh(); void loadProfile(); } });
void loadMenuAnimation();
void initAuth(accountChanged).catch(error => { settleAuth(null); $('authStatus').textContent = authError(error); });
