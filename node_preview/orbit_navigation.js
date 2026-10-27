/* Approved public Orbit navigation; existing controls keep their listeners. */
(() => {
  'use strict';

  const body = document.body;
  if (!body?.classList.contains('mc-orbit-sidebar') || body.dataset.orbitSidebar === 'ready') return;
  const sidebar = body.querySelector('.mc-sidebar');
  const toggle = body.querySelector('.mc-sidebar-toggle');
  if (!sidebar || !toggle) return;
  body.dataset.orbitSidebar = 'ready';

  if (!sidebar.id) sidebar.id = 'mc-sidebar-menu';
  if (!body.id) body.id = 'top';
  toggle.setAttribute('aria-controls', sidebar.id);
  toggle.setAttribute('aria-label', '打开板块导航');

  const english = () => document.documentElement.lang.toLowerCase().startsWith('en');
  const desktop = window.matchMedia('(min-width: 960px)');
  const persistent = body.classList.contains('mc-sidebar-persistent');
  const highlightReport = body.classList.contains('mc-report-highlight');
  const fixedNavigation = () => desktop.matches || persistent;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const mobileBrand = body.querySelector('.m-brand-main');
  if (mobileBrand && !persistent) {
    toggle.classList.add('mc-sidebar-toggle--mobile');
    mobileBrand.prepend(toggle);
  }

  // Move the actual controls so their existing listeners and language state survive.
  const controls = body.querySelector('.top-actions');
  if (controls) {
    const actions = document.createElement('div');
    actions.className = 'mc-sidebar-actions';
    actions.setAttribute('aria-label', '网站工具');
    Array.from(controls.children).forEach(node => {
      if (highlightReport && node.matches('.share-trigger')) {
        const report = document.createElement('div');
        report.className = 'mc-sidebar-report';
        report.append(node);
        sidebar.querySelector('.mc-sidebar-brand').after(report);
        sidebar.classList.add('mc-has-report');
      } else if (node.matches('.share-trigger, .lang-toggle')) actions.append(node);
    });
    if (actions.childElementCount) sidebar.append(actions);
  }
  if (highlightReport && mobileBrand) {
    const desktopLink = body.querySelector('.m-brand-actions .m-desktop-link');
    if (desktopLink) {
      const actions = document.createElement('div');
      actions.className = 'mc-sidebar-actions';
      actions.setAttribute('aria-label', '网站工具');
      actions.append(desktopLink);
      sidebar.append(actions);
    }
  }

  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'mc-sidebar-close';
  close.setAttribute('aria-label', '关闭板块导航');
  close.textContent = '×';
  sidebar.prepend(close);

  const backdrop = document.createElement('button');
  backdrop.type = 'button';
  backdrop.className = 'mc-sidebar-backdrop';
  backdrop.tabIndex = -1;
  backdrop.setAttribute('aria-label', '关闭板块导航');
  backdrop.setAttribute('aria-hidden', 'true');
  sidebar.before(backdrop);

  const allowed = new Set(['top', 'rank', 'pulse', 'core', 'growth', 'equation', 'risk']);
  const destinations = [];
  sidebar.querySelectorAll('a[href^="#"]').forEach(link => {
    let id = link.getAttribute('href').slice(1);
    if (id === 'pulse' && !document.getElementById(id) && document.getElementById('core')) id = 'core';
    const target = allowed.has(id) ? document.getElementById(id) : null;
    if (!target) {
      link.hidden = true;
      return;
    }
    link.setAttribute('href', '#' + id);
    destinations.push({link, target, id});
  });
  const navItems = destinations.filter(item => item.link.closest('nav'));

  let opened = false;
  let scrollFrame = 0;

  const focusDestination = target => {
    const hadTabindex = target.hasAttribute('tabindex');
    if (!hadTabindex) target.setAttribute('tabindex', '-1');
    target.focus({preventScroll: true});
    if (!hadTabindex) target.addEventListener('blur', () => target.removeAttribute('tabindex'), {once: true});
  };

  const setOpen = (next, focusTarget = null) => {
    opened = Boolean(next && !fixedNavigation());
    // Focus leaves the drawer before its descendants become inert.
    if (!opened && sidebar.contains(document.activeElement) && !fixedNavigation()) {
      if (focusTarget) focusDestination(focusTarget);
      else toggle.focus({preventScroll: true});
    }
    body.classList.toggle('mc-sidebar-open', opened);
    toggle.setAttribute('aria-expanded', String(opened));
    toggle.setAttribute('aria-label', english() ? (opened ? 'Close navigation' : 'Open navigation') : (opened ? '关闭板块导航' : '打开板块导航'));
    if (fixedNavigation() || opened) {
      sidebar.removeAttribute('aria-hidden');
      sidebar.inert = false;
    } else {
      sidebar.setAttribute('aria-hidden', 'true');
      sidebar.inert = true;
    }
    backdrop.hidden = !opened;
    if (opened) close.focus({preventScroll: true});
  };

  const markCurrent = () => {
    scrollFrame = 0;
    if (!navItems.length) return;
    const threshold = desktop.matches ? 90 : 125;
    let current = navItems[0];
    navItems.forEach(item => {
      if (item.target.getBoundingClientRect().top <= threshold) current = item;
    });
    const root = document.documentElement;
    if (root.scrollHeight > window.innerHeight + 50 && window.scrollY + window.innerHeight >= root.scrollHeight - 4) {
      current = navItems[navItems.length - 1];
    }
    navItems.forEach(item => {
      if (item === current) item.link.setAttribute('aria-current', 'location');
      else item.link.removeAttribute('aria-current');
    });
  };
  const scheduleCurrent = () => {
    if (!scrollFrame) scrollFrame = window.requestAnimationFrame(markCurrent);
  };

  toggle.addEventListener('click', () => setOpen(!opened));
  close.addEventListener('click', () => setOpen(false));
  backdrop.addEventListener('click', () => setOpen(false));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && opened) {
      event.preventDefault();
      setOpen(false);
    }
  });
  destinations.forEach(item => {
    item.link.addEventListener('click', event => {
      // Preserve ordinary modified clicks and allow the browser's native anchor navigation.
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (opened) setOpen(false, item.target);
      if (reduceMotion.matches) {
        event.preventDefault();
        if (window.location.hash !== '#' + item.id) window.history.pushState(null, '', '#' + item.id);
        item.target.scrollIntoView({behavior: 'instant', block: 'start'});
      }
      scheduleCurrent();
    });
  });
  window.addEventListener('scroll', scheduleCurrent, {passive: true});
  window.addEventListener('resize', scheduleCurrent, {passive: true});
  window.addEventListener('hashchange', scheduleCurrent);
  window.addEventListener('pageshow', scheduleCurrent);
  const resetLayout = () => {
    setOpen(false);
    scheduleCurrent();
  };
  if (typeof desktop.addEventListener === 'function') desktop.addEventListener('change', resetLayout);
  else if (typeof desktop.addListener === 'function') desktop.addListener(resetLayout);
  const syncLanguage = () => {
    sidebar.setAttribute('aria-label', english() ? 'Section navigation' : '板块导航');
    toggle.setAttribute('aria-label', english() ? (opened ? 'Close navigation' : 'Open navigation') : (opened ? '关闭板块导航' : '打开板块导航'));
    close.setAttribute('aria-label', english() ? 'Close navigation' : '关闭板块导航');
    backdrop.setAttribute('aria-label', english() ? 'Close navigation' : '关闭板块导航');
    sidebar.querySelectorAll('.mc-sidebar-actions').forEach(actions => actions.setAttribute('aria-label', english() ? 'Website tools' : '网站工具'));
  };
  new MutationObserver(syncLanguage).observe(document.documentElement, {attributes: true, attributeFilter: ['lang']});
  syncLanguage();

  setOpen(false);
  markCurrent();
})();
