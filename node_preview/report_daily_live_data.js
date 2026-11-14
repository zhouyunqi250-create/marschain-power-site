/* Four reports share verified evidence. No DOM reads, network calls or writes. */
(() => {
  'use strict';

  const createModel = (language = 'zh') => {
  const english = language !== 'zh';
  const dictionary = window.MarsReportLocales?.[language];
  const MISSING = english ? (dictionary?.missing || 'Pending') : '待补齐';
  const locale = english ? (dictionary?.locale || 'en-US') : 'zh-CN';
  const choose = (zh, en) => english ? (dictionary?.model?.[en] ?? en) : zh;
  const DAY = 86400;
  const WEI = 10n ** 18n;
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
  const integer = (value) => {
    if (typeof value === 'bigint') return value;
    if (typeof value === 'number') return Number.isSafeInteger(value) ? BigInt(value) : null;
    if (typeof value !== 'string' || !/^-?\d+$/.test(value.trim())) return null;
    try { return BigInt(value.trim()); } catch (_) { return null; }
  };
  const unsigned = (value) => {
    const parsed = integer(value);
    return parsed !== null && parsed >= 0n ? parsed : null;
  };
  const numeric = (value) => {
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    if (typeof value !== 'string' || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const stamp = (value) => {
    const parsed = integer(value);
    return parsed !== null && parsed > 0n && parsed <= BigInt(Number.MAX_SAFE_INTEGER)
      && Number.isFinite(new Date(Number(parsed) * 1000).getTime()) ? Number(parsed) : null;
  };
  const trim = (text) => text.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
  const atTime = (value, offset = english ? 0 : 8 * 3600) => {
    const parsed = stamp(value);
    return parsed === null ? MISSING : new Date((parsed + offset) * 1000).toISOString().slice(0, 16).replace('T', ' ');
  };
  const localTimestamp = (value) => {
    if (typeof value !== 'string') return null;
    const matched = value.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$/);
    if (!matched) return null;
    const [year, month, day, hour, minute, second] = matched.slice(1).map((part) => Number(part || 0));
    const wall = Date.UTC(year, month - 1, day, hour, minute, second);
    const date = new Date(wall);
    if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1
      || date.getUTCDate() !== day || date.getUTCHours() !== hour
      || date.getUTCMinutes() !== minute || date.getUTCSeconds() !== second) return null;
    return wall / 1000 - 8 * 3600;
  };
  const exactRange = (meta, prefix) => {
    const start = stamp(meta[`${prefix}_start_timestamp`]);
    const end = stamp(meta[`${prefix}_end_timestamp`]);
    if (start === null || end === null || start >= end) return null;
    for (const [name, expected] of [['start', start], ['end', end]]) {
      if (own(meta, `${prefix}_${name}_local`)
        && localTimestamp(meta[`${prefix}_${name}_local`]) !== expected) return null;
    }
    return { start, end, text: `${atTime(start)}${choose(' 至 ', ' to ')}${atTime(end)}` };
  };

  // Integer arithmetic keeps wei differences exact. Only the final display is
  // rounded; values are never guessed to be whole coins by their magnitude.
  const compactInteger = (value, scale = 1n, signed = false) => {
    if (value === null) return MISSING;
    const absolute = value < 0n ? -value : value;
    let denominator = scale;
    let suffix = '';
    const units = english
      ? [[10n ** 12n, 'T'], [10n ** 9n, 'B'], [10n ** 6n, 'M'], [10n ** 3n, 'K']]
      : [[10n ** 12n, '万亿'], [10n ** 8n, '亿'], [10n ** 4n, '万']];
    for (const [threshold, unit] of units) {
      if (absolute >= threshold * scale) { denominator *= threshold; suffix = unit; break; }
    }
    const digits = absolute > 0n && absolute < scale ? 6 : 3;
    const factor = 10n ** BigInt(digits);
    const rounded = (absolute * factor + denominator / 2n) / denominator;
    const main = rounded / factor;
    const fraction = String(rounded % factor).padStart(digits, '0').replace(/0+$/, '');
    const sign = value < 0n ? '−' : signed && value > 0n ? '+' : '';
    const text = rounded === 0n && absolute > 0n ? `<0.${'0'.repeat(digits - 1)}1`
      : main.toLocaleString(locale) + (fraction ? `.${fraction}` : '');
    return sign + text + suffix;
  };
  const power = (value, signed = false) => compactInteger(integer(value), 1n, signed);
  const tokens = (value) => {
    const parsed = unsigned(value);
    return parsed === null ? MISSING : `${compactInteger(parsed, WEI)}${choose('枚', ' MARS')}`;
  };
  const count = (value, suffix) => {
    const parsed = unsigned(value);
    return parsed === null ? MISSING : parsed.toLocaleString(locale) + (english ? '' : suffix);
  };
  const compactNumber = (value) => {
    const parsed = numeric(value);
    if (parsed === null || parsed < 0) return MISSING;
    const units = english ? [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']]
      : [[1e12, '万亿'], [1e8, '亿'], [1e4, '万']];
    for (const [threshold, unit] of units) {
      if (parsed >= threshold) return trim((parsed / threshold).toFixed(3)) + unit;
    }
    return parsed > 0 && parsed < 1 ? trim(parsed.toFixed(6))
      : parsed.toLocaleString(locale, { maximumFractionDigits: 3 });
  };
  const price = (value) => {
    const parsed = numeric(value);
    return parsed === null || parsed < 0 ? MISSING : trim(parsed.toFixed(parsed < 1 ? 6 : 3));
  };
  const source = (meta, key) => (meta.preview_metric_sources || {})[key] || {};
  const officialOkay = (meta, key, retrieved) => {
    const entry = source(meta, key);
    const time = stamp(entry.fetched_at);
    return entry.source === 'official' && ['ok', 'stale'].includes(entry.status) && time !== null
      && time <= retrieved + 60;
  };
  const nodeOkay = (meta, key, block) => {
    const entry = source(meta, key);
    return entry.source === 'node' && entry.status === 'ok'
      && integer(entry.block) === block;
  };
  const period = (meta, days, dailyEnd) => {
    const prefix = `period_${days}d`;
    const range = exactRange(meta, prefix);
    const validRange = range && range.end - range.start === days * DAY
      && range.end <= dailyEnd && range.end % DAY === 0;
    if (!validRange) return { days, power: MISSING, addresses: MISSING, burned: MISSING,
      rangeText: choose('统计时间待核验', 'Window pending verification'),
      state: choose('统计时间待核验', 'Window pending verification') };
    const address = unsigned(meta[`${prefix}_new_candidate_address_count`]);
    const estimated = meta[`${prefix}_new_candidate_address_estimated`] === true
      || (typeof meta[`${prefix}_new_candidate_address_display`] === 'string'
        && meta[`${prefix}_new_candidate_address_display`].trim().startsWith('约'));
    const summary = { days, power: power(meta[`${prefix}_new_power`]),
      addresses: address === null ? choose('暂无可比采样', 'No comparable samples')
        : `${estimated ? choose('约', '~') : ''}${count(address, '个')}`,
      burned: tokens(meta[`${prefix}_burned_tokens`]), rangeText: range.text, state: '' };
    if (range.end < dailyEnd) summary.state = choose('保留上次统计', 'Last verified period');
    else if (meta[`${prefix}_complete`] === false || summary.power === MISSING
      || summary.burned === MISSING || address === null) summary.state = choose('部分数据待补齐', 'Partially available');
    return summary;
  };

  const validateDaily = (bundle) => {
    const daily = bundle.daily || {};
    const meta = daily.meta || {};
    const evidence = bundle.daily_evidence || {};
    const range = exactRange(meta, 'statistics_window');
    const target = stamp(evidence.target_timestamp);
    const actual = meta.preview_daily_status || {};
    const proof = evidence.daily_statistics_status || {};
    const block = unsigned(evidence.source_block);
    const node = meta.node_preview || {};
    if (!range || target === null || target % DAY !== 0 || range.end !== target
      || range.end - range.start !== DAY || actual.state !== 'verified_node_window'
      || proof.state !== 'verified_node_window' || stamp(actual.frozen_end_timestamp) !== target
      || stamp(proof.frozen_end_timestamp) !== target
      || (own(actual, 'expected_end_timestamp') && stamp(actual.expected_end_timestamp) !== target)
      || (own(proof, 'expected_end_timestamp') && stamp(proof.expected_end_timestamp) !== target)
      || block === null || block === 0n || unsigned(node.source_block) !== block
      || node.state !== 'frozen' || (own(node, 'target_timestamp') && stamp(node.target_timestamp) !== target)
      || !/^[a-f0-9]{64}$/i.test(String(evidence.sha256 || ''))) {
      throw new Error(choose('日报归档的日结状态、统计区间或固定区块证据不一致',
        'Daily archive evidence does not match its window or fixed block'));
    }
    return { meta, range, target, block };
  };

  const build = (bundle) => {
    if (!bundle || bundle.schema !== 1) throw new Error(choose('日报 / 快报数据版本不受支持', 'Unsupported report data version'));
    const retrieved = stamp(bundle.retrieved_at);
    if (retrieved === null) throw new Error(choose('缺少本次数据实际读取时间', 'Missing snapshot retrieval timestamp'));
    const archive = validateDaily(bundle);
    const separate = own(bundle, 'assembled_at');
    const assembled = separate ? stamp(bundle.assembled_at) : retrieved;
    if (separate && (assembled === null || assembled < retrieved
      || archive.target > Math.floor(assembled / DAY) * DAY)) {
      throw new Error(choose('日报归档的日结状态、统计区间或固定区块证据不一致',
        'Daily archive evidence does not match its window or fixed block'));
    }
    const dm = archive.meta;
    const lm = (bundle.live || {}).meta || {};
    const liveNode = lm.node_preview || {};
    const liveBlock = unsigned(liveNode.source_block);
    const liveTime = stamp(liveNode.source_block_timestamp);
    const liveReady = liveNode.state === 'ready' && liveBlock !== null && liveBlock > 0n
      && liveTime !== null && liveTime <= retrieved + 60;
    const livePowerOkay = liveReady && nodeOkay(lm, 'network_total_power', liveBlock);
    const liveBurnOkay = liveReady && nodeOkay(lm, 'network_total_burned_tokens', liveBlock);
    const powerValue = livePowerOkay ? unsigned(lm.network_total_power) : null;
    const burnedValue = liveBurnOkay ? unsigned(lm.network_total_burned_tokens) : null;
    const priceOkay = officialOkay(lm, 'network_current_price', retrieved);
    const highOkay = officialOkay(lm, 'network_highest_price', retrieved);
    const addressesOkay = officialOkay(lm, 'explorer_total_addresses', retrieved);
    const circulationOkay = officialOkay(lm, 'network_total_circulation_tokens', retrieved);
    const modelSource = source(lm, 'emission');
    const modelOkay = liveReady && livePowerOkay && lm.emission_model_status === 'verified'
      && modelSource.status === 'ok' && integer(modelSource.block) === liveBlock
      && integer(lm.emission_reference_block) === liveBlock;
    const required = modelOkay ? numeric(lm.power_required_per_mars_daily) : null;
    const perCoinOkay = required !== null && required > 0;

    let top100Share = MISSING;
    const rows = (bundle.live || {}).rows;
    if (powerValue !== null && powerValue > 0n && Array.isArray(rows) && rows.length >= 100) {
      const powers = rows.slice(0, 100).map((row) => unsigned(row && row.power));
      if (powers.every((value) => value !== null)) {
        const total = powers.reduce((sum, value) => sum + value, 0n);
        if (total <= powerValue) {
          const hundredths = (total * 10000n + powerValue / 2n) / powerValue;
          top100Share = `${hundredths / 100n}.${String(hundredths % 100n).padStart(2, '0')}%`;
        }
      }
    }
    // Holdings are collected independently of the power snapshot. Use only the
    // frozen, non-system aggregates and retain their own block and source clock.
    // Old report caches have no summary; that remains missing, never zero.
    let top100HolderShare = MISSING;
    let holderTimeText = MISSING, holderUtcText = MISSING, holderBlockText = MISSING;
    const holders = (bundle.live || {}).holders_summary;
    if (holders && typeof holders === 'object' && !Array.isArray(holders)) {
      const decimal = value => typeof value === 'string' && /^(?:0|[1-9][0-9]{0,95})$/.test(value)
        ? BigInt(value) : null;
      const holderTotal = decimal(holders.top100_total_wei);
      const holderDenominator = decimal(holders.share_denominator_wei);
      const holderBlock = unsigned(holders.block_number);
      const holderTime = stamp(holders.block_timestamp);
      if (holders.row_count === 100 && holders.excluded_systems === true
        && holders.scope === 'indexed_non_system_addresses'
        && holderBlock !== null && holderBlock > 0n && holderBlock <= BigInt(Number.MAX_SAFE_INTEGER)
        && /^0x[a-f0-9]{64}$/i.test(String(holders.block_hash || ''))
        && holderTime !== null && holderTime >= retrieved - 300 && holderTime <= retrieved + 30
        && stamp(lm.generated_at) !== null && holderTime <= stamp(lm.generated_at)
        && holderTotal !== null && holderDenominator !== null && holderDenominator > 0n
        && holderTotal <= holderDenominator) {
        const hundredths = (holderTotal * 10000n + holderDenominator / 2n) / holderDenominator;
        top100HolderShare = `${hundredths / 100n}.${String(hundredths % 100n).padStart(2, '0')}%`;
        holderTimeText = atTime(holderTime);
        holderUtcText = atTime(holderTime, 0);
        holderBlockText = holderBlock.toLocaleString(locale);
      }
    }
    const times = ['network_current_price', 'network_highest_price', 'explorer_total_addresses',
      'network_total_circulation_tokens'].filter((key) => officialOkay(lm, key, retrieved))
      .map((key) => stamp(source(lm, key).fetched_at));
    const firstTime = times.length ? Math.min(...times) : null;
    const lastTime = times.length ? Math.max(...times) : null;
    const officialTimeText = firstTime === null ? MISSING : firstTime === lastTime
      ? atTime(firstTime) : `${atTime(firstTime)}${choose(' 至 ', ' to ')}${atTime(lastTime)}`;
    const retainedTime = (key, okay) => okay && source(lm, key).status === 'stale'
      ? atTime(stamp(source(lm, key).fetched_at)) : '';
    const officialRetainedTimes = {
      price: retainedTime('network_current_price', priceOkay),
      highestPrice: retainedTime('network_highest_price', highOkay),
      triggerPrice: retainedTime('network_highest_price', highOkay),
      circulation: retainedTime('network_total_circulation_tokens', circulationOkay),
      addresses: retainedTime('explorer_total_addresses', addressesOkay),
    };
    // The one poster clock describes the server's assembled snapshot. Redrawing
    // or fetching that snapshot locally must not give the underlying data a new time.
    const snapshotTime = stamp(lm.generated_at);
    const snapshotReady = liveReady && snapshotTime !== null
      && snapshotTime >= liveTime && snapshotTime <= retrieved + 60
      && (lastTime === null || snapshotTime >= lastTime);

    const startPower = unsigned(dm.network_total_power);
    const startBurned = unsigned(dm.network_total_burned_tokens);
    const ordered = liveReady && liveBlock >= archive.block && liveTime >= archive.target
      && archive.target === Math.floor(liveTime / DAY) * DAY;
    const powerComparable = ordered && startPower !== null && powerValue !== null;
    const burnComparable = ordered && startBurned !== null && burnedValue !== null && burnedValue >= startBurned;
    const intraday = {
      available: powerComparable || burnComparable,
      rangeText: ordered ? `${atTime(archive.target)}${choose(' 至 ', ' to ')}${atTime(liveTime)}`
        : choose('统计时间待核验', 'Window pending verification'),
      power: powerComparable ? power(powerValue - startPower, true) : MISSING,
      burned: burnComparable ? tokens(burnedValue - startBurned) : MISSING,
      // Address samples have their own receipt clock. The fixed-block archive's
      // latest official total is not proof of an exact 08:00 address baseline.
      addresses: MISSING,
      state: powerComparable && burnComparable ? choose('统计中', 'In progress')
        : choose('统计中 · 部分数据待补齐', 'In progress / partially available'),
    };
    const addressSamples = bundle.daily_evidence?.address_samples;
    const estimatedAddresses = addressSamples?.estimated === true;
    const addressSampleTime = value => {
      const time = stamp(value);
      if (time === null) return '';
      return new Date((time + (english ? 0 : 8 * 3600)) * 1000).toISOString().slice(5, 19).replace('T', ' ');
    };
    const daily = {
      rangeText: archive.range.text,
      dateLabel: atTime(archive.target).slice(0, 10).replace(/-/g, '.'),
      retained: separate && archive.target < Math.floor(assembled / DAY) * DAY,
      newPower: power(dm.statistics_window_new_power, true),
      burned: tokens(dm.statistics_window_burned_tokens),
      transactions: count(dm.statistics_window_active_transactions_seen, '笔'),
      volume: tokens(dm.statistics_window_transaction_volume_wei),
      activeAddresses: count(dm.statistics_window_active_wallet_address_count, '个'),
      newAddresses: dm.statistics_window_new_candidate_address_count == null
        ? '—' : (estimatedAddresses ? choose('约', '≈') : '') + count(dm.statistics_window_new_candidate_address_count, '个'),
      addressSampleRange: estimatedAddresses
        ? `${addressSampleTime(addressSamples.start_received_at)} → ${addressSampleTime(addressSamples.end_received_at)}` : '',
      endPower: compactInteger(unsigned(dm.statistics_window_end_total_power)),
      totalBurned: tokens(dm.network_total_burned_tokens),
      blockText: archive.block.toLocaleString(locale),
      periods: [7, 30].map((days) => period(dm, days, archive.target)),
    };
    const live = {
      retained: bundle.state === 'retained',
      timeText: liveReady ? atTime(liveTime) : MISSING,
      utcText: liveReady ? atTime(liveTime, 0) : MISSING,
      snapshotTimeText: snapshotReady ? atTime(snapshotTime) : MISSING,
      snapshotUtcText: snapshotReady ? atTime(snapshotTime, 0) : MISSING,
      blockText: liveReady ? liveBlock.toLocaleString(locale) : MISSING,
      totalPower: compactInteger(powerValue),
      perCoin: perCoinOkay ? compactNumber(required) : MISSING,
      oneYiOutput: perCoinOkay ? `${compactNumber(100000000 / required)}${choose('枚/日', ' MARS/day')}` : MISSING,
      top100Share,
      top100HolderShare,
      holderTimeText,
      holderUtcText,
      holderBlockText,
      addresses: addressesOkay ? count(lm.explorer_total_addresses, '个') : MISSING,
      price: priceOkay ? price(lm.network_current_price) : MISSING,
      highestPrice: highOkay ? price(lm.network_highest_price) : MISSING,
      triggerPrice: highOkay && numeric(lm.network_highest_price) !== null
        ? price(numeric(lm.network_highest_price) / 2) : MISSING,
      circulation: circulationOkay ? tokens(lm.network_total_circulation_tokens) : MISSING,
      burned: tokens(burnedValue),
      officialTimeText,
      officialRetainedTimes,
      officialRetained: Object.values(officialRetainedTimes).some(Boolean),
      modelNote: choose('产出按当前区块奖励与全网算力估算', 'Estimated from current block rewards and network power'),
      intraday,
    };
    return { daily, live, language, timeZone: english ? 'UTC' : 'Asia/Shanghai' };
  };

  return build;
  };
  const models = Object.fromEntries(['zh', 'en', 'ja', 'ko'].map(language => [language, createModel(language)]));
  window.MarsReportData = Object.freeze({
    build: (bundle, options = {}) => models[options.language] ? models[options.language](bundle) : models.zh(bundle),
  });
})();
