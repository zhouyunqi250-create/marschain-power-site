'use strict';
// Formal runtime model regressions: no browser, network, RPC, or publication.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const core = require('./holder_ranking.js');
const {WEI, SYSTEM} = core;
const NOW = 2000000000;
const fixture = (count = 101) => {
  const rows = Array.from({length: Math.min(100, count)}, (_, index) => ({
    address: `0x${(0xa000n + BigInt(index)).toString(16).padStart(40, '0')}`,
    balance_wei: String(BigInt(2000 - index) * WEI + BigInt(index)),
    kind: ['wallet', 'contract', 'burn'][index % 3], rank: index + 1,
  }));
  const total = rows.reduce((sum, row) => sum + BigInt(row.balance_wei), 0n);
  return {
    meta: {complete: true, address_count: count + 1, ranking_address_count: count,
      block_number: 7243216, block_timestamp: 1793867046,
      share_denominator_scope: 'indexed_non_system_addresses', system_balances_excluded: true,
      share_denominator_wei: String(count <= 100 ? total : 1000000n * WEI)},
    systems: [{address: SYSTEM, kind: 'system', name: 'POWER'}], rows,
  };
};
const valid = input => core.validateSnapshot(input, NOW);
const powerFixture = (count = 101) => ({
  meta: {positive_power_count: count, network_total_power: '1000000000000000000000000000000'},
  rows: Array.from({length: Math.min(100, count)}, (_, index) => ({
    address: `0x${(0xb000n + BigInt(index)).toString(16).padStart(40, '0')}`,
    power: String(9007199254740993000000n - BigInt(index)),
  })),
});

test('formal module accepts complete snapshots, strips extra row/system fields, and preserves the input', () => {
  const input = fixture();
  input.systems[0].name = '<script>ignored</script>';
  input.rows[0].unexpected = 'ignored';
  input.wallets = [];
  const output = valid(input);
  assert.deepEqual(Object.keys(output), ['meta', 'systems', 'rows']);
  assert.deepEqual(output.systems, [{address: SYSTEM, kind: 'system'}]);
  assert.deepEqual(Object.keys(output.rows[0]), ['address', 'balance_wei', 'kind', 'rank']);
  assert.equal(output.rows.length, 100);
  assert.equal(input.systems[0].name, '<script>ignored</script>');
});

for (const [name, mutate] of [
  ['missing metadata', x => {delete x.meta;}],
  ['incomplete snapshot', x => {x.meta.complete = false;}],
  ['wrong denominator scope', x => {x.meta.share_denominator_scope = 'all_addresses';}],
  ['system exclusion missing', x => {delete x.meta.system_balances_excluded;}],
  ['system exclusion false', x => {x.meta.system_balances_excluded = false;}],
  ['unsafe address count', x => {x.meta.address_count = Number.MAX_SAFE_INTEGER + 1;}],
  ['mismatched address count', x => {x.meta.address_count++;}],
  ['future block timestamp', x => {x.meta.block_timestamp = NOW + 601;}],
  ['millisecond block timestamp', x => {x.meta.block_timestamp *= 1000;}],
  ['missing top row', x => {x.rows.pop();}],
  ['nonconsecutive rank', x => {x.rows[0].rank = 2;}],
  ['invalid address', x => {x.rows[0].address = 'javascript:alert(1)';}],
  ['duplicate address', x => {x.rows[1].address = x.rows[0].address.toUpperCase().replace('0X', '0x');}],
  ['ascending balances', x => {x.rows[0].balance_wei = '0';}],
  ['system disguised as wallet', x => {x.rows[0].address = SYSTEM; x.rows[0].kind = 'wallet';}],
  ['system kind in ranking', x => {x.rows[0].kind = 'system';}],
  ['missing system identity', x => {x.systems = [];}],
  ['unknown system identity', x => {x.systems[0].address = x.rows[0].address;}],
  ['duplicate system identity', x => {x.systems.push({...x.systems[0]});}],
  ['system balance disclosed', x => {x.systems[0].balance_wei = '0';}],
  ['system balance null disclosed', x => {x.systems[0].balance_wei = null;}],
  ['top total exceeds denominator', x => {x.meta.share_denominator_wei = '1';}],
  ['balance exceeds EVM uint256', x => {x.rows[0].balance_wei = String(1n << 256n); x.meta.share_denominator_wei = String(1n << 257n);}],
]) {
  test(`snapshot rejects ${name}`, () => {const data = fixture(); mutate(data); assert.throws(() => valid(data));});
}

for (const field of ['balance_wei', 'share_denominator_wei']) {
  test(`${field} rejects unsafe or noncanonical decimal representations`, () => {
    for (const value of ['-1', '1e18', '0x10', '1.5', '', '00', '01', '1'.repeat(97), 100, null]) {
      const data = fixture();
      if (field === 'balance_wei') data.rows[0][field] = value; else data.meta[field] = value;
      assert.throws(() => valid(data), `${field}=${value}`);
    }
  });
}

