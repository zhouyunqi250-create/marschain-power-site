/* Pure controller regressions. These state-only doubles do not render a DOM,
 * open a browser, validate pixels, or claim that a real download succeeded. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, 'share_report_ui.js'), 'utf8');
const CLOCK = Date.parse('2026-10-29T16:42:00Z');
const SECOND = CLOCK / 1000;
const PENDING = '快报数据更新中，补齐后将自动生成。';
const REQUIRED = ['price', 'highestPrice', 'triggerPrice', 'circulation', 'addresses'];
const model = (live = {}) => ({
  live: {
    price: '0.01035', highestPrice: '0.02254', triggerPrice: '0.01127',
    circulation: '201.86亿枚', addresses: '286,323个',
    snapshotTimeText: '2026-10-30 00:42', snapshotUtcText: '2026-10-29 16:42',
    totalPower: '576.127万亿', perCoin: '344.14万', oneYiOutput: '29.058枚/日',
    top100Share: '34.64%', burned: '350.857亿枚', ...live,
  },
  daily: {
    dateLabel: '2026.10.29',
    rangeText: '2026-10-28 08:00 至 2026-10-29 08:00',
    periods: [7, 30].map(days => ({ days, addresses: '暂无可比采样', power: '待补齐', burned: '待补齐' })),
  },
});
const bundle = (data, options = {}) => ({
  schema: 1, state: options.state || 'ready', retrieved_at: options.retrieved ?? SECOND,
  ...(options.retainedAt === undefined ? {} : { retained_at: options.retainedAt }),
  live: { meta: { node_preview: { state: 'ready', source_block: 1000,
    source_block_timestamp: options.sourceTime ?? (options.retrieved ?? SECOND) - 20 } } },
  daily: { meta: { statistics_window_end_timestamp: options.dailyEnd
    ?? Math.floor((options.retrieved ?? SECOND) / 86400) * 86400 } },
  model: data,
});
const element = () => {
  const classes = new Set();
  return {
    style: {}, disabled: false, hidden: false, textContent: '', dataset: {},
    setAttribute() {}, remove() {},
    classList: {
      toggle(name, on) { if (on) classes.add(name); else classes.delete(name); },
      contains(name) { return classes.has(name); },
    },
  };
};

const harness = (data) => {
  let clock = CLOCK;
  class ClockDate extends Date {
    constructor(...args) { super(...(args.length ? args : [clock])); }
    static now() { return clock; }
  }
  const calls = { render: [], encoded: 0, downloads: [], shares: [], prepare: 0 };
  const events = new Map();
  const nodes = Object.fromEntries(['download', 'share', 'refresh', 'status', 'snapshot', 'daily', 'scope']
    .map(key => [key, element()]));
  let displayed = null;
  const canvas = () => ({
    ...element(), width: 300, height: 150,
    replaceWith(next) { displayed = next; },
    toBlob(done) {
      calls.encoded += 1;
      Promise.resolve().then(() => done(new Blob(['encoded-report-state-double'], { type: 'image/png' })));
    },
  });
  displayed = canvas();
  const modal = {
    hidden: false,
    querySelector(selector) {
      const found = selector.match(/^\[data-poster-(download|share|refresh|status|snapshot|daily|scope)\]$/);
      if (!found) throw new Error(`Unexpected controller selector: ${selector}`);
      return nodes[found[1]];
    },
    querySelectorAll(selector) {
      assert.equal(selector, '.poster-actions button');
      return [nodes.download, nodes.share, nodes.refresh];
    },
  };
  const panel = element();
  const placeholder = element();
  const document = {
    documentElement: { lang: 'zh-CN' }, fonts: { ready: Promise.resolve() },
    addEventListener() {},
    createElement(tag) {
      if (tag === 'canvas') return canvas();
      assert.equal(tag, 'a');
      return { ...element(), click() { calls.downloads.push(this.download); } };
    },
    body: { appendChild() {} },
    getElementById() { return null; },
  };
  const window = {
    MarsShareReportPayload: bundle(data),
    MarsReportData: { build(value) {
      const data = structuredClone(value.model);
      data.live.retained = value.state === 'retained';
      return data;
    } },
    MarsUnifiedReport: {
      prepare() { calls.prepare += 1; return Promise.resolve(); },
      render(staged, value) {
        calls.render.push(value);
        staged.width = 1024;
        staged.height = 1776;
      },
    },
    addEventListener(name, handler) { events.set(name, handler); },
    setTimeout(handler) { handler(); return 0; },
  };
  // Expose only this module's private state in memory. The published source is
  // unmodified and gains no diagnostic globals or bypasses from this harness.
  const hooks = `
  window.reportHarness = {
    attach(value) { modal=value.modal; panel=value.panel; canvas=value.canvas; placeholder=value.placeholder; },
    state() { return { busy, exportable, rendered }; },
    generate, download, share, filename
  };
})();`;
  assert.match(SOURCE, /\}\)\(\);\s*$/);
  const instrumented = SOURCE.replace(/\}\)\(\);\s*$/, hooks);
  vm.runInNewContext(instrumented, {
    window, document, Date: ClockDate, Blob,
    navigator: { canShare: () => true, share: async value => { calls.shares.push(value); } },
    File: class { constructor(parts, name, options) { this.parts=parts; this.name=name; this.type=options.type; } },
    URL: { createObjectURL: () => 'blob:controller-state-double', revokeObjectURL() {} },
  }, { filename: 'share_report_ui.js', timeout: 1000 });
  const api = window.reportHarness;
  api.attach({ modal, panel, canvas: displayed, placeholder });
  return {
    api, calls, nodes, modal, placeholder,
    displayed: () => displayed,
    update(next, options) {
      window.MarsShareReportPayload = bundle(next, options);
      const handler = events.get('mars-share-report-data');
      assert.equal(typeof handler, 'function');
      handler();
    },
    setBundle(next, options) { window.MarsShareReportPayload = bundle(next, options); },
    setRawBundle(next) { window.MarsShareReportPayload = next; },
    advance(seconds) { clock += seconds * 1000; },
  };
};
const flush = () => new Promise(resolve => setImmediate(resolve));
const atTime = (second, offset = 8 * 3600) => new Date((second + offset) * 1000).toISOString().slice(0, 16).replace('T', ' ');
const modelAt = (second) => {
  const result = model({ snapshotTimeText: atTime(second), snapshotUtcText: atTime(second, 0) });
  const end = Math.floor(second / 86400) * 86400;
  result.daily.dateLabel = atTime(end).slice(0, 10).replace(/-/g, '.');
  result.daily.rangeText = `${atTime(end - 86400)} 至 ${atTime(end)}`;
  return result;
};

test('missing official fields cannot reach render, encoding, download or share', async () => {
  for (const key of REQUIRED) {
    for (const missing of ['待补齐', '', '   ', null, undefined]) {
      const h = harness(model({ [key]: missing }));
      await h.api.generate();
      assert.equal(h.api.state().exportable, false, key);
      assert.equal(h.calls.render.length, 0, key);
      assert.equal(h.calls.encoded, 0, key);
      assert.equal(h.nodes.download.disabled, true, key);
      assert.equal(h.nodes.share.disabled, true, key);
      assert.equal(h.nodes.status.textContent, PENDING, key);
      assert.equal(h.nodes.status.classList.contains('is-error'), false, key);
      assert.equal(await h.api.download(), false, key);
      await h.api.share();
      assert.equal(h.calls.downloads.length, 0, key);
      assert.equal(h.calls.shares.length, 0, key);
    }
  }
});

test('a new complete bundle automatically recovers pending generation', async () => {
  const h = harness(model({ circulation: '待补齐' }));
  await h.api.generate();
  h.update(model());
  await flush();
  assert.equal(h.api.state().exportable, true);
  assert.equal(h.calls.render.length, 1);
  assert.equal(h.calls.encoded, 1);
  assert.equal(h.nodes.download.disabled, false);
  assert.equal(h.nodes.share.disabled, false);
  assert.equal(await h.api.download(), true);
  assert.equal(h.calls.downloads[0], 'marschain-report-2026-10-30-0042.png');
  assert.equal(h.calls.render[0].daily.periods[0].addresses, '暂无可比采样');
  assert.equal(h.calls.render[0].daily.periods[1].burned, '待补齐');
});

test('numeric zero and formatted zero values remain valid', async () => {
  const h = harness(model({ price: 0, highestPrice: '0', triggerPrice: '0.000000', circulation: '0枚', addresses: '0个' }));
  await h.api.generate();
  assert.equal(h.api.state().exportable, true);
  assert.equal(h.calls.render.length, 1);
  await h.api.share();
  assert.equal(h.calls.shares.length, 1);
});

test('a complete generated picture remains a fixed snapshot on later live updates', async () => {
  const first = model();
  const h = harness(first);
  await h.api.generate();
  const firstPicture = h.api.state().rendered.data;
  h.update(model({ price: '0.01036', snapshotTimeText: '2026-10-30 00:43' }));
  await flush();
  assert.equal(h.calls.render.length, 1);
  assert.equal(h.api.state().rendered.data, firstPicture);
  assert.equal(await h.api.download(), true);
  assert.equal(h.calls.downloads[0], 'marschain-report-2026-10-30-0042.png');
});

test('export rechecks the stored model and blocks a historical partial image', async () => {
  const h = harness(model());
  await h.api.generate();
  h.api.state().rendered.data.live.addresses = '待补齐';
  assert.equal(await h.api.download(), false);
  await h.api.share();
  assert.equal(h.api.state().exportable, false);
  assert.equal(h.calls.downloads.length, 0);
  assert.equal(h.calls.shares.length, 0);
  assert.equal(h.nodes.status.textContent, PENDING);
  h.update(model({ snapshotTimeText: '2026-10-30 00:43' }));
  await flush();
  assert.equal(h.api.state().exportable, true);
  assert.equal(h.calls.render.length, 2);
});

test('an incomplete refresh retains the previous complete snapshot with the retained title', async () => {
  const h = harness(model());
  await h.api.generate();
  const previous = h.displayed();
  h.setBundle(model({ price: '待补齐' }));
  await h.api.generate();
  assert.notEqual(h.displayed(), previous);
  assert.equal(h.api.state().exportable, true);
  assert.equal(h.calls.render.length, 2);
  assert.equal(h.api.state().rendered.data.live.retained, true);
  assert.equal(h.api.state().rendered.data.live.price, '0.01035');
  assert.match(h.nodes.status.textContent, /^更新暂缓，保留上次完整快照。/);
});

test('download names use the verified snapshot wall time, with the prior fallback only for invalid times', () => {
  const h = harness(model());
  assert.equal(h.api.filename(model()), 'marschain-report-2026-10-30-0042.png');
  assert.equal(h.api.filename(model({ snapshotTimeText: '2026-10-30 23:59' })), 'marschain-report-2026-10-30-2359.png');
  assert.equal(h.api.filename(model({ snapshotTimeText: '2026-02-30 00:42' })), 'marschain-report-2026-10-29.png');
  assert.equal(h.api.filename(model({ snapshotTimeText: '待补齐' })), 'marschain-report-2026-10-29.png');
});

test('a server-retained complete bundle remains downloadable with its original clocks and values', async () => {
  const originalTime = SECOND - 86400;
  const data = modelAt(originalTime);
  const input = bundle(data, { state: 'retained', retrieved: originalTime, retainedAt: SECOND });
  const before = JSON.stringify(input);
  const h = harness(model());
  h.setRawBundle(input);
  assert.equal(await h.api.generate(), true);
  assert.equal(h.api.state().rendered.data.live.retained, true);
  assert.equal(h.api.state().rendered.data.live.snapshotTimeText, atTime(originalTime));
  assert.equal(h.api.state().rendered.data.daily.rangeText, data.daily.rangeText);
  assert.equal(h.nodes.status.textContent, '更新暂缓，保留上次完整快照。');
  assert.match(h.nodes.scope.textContent, /数据时间保持原样/);
  assert.equal(await h.api.download(), true);
  assert.equal(h.calls.downloads[0], 'marschain-report-2026-10-29-0042.png');
  const sharing = h.api.share();
  assert.equal(h.calls.shares.length, 1, 'a prepared retained Blob uses system sharing immediately');
  await sharing;
  assert.equal(JSON.stringify(input), before, 'retention cannot rewrite the original bundle');
});

test('invalid original timestamps, node state or retained receipt cannot become a retained image', async () => {
  const cases = [
    { state: 'retained' },
    { state: 'retained', retainedAt: SECOND - 1 },
    { state: 'retained', retainedAt: SECOND + 61 },
    { state: 'retained', retainedAt: SECOND, sourceTime: SECOND - 301 },
    { state: 'retained', retainedAt: SECOND, sourceTime: SECOND + 61 },
    { state: 'retained', retainedAt: SECOND + 61, retrieved: SECOND + 61 },
    { state: 'retained', retainedAt: SECOND, dailyEnd: Math.floor(SECOND / 86400) * 86400 - 86400 },
  ];
  for (const options of cases) {
    const h = harness(model());
    h.setRawBundle(bundle(model(), options));
    assert.equal(await h.api.generate(), false, JSON.stringify(options));
    assert.equal(h.calls.render.length, 0);
    assert.equal(h.api.state().exportable, false);
    assert.equal(await h.api.download(), false);
  }
  const h = harness(model());
  const bad = bundle(model(), { state: 'retained', retainedAt: SECOND });
  bad.live.meta.node_preview.state = 'waiting';
  h.setRawBundle(bad);
  assert.equal(await h.api.generate(), false);
  assert.equal(h.calls.render.length, 0);
});

test('an old ready response becomes retained only if it was fresh and coherent when retrieved', async () => {
  const originalTime = SECOND - 3600;
  const h = harness(model());
  const input = bundle(modelAt(originalTime), { retrieved: originalTime });
  const before = JSON.stringify(input);
  h.setRawBundle(input);
  assert.equal(await h.api.generate(), true);
  assert.equal(h.api.state().rendered.bundle.state, 'retained');
  assert.equal(h.api.state().rendered.data.live.retained, true);
  assert.equal(h.api.state().rendered.bundle.retrieved_at, originalTime);
  assert.equal(h.api.state().rendered.bundle.retained_at, SECOND);
  assert.equal(JSON.stringify(input), before);
  const bad = harness(model());
  bad.setRawBundle(bundle(modelAt(originalTime), { retrieved: originalTime, sourceTime: originalTime - 301 }));
  assert.equal(await bad.api.generate(), false);
  assert.equal(bad.calls.render.length, 0);
});

test('one expired download click relabels the original bundle before downloading', async () => {
  const h = harness(model());
  await h.api.generate();
  const original = h.api.state().rendered.bundle;
  h.advance(301);
  assert.equal(await h.api.download(), true);
  assert.equal(h.calls.render.length, 2);
  assert.equal(h.calls.render[1].live.retained, true);
  assert.equal(h.calls.downloads.length, 1);
  assert.equal(h.calls.downloads[0], 'marschain-report-2026-10-30-0042.png');
  assert.equal(h.api.state().rendered.bundle.retrieved_at, original.retrieved_at);
  assert.equal(h.api.state().rendered.bundle.live, original.live);
  assert.equal(h.api.state().rendered.bundle.daily, original.daily);
  assert.match(h.nodes.status.textContent, /^更新暂缓，保留上次完整快照。/);
});

test('expired sharing prepares a retained Blob first and preserves native activation on the next click', async () => {
  const h = harness(model());
  await h.api.generate();
  h.advance(301);
  await h.api.share();
  assert.equal(h.calls.shares.length, 0);
  assert.equal(h.api.state().rendered.data.live.retained, true);
  assert.match(h.nodes.status.textContent, /请再次点击分享图片/);
  const secondClick = h.api.share();
  assert.equal(h.calls.shares.length, 1, 'no image-generation await occurs before navigator.share');
  await secondClick;
});

test('a recovered ready event automatically replaces a retained image', async () => {
  const h = harness(model());
  h.setBundle(modelAt(SECOND - 3600), { retrieved: SECOND - 3600 });
  await h.api.generate();
  assert.equal(h.api.state().rendered.data.live.retained, true);
  h.update(model({ price: '0.01036' }));
  await flush();
  assert.equal(h.calls.render.length, 2);
  assert.equal(h.api.state().rendered.data.live.retained, false);
  assert.equal(h.api.state().rendered.data.live.price, '0.01036');
  assert.equal(h.nodes.status.textContent, '快报已生成，可下载或分享。');
});

test('broken updates do not replace or disable an already-retained complete image', async () => {
  const h = harness(model());
  h.setBundle(modelAt(SECOND - 3600), { retrieved: SECOND - 3600 });
  await h.api.generate();
  const first = h.api.state().rendered;
  h.update(model({ price: '待补齐' }));
  await flush();
  assert.equal(h.api.state().rendered, first);
  assert.equal(h.api.state().exportable, true);
  assert.equal(h.calls.render.length, 1);
  assert.equal(await h.api.download(), true);
});

test('no prior complete image plus a waiting response stays non-exportable', async () => {
  const h = harness(model());
  h.setRawBundle({ schema: 1, state: 'waiting_for_settlement' });
  assert.equal(await h.api.generate(), false);
  assert.equal(h.calls.render.length, 0);
  assert.equal(await h.api.download(), false);
  assert.equal(h.nodes.download.disabled, true);
});

test('a retained response with missing core data never becomes the local complete cache', async () => {
  for (const key of ['totalPower', 'perCoin', 'oneYiOutput', 'top100Share', 'burned', 'snapshotTimeText', 'snapshotUtcText']) {
    const h = harness(model());
    h.setBundle(model({ [key]: '待补齐' }), { state: 'retained', retainedAt: SECOND });
    assert.equal(await h.api.generate(), false, key);
    assert.equal(h.calls.render.length, 0, key);
    assert.equal(h.api.state().rendered, null, key);
  }
});

test('crossing Beijing 08:00 retains the old actual daily range instead of relabelling it as today', async () => {
  const h = harness(model());
  await h.api.generate();
  const oldRange = h.api.state().rendered.data.daily.rangeText;
  h.advance(86400);
  assert.equal(await h.api.download(), true);
  assert.equal(h.api.state().rendered.data.live.retained, true);
  assert.equal(h.api.state().rendered.data.daily.rangeText, oldRange);
  assert.equal(h.api.state().rendered.data.live.snapshotTimeText, '2026-10-30 00:42');
});

test('the actual model and renderer retain only the label, never replacing source clocks or daily evidence', async () => {
  const end = Math.floor(SECOND / 86400) * 86400;
  const status = { state: 'verified_node_window', frozen_end_timestamp: end, expected_end_timestamp: end };
  const node = { source: 'node', status: 'ok', block: 1000 };
  const official = { source: 'official', status: 'ok', fetched_at: SECOND };
  const raw = {
    schema: 1, state: 'ready', retrieved_at: SECOND,
    live: {
      meta: {
        generated_at: SECOND,
        node_preview: { state: 'ready', source_block: 1000, source_block_timestamp: SECOND - 20 },
        network_total_power: '1000', network_total_burned_tokens: '100000000000000000000',
        network_current_price: '0.01035', network_highest_price: '0.02254', explorer_total_addresses: 286323,
        network_total_circulation_tokens: '20186000000000000000000000000',
        power_required_per_mars_daily: 10, emission_model_status: 'verified', emission_reference_block: 1000,
        preview_metric_sources: {
          network_total_power: node, network_total_burned_tokens: node, emission: node,
          network_current_price: official, network_highest_price: official,
          explorer_total_addresses: official, network_total_circulation_tokens: official,
        },
      },
      rows: Array.from({ length: 100 }, () => ({ power: '1' })),
    },
    daily: { meta: {
      statistics_window_start_timestamp: end - 86400, statistics_window_end_timestamp: end,
      preview_daily_status: status,
      node_preview: { state: 'frozen', source_block: 900, target_timestamp: end },
      network_total_power: '900', network_total_burned_tokens: '90000000000000000000',
      statistics_window_new_power: '100', statistics_window_burned_tokens: '10000000000000000000',
      statistics_window_active_transactions_seen: 3, statistics_window_transaction_volume_wei: '1000000000000000000',
      statistics_window_active_wallet_address_count: 2, statistics_window_new_candidate_address_count: 0,
      statistics_window_end_total_power: '900',
    } },
    daily_evidence: { target_timestamp: end, source_block: 900, sha256: '1'.repeat(64), daily_statistics_status: status },
  };
  for (const days of [7, 30]) {
    Object.assign(raw.daily.meta, {
      [`period_${days}d_start_timestamp`]: end - days * 86400,
      [`period_${days}d_end_timestamp`]: end,
      [`period_${days}d_new_power`]: '100', [`period_${days}d_burned_tokens`]: '1000000000000000000',
      [`period_${days}d_new_candidate_address_count`]: null,
    });
  }
  const window = {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'report_daily_live_data.js'), 'utf8'), { window });
  const ready = window.MarsReportData.build(raw);
  const retainedInput = { ...raw, state: 'retained', retained_at: SECOND + 30 };
  const saved = JSON.stringify(retainedInput);
  const retained = window.MarsReportData.build(retainedInput);
  assert.equal(ready.live.retained, false);
  assert.equal(retained.live.retained, true);
  assert.equal(retained.live.snapshotTimeText, ready.live.snapshotTimeText);
  assert.equal(retained.live.snapshotUtcText, ready.live.snapshotUtcText);
  assert.deepEqual(retained.daily, ready.daily);
  assert.equal(JSON.stringify(retainedInput), saved);
  const invalid = structuredClone(retainedInput);
  invalid.daily_evidence.sha256 = 'invalid';
  assert.throws(() => window.MarsReportData.build(invalid), /证据不一致/);

  // Inspect text passed to the actual renderer, not browser pixels or QR output.
  const text = [];
  window.MarsReportCanvas = { fillRound() {}, fitText(_ctx, value) { text.push(String(value)); }, drawQr() {} };
  class ReadyImage {
    naturalWidth = 4096;
    naturalHeight = 1024;
    set src(_value) { queueMicrotask(() => this.onload()); }
  }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'report_unified_canvas.js'), 'utf8'), { window, Image: ReadyImage });
  await window.MarsUnifiedReport.prepare();
  const gradient = () => ({ addColorStop() {} });
  const context = new Proxy({}, { get(target, key) {
    if (key === 'createLinearGradient' || key === 'createRadialGradient') return gradient;
    return target[key] || (() => {});
  } });
  const canvas = { getContext: () => context };
  window.MarsUnifiedReport.render(canvas, ready);
  assert.ok(text.includes('实时快照'));
  assert.ok(!text.includes('上次有效快照'));
  text.length = 0;
  window.MarsUnifiedReport.render(canvas, retained);
  assert.ok(text.includes('上次有效快照'));
  assert.ok(!text.includes('实时快照'));
  assert.ok(text.includes(`快照时间 ${ready.live.snapshotTimeText}（北京时间）`));
  assert.ok(text.includes(ready.daily.rangeText));
  assert.equal(canvas.width, 1024);
  assert.equal(canvas.height, 1776);
});
