/* Isolated preview: static layout, versioned lightweight data, no wallet balances. */
(() => {
  'use strict';
  const intervalMs = 25000;
  const mobile = Boolean(document.getElementById('mobileRankList'));
  const grid = document.getElementById(mobile ? 'mobileRankList' : 'rankGrid');
  const pageSize = 10;
  let currentPage = 1;
  let currentRows = [];
  let lastVersion = '';
  let latestPayload = null;
  let pending = false;
  let transportError = '';
  let latestEtag = '';
  const banner = document.querySelector('[data-node-preview]');
  const dataNotes = document.querySelector('[data-node-data-notes]');
  const english = () => document.documentElement.lang === 'en';
  const copy = (zh, en) => english() ? en : zh;
  const rankDataNode = document.getElementById('rankData');
  const trendDataNode = document.getElementById('metricTrendData');
  const setText = (node, value) => {
    if (node) node.replaceChildren(document.createTextNode(String(value ?? '待刷新')));
  };
  const number = (value) => Number(value || 0).toLocaleString('zh-CN');
  const displayPower = (value) => {
    if (value === null || value === undefined || value === '') return '待刷新';
    const power = Number(value);
    if (!Number.isFinite(power)) return '待刷新';
    const abs = Math.abs(power);
    if (abs >= 1e12) return `${(power / 1e12).toFixed(3)}万亿`;
    if (abs >= 1e8) return `${(power / 1e8).toFixed(3)}亿`;
    if (abs >= 1e4) return `${(power / 1e4).toFixed(3)}万`;
    return power.toLocaleString('zh-CN', { maximumFractionDigits: 0 });
  };
  const timestamp = (value) => {
    if (typeof value === 'number') return value < 1e12 ? value * 1000 : value;
    if (typeof value === 'string' && /^\d+(\.\d+)?$/.test(value)) return timestamp(Number(value));
    return Date.parse(value || '');
  };
  const displayTime = (value) => {
    const date = timestamp(value);
    if (!Number.isFinite(date)) return copy('尚未发布', 'Not published');
    return new Intl.DateTimeFormat(english() ? 'en-GB' : 'zh-CN', {
      timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }).format(date) + copy(' 北京时间', ' Beijing time');
  };

  const getPagination = () => {
    let controls = document.querySelector(mobile ? '[data-mobile-rank-pagination]' : '[data-rank-pagination]');
    if (!controls && grid) {
      controls = document.createElement('div');
      controls.className = mobile ? 'm-rank-pagination' : 'rank-pagination';
      controls.setAttribute(mobile ? 'data-mobile-rank-pagination' : 'data-rank-pagination', '');
      grid.after(controls);
    }
    return controls;
  };
  const renderPage = () => {
    if (!grid) return;
    const pages = Math.max(1, Math.ceil(currentRows.length / pageSize));
    currentPage = Math.max(1, Math.min(pages, currentPage));
    Array.from(grid.children).forEach((card, index) => {
      const visible = index >= (currentPage - 1) * pageSize && index < currentPage * pageSize;
      card.classList.toggle('is-page-hidden', !visible);
      if (visible) card.classList.add('visible');
    });
    const controls = getPagination();
    if (!controls) return;
    const buttonClass = mobile ? 'm-rank-page-button' : 'rank-page-button';
    const button = (label, page, disabled = false) => {
      const element = document.createElement('button');
      element.type = 'button';
      element.className = buttonClass;
      element.dataset.previewPage = String(page);
      element.disabled = disabled;
      element.textContent = label;
      return element;
    };
    const children = [button('上一页', currentPage - 1, currentPage === 1)];
    const group = document.createElement('div');
    group.className = mobile ? 'm-rank-pages' : 'rank-pages';
    for (let page = 1; page <= pages; page += 1) {
      const element = button(String(page), page);
      element.classList.toggle('is-active', currentPage === page);
      element.setAttribute('aria-current', currentPage === page ? 'page' : 'false');
      group.append(element);
    }
    children.push(group, button('下一页', currentPage + 1, currentPage === pages));
    const count = document.createElement('span');
    count.className = mobile ? 'm-rank-count' : 'rank-count';
    count.textContent = currentRows.length
      ? `第 ${currentPage} / ${pages} 页 · 当前显示 ${(currentPage - 1) * pageSize + 1}-${Math.min(currentPage * pageSize, currentRows.length)} / 共 ${currentRows.length} 名`
      : '榜单正在核验，暂未发布排名';
    children.push(count);
    controls.replaceChildren(...children);
  };
  document.addEventListener('click', (event) => {
    const copyButton = event.target.closest('[data-node-copy-address]');
    if (copyButton) {
      const value = copyButton.dataset.nodeCopyAddress || '';
      if (!/^0x[0-9a-fA-F]{40}$/.test(value)) return;
      if (!navigator.clipboard?.writeText) {
        setText(copyButton, copy('请手动复制地址', 'Copy the address manually'));
        return;
      }
      navigator.clipboard.writeText(value).then(() => {
        setText(copyButton, copy('已复制', 'Copied'));
        window.setTimeout(() => setText(copyButton, copy('复制地址', 'Copy address')), 1800);
      }).catch(() => setText(copyButton, copy('请手动复制地址', 'Copy the address manually')));
      return;
    }
    const button = event.target.closest('[data-preview-page]');
    if (!button || button.disabled) return;
    currentPage = Number(button.dataset.previewPage || 1);
    renderPage();
    window.applyMarsLanguage?.();
  });

  const renderRanks = (rows) => {
    if (!grid) throw new Error('排行榜容器不存在，请重新部署预览页面');
    currentRows = rows;
    const maxPower = Math.max(1, ...rows.map((row) => Number(row.power) || 0));
    // Keep stable article nodes. Existing hover/focus behavior and pagination survive.
    rows.forEach((row, index) => {
      let card = grid.children[index];
      if (!card) {
        card = document.createElement('article');
        card.className = mobile ? 'm-rank-card m-reveal visible' : 'rank-card reveal visible';
        grid.append(card);
      }
      const top = document.createElement('div');
      top.className = mobile ? 'm-rank-top' : 'rank-top';
      const rank = document.createElement('em');
      rank.textContent = `${mobile ? '#' : ''}${String(row.rank ?? index + 1).padStart(2, '0')}`;
      const power = document.createElement('strong');
      power.textContent = displayPower(row.power);
      top.append(rank, power);
      const address = document.createElement('code');
      address.textContent = String(row.address || '');
      const actions = document.createElement('div');
      actions.className = 'node-rank-actions';
      const copyButton = document.createElement('button');
      copyButton.type = 'button';
      copyButton.dataset.nodeCopyAddress = String(row.address || '');
      copyButton.textContent = '复制地址';
      const explorer = document.createElement('a');
      explorer.href = `https://explorer.marschain.net/address/${encodeURIComponent(row.address || '')}`;
      explorer.target = '_blank';
      explorer.rel = 'noopener noreferrer';
      explorer.textContent = '查看地址';
      actions.append(copyButton, explorer);
      const bar = document.createElement('span');
      bar.className = mobile ? 'm-bar' : 'rank-bar';
      const width = document.createElement('i');
      width.style.width = `${Math.max(4, Math.min(100, Number(row.power) / maxPower * 100))}%`;
      bar.append(width);
      card.replaceChildren(top, address, actions, bar);
    });
    while (grid.children.length > rows.length) grid.lastElementChild.remove();
    grid.dataset.totalCount = String(rows.length);
    renderPage();
  };

  const replaceMarkup = (selector, markup) => {
    const node = document.querySelector(selector);
    if (!node || typeof markup !== 'string') throw new Error(`展示数据不完整：${selector}`);
    node.innerHTML = markup;
  };
  const renderMetrics = (view) => {
    const device = mobile ? view.mobile : view.desktop;
    if (!device || !device.metrics || !Array.isArray(device.trends)) {
      throw new Error('缺少核心指标或趋势数据，请检查采集发布程序');
    }
    const hero = document.querySelector(mobile ? '.m-hero' : '.hero-copy');
    let warning = hero?.querySelector('[data-preview-coverage-warning]');
    // Remove legacy seed warnings even when upstream markup nests them. Keep
    // exactly one owned slot so a false -> true target update really clears it.
    hero?.querySelectorAll('.alert,.m-note').forEach((node) => {
      if (warning?.contains(node)) return;
      if (/本轮(?:算力)?覆盖率|页面仍发布当轮最佳扫描结果/.test(node.textContent || '')) node.remove();
    });
    if (!warning && hero) {
      warning = document.createElement('div');
      warning.dataset.previewCoverageWarning = '';
      hero.append(warning);
    }
    if (warning) warning.innerHTML = device.warning || '';
    document.querySelectorAll(mobile ? '.m-card[data-label]' : '.metric[data-label]').forEach((card) => {
      const value = device.metrics[card.dataset.label];
      if (!value || typeof value.html !== 'string') throw new Error(`指标未提供：${card.dataset.label}`);
      card.innerHTML = value.html;
      // The original mobile hero renders a subset and resets indexes. Point each
      // duplicate card to the full list so its chart opens the intended metric.
      card.dataset.trendIndex = String(value.trend_index);
    });
    if (mobile) {
      replaceMarkup('.m-meta', device.timeline);
      setText(document.querySelector('.m-primary b'), view.coverage);
    } else {
      replaceMarkup('.timeline', device.timeline);
      replaceMarkup('.growth-grid', device.growth);
      replaceMarkup('.footer-marquee .track', device.marquee);
      document.querySelectorAll('.growth-card.reveal').forEach((node) => node.classList.add('visible'));
      setText(document.querySelector('.radar .core b'), view.coverage);
      setText(document.querySelector('.hero-actions .btn.hot'), `算力覆盖率 ${view.coverage}`);
      const stats = document.querySelectorAll('.radar .mini b');
      [view.candidate_count, view.positive_ratio, view.uncovered_power].forEach((value, index) => setText(stats[index], value));
    }
    if (trendDataNode) trendDataNode.textContent = JSON.stringify(device.trends);
    window.dispatchEvent(new CustomEvent('mars-preview-data', { detail: { trends: device.trends } }));
    const estimate = view.burn;
    if (!estimate) throw new Error('缺少算力销毁测算数据');
    document.querySelectorAll('[data-burn-calculator]').forEach((panel) => {
      panel.dataset.burnRate = String(estimate.burn_per_power_attr || '0');
      panel.querySelectorAll('[data-burn-field]').forEach(node => {
        setText(node, estimate[node.dataset.burnField]);
      });
      panel.querySelector('[data-burn-power]')?.dispatchEvent(new Event('input', { bubbles: true }));
    });
    document.querySelectorAll('[data-christmas-forecast]').forEach(panel => {
      if (typeof view.christmas !== 'string') return;
      const expanded = Boolean(panel.querySelector('details')?.open);
      panel.innerHTML = view.christmas;
      if (expanded && panel.querySelector('details')) panel.querySelector('details').open = true;
    });
    setText(document.querySelector(mobile ? '.m-footer' : '.footer'), mobile
      ? `MarsChain Rank 手机版 · 最近刷新：${view.generated_at} · 统计周期：${view.statistics_window_label}`
      : `基于公开 API、RPC 与合约日志生成的 best effort 榜单 · 最近刷新：${view.generated_at} · 统计周期：${view.statistics_window_label}`);
    if (!mobile) {
      const brand = document.createElement('strong');
      brand.textContent = 'www.marschainrank.com';
      document.querySelector('.footer')?.append(brand);
    }
  };

  const updateBanner = () => {
    if (!banner) return;
    const meta = latestPayload?.meta || {};
    const preview = meta.node_preview || {};
    const stamp = preview.source_block_timestamp;
    const age = Date.now() - timestamp(stamp);
    const generationAge = Date.now() - timestamp(preview.generated_at || meta.generated_at);
    const missing = !Number.isFinite(age) || !Number.isFinite(generationAge);
    const stale = missing || age > 3 * 60 * 1000 || generationAge > 3 * 60 * 1000;
    const ready = preview.state === 'ready';
    const state = transportError ? copy('暂未取到新数据', 'New data unavailable')
      : stale ? copy('数据更新延迟', 'Data update delayed')
        : ready ? copy('每分钟更新', 'Minute updates') : copy('数据核验中', 'Verifying data');
    setText(banner.querySelector('[data-preview-state]'), state);
    setText(banner.querySelector('[data-preview-data-time]'),
      `${copy('数据时间：', 'Data time: ')}${displayTime(stamp)}`);
    banner.classList.toggle('is-error', Boolean(transportError));
    banner.classList.toggle('is-stale', stale);
    if (!dataNotes) return;
    setText(dataNotes.querySelector('[data-preview-scope]'), english()
      ? `The ranking shows the top 100 among ${number(meta.candidate_count ?? preview.known_addresses)} discovered addresses. The address list is incomplete; power coverage is not address coverage.`
      : `榜单展示已发现的 ${number(meta.candidate_count ?? preview.known_addresses)} 个地址中的算力前 100 名。地址名单尚不完整；算力覆盖率不代表地址覆盖率。`);
    setText(dataNotes.querySelector('[data-preview-frequency]'), copy(
      '榜单约每分钟刷新，已确认交易可能稍后显示；每日统计按北京时间 08:00 日结。',
      'The ranking refreshes about every minute. Confirmed transactions may appear later; daily statistics close at 08:00 Beijing time.'));
    setText(dataNotes.querySelector('[data-preview-sources]'), copy(
      '算力和累计销毁来自自建节点；价格、全网地址总数、流通量来自官方浏览器。',
      'Power and cumulative burns come from the self-hosted node. Prices, total addresses and circulation come from the official explorer.'));
    setText(dataNotes.querySelector('[data-preview-sampling]'), copy(
      '官方价格、地址的日历史取自北京时间 08:00 附近采样；每日新增地址为相邻统计日采样差额，可能与精确截点值存在差异。',
      'Daily price and address history is sampled near 08:00 Beijing time. Daily new addresses are the difference between adjacent daily samples and may differ from exact cutoff measurements.'));
    const cutoff = meta.statistics_window_end_timestamp;
    const expected = timestamp(meta.preview_daily_status?.expected_end_timestamp);
    const dailyBehind = Number.isFinite(expected) && expected > timestamp(cutoff);
    const dailyNote = dailyBehind ? copy('（保留原截止数据）', ' (previous close retained)') : '';
    setText(dataNotes.querySelector('[data-preview-daily]'), english()
      ? `Daily statistics through ${displayTime(cutoff)}${dailyNote}. 7-day statistics through ${displayTime(meta.period_7d_end_timestamp)}. 30-day statistics through ${displayTime(meta.period_30d_end_timestamp)}.`
      : `每日统计截至 ${displayTime(cutoff)}${dailyNote}；7 天统计截至 ${displayTime(meta.period_7d_end_timestamp)}；30 天统计截至 ${displayTime(meta.period_30d_end_timestamp)}。`);
    const labels = {
      network_total_power: copy('算力', 'Power'),
      network_total_burned_tokens: copy('累计销毁', 'Cumulative burns'),
      explorer_total_addresses: copy('全网地址数', 'Total addresses'),
      network_total_circulation_tokens: copy('流通量', 'Circulation'),
      network_current_price: copy('价格', 'Price'),
      network_highest_price: copy('最高价格', 'Highest price'),
    };
    const notes = [];
    if (transportError || stale) notes.push(copy('暂未取得最新榜单，显示最近成功数据及其原时间。', 'The latest ranking is unavailable. The last successful data and its original time are shown.'));
    Object.entries(meta.preview_metric_sources || {}).forEach(([key, source]) => {
      if (!labels[key] || !source) return;
      const fetched = timestamp(source.fetched_at);
      const sourceOld = Number.isFinite(fetched) && Date.now() - fetched > 5 * 60 * 1000;
      if (source.status === 'ok' && !sourceOld) return;
      notes.push(english()
        ? `${labels[key]} has not refreshed${Number.isFinite(fetched) ? `; last success: ${displayTime(source.fetched_at)}` : '; no successful source time'}.`
        : `${labels[key]}暂未刷新${Number.isFinite(fetched) ? `，上次成功：${displayTime(source.fetched_at)}` : '，暂无成功来源时间'}。`);
    });
    const note = dataNotes.querySelector('[data-preview-source-notes]');
    if (note) {
      note.hidden = notes.length === 0;
      if (notes.length) setText(note, notes.join(' '));
    }
  };
  const apply = (payload) => {
    if (!payload || !payload.meta || !Array.isArray(payload.rows) || payload.rows.length > 100) {
      throw new Error('排行榜数据格式错误');
    }
    if (payload.view?.schema !== 1) throw new Error('展示数据版本不兼容，请重新部署预览页面');
    const preview = payload.meta.node_preview || {};
    const version = String(preview.version || payload.meta.generated_at_unix || payload.meta.generated_at || '');
    if (!version) throw new Error('排行榜缺少数据版本');
    latestPayload = payload;
    if (version !== lastVersion) {
      renderMetrics(payload.view);
      renderRanks(payload.rows);
      if (rankDataNode) rankDataNode.textContent = JSON.stringify({ meta: payload.meta, rows: payload.rows });
      lastVersion = version;
      window.applyMarsLanguage?.();
    }
    updateBanner();
  };
  const refresh = async () => {
    if (document.hidden || pending) return;
    pending = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    try {
      const headers = latestEtag ? { 'If-None-Match': latestEtag } : {};
      const response = await fetch(`/data/latest.json?v=${Date.now()}`, { cache: 'no-store', headers, signal: controller.signal });
      if (response.status === 304) {
        transportError = '';
        updateBanner();
        return;
      }
      if (!response.ok) throw new Error(`读取最新榜单失败 HTTP ${response.status}`);
      const payload = await response.json();
      transportError = '';
      apply(payload);
      latestEtag = response.headers.get('ETag') || '';
    } catch (error) {
      transportError = error.name === 'AbortError' ? 'timeout' : 'unavailable';
      updateBanner();
    } finally {
      window.clearTimeout(timeout);
      pending = false;
    }
  };
  try {
    const seed = JSON.parse(rankDataNode?.textContent || '{}');
    apply(seed);
  } catch (error) {
    transportError = 'unavailable';
    updateBanner();
  }
  refresh();
  window.setInterval(() => { updateBanner(); refresh(); }, intervalMs);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.addEventListener('focus', refresh);
  document.querySelectorAll('[data-lang-toggle]').forEach((button) => {
    button.addEventListener('click', () => window.setTimeout(updateBanner, 0));
  });
})();