test('small complete rankings reconcile all non-system balances exactly', () => {
  const data = fixture(3);
  assert.equal(valid(data).rows.length, 3);
  data.meta.share_denominator_wei = String(BigInt(data.meta.share_denominator_wei) + 1n);
  assert.throws(() => valid(data));
});
test('adjacent BigInt balances above Number precision remain correctly ordered', () => {
  const data = fixture();
  data.rows[0].balance_wei = '9007199254740993000000000000000000001';
  data.rows[1].balance_wei = '9007199254740993000000000000000000000';
  data.meta.share_denominator_wei = String(10n ** 45n);
  assert.equal(valid(data).rows[0].balance_wei, data.rows[0].balance_wei);
  [data.rows[0].balance_wei, data.rows[1].balance_wei] = [data.rows[1].balance_wei, data.rows[0].balance_wei];
  assert.throws(() => valid(data));
});
test('exact formatting keeps all 18 decimals and compact formatting handles tiny balances', () => {
  assert.equal(core.exact(WEI + 1n), '1.000000000000000001 MARS');
  assert.equal(core.exact(10n ** 30n + 1n), '1,000,000,000,000.000000000000000001 MARS');
  assert.equal(core.balance(0n), '0');
  assert.equal(core.balance(1n), '<0.000001');
  assert.equal(core.balance(1000000000000n), '0.000001');
  assert.equal(core.balance(WEI / 2n), '0.5');
});
test('compact formats use Chinese and international units without losing integer precision', () => {
  assert.equal(core.balance(10000n * WEI, 'zh'), '1万');
  assert.equal(core.balance(100000000n * WEI, 'zh'), '1亿');
  assert.equal(core.balance(1000000000000n * WEI, 'zh'), '1万亿');
  for (const lang of ['en', 'ja', 'ko']) {
    for (const [amount, label] of [[1000n, '1K'], [1000000n, '1M'], [1000000000n, '1B'], [1000000000000n, '1T']]) {
      assert.equal(core.balance(amount * WEI, lang), label);
    }
  }
});
test('shares reject invalid denominators and do not round partial ownership to 100%', () => {
  assert.equal(core.share(0n, 100n), '0%');
  assert.equal(core.share(1n, 3n), '33.3333%');
  assert.equal(core.share(1n, 1000001n), '<0.0001%');
  assert.equal(core.share(1n, 1000000n), '0.0001%');
  assert.equal(core.share(9999999999n, 10000000000n), '<100%');
  assert.equal(core.share(3n, 3n), '100%');
  for (const [value, denominator] of [[1n, 0n], [-1n, 10n], [11n, 10n], [null, 10n], [1n, null]]) {
    assert.equal(core.share(value, denominator), '—');
  }
});

for (const page of [1, 2, 10]) {
  test(`pagination page ${page} returns exactly the original ten ranks`, () => {
    const result = core.paginate(valid(fixture()).rows, page);
    assert.equal(result.page, page); assert.equal(result.pages, 10); assert.equal(result.total, 100);
    assert.equal(result.start, (page - 1) * 10 + 1); assert.equal(result.end, page * 10);
    assert.deepEqual(result.rows.map(row => row.rank), Array.from({length: 10}, (_, i) => (page - 1) * 10 + i + 1));
  });
}
test('pagination clamps out of range pages and defaults malformed pages to one', () => {
  const rows = valid(fixture()).rows;
  for (const page of [-1, 0, NaN, Infinity, 1.2, '10', null, undefined]) assert.equal(core.paginate(rows, page).page, 1);
  assert.equal(core.paginate(rows, 999).page, 10);
  assert.equal(core.paginate(rows, Number.MAX_SAFE_INTEGER).page, 10);
});
test('pagination handles an empty list and a partial final page without phantom rows', () => {
  assert.deepEqual(core.paginate([], 10), {rows: [], page: 1, pages: 1, total: 0, start: 0, end: 0});
  const rows = valid(fixture(11)).rows;
  const final = core.paginate(rows, 10);
  assert.equal(final.page, 2); assert.equal(final.start, 11); assert.equal(final.end, 11);
  assert.deepEqual(final.rows.map(row => row.rank), [11]);
});

