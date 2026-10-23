"""Fixed-cutoff, completeness and atomic archive regression checks."""
import copy
import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from node_preview.chain import ChainError, ReconcileRequired, ZERO
from node_preview.freeze import CutoffNotReady, archive_name, freeze, sha256
from node_preview.test_collector import FakeRPC, account, user


class FakeMetrics:
    def __init__(self):
        self.calls = []

    def bootstrap(self):
        return {'payload': {'meta': {'generated_at': 1700000000, 'network_total_power': '100000',
            'statistics_window_end_timestamp': 1699920000, 'rpc_url': 'PRIVATE_RPC_ENDPOINT'}, 'rows': []}}

    def refresh(self, meta, at):
        self.calls.append(at)
        result = copy.deepcopy(meta)
        result['network_total_power'] = '999'
        result['preview_daily_status'] = {'state': 'preserved_published_snapshot'}
        return result


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'state').mkdir()
        self.accounts = [ZERO] + list(map(account, range(106)))
        with sqlite3.connect(self.root / 'state/preview.sqlite3') as db:
            db.execute('CREATE TABLE addresses(address TEXT PRIMARY KEY)')
            db.executemany('INSERT INTO addresses VALUES(?)', [(a,) for a in self.accounts])
        self.rpc = FakeRPC(head=16)  # safe 10, block 10 timestamp 1700000030.
        self.rpc.data = {a: user(a, index * 100) for index, a in enumerate(self.accounts)}
        self.target = 1700000030
        self.metrics = FakeMetrics()

    def tearDown(self):
        self.temp.cleanup()

    def freeze(self, **kwargs):
        return freeze(self.root, at=kwargs.pop('at', self.target), workers=3, batch_size=11,
                      rpc_client=self.rpc, metric_store=self.metrics, **kwargs)

    def test_all_known_addresses_fixed_block_and_max_100_public(self):
        result = self.freeze()
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['source_block'], 9)
        self.assertEqual({at for _, at in self.rpc.user_calls}, {9})
        self.assertEqual(result['known_count'], len(self.accounts))
        self.assertEqual(result['processed_count'], len(self.accounts))
        self.assertTrue(result['complete_for_known_addresses'])
        self.assertFalse(result['full_chain_census'])
        archive = Path(result['archive'])
        with gzip.open(archive / 'power.jsonl.gz', 'rt') as file:
            records = [json.loads(line) for line in file]
        self.assertEqual({r['address'] for r in records}, set(self.accounts))
        self.assertEqual({r['fetched_block'] for r in records}, {9})
        self.assertNotIn('balance', json.dumps(records))
        public = json.loads((archive / 'public.json').read_text())
        self.assertEqual(len(public['rows']), 100)
        self.assertEqual(set(public['rows'][0]), {'rank', 'address', 'power'})
        self.assertEqual(public['rows'][0]['address'], self.accounts[-1])
        self.assertNotIn('PRIVATE_RPC_ENDPOINT', json.dumps(public))
        self.assertEqual(public['meta']['network_total_power'], str(self.rpc.global_power(9)))
        self.assertEqual(result['daily_statistics_cutoff'], 1699920000)
        self.assertEqual(self.metrics.calls[0], 10)  # daily metrics see confirmed end marker.
        self.assertEqual(self.metrics.calls[1]['number'], 9)  # archive rows/global stay at B.
        for name, info in result['files'].items():
            self.assertEqual(sha256(archive / name), info['sha256'])
        self.assertFalse((self.root / 'site').exists())

    def test_identical_target_is_idempotent_without_new_rpc(self):
        first = self.freeze()
        before = (Path(first['archive']) / 'manifest.json').read_bytes()
        self.rpc.head = lambda: (_ for _ in ()).throw(AssertionError('must not query latest state'))
        second = self.freeze()
        self.assertTrue(second['already_complete'])
        self.assertEqual(first['frozen_at'], second['frozen_at'])
        self.assertEqual(before, (Path(first['archive']) / 'manifest.json').read_bytes())

    def test_failed_new_target_keeps_previous_success_and_no_partial_directory(self):
        first = self.freeze()
        original = (Path(first['archive']) / 'manifest.json').read_bytes()
        self.rpc.current_head = 17  # safe block11 confirms next manual target.
        self.rpc.fail_users = True
        with self.assertRaises(ChainError):
            self.freeze(at=self.target + 3)
        self.assertEqual(original, (Path(first['archive']) / 'manifest.json').read_bytes())
        self.assertFalse((self.root / 'archives' / archive_name(self.target + 3)).exists())
        self.assertEqual(len(list((self.root / 'archives').iterdir())), 1)
        status = json.loads((self.root / 'state/freezer-status.json').read_text())
        self.assertEqual(status['state'], 'error')
        self.assertEqual(status['last_success']['target_timestamp'], self.target)

    def test_hash_changed_during_query_refuses_finalization(self):
        self.rpc.change_hash_after_users = True
        with self.assertRaises(ReconcileRequired):
            self.freeze()
        self.assertFalse((self.root / 'archives' / archive_name(self.target)).exists())
        self.assertEqual(list((self.root / 'archives').iterdir()), [])
        status = json.loads((self.root / 'state/freezer-status.json').read_text())
        self.assertEqual(status['state'], 'error')

    def test_waits_for_confirmed_cutoff_marker(self):
        self.rpc.current_head = 15  # safe9 is still before target.
        with self.assertRaises(CutoffNotReady):
            self.freeze()
        self.assertEqual(self.rpc.user_calls, [])
        status = json.loads((self.root / 'state/freezer-status.json').read_text())
        self.assertEqual(status['state'], 'waiting')

    def test_daily_directory_and_manual_directory_cannot_collide(self):
        midnight = 1792713600 // 86400 * 86400
        self.assertTrue(archive_name(midnight).endswith('-0800'))
        self.assertTrue(archive_name(midnight + 1).endswith('-manual'))
        self.assertNotEqual(archive_name(midnight), archive_name(midnight + 1))


if __name__ == '__main__':
    unittest.main()
