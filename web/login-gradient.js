(() => {
      'use strict';

      const canvas = document.getElementById('loginFluid');
      const ctx = canvas.getContext('2d', { alpha: false });
      if (!ctx) return;

      // Seamless mathematical loop: every oscillation completes an integer
      // number of cycles within LOOP_MS, including breathing and rotation.
      const LOOP_MS = 36000;
      const TAU = Math.PI * 2;
      const fps = 30;
      const motionQuery = matchMedia('(prefers-reduced-motion: reduce)');

      // x, y, radii, and travel distances are fractions of the viewport.
      // The large, overlapping radial fields create a smooth, non-orb-like flow.
      const lights = [
        { x:.21, y:.78, rx:.75, ry:.78, dx:.13, dy:.08, turns:1, angle:-.43, c:[41,44,37],     a:.68, seed:.3 },
        { x:.80, y:.23, rx:.68, ry:.64, dx:.12, dy:.14, turns:1, angle:.42,  c:[32,54,35],     a:.69, seed:1.9 },
        { x:.51, y:.60, rx:.58, ry:.60, dx:.15, dy:.11, turns:2, angle:-.38, c:[86,115,63],    a:.53, seed:3.4 },
        { x:.79, y:.47, rx:.39, ry:.54, dx:.15, dy:.14, turns:1, angle:.27,  c:[206,243,150],  a:.94, seed:2.2 },
        { x:.32, y:.42, rx:.51, ry:.53, dx:.13, dy:.12, turns:2, angle:.68,  c:[124,164,80],   a:.48, seed:4.6 },
        { x:.19, y:.76, rx:.49, ry:.36, dx:.08, dy:.13, turns:1, angle:-.70, c:[206,243,150],  a:.35, seed:5.6 },
        { x:.63, y:.30, rx:.48, ry:.34, dx:.12, dy:.13, turns:2, angle:.32,  c:[154,194,111],  a:.34, seed:2.9 },
        { x:.94, y:.81, rx:.49, ry:.43, dx:.10, dy:.11, turns:1, angle:-.54, c:[86,120,65],    a:.45, seed:1.1 },
        { x:.47, y:.91, rx:.65, ry:.37, dx:.12, dy:.08, turns:2, angle:.12,  c:[39,65,42],     a:.66, seed:4.1 },
        { x:.73, y:.55, rx:.26, ry:.30, dx:.11, dy:.12, turns:1, angle:.12,  c:[225,246,184],  a:.53, seed:3.1 }
      ];

      let width = 1;
      let height = 1;
      let frame = 0;
      let lastDraw = -Infinity;
      let start = performance.now();
      let pending = 0;

      function resize() {
        const rect = canvas.getBoundingClientRect();
        // The soft fields intentionally render below native resolution.
        // Upscaling provides extra blur while saving battery/GPU work.
        const quality = Math.min(0.8, 1400 / rect.width, 900 / rect.height);
        width = canvas.width = Math.max(1, Math.round(rect.width * quality));
        height = canvas.height = Math.max(1, Math.round(rect.height * quality));
        draw((performance.now() - start) % LOOP_MS / LOOP_MS);
      }

      function ellipseGradient(light, phase) {
        const cycle = phase * TAU * light.turns;
        const x = (light.x + light.dx * Math.cos(cycle + light.seed)) * width;
        const y = (light.y + light.dy * Math.sin(cycle + light.seed * 1.37)) * height;
        const breathe = 1 + .075 * Math.sin(phase * TAU * 2 + light.seed);
        const rx = Math.max(1, light.rx * width * breathe);
        const ry = Math.max(1, light.ry * height / breathe);
        const angle = light.angle + .22 * Math.sin(phase * TAU + light.seed);
        const [r, g, b] = light.c;

        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(angle);
        ctx.scale(rx, ry);

        const gradient = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
        const rgba = alpha => `rgba(${r},${g},${b},${(alpha * light.a).toFixed(4)})`;
        gradient.addColorStop(0, rgba(.94));
        gradient.addColorStop(.19, rgba(.78));
        gradient.addColorStop(.43, rgba(.42));
        gradient.addColorStop(.69, rgba(.12));
        gradient.addColorStop(1, rgba(0));

        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(0, 0, 1, 0, TAU);
        ctx.fill();
        ctx.restore();
      }

      function draw(phase) {
        ctx.globalCompositeOperation = 'source-over';
        ctx.fillStyle = '#050605';
        ctx.fillRect(0, 0, width, height);
        ctx.globalCompositeOperation = 'screen';
        for (const light of lights) ellipseGradient(light, phase);
        ctx.globalCompositeOperation = 'source-over';
      }

      function animate(now) {
        pending = 0;
        if (document.hidden || document.getElementById('accountGate').hidden || motionQuery.matches) return;
        if (now - lastDraw >= 1000 / fps) {
          draw(((now - start) % LOOP_MS) / LOOP_MS);
          lastDraw = now;
        }
        pending = requestAnimationFrame(animate);
      }

      function syncMotion() {
        cancelAnimationFrame(pending);
        pending = 0;
        if (motionQuery.matches) {
          draw(0.15); // Static composition for reduced-motion preference.
        } else if (!document.hidden && !document.getElementById('accountGate').hidden) {
          start = performance.now();
          pending = requestAnimationFrame(animate);
        }
      }

      const observer = new ResizeObserver(resize);
      observer.observe(canvas);
      motionQuery.addEventListener?.('change', syncMotion);
      document.addEventListener('visibilitychange', syncMotion);
      new MutationObserver(syncMotion).observe(document.getElementById('accountGate'), {attributes:true,attributeFilter:['hidden']});
      resize();
      syncMotion();
    })();
