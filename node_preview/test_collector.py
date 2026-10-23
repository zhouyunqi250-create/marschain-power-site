"""Regression checks for data loss, upline effects and block consistency."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from node_preview.chain import BatchError, ChainError, EVENTS, RPC, UserInfo, ZERO
from node_preview.collector import initialize, meta_get, meta_set, run_once


def account(n):
    return '0x' + f'{n + 2**80:040x}'


def block_hash(n, suffix=0):
    return '0x' + f'{n * 100 + suffix:064x}'


def user(n, power=0, upline1=None, upline2=None):
    return UserInfo(n, power, power // 2, power // 2, upline1, upline2, 0, 0, 0, False)


def block(n, txs=None, miner=None):
    return {'number': hex(n), 'hash': block_hash(n), 'parentHash': block_hash(n-1),
            'timestamp': hex(1700000000 + n * 3), 'transactions': txs or [], 'miner': miner}


class FakeRPC(RPC):
    def __init__(self, head=16):
        self.current_head = head
        self.all_blocks = {n: block(n) for n in range(30)}
        self.data = {}
        self.user_calls = []
        self.logs_data = []
        self.fail_users = False
        self.change_hash_after_users = False
        self.receipts = []
        self._event_topics = {'0x' + f'{i:064x}': name for i, name in enumerate(EVENTS, 1)}

    def head(self):
        return self.current_head

    def block(self, number, transactions=False):
        return self.all_blocks[number].copy()

    def blocks(self, start, end):
        return [self.block(n, True) for n in range(start, end + 1)]

    def users(self, accounts, at):
        self.user_calls.append((list(accounts), at))
        if self.fail_users:
            raise ChainError('deliberate RPC failure')
        if self.change_hash_after_users:
            self.all_blocks[at]['hash'] = block_hash(at, 9)
        return {a: self.data.get(a, user(a)) for a in accounts}

    def global_power(self, at):
        return sum(item.power for item in self.data.values())

    def logs(self, start, end):
        return [item for item in self.logs_data if start <= int(item['blockNumber'], 16) <= end]

    def batch(self, calls):
        return self.receipts

    def event(self, name, n, indexed, data):
        topic = next(topic for topic, label in self._event_topics.items() if label == name)
        encode = lambda item: f'{int(item, 16) if isinstance(item, str) else item:064x}'
        return {'address': '0x' + '0' * 36 + '1001', 'blockHash': block_hash(n),
                'blockNumber': hex(n), 'transactionHash': block_hash(n, 5), 'logIndex': '0x0',
                'topics': [topic] + ['0x' + encode(item) for item in indexed],
                'data': '0x' + ''.join(encode(item) for item in data)}


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'preview.sqlite3'
        self.conn = sqlite3.connect(self.db)
        initialize(self.conn)
        with self.conn:
            meta_set(self.conn, 'checkpoint_block', 9)
            meta_set(self.conn, 'checkpoint_hash', block_hash(9))
            meta_set(self.conn, 'calibration_done', True)
            meta_set(self.conn, 'sweep_due', 9999999999)
        self.rpc = FakeRPC()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def run_once(self, **kwargs):
        return run_once(self.db, rpc_client=self.rpc, max_seconds=10, **kwargs)

    def test_refreshes_two_nontransacting_uplines_at_one_pinned_block(self):
        a, b, c, beyond = map(account, range(4))
        self.rpc.all_blocks[10] = block(10, [{'from': a, 'to': account(10), 'value': '0x10'}])
        self.rpc.data = {a: user(a, 100, b, c), b: user(b, 50, beyond), c: user(c, 25), beyond: user(beyond, 999)}
        result = self.run_once(calibration_limit=0)
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(meta_get(self.conn, 'checkpoint_block'), 10)
        rows = dict(self.conn.execute('SELECT address,power FROM preview_power_state'))
        self.assertEqual(rows[b], '50')
        self.assertEqual(rows[c], '25')
        self.assertNotIn(beyond, rows)  # no unlimited ancestor recursion
        self.assertEqual({at for _, at in self.rpc.user_calls}, {10})
        self.assertEqual(self.conn.execute('SELECT value_wei FROM preview_blocks WHERE number=10').fetchone()[0], '16')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM preview_pending').fetchone()[0], 0)

    def test_failure_keeps_checkpoint_and_old_power_and_retries(self):
        a = account(0)
        self.rpc.all_blocks[10] = block(10, [{'from': a, 'to': account(10), 'value': '0x0'}])
        with self.conn:
            self.conn.execute('INSERT INTO powers VALUES(?,?,?,?,?)', (a, '123', '1', '1', 1))
        self.rpc.fail_users = True
        result = self.run_once(calibration_limit=0)
        self.assertEqual(result['state'], 'error')
        self.assertEqual(meta_get(self.conn, 'checkpoint_block'), 9)
        self.assertEqual(self.conn.execute('SELECT power FROM powers WHERE address=?', (a,)).fetchone()[0], '123')
        self.assertGreater(self.conn.execute('SELECT count(*) FROM preview_pending').fetchone()[0], 0)
        self.rpc.fail_users = False
        self.rpc.data[a] = user(a, 500)
        result = self.run_once(calibration_limit=0)
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(meta_get(self.conn, 'checkpoint_block'), 10)
        self.assertEqual(self.conn.execute('SELECT power FROM powers WHERE address=?', (a,)).fetchone()[0], '500')

    def test_failed_upline_lookup_also_prevents_checkpoint_commit(self):
        a, b, c = map(account, range(3))
        self.rpc.all_blocks[10] = block(10, [{'from': a, 'to': account(10), 'value': '0x0'}])
        self.rpc.data[a] = user(a, 100, b, c)
        original = self.rpc.users
        def fail_parent(accounts, at):
            if b in accounts:
                raise ChainError('upline RPC unavailable')
            return original(accounts, at)
        self.rpc.users = fail_parent
        result = self.run_once(calibration_limit=0)
        self.assertEqual(result['state'], 'error')
        self.assertEqual(meta_get(self.conn, 'checkpoint_block'), 9)
        pending = {r[0] for r in self.conn.execute('SELECT address FROM preview_pending')}
        self.assertTrue({a, b, c}.issubset(pending))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM preview_power_state').fetchone()[0], 0)

    def test_network_outage_does_not_recursively_split_log_ranges(self):
        rpc = RPC()
        attempted = []
        def fail(method, params):
            attempted.append((method, params))
            raise ChainError('Node RPC request failed: timed out')
        rpc.call = fail
        with self.assertRaises(ChainError):
            rpc.logs(1, 100)
        self.assertEqual(len(attempted), 1)

    def test_reorg_during_power_calls_never_commits_checkpoint(self):
        a = account(0)
        self.rpc.all_blocks[10] = block(10, [{'from': a, 'to': account(10), 'value': '0x0'}])
        self.rpc.change_hash_after_users = True
        result = self.run_once(calibration_limit=0)
        self.assertEqual(result['state'], 'needs_reconcile')
        self.assertEqual(meta_get(self.conn, 'checkpoint_block'), 9)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM preview_power_state').fetchone()[0], 0)
        self.rpc.change_hash_after_users = False
        # Sticky halt until reconciliation: do not silently resume with suspect values.
        self.assertEqual(self.run_once(calibration_limit=0)['state'], 'needs_reconcile')

    def test_restart_resumes_only_uncommitted_blocks(self):
        self.rpc.current_head = 18
        first = self.run_once(max_blocks=2, block_batch=1, calibration_limit=0)
        self.assertEqual(first['through_block'], 11)
        second = self.run_once(max_blocks=2, block_batch=1, calibration_limit=0)
        self.assertEqual(second['from_block'], 12)
        self.assertEqual(second['through_block'], 12)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM preview_blocks').fetchone()[0], 3)

    def test_catchup_reads_current_confirmed_state_not_pruned_old_end(self):
        self.rpc.current_head = 26  # safe head 20, checkpoint 9.
        a, b = account(0), account(1)
        self.rpc.all_blocks[10] = block(10, [{'from': a, 'to': b, 'value': '0x0'}])
        self.rpc.data[a] = user(a, 100, b)
        original = self.rpc.users
        def only_unpruned_state(accounts, at):
            if at < 20:
                raise ChainError('missing trie node: old state pruned')
            return original(accounts, at)
        self.rpc.users = only_unpruned_state
        first = self.run_once(max_blocks=3, block_batch=2, calibration_limit=0)
        self.assertEqual(first['state'], 'catching_up')
        self.assertEqual(first['through_block'], 12)
        self.assertEqual(self.conn.execute('SELECT fetched_block FROM preview_power_state WHERE address=?', (a,)).fetchone()[0], 20)
        self.assertEqual(meta_get(self.conn, 'global_power_block'), 20)
        second = self.run_once(max_blocks=20, block_batch=2, calibration_limit=0)
        self.assertEqual(second['state'], 'ready')
        self.assertEqual(second['through_block'], 20)
        self.assertEqual({at for _, at in self.rpc.user_calls}, {20})

    def test_calibration_keyset_resume_and_no_seed_as_ready(self):
        accounts = list(map(account, range(5)))
        with self.conn:
            self.conn.executemany('INSERT INTO addresses VALUES(?)', [(a,) for a in accounts])
            meta_set(self.conn, 'checkpoint_block', 10)
            meta_set(self.conn, 'checkpoint_hash', block_hash(10))
            meta_set(self.conn, 'calibration_done', False)
        first = self.run_once(calibration_limit=2, calibration_batch=1)
        self.assertEqual(first['state'], 'warming')
        self.assertEqual(meta_get(self.conn, 'calibration_count'), 2)
        self.assertEqual(meta_get(self.conn, 'calibration_cursor'), accounts[1])
        second = self.run_once(calibration_limit=3, calibration_batch=1)
        self.assertEqual(second['state'], 'ready')
        self.assertTrue(meta_get(self.conn, 'calibration_done'))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM preview_power_state').fetchone()[0], 5)

    def test_typed_event_does_not_treat_year_or_nft_id_as_address(self):
        a, b, c = map(account, range(3))
        log = self.rpc.event('ChristmasTokensBurned', 10, [a, 2026], [500, 200])
        found, event = self.rpc.decode_log(log)
        self.assertEqual(found, {a})
        self.assertEqual(event['amount_wei'], '500')
        log = self.rpc.event('RelationEstablished', 10, [10000123, a], [b, c])
        found, _ = self.rpc.decode_log(log)
        self.assertEqual(found, {a, b, c})

    def test_deployed_get_user_info_abi_and_invalid_zero_result(self):
        a, b, c = map(account, range(3))
        data = [25, 3, 0, 5, 0, int(b, 16), int(c, 16), 7, 1]
        decoded = UserInfo.decode(a, '0x' + ''.join(f'{value:064x}' for value in data))
        self.assertEqual((decoded.power, decoded.burned, decoded.total_burned), (25, 3, 5))
        self.assertEqual((decoded.upline1, decoded.upline2), (b, c))
        with self.assertRaises(ChainError):
            UserInfo.decode(a, '0x')

    def test_batch_missing_result_is_an_error_not_zero(self):
        rpc = RPC()
        rpc._request = lambda payload: [{'id': 1, 'result': '0x1'}]
        with self.assertRaises(BatchError):
            rpc.batch([('eth_call', []), ('eth_call', [])])


if __name__ == '__main__':
    unittest.main()
