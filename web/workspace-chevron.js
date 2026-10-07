/* Lottie 5.13.0 SVG player. Entrance and idle use separate playback segments.
 * The JSON markers are also usable by native Lottie segment controllers. */
(() => {
  const assets = /* VISION_CHEVRONS */ {};
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  function create(host, direction) {
    const data = assets[direction];
    const idle = data.markers.find(marker => marker.cm === 'gradient-loop');
    const startFrame = idle.tm, endFrame = idle.tm + idle.dr;
    let ready = false, active = false;
    const animation = window.lottie.loadAnimation({
      container: host, renderer: 'svg', loop: false, autoplay: false,
      animationData: JSON.parse(JSON.stringify(data)),
      rendererSettings: {preserveAspectRatio: 'xMidYMid meet', hideOnTransparent: false}
    });
    function start() {
      active = true;
      if (!ready) return;
      animation.loop = false;
      if (reduced.matches) {
        animation.playSegments([0, endFrame], true);
        animation.goToAndStop(startFrame, true);
      }
      else {
        animation.playSegments([0, startFrame], true);
        if (document.hidden) animation.pause();
      }
    }
    function pause() { active = false; animation.pause(); }
    function visibility() {
      if (document.hidden || !active || reduced.matches) animation.pause();
      else if (ready) animation.play();
    }
    function motion() {
      // Reset the segment origin before using an absolute frame.
      animation.loop = false;
      animation.playSegments([0, endFrame], true);
      animation.goToAndStop(startFrame, true);
      if (active && !reduced.matches) {
        animation.loop = true;
        animation.playSegments([startFrame, endFrame], true);
        visibility();
      }
    }
    animation.addEventListener('DOMLoaded', () => {
      ready = true;
      if (active) start(); else animation.goToAndStop(startFrame, true);
    });
    animation.addEventListener('complete', () => {
      if (!active || reduced.matches) return;
      animation.loop = true;
      animation.playSegments([startFrame, endFrame], true);
      visibility();
    });
    document.addEventListener('visibilitychange', visibility);
    reduced.addEventListener('change', motion);
    return {start, pause, animation, destroy() {
      active = false;
      document.removeEventListener('visibilitychange', visibility);
      reduced.removeEventListener('change', motion);
      animation.destroy();
    }};
  }
  window.VisionChevron = {create};
})();
