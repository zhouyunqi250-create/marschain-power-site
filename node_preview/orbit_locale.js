/* Synchronize the approved hero with the existing data and language runtime. */
(() => {
  'use strict';
  const source = document.querySelector('[data-preview-data-time]');
  const state = document.querySelector('[data-preview-state]');
  const banner = document.querySelector('[data-node-preview]');
  const update = (node, value) => {
    if (node && node.textContent !== value) node.textContent = value;
  };
  const sync = () => {
    const english = document.documentElement.lang.toLowerCase().startsWith('en');
    document.querySelectorAll('[data-hero-time-label]').forEach(node => update(node, english ? 'Updated' : '数据时间'));
    const raw = (source?.textContent || '').replace(/^(?:数据时间[：:]|Data time:)\s*/i, '');
    const zh = raw.match(/^\d{4}[/-](\d{1,2})[/-](\d{1,2})\s+(\d{2}:\d{2})/);
    const en = raw.match(/^(\d{1,2})\/(\d{1,2})\/\d{4},?\s+(\d{2}:\d{2})/);
    if (zh || en) {
      const month = (zh ? zh[1] : en[2]).padStart(2, '0');
      const day = (zh ? zh[2] : en[1]).padStart(2, '0');
      const clock = zh ? zh[3] : en[3];
      const text = month + '-' + day + ' ' + clock + (english ? ' UTC+8' : ' 北京时间');
      document.querySelectorAll('[data-hero-data-time]').forEach(node => update(node, text));
    }
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
  if (source) new MutationObserver(sync).observe(source, {childList: true, subtree: true, characterData: true});
  if (state) new MutationObserver(sync).observe(state, {childList: true, subtree: true, characterData: true});
  if (banner) new MutationObserver(sync).observe(banner, {attributes: true, attributeFilter: ['class']});
  new MutationObserver(sync).observe(document.documentElement, {attributes: true, attributeFilter: ['lang']});
  sync();
})();
