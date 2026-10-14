/* Shared mobile touch gesture for the Vision, Venture, and Vortex menus.
 * Observe touch events without blocking native scrolling or pinch gestures.
 * Fast, intentional horizontal swipes change the menu; ordinary drags do not.
 */
(function () {
 'use strict';
 const mobile = matchMedia('(max-width:760px), (hover:none) and (pointer:coarse)');
 const blocked = 'dialog[open],input,textarea,select,[contenteditable],[role="textbox"],[role="slider"],[data-mobile-menu-swipe-ignore]';

 function install({enabled, isOpen, open, close, ignoreOpeningFrom = () => false}) {
  let gesture = null, suppressClick = null;

  function isBlocked(target, opening) {
   if (!(target instanceof Element) || target.closest(blocked)) return true;
   if (opening && ignoreOpeningFrom(target)) return true;
   // Never steal a horizontal carousel, code block, or sideways scrolling list.
   for (let node = target; node && node !== document.documentElement; node = node.parentElement) {
    if (node.scrollWidth > node.clientWidth + 12 && /auto|scroll/.test(getComputedStyle(node).overflowX)) return true;
   }
   return false;
  }
  function touchStart(event) {
   // A new touch is a new intent, not the click synthesized for the last swipe.
   suppressClick = null;
   gesture = null;
   if (event.touches.length !== 1 || !mobile.matches || !enabled() || document.querySelector('dialog[open]')) return;
   const touch = event.touches[0], opening = !isOpen();
   if (isBlocked(event.target, opening)) return;
   gesture = {id:touch.identifier, x:touch.clientX, y:touch.clientY, at:performance.now(), opening, axis:''};
  }
  function touchMove(event) {
   if (!gesture) return;
   if (event.touches.length !== 1 || event.touches[0].identifier !== gesture.id) { gesture = null; return; }
   const dx = event.touches[0].clientX - gesture.x, dy = event.touches[0].clientY - gesture.y;
   if (!gesture.axis && Math.hypot(dx, dy) > 16) {
    if (Math.abs(dy) > Math.abs(dx) * 1.05) { gesture = null; return; }
    if (Math.abs(dx) > Math.abs(dy) * 1.3) gesture.axis = 'horizontal';
   }
   if (gesture?.axis === 'horizontal' && Math.abs(dy) > Math.abs(dx) * .8) gesture = null;
  }
  function touchEnd(event) {
   const start = gesture;
   gesture = null;
   if (!start || event.touches.length || !mobile.matches || !enabled() || document.querySelector('dialog[open]')) return;
   const touch = Array.from(event.changedTouches).find(item => item.identifier === start.id);
   if (!touch) return;
   const dx = touch.clientX - start.x, dy = touch.clientY - start.y;
   if (Math.abs(dx) < 72 || Math.abs(dx) < Math.abs(dy) * 1.5 || performance.now() - start.at > 1000) return;
   if (start.opening && dx > 0 && !isOpen()) open();
   else if (!start.opening && dx < 0 && isOpen()) close();
   else return;
   // iOS can dispatch a synthetic click after touchend. Consume only the
   // click belonging to the swipe, never a subsequent intentional tap.
   suppressClick = {until:performance.now() + 450, fromX:start.x, fromY:start.y, toX:touch.clientX, toY:touch.clientY};
  }
  function swallowSwipeClick(event) {
   const swipe = suppressClick;
   if (!swipe) return;
   if (performance.now() > swipe.until) { suppressClick = null; return; }
   const nearStart = Math.hypot(event.clientX - swipe.fromX, event.clientY - swipe.fromY) < 80;
   const nearEnd = Math.hypot(event.clientX - swipe.toX, event.clientY - swipe.toY) < 80;
   if (!nearStart && !nearEnd) return;
   suppressClick = null;
   event.preventDefault();
   event.stopImmediatePropagation();
  }

  document.addEventListener('touchstart', touchStart, {capture:true, passive:true});
  document.addEventListener('touchmove', touchMove, {capture:true, passive:true});
  document.addEventListener('touchend', touchEnd, {capture:true, passive:true});
  document.addEventListener('touchcancel', () => { gesture = null; }, {capture:true, passive:true});
  document.addEventListener('click', swallowSwipeClick, true);
 }
 window.MobileMenuSwipe = {install};
})();
