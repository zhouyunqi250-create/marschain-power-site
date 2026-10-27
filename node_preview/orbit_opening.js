/* Approved public Orbit opening; decorative and independent of ranking data. */
(() => {
  'use strict';

  const body = document.body;
  if (!body?.classList.contains('mc-orbit-finish') || body.dataset.orbitFinishOpening === 'true') return;
  body.dataset.orbitFinishOpening = 'true';

  const storageKey = 'marschain.rank.opening-day.v1';
  const today = new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const english = () => document.documentElement.lang.toLowerCase().startsWith('en');
  const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  let active = null;
  let revealTimer = 0;

  const style = document.createElement('style');
  style.dataset.orbitFinishOpening = 'true';
  style.textContent = `
body.mc-orbit-finish .mc-opening-shell {
  position: fixed; inset: 0; z-index: 2147483000; isolation: isolate;
  overflow: hidden; background: #060c14; color: #e5edf2; pointer-events: none;
  animation: mc-opening-release 2450ms linear forwards;
}
body.mc-orbit-finish .mc-opening-stage { position: absolute; inset: 0; }
body.mc-orbit-finish .mc-opening-stage::after {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background: radial-gradient(ellipse at 78% 45%, #16364a2b, transparent 68%);
}
body.mc-orbit-finish .mc-opening-skip {
  position: absolute; z-index: 2; top: max(16px, env(safe-area-inset-top));
  right: max(16px, env(safe-area-inset-right)); min-height: 44px;
  padding: 0 16px; border: 1px solid #a3c8d52b; border-radius: 6px;
  background: #0b1521d9; color: #b1c4d0; font: 12px/1.4 -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
  cursor: pointer; pointer-events: auto;
}
body.mc-orbit-finish .mc-opening-skip:hover { color: #eff8fc; border-color: #a3c8d570; }
body.mc-orbit-finish .mc-opening-skip:focus-visible { outline: 2px solid #b4d6e1; outline-offset: 3px; }
body.mc-orbit-finish.mc-opening-reveal .mc-hero--orbit .orbit-copy,
body.mc-orbit-finish.mc-opening-reveal .mc-hero--orbit .orbit-footer {
  animation: mc-opening-copy-arrive 450ms cubic-bezier(.2,.65,.35,1) both;
}
body.mc-orbit-finish.mc-opening-reveal .mc-hero--orbit .orbit-visual {
  animation: mc-opening-world-arrive 600ms cubic-bezier(.2,.65,.35,1) both;
}
body.mc-orbit-finish .mc-opening-shell.is-static { animation: mc-opening-static-release 580ms linear forwards; }
/* The CSS release is also a fallback if a background tab delays its JS timer. */
@keyframes mc-opening-release {
  0%, 81.63265% { opacity: 1; visibility: visible; }
  100% { opacity: 0; visibility: hidden; }
}
@keyframes mc-opening-static-release {
  0% { opacity: 0; visibility: visible; }
  12%, 68.9655% { opacity: 1; visibility: visible; }
  100% { opacity: 0; visibility: hidden; }
}
@keyframes mc-opening-copy-arrive {
  from { opacity: .72; transform: translateY(5px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes mc-opening-world-arrive {
  from { opacity: .78; transform: scale(1.015); }
  to { opacity: 1; transform: scale(1); }
}
@media (prefers-reduced-motion: reduce) {
  body.mc-orbit-finish.mc-opening-reveal .mc-hero--orbit .orbit-copy,
  body.mc-orbit-finish.mc-opening-reveal .mc-hero--orbit .orbit-footer,
  body.mc-orbit-finish.mc-opening-reveal .mc-hero--orbit .orbit-visual { animation: none !important; }
}
`;
  body.append(style);

  const reveal = () => {
    window.clearTimeout(revealTimer);
    body.classList.add('mc-opening-reveal');
    revealTimer = window.setTimeout(() => {
      body.classList.remove('mc-opening-reveal');
      revealTimer = 0;
    }, 650);
  };

  const play = () => {
    if (active) active.finish(false);
    window.clearTimeout(revealTimer);
    revealTimer = 0;
    body.classList.remove('mc-opening-reveal');

    const renderer = window.MarsIntroVariants?.resonance;
    if (!renderer || typeof renderer.render !== 'function' || document.hidden) return;

    const reduced = motionQuery.matches;
    const hold = reduced ? 400 : 2000;
    const fade = reduced ? 180 : 450;
    const previousFocus = document.activeElement;
    const detach = [];
    const timers = [];
    let dispose = null;
    let finished = false;
    let shell = null;
    let skip = null;
    const instance = { finish: null };

    const listen = (target, name, handler) => {
      target.addEventListener(name, handler);
      detach.push(() => target.removeEventListener(name, handler));
    };

    const finish = (arrive = true) => {
      if (finished) return;
      finished = true;
      timers.forEach(timer => window.clearTimeout(timer));
      const restoreFocus = skip && document.activeElement === skip;
      try {
        if (typeof dispose === 'function') dispose();
      } catch (_) {
        // Decorative rendering must never keep the ranking covered.
      } finally {
        dispose = null;
        detach.forEach(remove => { try { remove(); } catch (_) { /* Release the remaining listeners. */ } });
        if (shell) shell.remove();
        shell = null;
        if (active === instance) {
          active = null;
          body.classList.remove('mc-opening-visible');
          if (arrive) reveal();
        }
        if (restoreFocus && previousFocus?.isConnected) {
          try { previousFocus.focus({ preventScroll: true }); } catch (_) { /* Never trap focus. */ }
        }
      }
    };

    instance.finish = finish;
    active = instance;

    try {
      shell = document.createElement('div');
      shell.className = `mc-opening-shell${reduced ? ' is-static' : ''}`;
      shell.setAttribute('role', 'region');
      shell.setAttribute('aria-label', english() ? 'MarsChain opening animation' : 'MarsChain 开场动画');
      const stage = document.createElement('div');
      stage.className = 'mc-opening-stage';
      stage.setAttribute('aria-hidden', 'true');
      skip = document.createElement('button');
      skip.type = 'button';
      skip.className = 'mc-opening-skip';
      skip.textContent = english() ? 'Skip' : '跳过';
      skip.setAttribute('aria-label', english() ? 'Skip opening animation' : '跳过开场动画');
      shell.append(stage, skip);
      body.append(shell);
      body.classList.add('mc-opening-visible');

      // Nothing here awaits the ranking data or modifies document scrolling.
      timers.push(window.setTimeout(reveal, hold));
      timers.push(window.setTimeout(() => finish(false), hold + fade));
      dispose = renderer.render(stage, { animated: !reduced });
      syncLanguage();
      const canvas = stage.querySelector('canvas');
      if (!canvas || !canvas.getContext('2d')) throw new Error('Opening canvas unavailable');

      listen(skip, 'click', () => finish());
      listen(document, 'keydown', event => { if (event.key === 'Escape') finish(); });
      listen(document, 'visibilitychange', () => { if (document.hidden) finish(false); });
      listen(window, 'pagehide', () => finish(false));
      listen(window, 'error', () => finish());
      listen(window, 'unhandledrejection', () => finish());
      const onMotionChange = event => { if (event.matches) finish(); };
      if (motionQuery.addEventListener) listen(motionQuery, 'change', onMotionChange);
      else if (motionQuery.addListener) {
        motionQuery.addListener(onMotionChange);
        detach.push(() => motionQuery.removeListener(onMotionChange));
      }
      try { window.localStorage.setItem(storageKey, today); } catch (_) { /* Storage is optional. */ }
    } catch (_) {
      finish();
    }
  };

  const syncLanguage = () => {
    const shell = body.querySelector('.mc-opening-shell');
    const skip = shell?.querySelector('.mc-opening-skip');
    if (shell) shell.setAttribute('aria-label', english() ? 'MarsChain opening animation' : 'MarsChain 开场动画');
    if (skip) {
      skip.textContent = english() ? 'Skip' : '跳过';
      skip.setAttribute('aria-label', english() ? 'Skip opening animation' : '跳过开场动画');
    }
    const subtitle = shell?.querySelector('.mi-resonance-subtitle');
    if (subtitle) subtitle.textContent = english() ? 'Power Ranking' : '算力排行榜';
    const scene = shell?.querySelector('.mars-intro-resonance');
    if (scene) scene.setAttribute('aria-label', english()
      ? 'MarsChain Power Ranking: orbital lights form a geometric M'
      : 'MarsChain 算力排行榜，两条精密光轨展开并汇聚成几何 M');
  };
  new MutationObserver(syncLanguage).observe(document.documentElement, {attributes: true, attributeFilter: ['lang']});
  syncLanguage();

  let seen = false;
  try { seen = window.localStorage.getItem(storageKey) === today; } catch (_) { /* Storage is optional. */ }
  if (motionQuery.matches || seen) return;
  if (!document.hidden) play();
  else {
    const whenVisible = () => {
      if (document.hidden) return;
      document.removeEventListener('visibilitychange', whenVisible);
      if (!motionQuery.matches) play();
    };
    document.addEventListener('visibilitychange', whenVisible);
    window.addEventListener('pagehide', () => document.removeEventListener('visibilitychange', whenVisible), { once: true });
  }
})();
