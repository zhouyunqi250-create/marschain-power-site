/* Production opening: the selected orbital scene, independent of ranking data. */
(() => {
  'use strict';

  const body = document.body;
  if (!body || body.dataset.rankOpeningInitialized === 'true') return;

  const duration = 2000;
  const storageKey = 'marschain.rank.opening-day.v1';
  let overlay = null;
  let style = null;
  let sceneCleanup = null;
  let removalTimer = 0;
  let languageObserver = null;
  let motionQuery = null;
  let previousFocus = null;
  let skip = null;
  let finished = false;
  const detach = [];

  const finish = () => {
    if (finished) return;
    finished = true;
    window.clearTimeout(removalTimer);
    removalTimer = 0;
    const restoreFocus = skip && document.activeElement === skip;
    try {
      if (typeof sceneCleanup === 'function') sceneCleanup();
    } catch (_) {
      // A decorative renderer must never prevent access to the ranking.
    } finally {
      sceneCleanup = null;
      detach.forEach(remove => {
        try { remove(); } catch (_) { /* Continue releasing other resources. */ }
      });
      detach.length = 0;
      if (languageObserver) languageObserver.disconnect();
      languageObserver = null;
      if (overlay) overlay.remove();
      if (style) style.remove();
      overlay = null;
      style = null;
      if (restoreFocus && previousFocus?.isConnected) {
        try { previousFocus.focus({ preventScroll: true }); } catch (_) { /* No focus trap. */ }
      }
    }
  };

  const listen = (target, name, handler) => {
    target.addEventListener(name, handler);
    detach.push(() => target.removeEventListener(name, handler));
  };

  try {
    if (document.hidden) return;
    const renderer = window.MarsIntroVariants?.resonance;
    if (!renderer || typeof renderer.render !== 'function') return;
    motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    if (motionQuery.matches) return;

    // Beijing has a fixed UTC+8 offset; this is independent of the device zone.
    const today = new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const forced = new URLSearchParams(window.location.search).get('intro') === '1';
    try {
      if (!forced && window.localStorage.getItem(storageKey) === today) return;
    } catch (_) {
      // Private/storage-restricted sessions can still view this brief opening.
    }
    body.dataset.rankOpeningInitialized = 'true';
    previousFocus = document.activeElement;

    style = document.createElement('style');
    style.textContent = `
.mars-rank-opening {
  position: fixed; inset: 0; z-index: 2147483000; isolation: isolate;
  overflow: hidden; background: #020407; color: #e5edf2; pointer-events: none;
  animation: mars-rank-opening-exit 2000ms linear forwards;
}
.mars-rank-opening .mars-rank-opening__stage { position: absolute; inset: 0; }
.mars-rank-opening .mars-rank-opening__skip {
  position: absolute; z-index: 1;
  top: max(16px, env(safe-area-inset-top)); right: max(16px, env(safe-area-inset-right));
  min-height: 44px; padding: 0 15px; border: 1px solid #9ab8c72b; border-radius: 999px;
  background: #0b121bd9; color: #b8c9d4; font: 12px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  cursor: pointer; pointer-events: auto;
}
.mars-rank-opening .mars-rank-opening__skip:hover { border-color: #c9e7f275; color: #f0f8fc; }
.mars-rank-opening .mars-rank-opening__skip:focus-visible { outline: 2px solid #bddfee; outline-offset: 3px; }
/* CSS also releases the view if a background tab delays the JavaScript timer. */
@keyframes mars-rank-opening-exit {
  0%, 87% { opacity: 1; visibility: visible; }
  100% { opacity: 0; visibility: hidden; }
}
@media (prefers-reduced-motion: reduce) {
  .mars-rank-opening { display: none !important; animation: none !important; }
}
`;
    overlay = document.createElement('div');
    overlay.className = 'mars-rank-opening';
    const stage = document.createElement('div');
    stage.className = 'mars-rank-opening__stage';
    stage.setAttribute('aria-hidden', 'true');
    skip = document.createElement('button');
    skip.type = 'button';
    skip.className = 'mars-rank-opening__skip';
    overlay.append(stage, skip);
    body.append(style, overlay);
    // Mount first so the canvas receives the actual desktop or phone dimensions.
    // Neither data polling, page interaction nor document scrolling waits for us.
    removalTimer = window.setTimeout(finish, duration);
    sceneCleanup = renderer.render(stage, { animated: true });
    const canvas = stage.querySelector('canvas');
    if (!canvas || !canvas.getContext('2d')) throw new Error('Opening canvas unavailable');

    const syncLanguage = () => {
      const english = document.documentElement.lang.toLowerCase().startsWith('en');
      skip.textContent = english ? 'Skip intro' : '跳过动画';
      const subtitle = stage.querySelector('.mi-resonance-subtitle');
      if (subtitle) subtitle.textContent = english ? 'Power Ranking' : '算力排行榜';
      const scene = stage.querySelector('.mars-intro-resonance');
      if (scene) scene.setAttribute('aria-label', english
        ? 'MarsChain Power Ranking: two orbital lights form a geometric M'
        : 'MarsChain 算力排行榜，两条精密光轨展开并汇聚成几何 M');
    };
    syncLanguage();
    if (typeof MutationObserver !== 'undefined') {
      languageObserver = new MutationObserver(syncLanguage);
      languageObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['lang'] });
    }

    listen(skip, 'click', finish);
    listen(document, 'keydown', event => { if (event.key === 'Escape') finish(); });
    listen(document, 'visibilitychange', () => { if (document.hidden) finish(); });
    listen(window, 'pagehide', finish);
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
})();
