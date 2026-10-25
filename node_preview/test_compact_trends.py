"""Focused regressions for trend periods, first-open layout and keyboard use."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

import build_frontend_dashboard as dashboard


class CompactTrendTests(unittest.TestCase):
    def test_calendar_periods_and_grouped_alignment(self):
        bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
        node = shutil.which("node") or str(bundled)
        self.assertTrue(Path(node).is_file())
        script = dashboard.METRIC_TREND_JS
        position = script.rfind("})();")
        script = script[:position] + """
window.testTrends = {slicePeriod, groupedSeries, countAxisBounds,
  period: value => activePeriod = value,
  metric: value => activeMetric = value};
""" + script[position:]
        harness = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = {window:{addEventListener(){}},document:{
  getElementById(){return {textContent:'[{"key":"test"}]'};},querySelectorAll(){return [];}
}};
vm.runInNewContext(SCRIPT, context);
const t = context.window.testTrends;
const points = ['2026-10-01','2026-10-17','2026-10-18','2026-10-19','2026-10-23','2026-10-25']
  .map((label,index)=>({label,value:index}));
const labels = rows => Array.from(rows, point=>point.label);
t.period('7');
assert.deepEqual(labels(t.slicePeriod(points)),['2026-10-19','2026-10-23','2026-10-25']);
t.period('30');assert.equal(t.slicePeriod(points).length,6);
t.period('all');assert.equal(t.slicePeriod(points).length,6);
t.period('7');assert.equal(t.slicePeriod([]).length,0);
assert.equal(t.slicePeriod([{label:'sample 1',value:1}]).length,0);
assert.deepEqual(labels(t.slicePeriod(points.slice().reverse())),['2026-10-19','2026-10-23','2026-10-25']);
t.metric({series:[{key:'a',points},{key:'b',points:[{label:'2026-10-17',value:1},{label:'2026-10-25',value:2}]}]});
const grouped=t.groupedSeries();
assert.deepEqual(labels(grouped[0].points),['2026-10-19','2026-10-23','2026-10-25']);
assert.deepEqual(labels(grouped[1].points),['2026-10-25']);
assert.deepEqual(Array.from(t.countAxisBounds(0,122.04,'daily_new_addresses',2)),[0,124]);
assert.deepEqual(Array.from(t.countAxisBounds(0,0,'daily_transaction_count',4)),[0,4]);
assert.deepEqual(Array.from(t.countAxisBounds(0.01,0.02,'network_current_price',4)),[0.01,0.02]);
console.log('calendar and sparse-history cases passed');
""".replace("SCRIPT", json.dumps(script))
        result = subprocess.run([node], input=harness, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_first_open_measures_visible_chart(self):
        code = dashboard.METRIC_TREND_JS.split("const openMetric =", 1)[1]
        self.assertLess(code.index("node.hidden = false"), code.index("renderChart();"))
        self.assertIn("L=112", dashboard.METRIC_TREND_JS)

    def test_popup_focus_and_refresh_hooks(self):
        code = dashboard.METRIC_TREND_JS
        self.assertIn("triggerCard?.focus({preventScroll: true})", code)
        self.assertIn("event.key !== 'Tab'", code)
        self.assertIn("const redrawVisibleChart =", code)
        self.assertIn("point.label === selectedDate", code)
        self.assertIn("requestAnimationFrame(redrawVisibleChart)", code)

    def test_signed_growth_is_translated_to_english_units(self):
        bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
        node = shutil.which("node") or str(bundled)
        source = dashboard.LANGUAGE_TOGGLE_JS
        source = source[source.index("  const trimZeros ="):source.index("  const trendHistoryNotes =")]
        script = "const assert=require('node:assert/strict');\n" + source + """
assert.equal(translateChineseNumericUnit('+10.001万亿'), '+10.001T');
assert.equal(translateChineseNumericUnit('-10.001万亿'), '-10.001T');
assert.equal(translateChineseNumericUnit('113个'), '113');
assert.equal(translateChineseNumericUnit('2.232亿/日'), '223.2M/day');
"""
        result = subprocess.run([node], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
