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
class FixedDate extends Date {
  constructor(...args) { super(...(args.length ? args : [CLOCK])); }
  static now() { return CLOCK; }
}

const model = (live = {}) => ({
  live: {
    price: '0.01035', highestPrice: '0.02254', triggerPrice: '0.01127',
    circulation: '201.86亿枚', addresses: '286,323个',
    snapshotTimeText: '2026-10-30 00:42', ...live,
  },
  daily: {
    dateLabel: '2026.10.29',
    rangeText: '2026-10-28 08:00 至 2026-10-29 08:00',
    periods: [7, 30].map(days => ({ days, addresses: '暂无可比采样', power: '待补齐', burned: '待补齐' })),
  },
});
const bundle = (data) => ({
  state: 'ready',
  live: { meta: { node_preview: { source_block_timestamp: SECOND - 20 } } },
  daily: { meta: { statistics_window_end_timestamp: Math.floor(SECOND / 86400) * 86400 } },
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
  const calls = { render: [], encoded: 0, downloads: [], shares: [], prepare: 0 };
  const events = new Map();
  const nodes = Object.fromEntries(['download', 'share', 'refresh', 'status', 'snapshot', 'daily']
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
      const found = selector.match(/^\[data-poster-(download|share|refresh|status|snapshot|daily)\]$/);
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
    MarsReportData: { build(value) { return value.model; } },
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
    window, document, Date: FixedDate, Blob,
    navigator: { canShare: () => true, share: async value => { calls.shares.push(value); } },
    File: class { constructor(parts, name, options) { this.parts=parts; this.name=name; this.type=options.type; } },
    URL: { createObjectURL: () => 'blob:controller-state-double', revokeObjectURL() {} },
  }, { filename: 'share_report_ui.js', timeout: 1000 });
  const api = window.reportHarness;
  api.attach({ modal, panel, canvas: displayed, placeholder });
  return {
    api, calls, nodes, modal, placeholder,
    displayed: () => displayed,
    update(next) {
      window.MarsShareReportPayload = bundle(next);
      const handler = events.get('mars-share-report-data');
      assert.equal(typeof handler, 'function');
      handler();
    },
    setBundle(next) { window.MarsShareReportPayload = bundle(next); },
  };
};
const flush = () => new Promise(resolve => setImmediate(resolve));

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
      assert.equal(h.api.download(), false, key);
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
  assert.equal(h.api.download(), true);
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
  h.update(model({ price: '0.01036', snapshotTimeText: '2026-10-30 00:43' }));
  await flush();
  assert.equal(h.calls.render.length, 1);
  assert.equal(h.api.state().rendered.data, first);
  assert.equal(h.api.download(), true);
  assert.equal(h.calls.downloads[0], 'marschain-report-2026-10-30-0042.png');
});

test('export rechecks the stored model and blocks a historical partial image', async () => {
  const h = harness(model());
  await h.api.generate();
  h.api.state().rendered.data.live.addresses = '待补齐';
  assert.equal(h.api.download(), false);
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

test('a pending refresh preserves the previous picture without enabling exports', async () => {
  const h = harness(model());
  await h.api.generate();
  const previous = h.displayed();
  h.setBundle(model({ price: '待补齐' }));
  await h.api.generate();
  assert.equal(h.displayed(), previous);
  assert.equal(h.api.state().exportable, false);
  assert.equal(h.calls.render.length, 1);
  assert.match(h.nodes.status.textContent, /^快报数据更新中，补齐后将自动生成。/);
  assert.match(h.nodes.status.textContent, /当前显示上次生成的图片。/);
});

test('download names use the verified snapshot wall time, with the prior fallback only for invalid times', () => {
  const h = harness(model());
  assert.equal(h.api.filename(model()), 'marschain-report-2026-10-30-0042.png');
  assert.equal(h.api.filename(model({ snapshotTimeText: '2026-10-30 23:59' })), 'marschain-report-2026-10-30-2359.png');
  assert.equal(h.api.filename(model({ snapshotTimeText: '2026-02-30 00:42' })), 'marschain-report-2026-10-29.png');
  assert.equal(h.api.filename(model({ snapshotTimeText: '待补齐' })), 'marschain-report-2026-10-29.png');
});
