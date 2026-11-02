/* Model/render regressions. Canvas doubles verify draw arguments, not pixels. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const read = name => fs.readFileSync(path.join(__dirname, name), 'utf8');
const DAY = 86400;
const AT = Date.parse('2026-11-01T16:01:00Z') / 1000;
const END = Math.floor(AT / DAY) * DAY;
const at = (value, offset = 0) => new Date((value + offset) * 1000).toISOString().slice(0, 16).replace('T', ' ');
const fixture = () => {
  const proof = { state: 'verified_node_window', frozen_end_timestamp: END, expected_end_timestamp: END };
  const node = { source: 'node', status: 'ok', block: 1000 };
  const official = { source: 'official', status: 'ok', fetched_at: AT };
  const base = {
    schema: 1, state: 'ready', retrieved_at: AT,
    live: { meta: {
      generated_at: AT,
      node_preview: { state: 'ready', source_block: 1000, source_block_timestamp: AT - 20 },
      network_total_power: '900719925474099312346',
      network_total_burned_tokens: '10000000000000000001',
      network_current_price: '0.01035', network_highest_price: '0.02254',
      explorer_total_addresses: '9007199254740993',
      network_total_circulation_tokens: '20186000000000000000000000000',
      power_required_per_mars_daily: 3441400, emission_model_status: 'verified', emission_reference_block: 1000,
      preview_metric_sources: {
        network_total_power: node, network_total_burned_tokens: node, emission: node,
        network_current_price: official, network_highest_price: official,
        explorer_total_addresses: official, network_total_circulation_tokens: official,
      },
    }, rows: Array.from({ length: 100 }, () => ({ power: '1000' })) },
    daily: { meta: {
      statistics_window_start_timestamp: END - DAY, statistics_window_end_timestamp: END,
      statistics_window_start_local: at(END - DAY, 8 * 3600), statistics_window_end_local: at(END, 8 * 3600),
      preview_daily_status: proof,
      node_preview: { state: 'frozen', source_block: 900, target_timestamp: END },
      network_total_power: '900719925474099312345', network_total_burned_tokens: '10000000000000000000',
      statistics_window_new_power: '-1', statistics_window_burned_tokens: '1',
      statistics_window_active_transactions_seen: 3, statistics_window_transaction_volume_wei: '1000000000000000001',
      statistics_window_active_wallet_address_count: 2, statistics_window_new_candidate_address_count: 0,
      statistics_window_end_total_power: '900719925474099312345',
    } },
    daily_evidence: { target_timestamp: END, source_block: 900, sha256: '1'.repeat(64), daily_statistics_status: proof },
  };
  for (const days of [7, 30]) Object.assign(base.daily.meta, {
    [`period_${days}d_start_timestamp`]: END - days * DAY,
    [`period_${days}d_end_timestamp`]: END,
    [`period_${days}d_start_local`]: at(END - days * DAY, 8 * 3600),
    [`period_${days}d_end_local`]: at(END, 8 * 3600),
    [`period_${days}d_new_power`]: '1234567890123456789',
    [`period_${days}d_burned_tokens`]: '1000000000000000000',
    [`period_${days}d_new_candidate_address_count`]: null,
  });
  return base;
};
const modelRuntime = () => {
  const window = {};
  for (const file of ['report_locales.js', 'report_daily_live_data.js']) vm.runInNewContext(read(file), { window });
  return window;
};

test('four reports use one immutable bundle and convert every actual time endpoint', () => {
  const window = modelRuntime(), raw = fixture(), original = JSON.stringify(raw);
  for (const language of ['zh', 'en', 'ja', 'ko']) {
    const value = window.MarsReportData.build(raw, { language });
    const offset = language === 'zh' ? 8 * 3600 : 0;
    assert.equal(value.language, language);
    assert.equal(value.timeZone, language === 'zh' ? 'Asia/Shanghai' : 'UTC');
    assert.equal(value.live.snapshotTimeText, at(AT, offset));
    assert.equal(value.live.snapshotUtcText, at(AT));
    assert.equal(value.live.timeText, at(AT - 20, offset));
    assert.equal(value.daily.rangeText.startsWith(at(END - DAY, offset)), true);
    assert.equal(value.daily.rangeText.endsWith(at(END, offset)), true);
    for (const item of value.daily.periods) {
      assert.equal(item.rangeText.startsWith(at(END - item.days * DAY, offset)), true);
      assert.equal(item.rangeText.endsWith(at(END, offset)), true);
    }
    assert.equal(value.live.intraday.rangeText.startsWith(at(END, offset)), true);
    assert.equal(value.live.intraday.rangeText.endsWith(at(AT - 20, offset)), true);
    assert.equal(value.live.triggerPrice, '0.01127');
    assert.equal(value.live.intraday.power, '+1', 'subtraction remains exact above Number.MAX_SAFE_INTEGER');
    assert.equal(value.daily.newPower, '−1');
    assert.equal(value.live.addresses, language === 'zh' ? '9,007,199,254,740,993个' : '9,007,199,254,740,993');
    assert.equal(value.daily.burned, language === 'zh' ? '<0.000001枚' : '<0.000001 MARS');
    assert.equal(value.daily.volume, language === 'zh' ? '1枚' : '1 MARS');
  }
  assert.equal(JSON.stringify(raw), original);
  assert.equal(window.MarsReportData.build(raw, { language: 'unsupported' }).language, 'zh');
});

test('retaining a bundle changes only the retained label, in all four languages', () => {
  const window = modelRuntime(), raw = fixture();
  for (const language of ['zh', 'en', 'ja', 'ko']) {
    const ready = window.MarsReportData.build(raw, { language });
    const retained = window.MarsReportData.build({ ...raw, state: 'retained', retained_at: AT + 10000 }, { language });
    assert.equal(retained.live.retained, true);
    retained.live.retained = false;
    assert.deepEqual(JSON.parse(JSON.stringify(retained)), JSON.parse(JSON.stringify(ready)));
  }
});

test('all languages reject inconsistent daily evidence and quarantine invalid period windows', () => {
  const window = modelRuntime();
  for (const language of ['zh', 'en', 'ja', 'ko']) {
    const bad = fixture();
    bad.daily.meta.statistics_window_end_local = '2026-11-01 00:00';
    assert.throws(() => window.MarsReportData.build(bad, { language }));
    const period = fixture();
    period.daily.meta.period_7d_end_timestamp -= 1;
    const value = window.MarsReportData.build(period, { language });
    assert.equal(value.daily.periods[0].power, language === 'zh' ? '待补齐' : window.MarsReportLocales[language].missing);
    assert.notEqual(value.daily.periods[1].power, value.daily.periods[0].power);
    const unsafe = fixture();
    unsafe.live.meta.explorer_total_addresses = 9007199254740992;
    assert.equal(window.MarsReportData.build(unsafe, { language }).live.addresses,
      language === 'zh' ? '待补齐' : window.MarsReportLocales[language].missing);
  }
});

test('foreign renderers draw native copy with UTC clocks and enforce complete frames', async () => {
  const window = modelRuntime(), drawn = [];
  window.MarsReportCanvas = { fillRound() {}, drawQr() {} };
  window.MarsUnifiedReport = {};
  class ReadyImage {
    naturalWidth = 4096; naturalHeight = 1024;
    set src(value) { assert.match(value, /^assets\/mars-masthead/); queueMicrotask(() => this.onload()); }
  }
  vm.runInNewContext(read('report_english_canvas.js'), { window, Image: ReadyImage });
  const gradient = () => ({ addColorStop() {} });
  const context = new Proxy({}, { get(target, key) {
    if (key === 'createLinearGradient') return gradient;
    if (key === 'measureText') return value => ({ width: String(value).length * 12 });
    if (key === 'fillText') return (value, x, y, width) => drawn.push({ value: String(value), x, y, width });
    return target[key] || (() => {});
  } });
  for (const language of ['en', 'ja', 'ko']) {
    drawn.length = 0;
    const renderer = window.MarsReportRenderers[language];
    await renderer.prepare();
    const canvas = { getContext: () => context };
    const data = window.MarsReportData.build(fixture(), { language });
    renderer.render(canvas, data);
    assert.deepEqual([canvas.width, canvas.height], [1024, 1840]);
    const text = drawn.map(item => item.value).join('\n');
    assert.ok(text.includes(window.MarsReportLocales[language].canvas.title));
    assert.ok(text.includes(data.live.snapshotTimeText));
    assert.ok(text.includes('UTC'));
    assert.ok(!text.includes('Beijing') && !text.includes('北京时间'));
    assert.ok(!text.includes('待补齐') && !text.includes('Window pending verification'));
    assert.ok(drawn.every(item => item.x >= 0 && item.x <= 1024 && item.y >= 0 && item.y <= 1840));
    const missing = JSON.parse(JSON.stringify(data));
    missing.live.totalPower = window.MarsReportLocales[language].missing;
    assert.throws(() => renderer.render(canvas, missing));
  }
});