test('all visible copy has four complete translations, official network-count labels, and no search copy', () => {
  assert.deepEqual(core.languages, ['zh', 'en', 'ja', 'ko']);
  for (const [key, translations] of Object.entries(core.COPY)) {
    assert.equal(translations.length, 4, key);
    assert.ok(translations.every(value => typeof value === 'string' && value.trim()), key);
    assert.doesNotMatch(translations[1], /[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/, key);
    assert.doesNotMatch(key, /search/i);
    for (const lang of core.languages) assert.notEqual(core.text(key, lang), key, `${key}/${lang}`);
  }
  assert.equal(core.text('addresses', 'zh'), '全网地址');
  const source = fs.readFileSync(path.join(__dirname, 'holder_ranking.js'), 'utf8');
  assert.doesNotMatch(source, /data-hrp-search|hrp-search|搜索本榜地址|Search this ranking/);
  assert.match(source, /<h2>TOP100<\/h2>/);
  assert.match(source, /data-hrp-copy/); assert.match(source, /https:\/\/explorer\.marschain\.net\/address\//);
  assert.doesNotMatch(source, /hrp-system-zone/);
});
test('language resolution respects current selection, region tags, URL, and default', () => {
  assert.equal(core.resolveLanguage({search: '?lang=ko', htmlLanguage: 'zh-CN'}), 'ko');
  assert.equal(core.resolveLanguage({localeLanguage: 'ja', search: '?lang=ko'}), 'ja');
  assert.equal(core.resolveLanguage({htmlLanguage: 'EN-us'}), 'en');
  assert.equal(core.resolveLanguage({search: '?lang=unknown', htmlLanguage: 'zh-CN'}), 'zh');
  assert.equal(core.resolveLanguage(), 'zh');
});
test('Chinese balance time is UTC+8; the other three languages use UTC', () => {
  const stamp = Date.parse('2026-11-05T00:00:00Z') / 1000;
  assert.equal(core.timestamp(stamp, 'zh'), '2026-11-05 08:00:00 UTC+8');
  for (const lang of ['en', 'ja', 'ko']) assert.equal(core.timestamp(stamp, lang), '2026-11-05 00:00:00 UTC');
  for (const bad of [null, -1, 0, 1.5, 1e12]) assert.equal(core.timestamp(bad), '—');
});

test('power summary sums all top 100 powers as BigInt without Number rounding', () => {
  const data = powerFixture();
  const expected = data.rows.reduce((total, row) => total + BigInt(row.power), 0n);
  assert.deepEqual(core.powerSummary(data), {count: 101, total: expected, denominator: BigInt(data.meta.network_total_power)});
});
test('power summary accepts safe numeric power fields and complete short rankings', () => {
  const data = powerFixture(2);
  data.rows[0].power = 30; data.rows[1].power = 20; data.meta.network_total_power = 100;
  assert.deepEqual(core.powerSummary(data), {count: 2, total: 50n, denominator: 100n});
});
for (const [name, mutate] of [
  ['zero count', x => {x.meta.positive_power_count = 0;}],
  ['missing row', x => {x.rows.pop();}],
  ['unsafe numeric power', x => {x.rows[0].power = Number.MAX_SAFE_INTEGER + 1;}],
  ['negative power', x => {x.rows[0].power = -1;}],
  ['noncanonical power', x => {x.rows[0].power = '01';}],
  ['duplicate address', x => {x.rows[1].address = x.rows[0].address;}],
  ['invalid address', x => {x.rows[0].address = 'bad';}],
  ['ascending power', x => {x.rows[0].power = '0';}],
  ['zero denominator', x => {x.meta.network_total_power = '0';}],
  ['insufficient denominator', x => {x.meta.network_total_power = '1';}],
]) {
  test(`power summary rejects ${name}`, () => {const data = powerFixture(); mutate(data); assert.equal(core.powerSummary(data), null);});
}

test('the current public-format snapshot reconciles holders, all pages, and power totals', t => {
  const filename = path.join(__dirname, '../output/holder-simple-preview-20261105/site/data/latest.json');
  const available = fs.existsSync(filename);
  const payload = available ? JSON.parse(fs.readFileSync(filename, 'utf8')) : {...powerFixture(), holders: fixture()};
  if (!available) t.diagnostic('Preview snapshot absent; using a deterministic public-format fixture.');
  const input = payload.holders;
  const data = core.validateSnapshot(input, Math.max(NOW, input.meta.block_timestamp));
  const denominator = BigInt(data.meta.share_denominator_wei);
  const top = data.rows.reduce((sum, row) => sum + BigInt(row.balance_wei), 0n);
  assert.ok(top <= denominator);
  assert.ok(data.rows.every(row => row.address !== SYSTEM && row.kind !== 'system'));
  assert.equal(Object.hasOwn(data.systems[0], 'balance_wei'), false);
  assert.notEqual(core.share(top, denominator), '—');
  const pages = Array.from({length: 10}, (_, i) => core.paginate(data.rows, i + 1));
  assert.deepEqual(pages.flatMap(page => page.rows), data.rows);
  assert.equal(new Set(pages.flatMap(page => page.rows).map(row => row.address)).size, data.rows.length);
  const power = core.powerSummary(payload);
  assert.ok(power); assert.ok(power.total <= power.denominator);
  if (available) {
    assert.ok(Number.isSafeInteger(payload.meta.explorer_total_addresses));
    assert.ok(payload.meta.explorer_total_addresses >= data.meta.address_count);
  }
});
