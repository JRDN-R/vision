/* User-supplied Menu in out Lottie. Keep its entrance, hold and exit separate.
 * The renderer and font are bundled so portable HTML needs no asset requests. */
(() => {
 const data = /* VISION_MENU_ANIMATION */ {};
 const reduced = matchMedia('(prefers-reduced-motion: reduce)');
 window.VisionMenuMotion = {create(host) {
  if (!window.lottie) return {setOpen(open) {host.style.opacity=open?'0':'1';}};
  let open=false,ready=false;
  const animation=window.lottie.loadAnimation({container:host,renderer:'svg',loop:false,autoplay:false,
   animationData:JSON.parse(JSON.stringify(data)),rendererSettings:{preserveAspectRatio:'xMidYMid meet',hideOnTransparent:false}});
  animation.setSpeed(1.6);
  function settle() {
   animation.playSegments([0,120],true);
   animation.goToAndStop(open?119:60,true);
   host.style.opacity=open?'0':'1';host.dataset.phase=open?'hidden':'visible';
  }
  function setOpen(next) {
   next=!!next;if(next===open)return;open=next;
   if(!ready)return;
   if(reduced.matches){settle();return;}
   host.style.opacity='1';host.dataset.phase=open?'exit':'enter';
   animation.playSegments(open?[80,120]:[0,51],true);
  }
  animation.addEventListener('DOMLoaded',()=>{ready=true;settle();host.querySelector('svg')?.setAttribute('aria-hidden','true');});
  animation.addEventListener('complete',settle);
  reduced.addEventListener('change',()=>{if(ready)settle();});
  return {setOpen};
 }};
})();
