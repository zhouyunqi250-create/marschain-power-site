/* Shared desktop/mobile controller for the public MarsChain report image. */
(() => {
  'use strict';
  if (window.__marsShareReportUiBound) return;
  window.__marsShareReportUiBound = true;

  const WIDTH = 1024;
  const HEIGHT = 1776;
  const DAY = 86400;
  let modal = null;
  let canvas = null;
  let panel = null;
  let placeholder = null;
  let lastTrigger = null;
  let previousOverflow = '';
  let operation = 0;
  let busy = false;
  let sharing = false;
  let exportable = false;
  let rendered = null;

  const COPY = {
    zh: {
      title: '火星快报',
      image: '火星快报图片',
      close: '关闭',
      download: '下载图片',
      share: '分享图片',
      refresh: '重新生成',
      scope: '实时快照展示当前数据；每日统计按北京时间 08:00 结算。图片保留实际数据时点及每日、7天、30天统计区间。',
      snapshot: '快照时间',
      daily: '每日统计区间',
      awaiting: '生成后显示',
      generating: '正在生成快报…',
      updating: '正在更新快报，当前显示上次生成的图片…',
      gate: '每日统计整理中，请稍后再试',
      failed: '快报暂未生成，请稍后重试。',
      oldFailed: '重新生成未完成，当前保留的是上次图片。请重新生成后再下载或分享。',
      ready: '快报已生成，可下载或分享。',
      downloaded: '图片已准备下载。',
      downloadFailed: '下载暂未完成，请重新点击下载图片。',
      fallback: '当前浏览器不支持图片分享，已改为下载图片。',
      shared: '已调用系统分享。',
      shareFailed: '系统分享未完成，可下载图片后转发。',
      sharing: '正在打开系统分享…',
      cancelled: '已取消分享，图片仍可下载。',
      expired: '数据已更新，请重新生成快报后下载或分享。',
      beijing: '（北京时间）',
    },
    en: {
      title: 'MarsChain Report',
      image: 'MarsChain report image',
      close: 'Close',
      download: 'Download Image',
      share: 'Share Image',
      refresh: 'Regenerate',
      scope: 'The live snapshot shows current data. Daily statistics settle at 08:00 Beijing time. The image retains the actual snapshot time and daily, 7-day and 30-day reporting windows.',
      snapshot: 'Snapshot Time',
      daily: 'Daily Reporting Window',
      awaiting: 'Shown after generation',
      generating: 'Generating the report…',
      updating: 'Updating the report; the previous image remains visible…',
      gate: 'Daily statistics are being prepared. Please try again shortly.',
      failed: 'The report could not be generated. Please try again shortly.',
      oldFailed: 'Regeneration did not complete. The previous image remains visible. Regenerate before downloading or sharing.',
      ready: 'The report is ready to download or share.',
      downloaded: 'The image is ready to download.',
      downloadFailed: 'The download did not complete. Please click Download Image again.',
      fallback: 'Image sharing is unavailable in this browser. The image has been downloaded instead.',
      shared: 'System sharing was opened.',
      shareFailed: 'System sharing did not complete. Download the image to forward it.',
      sharing: 'Opening system sharing…',
      cancelled: 'Sharing was cancelled. The image remains available to download.',
      expired: 'The data has changed. Regenerate the report before downloading or sharing.',
      beijing: ' (Beijing time)',
    },
  };
  const copy = () => COPY[String(document.documentElement.lang || '').toLowerCase().split('-')[0] === 'en' ? 'en' : 'zh'];
  const now = () => Math.floor(Date.now() / 1000);
  const stamp = (value) => {
    if (typeof value !== 'number' && typeof value !== 'string') return null;
    if (typeof value === 'string' && !/^\d+$/.test(value.trim())) return null;
    const parsed = Number(value);
    return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
  };
  const notReady = () => {
    const error = new Error('Report data is not ready');
    error.code = 'report_not_ready';
    return error;
  };
  const readBundle = () => {
    const payload = window.MarsShareReportPayload;
    if (payload && typeof payload === 'object' && !Array.isArray(payload)) return payload;
    const element = document.getElementById('rankData');
    try {
      return element ? JSON.parse(element.textContent || '{}').share_report : null;
    } catch (_) {
      return null;
    }
  };
  const assertReady = (bundle, current = now()) => {
    const sourceTime = stamp(bundle?.live?.meta?.node_preview?.source_block_timestamp);
    const dailyEnd = stamp(bundle?.daily?.meta?.statistics_window_end_timestamp);
    const expectedEnd = Math.floor(current / DAY) * DAY;
    if (!bundle || bundle.state !== 'ready' || sourceTime === null
      || sourceTime > current + 60 || current - sourceTime > 300 || dailyEnd !== expectedEnd) {
      throw notReady();
    }
    return bundle;
  };

  const setStatus = (text, kind = '') => {
    const status = modal?.querySelector('[data-poster-status]');
    if (!status) return;
    status.textContent = text;
    status.classList.toggle('is-error', kind === 'error');
    status.classList.toggle('is-ok', kind === 'ok');
  };
  const syncControls = () => {
    if (!modal) return;
    const unavailable = busy || sharing || !exportable;
    modal.querySelector('[data-poster-download]').disabled = unavailable;
    modal.querySelector('[data-poster-share]').disabled = unavailable;
    modal.querySelector('[data-poster-refresh]').disabled = busy || sharing;
    modal.querySelectorAll('.poster-actions button').forEach((button) => {
      button.style.opacity = button.disabled ? '.5' : '';
      button.style.cursor = button.disabled ? 'not-allowed' : '';
    });
    panel.setAttribute('aria-busy', String(busy));
  };
  const showImage = (visible) => {
    canvas.hidden = !visible;
    canvas.style.display = visible ? '' : 'none';
    placeholder.hidden = visible;
    placeholder.style.display = visible ? 'none' : '';
  };
  const syncMeta = () => {
    if (!modal) return;
    const c = copy();
    const value = rendered?.data;
    modal.querySelector('[data-poster-snapshot]').textContent = value
      ? value.live.snapshotTimeText + c.beijing : c.awaiting;
    modal.querySelector('[data-poster-daily]').textContent = value
      ? value.daily.rangeText + c.beijing : c.awaiting;
  };
  const syncLanguage = () => {
    if (!modal) return;
    const c = copy();
    for (const [selector, text] of [
      ['[data-poster-title]', c.title], ['[data-poster-scope]', c.scope],
      ['[data-poster-snapshot-label]', c.snapshot], ['[data-poster-daily-label]', c.daily],
      ['[data-poster-download]', c.download], ['[data-poster-share]', c.share],
      ['[data-poster-refresh]', c.refresh],
    ]) modal.querySelector(selector).textContent = text;
    modal.querySelector('[data-poster-close]').setAttribute('aria-label', c.close);
    canvas.setAttribute('aria-label', c.image);
    placeholder.textContent = c.generating;
    syncMeta();
  };
  const close = () => {
    if (!modal || modal.hidden) return;
    operation += 1;
    busy = false;
    sharing = false;
    modal.hidden = true;
    document.body.style.overflow = previousOverflow;
    syncControls();
    if (lastTrigger?.isConnected && typeof lastTrigger.focus === 'function') {
      lastTrigger.focus({ preventScroll: true });
    }
  };
  const canvasBlob = (element) => new Promise((resolve, reject) => {
    try {
      element.toBlob((blob) => {
        if (blob && blob.size > 0) resolve(blob);
        else reject(new Error('Report image could not be encoded'));
      }, 'image/png');
    } catch (error) {
      reject(error);
    }
  });
  const filename = (data) => {
    const date = String(data?.daily?.dateLabel || '');
    return /^\d{4}\.\d{2}\.\d{2}$/.test(date)
      ? `marschain-report-${date.replace(/\./g, '-')}.png` : 'marschain-report.png';
  };

  const generate = async () => {
    if (busy || sharing || !modal || modal.hidden) return;
    const token = ++operation;
    busy = true;
    exportable = false;
    syncControls();
    setStatus(rendered ? copy().updating : copy().generating);
    if (!rendered) showImage(false);
    try {
      assertReady(readBundle());
      if (typeof window.MarsReportData?.build !== 'function'
        || typeof window.MarsUnifiedReport?.prepare !== 'function'
        || typeof window.MarsUnifiedReport?.render !== 'function') {
        throw new Error('Report image is unavailable');
      }
      await Promise.all([
        document.fonts ? document.fonts.ready : Promise.resolve(),
        window.MarsUnifiedReport.prepare(),
      ]);
      if (token !== operation || modal.hidden) return;
      // A source update or the Beijing 08:00 boundary can occur while assets load.
      const bundle = assertReady(readBundle());
      const data = window.MarsReportData.build(bundle);
      const staged = document.createElement('canvas');
      window.MarsUnifiedReport.render(staged, data);
      if (staged.width !== WIDTH || staged.height !== HEIGHT) {
        throw new Error('Report image size is unavailable');
      }
      const blob = await canvasBlob(staged);
      if (token !== operation || modal.hidden) return;
      assertReady(bundle);
      // Replace the displayed element only after a complete image is available.
      // No failed attempt can clear the previously successful picture.
      staged.dataset.posterCanvas = '';
      staged.setAttribute('aria-label', copy().image);
      canvas.replaceWith(staged);
      canvas = staged;
      rendered = { data, bundle, blob, filename: filename(data) };
      exportable = true;
      syncMeta();
      showImage(true);
      setStatus(copy().ready, 'ok');
    } catch (error) {
      if (token !== operation || modal.hidden) return;
      exportable = false;
      const message = error?.code === 'report_not_ready' ? copy().gate : copy().failed;
      if (rendered) setStatus(`${message} ${copy().oldFailed}`, 'error');
      else {
        placeholder.textContent = message;
        showImage(false);
        setStatus(message, 'error');
      }
    } finally {
      if (token === operation) {
        busy = false;
        syncControls();
      }
    }
  };

  const readyForExport = () => {
    if (busy || sharing || !exportable || !rendered) return false;
    try {
      assertReady(rendered.bundle);
      return true;
    } catch (_) {
      exportable = false;
      syncControls();
      setStatus(copy().expired, 'error');
      return false;
    }
  };
  const download = () => {
    if (!readyForExport()) return false;
    let url = null;
    let anchor = null;
    try {
      url = URL.createObjectURL(rendered.blob);
      anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = rendered.filename;
      anchor.style.display = 'none';
      document.body.appendChild(anchor);
      anchor.click();
      setStatus(copy().downloaded, 'ok');
      return true;
    } catch (_) {
      setStatus(copy().downloadFailed, 'error');
      return false;
    } finally {
      anchor?.remove();
      if (url) window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  };
  const share = async () => {
    if (!readyForExport()) return;
    let file = null;
    let supported = false;
    try {
      if (typeof File === 'function' && typeof navigator.share === 'function'
        && typeof navigator.canShare === 'function') {
        file = new File([rendered.blob], rendered.filename, { type: 'image/png' });
        supported = navigator.canShare({ files: [file] });
      }
    } catch (_) {
      supported = false;
    }
    if (!supported) {
      if (download()) setStatus(copy().fallback, 'ok');
      return;
    }
    const token = operation;
    sharing = true;
    syncControls();
    setStatus(copy().sharing);
    try {
      // The Blob is prepared during generation, so this call keeps the click's
      // user activation rather than awaiting image work before system sharing.
      await navigator.share({ files: [file], title: 'MarsChain 火星快报' });
      if (token === operation && !modal.hidden) setStatus(copy().shared, 'ok');
    } catch (error) {
      if (token !== operation || modal.hidden) return;
      if (error?.name === 'AbortError') setStatus(copy().cancelled);
      else setStatus(copy().shareFailed, 'error');
    } finally {
      if (token === operation) {
        sharing = false;
        syncControls();
      }
    }
  };

  const ensureModal = () => {
    if (modal) return;
    modal = document.createElement('div');
    modal.className = 'poster-modal';
    modal.hidden = true;
    modal.innerHTML = `
      <section class="poster-panel" role="dialog" aria-modal="true" aria-labelledby="marsReportTitle" aria-describedby="marsReportScope" tabindex="-1">
        <div class="poster-preview">
          <canvas data-poster-canvas hidden></canvas>
          <p class="poster-status" data-poster-placeholder></p>
        </div>
        <div class="poster-copy">
          <div class="poster-head">
            <div><span>MARSCHAIN</span><h3 id="marsReportTitle" data-poster-title></h3></div>
            <button class="poster-close" type="button" data-poster-close>×</button>
          </div>
          <p id="marsReportScope" data-poster-scope></p>
          <ul class="poster-meta">
            <li><span data-poster-snapshot-label></span><b data-poster-snapshot></b></li>
            <li><span data-poster-daily-label></span><b data-poster-daily></b></li>
          </ul>
          <div class="poster-actions">
            <button type="button" data-poster-download disabled></button>
            <button type="button" data-poster-share disabled></button>
            <button type="button" data-poster-refresh></button>
          </div>
          <div class="poster-status" data-poster-status role="status" aria-live="polite" aria-atomic="true"></div>
        </div>
      </section>`;
    document.body.appendChild(modal);
    panel = modal.querySelector('.poster-panel');
    canvas = modal.querySelector('[data-poster-canvas]');
    placeholder = modal.querySelector('[data-poster-placeholder]');
    showImage(false);
    modal.addEventListener('click', (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (event.target === modal || target?.closest('[data-poster-close]')) close();
      else if (target?.closest('[data-poster-download]')) download();
      else if (target?.closest('[data-poster-share]')) void share();
      else if (target?.closest('[data-poster-refresh]')) void generate();
    });
  };
  const open = (trigger) => {
    ensureModal();
    if (!modal.hidden) return;
    lastTrigger = trigger || document.activeElement;
    previousOverflow = document.body.style.overflow;
    syncLanguage();
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    panel.scrollTop = 0;
    modal.querySelector('[data-poster-close]').focus({ preventScroll: true });
    void generate();
  };
  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target.closest('[data-share-poster]') : null;
    if (!target || target.disabled || target.getAttribute('aria-disabled') === 'true') return;
    event.preventDefault();
    open(target);
  });
  // A click may precede the first live-data response. Finish that pending
  // generation when it arrives; an already-generated picture stays fixed.
  window.addEventListener('mars-share-report-data', () => {
    if (modal && !modal.hidden && !busy && !sharing && !exportable) void generate();
  });
  document.addEventListener('keydown', (event) => {
    if (!modal || modal.hidden) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(panel.querySelectorAll(
      'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter((element) => element.tabIndex >= 0 && element.getClientRects().length > 0);
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!first) {
      event.preventDefault();
      panel.focus();
    } else if (event.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
    }
  });
  document.addEventListener('focusin', (event) => {
    if (modal && !modal.hidden && !panel.contains(event.target)) {
      modal.querySelector('[data-poster-close]').focus({ preventScroll: true });
    }
  });
})();
