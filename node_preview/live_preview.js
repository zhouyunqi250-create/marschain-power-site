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
    if (!Number.isFinite(date)) return String(value || '尚未发布');
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }).format(date) + ' 北京时间';
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
      : '正在校准已发现地址的算力，暂未发布排名';
    children.push(count);
    controls.replaceChildren(...children);
  };
  document.addEventListener('click', (event) => {
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
      const bar = document.createElement('span');
      bar.className = mobile ? 'm-bar' : 'rank-bar';
      const width = document.createElement('i');
      width.style.width = `${Math.max(4, Math.min(100, Number(row.power) / maxPower * 100))}%`;
      bar.append(width);
      card.replaceChildren(top, address, bar);
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
      const summaries = panel.querySelectorAll(mobile ? '.m-burn-stat' : '.burn-stats .line');
      setText(summaries[mobile ? 0 : 1]?.querySelector('b'), estimate.burn_pool_display);
      setText(summaries[mobile ? 1 : 2]?.querySelector('b'), estimate.sample_burn_display);
      panel.querySelector('[data-burn-power]')?.dispatchEvent(new Event('input', { bubbles: true }));
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
    const calibrated = Number(preview.calibration_count || 0);
    const total = Number(preview.calibration_total || 0);
    const rawState = String(preview.state || '等待数据');
    const ready = rawState === 'ready' || rawState === 'running' || rawState === 'live';
    const calibrating = preview.calibration_done !== true && (total > calibrated || /calibrat|warm|bootstrap/.test(rawState));
    const state = calibrating
      ? `初始算力校准中 ${number(calibrated)} / ${number(total)}，排名尚在核对`
      : ready ? '节点增量采集运行中 · 每分钟处理已确认新区块' : `采集状态：${rawState}`;
    setText(banner.querySelector('[data-preview-state]'), state);
    const stamp = preview.generated_at || meta.generated_at_unix || meta.generated_at;
    const age = Date.now() - timestamp(stamp);
    const collectionAge = Date.now() - timestamp(preview.last_collection_success || preview.source_block_timestamp);
    const stale = (Number.isFinite(age) && age > 3 * 60 * 1000)
      || (Number.isFinite(collectionAge) && collectionAge > 3 * 60 * 1000);
    const seedNote = preview.ranking_source === 'public_seed_until_calibration_complete'
      ? ` · 校准期间排名沿用官网 ${displayTime(preview.seed_generated_at)}` : '';
    setText(banner.querySelector('[data-preview-data-time]'),
      `数据：${displayTime(stamp)} · 已处理区块 ${number(preview.source_block)}${stale ? ' · 数据更新已延迟' : ''}${seedNote}`);
    setText(banner.querySelector('[data-preview-scope]'),
      `排名范围：已发现 ${number(preview.known_addresses ?? meta.candidate_count)} 个地址，尚未覆盖全部链上地址。算力覆盖率衡量已发现算力占比，不代表地址名单完整。${calibrating ? '校准完成后再评估头部排行。' : ''}`);
    let dailyNode = banner.querySelector('[data-preview-daily]');
    if (!dailyNode) {
      dailyNode = document.createElement('p');
      dailyNode.dataset.previewDaily = '';
      banner.insertBefore(dailyNode, banner.querySelector('[data-preview-error]'));
    }
    const cutoff = meta.statistics_window_end_timestamp;
    const dailyState = meta.preview_daily_status?.state || '';
    const expected = timestamp(meta.preview_daily_status?.expected_end_timestamp);
    const dailyBehind = Number.isFinite(expected) && expected > timestamp(cutoff);
    const dailyNote = dailyBehind
      ? ' · 待形成完整新日结，保留原截止数据'
      : /verified/.test(dailyState) ? ' · 节点已核验完整日结'
        : /preserv/.test(dailyState) ? ' · 沿用官网已发布完整日结' : '';
    setText(dailyNode, `每日统计截止：${displayTime(cutoff)}${dailyNote}；7天统计截止：${displayTime(meta.period_7d_end_timestamp)}；30天统计截止：${displayTime(meta.period_30d_end_timestamp)}。`);
    const sources = meta.preview_metric_sources || {};
    const groups = new Map();
    const sourceLabel = (key, source) => {
      if (/price/.test(key)) return '价格';
      if (key === 'explorer_total_addresses') return '地址数';
      if (key === 'network_total_circulation_tokens') return '流通量';
      if (key === 'network_total_power') return source.source === 'local_rpc_power_contract' ? '节点算力' : '算力';
      if (key === 'network_total_burned_tokens') return '累计销毁';
      return '统计';
    };
    Object.entries(sources).forEach(([key, source]) => {
      if (!source || !/stale|unavailable|preserved_seed/.test(source.status || '')) return;
      const id = String(source.source || 'unknown');
      let group = groups.get(id);
      if (!group) {
        group = { labels: new Set(), stamps: [], local: id === 'local_rpc_power_contract' };
        groups.set(id, group);
      }
      group.labels.add(sourceLabel(key, source));
      if (source.fetched_at) group.stamps.push(source.fetched_at);
    });
    const notes = [];
    if (transportError) notes.push(`${transportError}，保留最近已载入榜单`);
    const collectorError = preview.source_error || preview.error;
    if (collectorError) notes.push(`节点采集提示：${String(collectorError).slice(0, 250)}`);
    groups.forEach((group) => {
      const fetched = group.stamps.length
        ? group.stamps.reduce((oldest, value) => timestamp(value) < timestamp(oldest) ? value : oldest)
        : null;
      notes.push(`${group.local ? '本机' : '官方'}${Array.from(group.labels).join('/')}暂未刷新，${fetched ? `上次成功：${displayTime(fetched)}` : '暂无成功抓取时间'}，保留该来源最近成功值`);
    });
    const powerSource = sources.network_total_power || {};
    if (groups.size && powerSource.source === 'local_rpc_power_contract' && powerSource.status === 'ok') {
      notes.push(Number.isFinite(collectionAge) && collectionAge > 3 * 60 * 1000
        ? '节点算力等待采集进度更新' : `节点算力读取正常（区块 ${number(powerSource.block || preview.source_block)}）`);
    }
    const error = notes.join('；');
    const errorNode = banner.querySelector('[data-preview-error]');
    errorNode.hidden = !error;
    if (error) setText(errorNode, `来源提示：${error}。`);
    banner.classList.toggle('is-error', Boolean(error));
    banner.classList.toggle('is-stale', stale);
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
      transportError = error.name === 'AbortError' ? '读取最新榜单超时' : (error.message || '读取最新榜单失败');
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
    transportError = `初始页面数据：${error.message}`;
    updateBanner();
  }
  refresh();
  window.setInterval(() => { updateBanner(); refresh(); }, intervalMs);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.addEventListener('focus', refresh);
})();
