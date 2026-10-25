/* Original code-native orbital opening. The caller owns its two-second lifetime. */
(() => {
  'use strict';

  const registry = window.MarsIntroVariants = window.MarsIntroVariants || {};
  const duration = 2000;
  const tau = Math.PI * 2;
  let serial = 0;
  const clamp = value => Math.max(0, Math.min(1, value));
  const smooth = value => {
    const n = clamp(value);
    return n * n * (3 - 2 * n);
  };
  const easeOut = value => 1 - (1 - clamp(value)) ** 3;
  const rad = degrees => degrees * Math.PI / 180;
  const deltaAngle = (a, b) => Math.atan2(Math.sin(a - b), Math.cos(a - b));

  const css = `
.mars-intro-scene.mars-intro-resonance {
  position: relative; isolation: isolate; container-type: size; container-name: mars-resonance;
  width: 100%; height: 100%; overflow: hidden; background: #020407;
  color: #e5edf2; font-family: "Helvetica Neue", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.mars-intro-scene.mars-intro-resonance .mi-resonance-canvas {
  display: block; position: absolute; inset: 0;
  width: 100%; height: 100%; pointer-events: none;
}
.mars-intro-scene.mars-intro-resonance .mi-resonance-copy {
  position: absolute; left: 50%; top: 74%;
  display: flex; flex-direction: column; align-items: center;
  width: max-content; max-width: 92cqw; text-align: center;
  transform: translateX(-50%);
}
.mars-intro-scene.mars-intro-resonance .mi-resonance-name {
  color: #e8f0f5; font-size: clamp(17px, min(6cqw, 6.6cqh), 31px);
  font-weight: 450; line-height: 1.3; white-space: nowrap;
  letter-spacing: .3em; text-indent: .3em;
}
.mars-intro-scene.mars-intro-resonance .mi-resonance-subtitle {
  color: #8397a7; font-size: clamp(9px, min(2.6cqw, 2.8cqh), 12px);
  font-weight: 400; line-height: 1.5; white-space: nowrap;
  margin-top: clamp(9px, 2.6cqh, 14px);
  letter-spacing: .42em; text-indent: .42em;
}
.mars-intro-scene.mars-intro-resonance .mi-resonance-rule {
  height: 1px; width: clamp(35px, 9cqw, 58px);
  margin-top: clamp(13px, 3.2cqh, 18px);
  background: linear-gradient(90deg, transparent, #98b1c045, transparent);
}
.mars-intro-scene.mars-intro-resonance[data-mi-animated="true"] .mi-resonance-name {
  animation: mars-resonance-name-2s 2000ms cubic-bezier(.16,.7,.24,1) both;
}
.mars-intro-scene.mars-intro-resonance[data-mi-animated="true"] .mi-resonance-subtitle {
  animation: mars-resonance-subtitle-2s 2000ms ease both;
}
.mars-intro-scene.mars-intro-resonance[data-mi-animated="true"] .mi-resonance-rule {
  animation: mars-resonance-rule-2s 2000ms ease both;
}
@keyframes mars-resonance-name-2s {
  0%,44% { opacity: 0; transform: translateY(6px); letter-spacing: .4em; text-indent: .4em; }
  77%,100% { opacity: 1; transform: translateY(0); letter-spacing: .3em; text-indent: .3em; }
}
@keyframes mars-resonance-subtitle-2s {
  0%,57% { opacity: 0; transform: translateY(4px); }
  84%,100% { opacity: 1; transform: translateY(0); }
}
@keyframes mars-resonance-rule-2s {
  0%,64% { opacity: 0; transform: scaleX(.3); }
  89%,100% { opacity: 1; transform: scaleX(1); }
}
@container mars-resonance (max-aspect-ratio: 3/4) {
  .mars-intro-scene.mars-intro-resonance .mi-resonance-copy { top: 67%; }
}
@media (prefers-reduced-motion: reduce) {
  .mars-intro-scene.mars-intro-resonance[data-mi-animated="true"] * { animation: none !important; }
}
`;

  registry.resonance = {
    title: '轨道共振',
    subtitle: '双轨展开 · 铂金汇聚',
    render(host, { animated = true } = {}) {
      const id = `mars-resonance-${Date.now().toString(36)}-${++serial}`;
      const style = document.createElement('style');
      style.textContent = css;
      const scene = document.createElement('div');
      scene.className = 'mars-intro-scene mars-intro-resonance';
      scene.setAttribute('role', 'img');
      scene.setAttribute('aria-label', 'MarsChain 算力排行榜，两条精密光轨展开并汇聚成几何 M');
      const canvas = document.createElement('canvas');
      canvas.id = `${id}-canvas`;
      canvas.className = 'mi-resonance-canvas';
      canvas.setAttribute('aria-hidden', 'true');
      const copy = document.createElement('div');
      copy.className = 'mi-resonance-copy';
      copy.setAttribute('aria-hidden', 'true');
      copy.innerHTML = '<div class="mi-resonance-name">MARSCHAIN</div><div class="mi-resonance-subtitle">算力排行榜</div><div class="mi-resonance-rule"></div>';
      scene.append(canvas, copy);
      host.append(style, scene);

      const context = canvas.getContext('2d', { alpha: false });
      const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
      let moving = Boolean(animated) && !motionQuery.matches;
      scene.dataset.miAnimated = moving ? 'true' : 'false';
      let width = 1;
      let height = 1;
      let radius = 1;
      let cx = .5;
      let cy = .5;
      let pixelRatio = 1;
      let frame = 0;
      let elapsed = moving ? 0 : duration;
      let disposed = false;
      const startedAt = performance.now();

      const geometry = ms => {
        const opening = easeOut((ms - 45) / 770);
        const settled = smooth((ms - 720) / 1100);
        const scale = .57 + .43 * opening;
        // Two actual circles in differently inclined 3D planes. The camera sees
        // their near-edge-on opening, precession, foreshortening and final depth.
        return [
          {
            r: radius * scale,
            x: rad(86 - 25 * opening),
            y: rad(-12 + 8 * (1 - opening)),
            z: rad(-24 - 19 * (1 - opening) + (1 - settled) * 1.7 * Math.sin(ms / 330)),
            head: -2.8 + tau * 1.12 * easeOut(ms / 1650),
          },
          {
            r: radius * .85 * scale,
            x: rad(-87 + 21 * opening),
            y: rad(15 - 12 * (1 - opening)),
            z: rad(39 + 23 * (1 - opening) - (1 - settled) * 2 * Math.sin(ms / 370)),
            head: 1.25 - tau * 1.02 * easeOut(ms / 1700),
          },
        ];
      };

      const project = (orbit, angle) => {
        const x = Math.cos(angle) * orbit.r;
        const y = Math.sin(angle) * orbit.r;
        const y1 = y * Math.cos(orbit.x);
        const z1 = y * Math.sin(orbit.x);
        const x2 = x * Math.cos(orbit.y) + z1 * Math.sin(orbit.y);
        const z2 = -x * Math.sin(orbit.y) + z1 * Math.cos(orbit.y);
        const x3 = x2 * Math.cos(orbit.z) - y1 * Math.sin(orbit.z);
        const y3 = x2 * Math.sin(orbit.z) + y1 * Math.cos(orbit.z);
        const focal = radius * 6;
        const perspective = focal / (focal - z2);
        return { x: cx + x3 * perspective, y: cy + y3 * perspective, z: z2 };
      };

      const dot = (point, brightness, size = 1.1) => {
        context.save();
        context.globalAlpha = brightness;
        context.fillStyle = '#b8deef';
        context.beginPath();
        context.arc(point.x, point.y, size * 3.8, 0, tau);
        context.globalAlpha = brightness * .055;
        context.fill();
        context.globalAlpha = brightness * .17;
        context.beginPath();
        context.arc(point.x, point.y, size * 2, 0, tau);
        context.fill();
        context.globalAlpha = brightness;
        context.fillStyle = '#effbff';
        context.beginPath();
        context.arc(point.x, point.y, size, 0, tau);
        context.fill();
        context.restore();
      };

      const segment = (item, ringOpacity, ms) => {
        const front = item.z >= 0;
        const distance = Math.abs(deltaAngle(item.angle, item.head));
        const specular = Math.exp(-distance * distance / .13);
        const lightSettles = 1 - .34 * smooth((ms - 800) / 750);
        const shine = specular * lightSettles;
        const alpha = ringOpacity * ((front ? .45 : .19) + shine * (front ? .40 : .20));
        const base = front ? [165, 195, 215] : [104, 139, 165];
        const rgb = base.map((channel, index) => Math.round(channel + ([239, 251, 255][index] - channel) * shine * .83));
        context.strokeStyle = `rgba(${rgb.join(',')},${alpha})`;
        context.lineWidth = front ? .88 : .67;
        context.beginPath();
        context.moveTo(item.a.x, item.a.y);
        context.lineTo(item.b.x, item.b.y);
        context.stroke();
      };

      const glyphPoints = () => [
        { x: cx - radius * .32, y: cy + radius * .25 },
        { x: cx - radius * .32, y: cy - radius * .25 },
        { x: cx, y: cy + radius * .07 },
        { x: cx + radius * .32, y: cy - radius * .25 },
        { x: cx + radius * .32, y: cy + radius * .25 },
      ];

      const tracePolyline = (points, progress) => {
        const lengths = points.slice(1).map((point, i) => Math.hypot(point.x - points[i].x, point.y - points[i].y));
        let remaining = lengths.reduce((total, n) => total + n, 0) * clamp(progress);
        let tip = points[0];
        context.beginPath();
        context.moveTo(points[0].x, points[0].y);
        for (let i = 0; i < lengths.length && remaining > 0; i += 1) {
          const ratio = Math.min(1, remaining / lengths[i]);
          tip = {
            x: points[i].x + (points[i + 1].x - points[i].x) * ratio,
            y: points[i].y + (points[i + 1].y - points[i].y) * ratio,
          };
          context.lineTo(tip.x, tip.y);
          remaining -= lengths[i];
        }
        return tip;
      };

      const glyph = ms => {
        const progress = smooth((ms - 655) / 690);
        if (progress <= 0) return;
        const points = glyphPoints();
        context.save();
        context.lineJoin = 'bevel';
        context.lineCap = 'square';
        // A narrow dark occlusion edge puts the mark between the back and front
        // orbital arcs, instead of treating the whole scene as one flat drawing.
        context.strokeStyle = '#020407';
        context.lineWidth = Math.max(3.5, radius * .025);
        tracePolyline(points, progress);
        context.stroke();
        context.translate(.6, .7);
        context.strokeStyle = 'rgba(99,144,170,.30)';
        context.lineWidth = Math.max(1.7, radius * .014);
        tracePolyline(points, progress);
        context.stroke();
        context.translate(-.6, -.7);
        const gradient = context.createLinearGradient(cx - radius * .3, cy - radius * .3, cx + radius * .35, cy + radius * .3);
        gradient.addColorStop(0, '#f2fbff');
        gradient.addColorStop(.44, '#d5e6ef');
        gradient.addColorStop(.71, '#aec9da');
        gradient.addColorStop(1, '#f0f9fc');
        context.strokeStyle = gradient;
        context.lineWidth = Math.max(1.25, radius * .009);
        const tip = tracePolyline(points, progress);
        context.stroke();
        context.restore();
        if (progress < .98) dot(tip, .75 * Math.sin(Math.PI * progress), .85);
      };

      const inwardPoints = ms => {
        const t = clamp((ms - 700) / 655);
        if (t <= 0 || t >= 1) return;
        const starts = geometry(700);
        starts.forEach((orbit, index) => {
          const start = project(orbit, orbit.head);
          const end = {
            x: cx + radius * (index === 0 ? -.32 : .32),
            y: cy - radius * .25,
          };
          const control = {
            x: start.x + (end.x - start.x) * .28,
            y: start.y - radius * (index === 0 ? .26 : .13),
          };
          const position = ratio => {
            const p = smooth(ratio);
            return {
              x: (1 - p) ** 2 * start.x + 2 * (1 - p) * p * control.x + p ** 2 * end.x,
              y: (1 - p) ** 2 * start.y + 2 * (1 - p) * p * control.y + p ** 2 * end.y,
            };
          };
          const opacity = Math.sin(Math.PI * t) ** .55;
          context.save();
          context.lineWidth = .75;
          for (let tail = 6; tail > 0; tail -= 1) {
            const a = position(Math.max(0, t - tail * .027));
            const b = position(Math.max(0, t - (tail - 1) * .027));
            context.strokeStyle = `rgba(198,233,250,${opacity * (7 - tail) * .055})`;
            context.beginPath();
            context.moveTo(a.x, a.y);
            context.lineTo(b.x, b.y);
            context.stroke();
          }
          context.restore();
          dot(position(t), opacity * .95, 1.1);
        });
      };

      const draw = ms => {
        if (!context || disposed) return;
        context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
        context.fillStyle = '#020407';
        context.fillRect(0, 0, width, height);
        // Only a few quiet distant points; no grids, telemetry or simulated data.
        const starOpacity = .23 * smooth(ms / 460);
        [[.13,.23],[.84,.18],[.21,.65],[.90,.61],[.67,.10]].forEach(([x,y], index) => {
          context.fillStyle = `rgba(137,168,190,${starOpacity * (index % 2 ? .6 : 1)})`;
          context.beginPath();
          context.arc(width * x, height * y, .55, 0, tau);
          context.fill();
        });

        const ringOpacity = .08 + .92 * smooth((ms - 20) / 520);
        const pieces = [];
        const lightPoints = [];
        geometry(ms).forEach((orbit, ringIndex) => {
          const samples = 224;
          let previous = project(orbit, 0);
          for (let i = 1; i <= samples; i += 1) {
            const angle = tau * i / samples;
            const point = project(orbit, angle);
            pieces.push({ a: previous, b: point, z: (previous.z + point.z) / 2, angle: angle - Math.PI / samples, head: orbit.head });
            previous = point;
          }
          const head = project(orbit, orbit.head);
          lightPoints.push({ point: head, ringIndex });
        });
        pieces.sort((a, b) => a.z - b.z);
        context.lineCap = 'butt';
        pieces.filter(item => item.z < 0).forEach(item => segment(item, ringOpacity, ms));
        lightPoints.filter(item => item.point.z < 0).forEach(item => dot(item.point, ringOpacity * .46, .9));
        glyph(ms);
        pieces.filter(item => item.z >= 0).forEach(item => segment(item, ringOpacity, ms));
        const settledLight = 1 - .5 * smooth((ms - 900) / 750);
        lightPoints.filter(item => item.point.z >= 0).forEach(item => dot(item.point, ringOpacity * settledLight * .95, 1.15));
        inwardPoints(ms);
      };

      const fit = () => {
        if (disposed) return;
        const bounds = scene.getBoundingClientRect();
        width = Math.max(1, bounds.width);
        height = Math.max(1, bounds.height);
        pixelRatio = Math.min(window.devicePixelRatio || 1, 1.75);
        canvas.width = Math.max(1, Math.round(width * pixelRatio));
        canvas.height = Math.max(1, Math.round(height * pixelRatio));
        radius = Math.min(width * .425, height * .385);
        cx = width * .5;
        cy = height * .41;
        draw(elapsed);
      };

      const tick = now => {
        frame = 0;
        if (disposed || !moving) return;
        elapsed = Math.min(duration, Math.max(0, now - startedAt));
        draw(elapsed);
        if (elapsed < duration) frame = window.requestAnimationFrame(tick);
      };

      const onMotionChange = event => {
        if (!event.matches || disposed) return;
        moving = false;
        scene.dataset.miAnimated = 'false';
        if (frame) window.cancelAnimationFrame(frame);
        frame = 0;
        elapsed = duration;
        draw(elapsed);
      };

      fit();
      let resizeObserver = null;
      if (typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(fit);
        resizeObserver.observe(scene);
      } else window.addEventListener('resize', fit);
      if (motionQuery.addEventListener) motionQuery.addEventListener('change', onMotionChange);
      else if (motionQuery.addListener) motionQuery.addListener(onMotionChange);
      if (moving && context) frame = window.requestAnimationFrame(tick);

      return () => {
        if (disposed) return;
        disposed = true;
        if (frame) window.cancelAnimationFrame(frame);
        frame = 0;
        if (resizeObserver) resizeObserver.disconnect();
        else window.removeEventListener('resize', fit);
        if (motionQuery.removeEventListener) motionQuery.removeEventListener('change', onMotionChange);
        else if (motionQuery.removeListener) motionQuery.removeListener(onMotionChange);
        scene.remove();
        style.remove();
      };
    },
  };
})();
