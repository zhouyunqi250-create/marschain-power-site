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
  const syncTime = english => {
    if (!panel) return;
    update(panel.querySelector('[data-dual-time-caption]'), english ? 'Data time' : '数据时间');
    update(panel.querySelector('[data-dual-beijing-label]'), english ? 'Beijing time' : '北京时间');
    update(panel.querySelector('[data-dual-utc-label]'), english ? 'Universal time' : '世界标准时间');
    panel.setAttribute('aria-label', english ? 'Data time' : '数据时间');
    let stamp = null;
    try { stamp = JSON.parse(data?.textContent || '').meta?.node_preview?.source_block_timestamp; } catch (_) {}
    const seconds = typeof stamp === 'number' || (typeof stamp === 'string' && /^\d+(?:\.\d+)?$/.test(stamp))
      ? Number(stamp) : NaN;
    const date = new Date(seconds * 1000);
    const beijing = panel.querySelector('[data-dual-beijing]');
    const utc = panel.querySelector('[data-dual-utc]');
    if (!(seconds > 0) || !Number.isFinite(date.getTime())) {
      update(beijing, english ? 'Not available' : '待刷新');
      update(utc, english ? 'Not available' : '待刷新');
      panel.querySelectorAll('time').forEach(node => node.removeAttribute('datetime'));
      return;
    }
    update(beijing, dateText(date, 'Asia/Shanghai'));
    update(utc, dateText(date, 'UTC'));
    panel.querySelectorAll('time').forEach(node => node.setAttribute('datetime', date.toISOString()));
  };
  const sync = () => {
    const english = document.documentElement.lang.toLowerCase().startsWith('en');
    syncTime(english);
    const label = document.querySelector('[data-orbit-update-state]');
    if (!label) return;
    const isDelayed = banner?.classList.contains('is-stale') || banner?.classList.contains('is-error');
    const current = (state?.textContent || '').trim();
    const waiting = !current || /^(?:正在读取|Loading|每分钟更新|Minute updates)/i.test(current);
    update(label, isDelayed ? current : waiting
      ? (english ? 'On-chain data · Minute updates' : '链上数据 · 每分钟更新')
      : current);
    label.closest('.orbit-eyebrow')?.classList.toggle('is-stale', Boolean(isDelayed));
  };
  if (data) new MutationObserver(sync).observe(data, {childList: true, subtree: true, characterData: true});
  if (state) new MutationObserver(sync).observe(state, {childList: true, subtree: true, characterData: true});
  if (banner) new MutationObserver(sync).observe(banner, {attributes: true, attributeFilter: ['class']});
  new MutationObserver(sync).observe(document.documentElement, {attributes: true, attributeFilter: ['lang']});
  sync();
})();
