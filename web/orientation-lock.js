/* Portrait-only mobile presentation shared by Vision, Venture and Vortex.
 * Installed apps/browsers that permit it receive a native orientation lock.
 * Safari and other restricted browsers get a non-destructive portrait guard.
 * A normal web page cannot override the device's physical rotation setting.
 */
(() => {
  'use strict';
  const GUARD_ID = 'mobilePortraitGuard';
  let nativeAttempted = false;

  function isMobileTouchDevice() {
    const coarse = window.matchMedia('(pointer: coarse)').matches;
    const touchOnly = navigator.maxTouchPoints > 0 &&
      window.matchMedia('(hover: none)').matches &&
      Math.min(screen.width || window.innerWidth, screen.height || window.innerHeight) <= 1100;
    return coarse || touchOnly;
  }

  function isLandscape() {
    return window.matchMedia('(orientation: landscape)').matches;
  }

  function ensureGuard() {
    let guard = document.getElementById(GUARD_ID);
    if (guard || !document.body) return guard;
    const style = document.createElement('style');
    style.id = 'mobilePortraitGuardStyle';
    style.textContent = [
      '#mobilePortraitGuard[hidden]{display:none!important}',
      '#mobilePortraitGuard{position:fixed;inset:0;z-index:2147483647;box-sizing:border-box;display:grid;place-items:center;overflow:auto;overscroll-behavior:contain;padding:16px max(20px,env(safe-area-inset-right,0px)) 16px max(20px,env(safe-area-inset-left,0px));background:#111715;color:#eef1e8;font:inherit;text-align:center}',
      '#mobilePortraitGuard .portrait-lock-inner{max-width:320px;margin:auto}',
      '#mobilePortraitGuard svg{display:block;width:34px;height:34px;margin:0 auto 10px;color:#c7e4ba}',
      '#mobilePortraitGuard h2{margin:0 0 7px;color:inherit;font:600 17px/1.3 system-ui,sans-serif;letter-spacing:0}',
      '#mobilePortraitGuard p{margin:0;color:#aebeb0;font:400 12px/1.5 system-ui,sans-serif}'
    ].join('\n');
    document.head.appendChild(style);
    guard = document.createElement('section');
    guard.id = GUARD_ID;
    guard.hidden = true;
    guard.setAttribute('role', 'status');
    guard.setAttribute('aria-live', 'polite');
    guard.setAttribute('aria-label', 'Portrait orientation required');
    guard.innerHTML = '<div class="portrait-lock-inner">' +
      '<svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="16" y="5" width="16" height="38" rx="4"/><path d="M22 38h4"/></svg>' +
      '<h2>Portrait view only</h2>' +
      '<p>Rotate your device upright to continue. Your work remains in place.</p></div>';
    document.body.appendChild(guard);
    return guard;
  }

  function refreshGuard() {
    const blocked = isMobileTouchDevice() && isLandscape();
    const guard = document.getElementById(GUARD_ID) || (blocked ? ensureGuard() : null);
    if (guard) guard.hidden = !blocked;
    document.documentElement.classList.toggle('mobile-portrait-required', blocked);
  }

  function requestNativeLock() {
    if (nativeAttempted || !isMobileTouchDevice() || document.visibilityState === 'hidden') return;
    if (typeof screen.orientation?.lock !== 'function') return;
    nativeAttempted = true;
    try {
      // May require an installed app or fullscreen. Unsupported contexts reject.
      Promise.resolve(screen.orientation.lock('portrait-primary')).then(refreshGuard, refreshGuard);
    } catch {
      // iOS Safari does not implement this API; keep the portrait guard instead.
    }
  }

  function resume() {
    nativeAttempted = false;
    requestNativeLock();
    refreshGuard();
  }

  function init() {
    resume();
    window.addEventListener('resize', refreshGuard, {passive: true});
    window.addEventListener('orientationchange', refreshGuard, {passive: true});
    window.addEventListener('pageshow', resume);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'hidden') resume();
    });
    document.addEventListener('fullscreenchange', resume);
    screen.orientation?.addEventListener?.('change', refreshGuard);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, {once: true});
  } else {
    init();
  }
})();
