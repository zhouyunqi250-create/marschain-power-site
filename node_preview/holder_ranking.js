/* Native MARS holder ranking. Shares the site's published data transport. */
(() => {
  'use strict';
  const COPY = {
    power: ['算力排行', 'Power Ranking', '演算力ランキング', '연산력 순위'],
    holders: ['持币排行', 'MARS Holders', 'MARS保有ランキング', 'MARS 보유 순위'],
    addressRank: ['地址排行', 'Address Rankings', 'アドレスランキング', '주소 순위'],
    sort: ['余额从高到低', 'Balance: highest first', '残高の多い順', '잔액 높은 순'],
    title: ['MARS 持币排行', 'MARS Holder Ranking', 'MARS保有ランキング', 'MARS 보유 순위'],
    intro: ['按同一区块的 MARS 余额排序，观察头部地址的持币分布。', 'Balances at the same confirmed block, ranked to show how MARS holdings are distributed.', '同一確定ブロック時点のMARS残高から、上位アドレスの保有分布を確認できます。', '동일 확정 블록의 MARS 잔액으로 상위 주소의 보유 분포를 확인합니다.'],
    systemsTitle: ['系统地址专区', 'System Addresses', 'システムアドレス', '시스템 주소'],
    systemsKicker: ['SYSTEM / 独立展示', 'SYSTEM / SEPARATE VIEW', 'SYSTEM / 個別表示', 'SYSTEM / 별도 표시'],
    systemsNote: ['仅列系统地址，余额不纳入统计。', 'System addresses only; their balances are excluded from statistics.', 'システムアドレスのみを表示し、その残高は集計に含めません。', '시스템 주소만 표시하며 잔액은 집계에서 제외합니다.'],
    powerSystem: ['POWER 系统合约', 'POWER System Contract', 'POWERシステムコントラクト', 'POWER 시스템 컨트랙트'],
    allRanking: ['全部地址排行', 'All Addresses', '全アドレスランキング', '전체 주소 순위'],
    rankingNote: ['包含钱包、合约及已识别的销毁地址；系统地址已在上方单列。', 'Includes wallets, contracts and identified burn addresses. System addresses are listed separately above.', 'ウォレット、コントラクト、識別済みバーンアドレスを含みます。システムアドレスは上に別途表示しています。', '지갑, 컨트랙트, 식별된 소각 주소를 포함하며 시스템 주소는 위에 별도로 표시합니다.'],
    addresses: ['参与排名地址', 'Ranking Addresses', 'ランキング対象アドレス', '순위 집계 주소'],
    topTotal: ['本榜 TOP100 合计', 'This Top 100 Total', 'この上位100件の合計', '현재 상위 100개 합계'],
    topShare: ['本榜 TOP100 占比', 'This Top 100 Share', 'この上位100件の比率', '현재 상위 100개 비중'],
    powerTopTotal: ['本榜 TOP100 算力合计', 'Top 100 Total Power', '上位100件の演算力合計', '상위 100개 연산력 합계'],
    powerTopShare: ['本榜 TOP100 算力占比', 'Top 100 Power Share', '上位100件の演算力比率', '상위 100개 연산력 비중'],
    powerCountNote: ['已收录地址中，当前算力大于零的地址。', 'Indexed addresses with positive current power.', '収録済みアドレスのうち、現在の演算力がゼロより大きいアドレスです。', '수집된 주소 중 현재 연산력이 0보다 큰 주소입니다.'],
    powerShareNote: ['前100名算力合计 ÷ 同一快照的全网总算力。', 'Top 100 total power divided by network total power from the same snapshot.', '同じスナップショットの上位100件の演算力合計 ÷ ネットワーク総演算力。', '동일 스냅샷의 상위 100개 연산력 합계 ÷ 네트워크 전체 연산력입니다.'],
    number: ['名次', 'Rank', '順位', '순위'],
    address: ['地址', 'Address', 'アドレス', '주소'],
    balance: ['MARS 余额', 'MARS Balance', 'MARS残高', 'MARS 잔액'],
    share: ['余额占比', 'Balance Share', '残高比率', '잔액 비중'],
    placeholder: ['搜索本榜 TOP100 地址', 'Search this top 100', 'この上位100件から検索', '현재 상위 100개 주소 검색'],
    search: ['搜索地址', 'Search addresses', 'アドレスを検索', '주소 검색'],
    snapshot: ['区块快照', 'Block Snapshot', 'ブロックスナップショット', '블록 스냅샷'],
    source: ['数据来自自建节点，同一确认区块读取余额。榜单约每分钟更新，时间为余额所在区块的实际时间。', 'Balances are read from our node at the same confirmed block. Rankings update about every minute; the displayed time is the actual balance block time.', '自前ノードの同一確定ブロックから残高を取得します。ランキングは約1分ごとに更新し、表示時刻は残高を取得したブロックの実際の時刻です。', '자체 노드의 동일 확정 블록에서 잔액을 조회합니다. 순위는 약 1분마다 갱신되며 표시 시각은 잔액을 조회한 블록의 실제 시각입니다.'],
    stale: ['数据暂未更新，保留最近一次成功结果及其原时间。', 'Updates are delayed. The last successful ranking and its original time are retained.', '更新が遅れています。最後に取得できたランキングとその時刻を表示しています。', '갱신이 지연되고 있습니다. 마지막으로 확인한 순위와 해당 시각을 유지합니다.'],
    coverage: ['已收录地址中的排名，名单尚未覆盖全链。', 'Ranks indexed addresses; the list does not yet cover the entire chain.', '収録済みアドレス内の順位です。一覧はチェーン全体を網羅していません。', '수집된 주소 내 순위이며 목록은 아직 전체 체인을 포함하지 않습니다.'],
    denominator: ['占比 = 地址余额 ÷ 已收录非系统地址余额合计（含钱包、非系统合约及已识别销毁地址）。系统余额完全排除，不代表全链流通量占比。', 'Share = address balance / total balance of indexed non-system addresses, including wallets, non-system contracts and identified burn addresses. System balances are excluded entirely. This is not a share of chain-wide circulating supply.', '比率 = アドレス残高 ÷ 収録済み非システムアドレスの残高合計（ウォレット、非システムコントラクト、識別済みバーンアドレスを含む）。システム残高は完全に除外し、チェーン全体の流通量に対する比率ではありません。', '비중 = 주소 잔액 ÷ 수집된 비시스템 주소의 잔액 합계(지갑, 비시스템 컨트랙트, 식별된 소각 주소 포함)입니다. 시스템 잔액은 완전히 제외하며 전체 체인 유통량 대비 비중이 아닙니다.'],
    methodology: ['统计口径与数据来源', 'Methodology & Source', '集計基準とデータ出典', '집계 기준 및 출처'],
    copy: ['复制地址', 'Copy address', 'アドレスをコピー', '주소 복사'],
    copied: ['已复制', 'Copied', 'コピー済み', '복사됨'],
    copyFailed: ['复制失败，请手动复制地址', 'Copy failed. Select the address to copy it.', 'コピーできませんでした。アドレスを選択してコピーしてください。', '복사하지 못했습니다. 주소를 선택해 복사하세요.'],
    explorer: ['浏览器查看', 'View in explorer', 'エクスプローラーで見る', '탐색기에서 보기'],
    viewAddress: ['查看地址', 'View address', 'アドレスを見る', '주소 보기'],
    exact: ['查看精确余额', 'Show exact balance', '正確な残高を表示', '정확한 잔액 보기'],
    wallet: ['钱包', 'Wallet', 'ウォレット', '지갑'],
    contract: ['合约', 'Contract', 'コントラクト', '컨트랙트'],
    system: ['系统', 'System', 'システム', '시스템'],
    burn: ['销毁', 'Burn', 'バーン', '소각'],
    prev: ['上一页', 'Previous', '前へ', '이전'],
    next: ['下一页', 'Next', '次へ', '다음'],
    noResults: ['本榜 TOP100 中没有匹配地址', 'No match in this top 100', 'この上位100件に該当アドレスはありません', '현재 상위 100개에서 일치하는 주소가 없습니다'],
    loading: ['正在读取持币快照…', 'Loading holder snapshot…', '保有スナップショットを読み込み中…', '보유 스냅샷을 불러오는 중…'],
    error: ['持币快照暂时无法读取，请稍后重试。', 'The holder snapshot could not be loaded. Please retry.', '保有スナップショットを読み込めませんでした。再試行してください。', '보유 스냅샷을 불러오지 못했습니다. 다시 시도하세요.'],
    retry: ['重新读取', 'Retry', '再試行', '다시 시도'],
    block: ['区块', 'Block', 'ブロック', '블록'],
    time: ['余额时间', 'Balance Time', '残高時刻', '잔액 시각'],
    matches: ['匹配', 'Matches', '該当', '검색 결과'],
  };
  const core = (() => {
    const WEI = 10n ** 18n;
    const MAX_BALANCE = (1n << 256n) - 1n;
    const SYSTEM = '0x0000000000000000000000000000000000001001';
    const languages = ['zh', 'en', 'ja', 'ko'];
    const normalizeLanguage = value => typeof value === 'string' ? value.toLowerCase().match(/^(zh|en|ja|ko)(?:-|$)/)?.[1] || null : null;
    const resolveLanguage = ({localeLanguage, htmlLanguage, search} = {}) => normalizeLanguage(localeLanguage) || normalizeLanguage(new URLSearchParams(search || '').get('lang')) || normalizeLanguage(htmlLanguage) || 'zh';
    const text = (key, lang) => COPY[key]?.[languages.indexOf(normalizeLanguage(lang) || 'zh')] || key;
    // A length limit rejects corrupted/malicious numeric strings before BigInt parsing.
    const decimal = value => typeof value === 'string' && /^(?:0|[1-9]\d{0,95})$/.test(value) ? BigInt(value) : null;
    const commas = value => String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    const scaled = (value, divisor, places = 3) => {
      const factor = 10n ** BigInt(places);
      const rounded = (value * factor + divisor / 2n) / divisor;
      const fraction = String(rounded % factor).padStart(places, '0').replace(/0+$/, '');
      return commas(rounded / factor) + (fraction ? `.${fraction}` : '');
    };
    const exact = value => {
      const fraction = String(value % WEI).padStart(18, '0').replace(/0+$/, '');
      return `${commas(value / WEI)}${fraction ? `.${fraction}` : ''} MARS`;
    };
    const balance = (value, lang = 'zh') => {
      const units = normalizeLanguage(lang) === 'zh' ? [[10n ** 12n, '万亿'], [10n ** 8n, '亿'], [10n ** 4n, '万']] : [[10n ** 12n, 'T'], [10n ** 9n, 'B'], [10n ** 6n, 'M'], [10n ** 3n, 'K']];
      const selected = units.find(([size]) => value >= WEI * size);
      if (selected) return scaled(value, WEI * selected[0]) + selected[1];
      if (value > 0n && value < WEI / 1000000n) return '<0.000001';
      return scaled(value, WEI, value < WEI ? 6 : 3);
    };
    const share = (value, denominator) => {
      if (typeof value !== 'bigint' || typeof denominator !== 'bigint' || denominator <= 0n || value < 0n || value > denominator) return '—';
      if (value > 0n && value * 1000000n < denominator) return '<0.0001%';
      const formatted = scaled(value * 100n, denominator, 4);
      return value < denominator && formatted === '100' ? '<100%' : `${formatted}%`;
    };
    const timestamp = (value, lang = 'zh') => {
      if (!Number.isSafeInteger(value) || value <= 0 || value >= 1e11) return '—';
      const chinese = normalizeLanguage(lang) === 'zh';
      const values = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {timeZone: chinese ? 'Asia/Shanghai' : 'UTC', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'}).formatToParts(new Date(value * 1000)).map(part => [part.type, part.value]));
      return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second} ${chinese ? 'UTC+8' : 'UTC'}`;
    };
    const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
    const positiveInteger = value => Number.isSafeInteger(value) && value > 0;
    const validateSnapshot = (input, nowSeconds = Math.floor(Date.now() / 1000)) => {
      if (!object(input) || !object(input.meta)) throw Error('Invalid snapshot metadata');
      const meta = input.meta;
      if (meta.complete !== true || !positiveInteger(meta.address_count) || !positiveInteger(meta.ranking_address_count) || meta.share_denominator_scope !== 'indexed_non_system_addresses' || meta.system_balances_excluded !== true || !positiveInteger(meta.block_number) || !positiveInteger(meta.block_timestamp) || meta.block_timestamp >= 1e11 || meta.block_timestamp > nowSeconds + 600) throw Error('Invalid snapshot metadata');
      const denominator = decimal(meta.share_denominator_wei);
      if (denominator === null || denominator <= 0n) throw Error('Invalid snapshot denominator');
      if (!Array.isArray(input.systems) || input.systems.length !== 1) throw Error('Invalid system address list');
      const systems = input.systems.map(row => {
        if (!object(row) || typeof row.address !== 'string' || row.address.toLowerCase() !== SYSTEM || row.kind !== 'system' || Object.prototype.hasOwnProperty.call(row, 'balance_wei')) throw Error('Invalid system address');
        return {address: SYSTEM, kind: 'system'};
      });
      if (meta.ranking_address_count + systems.length !== meta.address_count || !Array.isArray(input.rows) || input.rows.length !== Math.min(100, meta.ranking_address_count)) throw Error('Incomplete ranking snapshot');
      const seen = new Set([SYSTEM]);
      let previous = null;
      let total = 0n;
      const rows = input.rows.map((row, index) => {
        if (!object(row) || typeof row.address !== 'string' || !/^0x[0-9a-f]{40}$/i.test(row.address)) throw Error('Invalid ranking address');
        const address = row.address.toLowerCase();
        const value = decimal(row.balance_wei);
        if (value === null || value > MAX_BALANCE || (previous !== null && value > previous) || seen.has(address) || !['wallet', 'contract', 'burn'].includes(row.kind) || row.rank !== index + 1) throw Error('Invalid balance snapshot');
        seen.add(address); previous = value; total += value;
        return {address, balance_wei: row.balance_wei, kind: row.kind, rank: index + 1};
      });
      if (total > denominator || (meta.ranking_address_count <= 100 && total !== denominator)) throw Error('Invalid snapshot total');
      // Strip compatibility payloads and unused server fields, including names and balances of system addresses.
      return {meta: {...meta}, systems, rows};
    };
    const paginate = (rows, query, requestedPage) => {
      const normalized = typeof query === 'string' ? query.trim().toLowerCase() : '';
      const filtered = rows.filter(row => row.address.toLowerCase().includes(normalized));
      const total = filtered.length;
      const pages = Math.max(1, Math.ceil(total / 10));
      const page = Math.min(pages, Math.max(1, Number.isSafeInteger(requestedPage) ? requestedPage : 1));
      const offset = (page - 1) * 10;
      return {rows: filtered.slice(offset, offset + 10), page, pages, total, start: total ? offset + 1 : 0, end: Math.min(offset + 10, total)};
    };
    const powerSummary = input => {
      const integer = value => typeof value === 'number' ? (Number.isSafeInteger(value) && value >= 0 ? BigInt(value) : null) : decimal(value);
      const count = input?.meta?.positive_power_count;
      const rows = input?.rows;
      const denominator = integer(input?.meta?.network_total_power);
      if (!positiveInteger(count) || !Array.isArray(rows) || rows.length !== Math.min(100, count) || denominator === null || denominator <= 0n) return null;
      const seen = new Set();
      let previous = null, total = 0n;
      for (const row of rows) {
        const value = integer(row?.power);
        const address = typeof row?.address === 'string' ? row.address.toLowerCase() : '';
        if (value === null || !/^0x[0-9a-f]{40}$/.test(address) || seen.has(address) || (previous !== null && value > previous)) return null;
        seen.add(address); previous = value; total += value;
      }
      if (total > denominator) return null;
      return {count, total, denominator};
    };
    return {WEI, SYSTEM, COPY, languages, normalizeLanguage, resolveLanguage, text, decimal, commas, exact, balance, share, timestamp, validateSnapshot, paginate, powerSummary};
  })();
  if (typeof module === 'object' && module.exports) {module.exports = core; return;}
  const init = () => {
    const section = document.getElementById('rank');
    if (!section || section.querySelector('[data-holder-ranking]')) return;
    const mobile = Boolean(document.getElementById('mobileRankList'));
    const pager = mobile
      ? {container: 'm-rank-pagination', group: 'm-rank-pages', button: 'm-rank-page-button', count: 'm-rank-count'}
      : {container: 'rank-pagination', group: 'rank-pages', button: 'rank-page-button', count: 'rank-count'};
    const refreshMs = 60000;
    const staleAfterSeconds = 180;
    const state = {mode: 'power', page: 1, query: '', data: null, loading: true, error: false, expanded: new Set(), loadId: 0, lastSharedAt: 0, lastAttemptAt: 0, fingerprint: ''};
    const copy = COPY;
    const language = () => core.resolveLanguage({
      localeLanguage: window.MarsWebsiteLocale?.language?.(),
      htmlLanguage: document.documentElement.lang,
      search: window.location.search,
    });
    const t = key => core.text(key, language());
    window.MarsWebsiteLocale?.addEnglish?.('地址排行', copy.addressRank[1]);
    if (window.MarsWebsiteLocale?.dictionaries) {
      window.MarsWebsiteLocale.dictionaries.ja['地址排行'] = copy.addressRank[2];
      window.MarsWebsiteLocale.dictionaries.ko['地址排行'] = copy.addressRank[3];
      // Every label below is already localized here, including the four power
      // overview labels. Identity entries keep the site's second translation
      // pass from replacing 算力 inside Japanese 演算力 or joined numeric labels.
      for (const key of Object.keys(copy)) {
        for (const [lang, index] of [['ja', 2], ['ko', 3]]) {
          const dictionary = window.MarsWebsiteLocale.dictionaries[lang];
          if (dictionary) dictionary[copy[key][index]] = copy[key][index];
        }
      }
    }
    const esc = value => String(value).replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
    const {decimal, commas, exact} = core;
    const balance = value => core.balance(value, language());
    const share = value => core.share(value, decimal(state.data?.meta?.share_denominator_wei));
    const timestamp = value => core.timestamp(value, language());
    const toolbar = document.createElement('div');
    toolbar.className = 'hrp-toolbar';
    toolbar.dataset.holderRanking = '1';
    toolbar.setAttribute('aria-label', t('holders'));
    const panel = document.createElement('div');
    panel.id = 'holderRanking';
    panel.className = 'hrp-panel';
    section.prepend(panel);
    section.prepend(toolbar);
    const powerGrid = section.querySelector('#rankGrid, #mobileRankList');
    const powerOverview = document.createElement('div');
    powerOverview.className = 'hrp-overview hrp-power-overview';
    powerOverview.dataset.hrpPowerSummary = '';
    powerOverview.hidden = true;
    powerGrid?.before(powerOverview);
    const renderPowerOverview = () => {
      let payload;
      try {payload = JSON.parse(document.getElementById('rankData')?.textContent || '{}');} catch (_) {return;}
      const summary = core.powerSummary(payload);
      if (!summary) {powerOverview.hidden = true; return;}
      powerOverview.innerHTML = `<div title="${esc(t('powerCountNote'))}"><span>${esc(t('addresses'))}</span><b>${esc(commas(summary.count))}</b></div><div><span>${esc(t('powerTopTotal'))}</span><b title="${esc(commas(summary.total))}">${esc(balance(summary.total * core.WEI))}</b></div><div title="${esc(t('powerShareNote'))}"><span>${esc(t('powerTopShare'))}</span><b>${esc(core.share(summary.total, summary.denominator))}</b></div>`;
      powerOverview.hidden = false;
    };

    const renderToolbar = () => {
      document.querySelectorAll('nav a[href="#rank"]').forEach(link => {
        if (link.textContent !== t('addressRank')) link.textContent = t('addressRank');
      });
      // Keep controls mounted so a pointer or keyboard selection retains focus.
      if (!toolbar.querySelector('[data-hrp-mode]')) toolbar.innerHTML = '<div class="hrp-main-switch" role="group"><button type="button" data-hrp-mode="power"></button><button type="button" data-hrp-mode="holders"></button></div>';
      toolbar.setAttribute('aria-label', t('holders'));
      toolbar.querySelector('.hrp-main-switch').setAttribute('aria-label', t('holders'));
      for (const mode of ['power', 'holders']) {
        const button = toolbar.querySelector(`[data-hrp-mode="${mode}"]`);
        button.textContent = t(mode);
        button.setAttribute('aria-pressed', String(state.mode === mode));
      }
      section.classList.toggle('hrp-holders-mode', state.mode === 'holders');
      panel.hidden = state.mode !== 'holders';
    };
    const currentRows = () => state.data?.rows || [];
    const addressActions = address => `<div class="node-rank-actions hrp-address-actions"><button type="button" data-hrp-copy="${esc(address)}">${esc(t('copy'))}</button><a href="https://explorer.marschain.net/address/${encodeURIComponent(address)}" target="_blank" rel="noopener noreferrer">${esc(t('viewAddress'))}</a></div>`;
    const systemsMarkup = () => `<section class="hrp-system-zone" aria-label="${esc(t('systemsTitle'))}"><div class="hrp-system-header"><div><span class="hrp-eyebrow">${esc(t('systemsKicker'))}</span><h3>${esc(t('systemsTitle'))}</h3></div><p>${esc(t('systemsNote'))}</p></div>${state.data.systems.map(row => `<article class="hrp-system-card"><div class="hrp-system-identity"><span class="hrp-system-emblem" aria-hidden="true">P</span><div><h4>${esc(t('powerSystem'))}</h4><span class="hrp-kind hrp-kind-system">${esc(t('system'))}</span></div></div><div class="hrp-system-address"><a href="https://explorer.marschain.net/address/${encodeURIComponent(row.address)}" target="_blank" rel="noopener noreferrer" title="${esc(t('explorer'))}">${esc(row.address)}</a>${addressActions(row.address)}</div></article>`).join('')}</section>`;
    const rowsMarkup = rows => rows.map(row => {
      const value = decimal(row.balance_wei);
      const kind = row.kind;
      const address = row.address;
      const rank = String(row.rank).padStart(2, '0');
      const expanded = state.expanded.has(address);
      return `<article role="row" class="hrp-row${row.rank <= 3 ? ' hrp-leading' : ''}" data-hrp-address="${esc(address)}"><div class="hrp-rank" role="cell"><span>${esc(rank)}</span></div><div class="hrp-address" role="cell"><div class="hrp-address-line"><a href="https://explorer.marschain.net/address/${encodeURIComponent(address)}" target="_blank" rel="noopener noreferrer" title="${esc(t('explorer'))}" aria-label="${esc(t('explorer'))}: ${esc(address)}"><span class="hrp-full-address">${esc(address)}</span><span class="hrp-short-address">${esc(address.slice(0, 8))}…${esc(address.slice(-6))}</span></a></div><span class="hrp-kind hrp-kind-${kind}">${esc(t(kind))}</span>${addressActions(address)}</div><div class="hrp-balance" role="cell"><button type="button" data-hrp-exact aria-expanded="${expanded}" title="${esc(exact(value))}" aria-label="${esc(t('exact'))}: ${esc(exact(value))}"><strong>${esc(balance(value))}</strong><small>MARS</small></button><span class="hrp-exact" ${expanded ? '' : 'hidden'}>${esc(exact(value))}</span></div><div class="hrp-share" role="cell"><span>${esc(share(value))}</span></div></article>`;
    }).join('');
    const pageCaption = (start, end, total, pages) => {
      const lang = language();
      if (lang === 'zh') return `第 ${state.page} / ${pages} 页 · 当前显示 ${start}-${end} / 共 ${total} 名`;
      if (lang === 'ja') return `${state.page} / ${pages} ページ · ${start}–${end} / ${total}件`;
      if (lang === 'ko') return `${state.page} / ${pages} 페이지 · ${start}–${end} / ${total}개`;
      return `Page ${state.page} / ${pages} · ${start}–${end} of ${total}`;
    };
    const renderList = ({focusPage = false} = {}) => {
      const list = panel.querySelector('[data-hrp-list]');
      if (!list || !state.data) return;
      const result = core.paginate(currentRows(), state.query, state.page);
      state.page = result.page;
      const {pages, total, start, end} = result;
      list.innerHTML = total ? rowsMarkup(result.rows) : `<div class="hrp-empty" role="status">${esc(t('noResults'))}</div>`;
      const pagination = panel.querySelector('[data-hrp-pagination]');
      pagination.className = `hrp-pagination ${pager.container}`;
      pagination.innerHTML = `<button class="${pager.button}" type="button" data-hrp-page="${state.page - 1}" ${state.page === 1 ? 'disabled' : ''} aria-label="${esc(t('prev'))}">${esc(t('prev'))}</button><div class="${pager.group}">${Array.from({length: pages}, (_, index) => `<button class="${pager.button}${state.page === index + 1 ? ' is-active' : ''}" type="button" data-hrp-page="${index + 1}" ${state.page === index + 1 ? 'aria-current="page"' : ''}>${index + 1}</button>`).join('')}</div><button class="${pager.button}" type="button" data-hrp-page="${state.page + 1}" ${state.page === pages ? 'disabled' : ''} aria-label="${esc(t('next'))}">${esc(t('next'))}</button><span class="${pager.count}" aria-live="polite">${esc(pageCaption(start, end, total, pages))}</span>`;
      if (focusPage) pagination.querySelector('[aria-current="page"]')?.focus({preventScroll: true});
    };
    const renderFreshness = () => {
      const stale = Boolean(state.data && (state.error || Date.now() / 1000 - state.data.meta.block_timestamp > staleAfterSeconds));
      panel.classList.toggle('is-stale', stale);
      const note = panel.querySelector('[data-hrp-freshness]');
      if (note) {note.hidden = !stale; note.textContent = stale ? t('stale') : '';}
    };
    const render = () => {
      // A background update keeps the visitor's search, details and keyboard focus.
      const active = document.activeElement;
      const focused = panel.contains(active) ? active : null;
      const focusedAddress = focused?.closest('[data-hrp-address]')?.dataset.hrpAddress;
      const searchFocused = focused?.matches('[data-hrp-search]');
      const focusPage = focused?.hasAttribute('data-hrp-page');
      const focusExact = focused?.hasAttribute('data-hrp-exact');
      const focusCopy = focused?.hasAttribute('data-hrp-copy');
      const selection = searchFocused ? [focused.selectionStart, focused.selectionEnd] : null;
      const methodOpen = Boolean(panel.querySelector('.hrp-notes details[open]'));
      renderToolbar();
      const head = `<header class="hrp-heading"><div><h2>${esc(t('title'))}</h2><p>${esc(t('intro'))}</p></div><span class="hrp-snapshot-tag"><i></i>${esc(t('snapshot'))}</span></header>`;
      if (!state.data && (state.loading || state.error)) {
        panel.innerHTML = head + `<div class="hrp-empty" role="status">${esc(t(state.loading ? 'loading' : 'error'))}${state.error ? `<button type="button" data-hrp-retry>${esc(t('retry'))}</button>` : ''}</div>`;
        return;
      }
      const meta = state.data.meta;
      const topTotal = currentRows().reduce((sum, row) => sum + decimal(row.balance_wei), 0n);
      panel.innerHTML = head + `<div class="hrp-data-line"><span>${esc(t('time'))}<time>${esc(timestamp(meta.block_timestamp))}</time></span><span>${esc(t('block'))}<b>#${esc(commas(meta.block_number))}</b></span></div>${systemsMarkup()}<div class="hrp-ranking-heading"><h3>${esc(t('allRanking'))}</h3><span>TOP 100</span></div><div class="hrp-overview"><div><span>${esc(t('addresses'))}</span><b>${esc(commas(meta.ranking_address_count))}</b></div><div><span>${esc(t('topTotal'))}</span><b title="${esc(exact(topTotal))}">${esc(balance(topTotal))}<small> MARS</small></b></div><div><span>${esc(t('topShare'))}</span><b>${esc(share(topTotal))}</b></div></div><div class="hrp-controls"><span class="hrp-sort">${esc(t('sort'))}</span><label class="hrp-search"><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5"/><path d="m13 13 4 4"/></svg><input type="search" autocomplete="off" spellcheck="false" maxlength="42" value="${esc(state.query)}" placeholder="${esc(t('placeholder'))}" aria-label="${esc(t('search'))}" data-hrp-search></label></div><p class="hrp-filter-note">${esc(t('rankingNote'))}</p><div class="hrp-table" role="table" aria-label="${esc(t('allRanking'))}"><div class="hrp-table-head" role="row"><span role="columnheader">${esc(t('number'))}</span><span role="columnheader">${esc(t('address'))}</span><span role="columnheader">${esc(t('balance'))}</span><span role="columnheader">${esc(t('share'))}</span></div><div data-hrp-list role="rowgroup"></div></div><nav class="hrp-pagination" data-hrp-pagination aria-label="${esc(t('holders'))}"></nav><footer class="hrp-notes"><p>${esc(t('coverage'))}</p><details><summary>${esc(t('methodology'))}</summary><p>${esc(t('source'))}</p><p>${esc(t('denominator'))}</p></details></footer><div class="hrp-notice" role="status" aria-live="polite"></div>`;
      renderList();
      const line = panel.querySelector('.hrp-data-line');
      if (line) {
        const note = document.createElement('p');
        note.className = 'hrp-refresh-status';
        note.dataset.hrpFreshness = '';
        note.setAttribute('role', 'status');
        note.hidden = true;
        line.after(note);
      }
      if (methodOpen) panel.querySelector('.hrp-notes details')?.setAttribute('open', '');
      let restored = null;
      if (searchFocused) restored = panel.querySelector('[data-hrp-search]');
      else if (focusPage) restored = panel.querySelector('[aria-current="page"]');
      else if (focusExact && focusedAddress) restored = panel.querySelector(`[data-hrp-address="${focusedAddress}"] [data-hrp-exact]`);
      else if (focusCopy && focusedAddress) restored = panel.querySelector(`[data-hrp-address="${focusedAddress}"] [data-hrp-copy]`);
      restored?.focus({preventScroll: true});
      if (searchFocused && restored && selection) {
        try {restored.setSelectionRange(...selection);} catch (_) { /* Search selection is optional in some browsers. */ }
      }
      renderFreshness();
    };
    const accept = input => {
      const validated = core.validateSnapshot(input);
      // A cached response must never move an already displayed ranking backwards.
      if (state.data && (validated.meta.block_number < state.data.meta.block_number || validated.meta.block_timestamp < state.data.meta.block_timestamp)) throw Error('Older ranking');
      if (state.data && validated.meta.block_number === state.data.meta.block_number && validated.meta.block_hash !== state.data.meta.block_hash) throw Error('Conflicting ranking block');
      const fingerprint = JSON.stringify(validated);
      const changed = fingerprint !== state.fingerprint;
      state.data = validated;
      state.fingerprint = fingerprint;
      state.loading = false;
      state.error = false;
      if (changed) render(); else renderFreshness();
    };
    const receiveShared = () => {
      try {
        accept(window.MarsHolderRankingPayload);
        state.lastSharedAt = Date.now();
        // Shared transport is authoritative over an older in-flight fallback.
        state.loadId += 1;
        state.loadController?.abort();
        state.loadController = null;
        return true;
      } catch (_) {
        state.error = true;
        state.loading = false;
        if (state.data) renderFreshness(); else render();
        return false;
      }
    };
    const load = async () => {
      if (document.hidden) return;
      state.loadController?.abort();
      const loadId = state.loadId + 1;
      state.loadId = loadId;
      state.lastAttemptAt = Date.now();
      state.loading = !state.data;
      if (!state.data) {state.error = false; render();}
      const controller = new AbortController();
      state.loadController = controller;
      const timer = setTimeout(() => controller.abort(), 15000);
      try {
        const response = await fetch(`/data/latest.json?v=${Date.now()}`, {cache: 'no-store', signal: controller.signal});
        if (!response.ok) throw Error('Snapshot unavailable');
        const payload = await response.json();
        if (loadId !== state.loadId) return;
        accept(payload?.holders);
      } catch (_) {if (loadId === state.loadId) state.error = true;}
      finally {
        clearTimeout(timer);
        if (loadId !== state.loadId) return;
        state.loadController = null; state.loading = false;
        if (state.data) renderFreshness(); else render();
      }
    };
    toolbar.addEventListener('click', event => {
      const button = event.target.closest('[data-hrp-mode]');
      if (!button) return;
      if (!['power', 'holders'].includes(button.dataset.hrpMode)) return;
      state.mode = button.dataset.hrpMode;
      renderToolbar();
    });
    panel.addEventListener('input', event => {
      if (!event.target.matches('[data-hrp-search]')) return;
      state.query = event.target.value.trim(); state.page = 1; renderList();
    });
    panel.addEventListener('click', async event => {
      const button = event.target.closest('button');
      if (!button) return;
      if (button.hasAttribute('data-hrp-retry')) {load(); return;}
      if (button.hasAttribute('data-hrp-page') && !button.disabled) {state.page = Number(button.dataset.hrpPage); renderList({focusPage: true}); return;}
      if (button.hasAttribute('data-hrp-exact')) {
        const detail = button.parentElement.querySelector('.hrp-exact');
        detail.hidden = !detail.hidden; button.setAttribute('aria-expanded', String(!detail.hidden));
        const address = button.closest('[data-hrp-address]')?.dataset.hrpAddress;
        if (address) {if (detail.hidden) state.expanded.delete(address); else state.expanded.add(address);}
        return;
      }
      if (button.hasAttribute('data-hrp-copy')) {
        let copied = true;
        try {await navigator.clipboard.writeText(button.dataset.hrpCopy);} catch (_) {copied = false;}
        const target = panel.querySelector('.hrp-notice');
        if (target) {
          clearTimeout(state.noticeTimer);
          if (copied) {target.textContent = t('copied'); state.noticeTimer = setTimeout(() => {target.textContent = '';}, 2500);}
          else {
            // A truncated mobile address cannot be selected in full. Offer its full value if clipboard permission is denied.
            target.innerHTML = `<label>${esc(t('copyFailed'))}<input class="hrp-copy-fallback" type="text" readonly value="${esc(button.dataset.hrpCopy)}" aria-label="${esc(t('copy'))}"></label>`;
            const input = target.querySelector('input');
            input?.focus({preventScroll: true}); input?.select();
          }
        }
      }
    });
    const refreshIfNeeded = () => {
      if (document.hidden) return;
      renderFreshness();
      // The existing live runtime normally supplies a shared update every 25 s.
      // Fetch separately only if that transport has been silent for a full minute.
      if (Date.now() - state.lastSharedAt >= refreshMs && Date.now() - state.lastAttemptAt >= refreshMs) load();
    };
    window.addEventListener('mars-website-language', () => {render(); renderPowerOverview();});
    window.addEventListener('mars-share-report-data', () => {renderPowerOverview(); receiveShared();});
    document.addEventListener('visibilitychange', refreshIfNeeded);
    window.addEventListener('focus', refreshIfNeeded);
    window.addEventListener('pageshow', refreshIfNeeded);
    window.setInterval(refreshIfNeeded, refreshMs);
    render();
    renderPowerOverview();
    document.documentElement.dataset.holderRanking = '1';
    if (!receiveShared()) load();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true});
  else init();
})();
