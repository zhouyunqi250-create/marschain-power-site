/* Synchronize the approved hero with the existing data and language runtime. */
(() => {
  'use strict';
  const data = document.getElementById('rankData');
  const panel = document.querySelector('.mc-dual-time');
  const state = document.querySelector('[data-preview-state]');
  const banner = document.querySelector('[data-node-preview]');
  const update = (node, value) => {
    if (node && node.textContent !== value) node.textContent = value;
  };
  const dateText = (date, zone) => {
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(date);
    const p = Object.fromEntries(parts.map(x => [x.type, x.value]));
    return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
  };
  const text = (zh, en) => window.MarsWebsiteLocale?.copy(zh, en) || zh;
  const syncTime = () => {
    if (!panel) return;
    update(panel.querySelector('[data-dual-time-caption]'), text('数据时间', 'Data time'));
    update(panel.querySelector('[data-dual-beijing-label]'), text('北京时间', 'Beijing time'));
    update(panel.querySelector('[data-dual-utc-label]'), text('世界标准时间', 'Universal time'));
    panel.setAttribute('aria-label', text('数据时间', 'Data time'));
    let stamp = null;
    try { stamp = JSON.parse(data?.textContent || '').meta?.node_preview?.source_block_timestamp; } catch (_) {}
    const seconds = typeof stamp === 'number' || (typeof stamp === 'string' && /^\d+(?:\.\d+)?$/.test(stamp))
      ? Number(stamp) : NaN;
    const date = new Date(seconds * 1000);
    const beijing = panel.querySelector('[data-dual-beijing]');
    const utc = panel.querySelector('[data-dual-utc]');
    if (!(seconds > 0) || !Number.isFinite(date.getTime())) {
      update(beijing, text('待刷新', 'Not available'));
      update(utc, text('待刷新', 'Not available'));
      panel.querySelectorAll('time').forEach(node => node.removeAttribute('datetime'));
      return;
    }
    update(beijing, dateText(date, 'Asia/Shanghai'));
    update(utc, dateText(date, 'UTC'));
    panel.querySelectorAll('time').forEach(node => node.setAttribute('datetime', date.toISOString()));
  };
  const sync = () => {
    syncTime();
    const label = document.querySelector('[data-orbit-update-state]');
    if (!label) return;
    const isDelayed = banner?.classList.contains('is-stale') || banner?.classList.contains('is-error');
    const raw = (state?.textContent || '').trim();
    const current = window.MarsWebsiteLocale?.translate(raw) || raw;
    const waiting = !current || /^(?:正在读取|Loading|每分钟更新|Minute updates|毎分更新|매분 갱신)/i.test(current);
    update(label, isDelayed ? current : waiting
      ? text('链上数据 · 每分钟更新', 'On-chain data · Minute updates')
      : current);
    label.closest('.orbit-eyebrow')?.classList.toggle('is-stale', Boolean(isDelayed));
  };
  if (data) new MutationObserver(sync).observe(data, {childList: true, subtree: true, characterData: true});
  if (state) new MutationObserver(sync).observe(state, {childList: true, subtree: true, characterData: true});
  if (banner) new MutationObserver(sync).observe(banner, {attributes: true, attributeFilter: ['class']});
  new MutationObserver(sync).observe(document.documentElement, {attributes: true, attributeFilter: ['lang']});
  sync();
})();
