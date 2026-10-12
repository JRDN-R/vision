/* Both supplied dotLottie clips are embedded at build time for offline playback. */
(() => {
  const clips = /* VISION_QWEN_ANIMATIONS */ [];
  class VentureQwenSymbol extends HTMLElement {
    connectedCallback() {
      if (this.players || !window.lottie || clips.length !== 2) return;
      this.players = [];
      this.frames = [];
      this.current = 0;
      this.ready = false;
      this.visible = false;
      this.motion = matchMedia('(prefers-reduced-motion: reduce)');
      this.sync = () => {
        if (!this.ready) return;
        if (this.motion.matches) {
          this.current = 0;
          this.players.forEach(player => player.goToAndStop(0, true));
          this.showCurrent();
        }
        this.players.forEach((player, index) => {
          if (index === this.current && this.visible && !document.hidden && !this.motion.matches) player.play();
          else player.pause();
        });
      };
      let loaded = 0;
      clips.forEach((data, index) => {
        const frame = document.createElement('span');
        frame.className = 'venture-qwen-frame';
        frame.hidden = index !== 0;
        this.appendChild(frame);
        this.frames.push(frame);
        const player = window.lottie.loadAnimation({
          container: frame, renderer: 'svg', loop: false, autoplay: false,
          animationData: JSON.parse(JSON.stringify(data)),
          rendererSettings: {preserveAspectRatio: 'xMidYMid meet', hideOnTransparent: false}
        });
        this.players.push(player);
        player.addEventListener('DOMLoaded', () => {
          if (!this.isConnected || !this.players?.includes(player)) return;
          frame.querySelector('svg')?.setAttribute('aria-hidden', 'true');
          if (++loaded !== clips.length) return;
          this.ready = true;
          this.setAttribute('data-ready', '');
          this.sync();
        });
        player.addEventListener('complete', () => {
          if (!this.ready || index !== this.current || this.motion.matches) return;
          this.current = (this.current + 1) % this.players.length;
          this.players[this.current].goToAndStop(0, true);
          this.showCurrent();
          this.sync();
        });
      });
      this.observer = new IntersectionObserver(entries => {
        this.visible = entries[0].isIntersecting;
        this.sync();
      });
      this.observer.observe(this);
      document.addEventListener('visibilitychange', this.sync);
      this.motion.addEventListener('change', this.sync);
    }
    showCurrent() {
      this.frames.forEach((frame, index) => { frame.hidden = index !== this.current; });
    }
    disconnectedCallback() {
      if (!this.players) return;
      this.ready = false;
      this.observer.disconnect();
      document.removeEventListener('visibilitychange', this.sync);
      this.motion.removeEventListener('change', this.sync);
      this.players.forEach(player => player.destroy());
      this.frames.forEach(frame => frame.remove());
      this.players = null;
      this.removeAttribute('data-ready');
    }
  }
  customElements.define('venture-qwen-symbol', VentureQwenSymbol);
})();
