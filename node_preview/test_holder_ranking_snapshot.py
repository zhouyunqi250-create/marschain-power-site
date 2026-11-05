"""Offline safety/regression checks: no real node or production files used."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from node_preview import holder_ranking_snapshot as snapshot


def address(number: int) -> str:
    return f"0x{number:040x}"


class FakeRpc:
    def __init__(self, balances=None, codes=None):
        self.balances = balances or {}
        self.codes = codes or {}
        self.calls = []
        self.syncing = False
        self.head = "0x6a"
        self.hash = "0x" + "ab" * 32
        self.final_hash = None
        self.header_count = 0
        self.override = None
        self.lock = threading.Lock()

    def __call__(self, calls):
        with self.lock:
            self.calls.extend(calls)
        if self.override:
            result = self.override(calls)
            if result is not NotImplemented:
                return result
        result = []
        for method, params in calls:
            if method == "eth_syncing":
                result.append(self.syncing)
            elif method == "eth_blockNumber":
                result.append(self.head)
            elif method == "eth_getBlockByNumber":
                self.header_count += 1
                result.append({"number": "0x64", "hash": self.final_hash if self.header_count > 1 and self.final_hash is not None else self.hash,
                               "timestamp": "0x6553f100"})
            elif method == "eth_getBalance":
                result.append(self.balances.get(params[0], "0x0"))
            elif method == "eth_getCode":
                result.append(self.codes.get(params[0], "0x"))
            else:
                raise AssertionError(method)
        return result


class SnapshotCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.make_source("one.sqlite3", [address(100000 + i) for i in range(140)])
        self.sources = [self.source]
        self.output = self.root / "output"

    def tearDown(self):
        self.temporary.cleanup()

    def make_source(self, name, values):
        path = self.root / name
        with sqlite3.connect(path) as database:
            database.execute("CREATE TABLE addresses(address TEXT)")
            database.executemany("INSERT INTO addresses VALUES(?)", ((value,) for value in values))
        return path

    def collect(self, fake=None, **options):
        return snapshot.collect_snapshot(self.sources, rpc=fake or FakeRpc(), wall_clock=lambda: 1790000000,
                                         **options)

    def seed_old(self):
        old = {"public": {"complete": True, "old": "public"},
               "private": {"old": "private"}, "receipt": {"old": "receipt"}}
        snapshot.write_snapshot(self.output, old)
        return self.output_bytes()

    def output_bytes(self):
        return {str(path.relative_to(self.output)): path.read_bytes() for path in self.output.rglob("*.json")}

    def assert_failure_preserves_old(self, fake, **options):
        old = self.seed_old()
        with self.assertRaises((RuntimeError, TimeoutError, OSError, ValueError)):
            result = self.collect(fake, **options)
            snapshot.write_snapshot(self.output, result)
        self.assertEqual(self.output_bytes(), old)

    def test_source_union_case_normalization_duplicates_and_invalids(self):
        target = address(100999)
        two = self.make_source("two?.sqlite3", [target, target.upper(), address(100000), None, "garbage", "0x123"])
        before = {path: path.read_bytes() for path in (self.source, two)}
        found, receipt = snapshot.read_addresses([self.source, two])
        self.assertEqual(len(found), 140 + 1 + 3)
        self.assertIn(target, found)
        self.assertEqual(receipt[1]["address_count"], 2)
        self.assertEqual(receipt[1]["invalid_rows"], 3)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_missing_source_is_not_created(self):
        missing = self.root / "missing.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            snapshot.read_addresses([missing])
        self.assertFalse(missing.exists())

    def test_same_confirmed_block_for_every_balance_and_code(self):
        fake = FakeRpc()
        result = self.collect(fake, batch_size=11, workers=4)
        self.assertEqual(result["public"]["meta"]["block_number"], 100)
        for method, params in fake.calls:
            if method in ("eth_getBalance", "eth_getCode", "eth_getBlockByNumber"):
                self.assertEqual(params[1] if method != "eth_getBlockByNumber" else params[0], "0x64")
        self.assertEqual(fake.header_count, 2)

    def test_system_balance_never_queried_published_ranked_or_summed(self):
        extra = self.make_source("systems.sqlite3", [snapshot.POWER, snapshot.POWER.upper(), address(2)])
        self.sources.append(extra)
        fake = FakeRpc({snapshot.POWER: hex(10 ** 70), address(2): "0x64", snapshot.ZERO: "0x2", snapshot.DEAD: "0x3"})
        result = self.collect(fake)
        public = result["public"]
        self.assertEqual(public["meta"]["share_denominator_wei"], "105")
        self.assertEqual(public["systems"], [{"address": snapshot.POWER, "kind": "system", "name": "POWER"}])
        self.assertNotIn(snapshot.POWER, [row["address"] for row in public["rows"]])
        self.assertNotIn(snapshot.POWER, [row["address"] for row in result["private"]["balances"]])
        self.assertFalse(any(params[0] == snapshot.POWER for method, params in fake.calls
                             if method in ("eth_getBalance", "eth_getCode")))
        low_row = next(row for row in public["rows"] if row["address"] == address(2))
        self.assertEqual(low_row["kind"], "wallet")
        self.assertEqual(result["receipt"]["system_balance_query_count"], 0)

    def test_integer_sort_ties_and_top_100_without_system_hole(self):
        balances = {address(100000 + i): hex(10 ** 60 + 140 - i) for i in range(140)}
        balances[address(100000)] = balances[address(100001)] = hex(10 ** 61)
        fake = FakeRpc(balances, {address(100000): "0x6000"})
        result = self.collect(fake)
        rows = result["public"]["rows"]
        self.assertEqual(len(rows), 100)
        self.assertEqual([row["rank"] for row in rows], list(range(1, 101)))
        self.assertEqual([row["address"] for row in rows[:2]], [address(100000), address(100001)])
        self.assertEqual(rows[0]["kind"], "contract")
        self.assertEqual(rows[1]["kind"], "wallet")
        self.assertEqual(rows[99]["address"], address(100099))
        self.assertTrue(all(type(row["balance_wei"]) is str for row in rows))
        self.assertEqual(int(result["public"]["meta"]["share_denominator_wei"]), sum(int(value, 16) for value in balances.values()))

    def test_burn_markers_are_ranked_and_no_code_request(self):
        result_rpc = FakeRpc({snapshot.ZERO: "0xff", snapshot.DEAD: "0xfe"})
        result = self.collect(result_rpc)
        self.assertEqual([row["kind"] for row in result["public"]["rows"][:2]], ["burn", "burn"])
        self.assertFalse(any(params[0] in (snapshot.ZERO, snapshot.DEAD) for method, params in result_rpc.calls
                             if method == "eth_getCode"))
        self.assertEqual(result["public"]["meta"]["public_schema"], 2)
        self.assertIs(result["public"]["meta"]["complete"], True)
        self.assertIs(result["public"]["meta"]["network_complete"], False)
        self.assertIsNone(result["public"]["meta"]["contracts_count"])

    def test_raw_same_block_classification_evidence_is_private_only(self):
        fake = FakeRpc({address(100000): "0xff"}, {address(100000): "0x60006000"})
        result = self.collect(fake)
        evidence = result["private"]["classification_evidence"]
        self.assertEqual(evidence["block_number"], 100)
        self.assertEqual(evidence["block_hash"], fake.hash)
        self.assertEqual(evidence["eth_getCode"][address(100000)], "0x60006000")
        self.assertNotIn(snapshot.POWER, evidence["eth_getCode"])
        self.assertNotIn("classification_evidence", result["public"])
        self.assertNotIn("0x60006000", json.dumps(result["public"]))

    def test_corrupted_raw_rpc_batches_keep_previous_complete_output(self):
        for corruption in ("duplicate", "missing-result", "error", "missing-item", "malformed-json"):
            with self.subTest(corruption=corruption):
                fake = FakeRpc()
                def opener(request, timeout):
                    request_batch = json.loads(request.data)
                    calls = [(item["method"], item["params"]) for item in request_batch]
                    responses = [{"jsonrpc": "2.0", "id": index, "result": value}
                                 for index, value in enumerate(fake(calls))]
                    if calls[0][0] == "eth_getBalance":
                        if corruption == "duplicate":
                            responses[-1]["id"] = responses[0]["id"]
                        elif corruption == "missing-result":
                            responses[-1].pop("result")
                        elif corruption == "error":
                            responses[-1] = {"jsonrpc": "2.0", "id": len(responses) - 1, "error": {"code": -1}}
                        elif corruption == "missing-item":
                            responses.pop()
                        else:
                            return io.BytesIO(b'{not-json')
                    return io.BytesIO(json.dumps(responses).encode())
                client = snapshot.JsonRpcClient(180.0, clock=lambda: 0.0, opener=opener)
                self.assert_failure_preserves_old(client, clock=lambda: 0.0)

    def test_partial_balance_failure_preserves_old(self):
        fake = FakeRpc()
        def fail(calls):
            if calls[0][0] == "eth_getBalance" and calls[0][1][0] != snapshot.ZERO:
                raise OSError("simulated RPC outage")
            return NotImplemented
        fake.override = fail
        self.assert_failure_preserves_old(fake, batch_size=10, workers=1)

    def test_incomplete_result_list_preserves_old(self):
        fake = FakeRpc()
        fake.override = lambda calls: [] if calls[0][0] == "eth_getBalance" else NotImplemented
        self.assert_failure_preserves_old(fake)

    def test_invalid_balance_quantities_preserve_old(self):
        for bad in (None, "0x", "0x00", "0x-1", "0xgg", "0x1\n", True, 10, "0x" + "1" * 65):
            with self.subTest(bad=bad):
                self.assert_failure_preserves_old(FakeRpc({snapshot.ZERO: bad}))

    def test_invalid_contract_code_preserves_old(self):
        for bad in (None, "0x0", "6000", "0xzz", "0x00\n", 0):
            with self.subTest(bad=bad):
                self.assert_failure_preserves_old(FakeRpc({address(100000): "0x1"}, {address(100000): bad}))

    def test_syncing_and_invalid_heads_preserve_old(self):
        for syncing in (True, {}, 0, None):
            with self.subTest(syncing=syncing):
                fake = FakeRpc(); fake.syncing = syncing
                self.assert_failure_preserves_old(fake)
        for head in ("0x5", "0x", "0x-1", None, "0x06a"):
            with self.subTest(head=head):
                fake = FakeRpc(); fake.head = head
                self.assert_failure_preserves_old(fake)

    def test_changed_hash_or_invalid_header_preserves_old(self):
        fake = FakeRpc(); fake.final_hash = "0x" + "cd" * 32
        self.assert_failure_preserves_old(fake)
        for malformed in (None, {}, {"number": "0x65", "hash": "0x" + "ab" * 32, "timestamp": "0x1"},
                          {"number": "0x64", "hash": "wrong", "timestamp": "0x1"},
                          {"number": "0x64", "hash": "0x" + "ab" * 32, "timestamp": "0x"}):
            with self.subTest(header=malformed):
                fake = FakeRpc()
                fake.override = lambda calls, malformed=malformed: [malformed] if calls[0][0] == "eth_getBlockByNumber" else NotImplemented
                self.assert_failure_preserves_old(fake)

    def test_budget_expiry_preserves_old(self):
        clock = [0.0]
        fake = FakeRpc()
        def expire(calls):
            if calls[0][0] == "eth_getBalance":
                clock[0] = 181.0
            return NotImplemented
        fake.override = expire
        self.assert_failure_preserves_old(fake, workers=1, clock=lambda: clock[0])

    def test_write_success_has_consistent_public_receipt_and_private(self):
        result = self.collect()
        snapshot.write_snapshot(self.output, result)
        loaded = {key: json.loads((self.output / rel).read_text()) for key, rel in
                  {"public": "site/data/holders.json", "private": "state/snapshot-private.json",
                   "receipt": "state/snapshot-receipt.json"}.items()}
        self.assertEqual(loaded, result)
        self.assertEqual((self.output / "state").stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.output / "state/snapshot-private.json").stat().st_mode & 0o777, 0o600)

    def test_staging_write_failure_preserves_entire_old_set(self):
        old = self.seed_old()
        artifacts = self.collect()
        def writer(path, value):
            if path.name == "new-1.json":
                raise OSError("simulated disk full")
            snapshot.atomic_json(path, value)
        with self.assertRaises(OSError):
            snapshot.write_snapshot(self.output, artifacts, writer=writer)
        self.assertEqual(self.output_bytes(), old)
        self.assertFalse(list((self.output / "state").glob(".holder-write-*")))

    def test_commit_failure_restores_old_private_and_receipt(self):
        old = self.seed_old()
        artifacts = self.collect()
        real_replace = os.replace
        def replace(source, target):
            if Path(target) == self.output / "site/data/holders.json":
                raise OSError("simulated public rename failure")
            return real_replace(source, target)
        with patch.object(snapshot.os, "replace", side_effect=replace):
            with self.assertRaises(OSError):
                snapshot.write_snapshot(self.output, artifacts)
        self.assertEqual(self.output_bytes(), old)

    def test_failed_first_commit_does_not_leave_partial_output(self):
        artifacts = self.collect()
        real_replace = os.replace
        def replace(source, target):
            if Path(target) == self.output / "site/data/holders.json":
                raise OSError("simulated public rename failure")
            return real_replace(source, target)
        with patch.object(snapshot.os, "replace", side_effect=replace):
            with self.assertRaises(OSError):
                snapshot.write_snapshot(self.output, artifacts)
        self.assertEqual(self.output_bytes(), {})


class TransportAndOptionsTests(unittest.TestCase):
    def responses(self):
        return [{"jsonrpc": "2.0", "id": 1, "result": "second"},
                {"jsonrpc": "2.0", "id": 0, "result": "first"}]

    def test_rpc_reordered_results_match_request_ids(self):
        self.assertEqual(snapshot.decode_rpc_batch(self.responses(), 2), ["first", "second"])

    def test_rpc_duplicate_missing_unexpected_ids_and_missing_results_rejected(self):
        cases = [None, {}, [], self.responses()[:1],
                 [{"jsonrpc": "2.0", "id": 0, "result": 1}] * 2,
                 [{"jsonrpc": "2.0", "id": True, "result": 1}, self.responses()[1]],
                 [{"jsonrpc": "2.0", "id": 2, "result": 1}, self.responses()[1]],
                 [{"jsonrpc": "2.0", "id": "1", "result": 1}, self.responses()[1]],
                 [{"jsonrpc": "2.0", "id": 1}, self.responses()[1]],
                 [{"jsonrpc": "2.0", "id": 1, "error": {"code": -1}}, self.responses()[1]],
                 [{"jsonrpc": "2.0", "id": 1, "result": None, "error": None}, self.responses()[1]],
                 [{"id": 1, "result": 1}, self.responses()[1]], [None, self.responses()[1]]]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(RuntimeError):
                snapshot.decode_rpc_batch(data, 2)

    def test_client_uses_loopback_budget_and_preserves_pinned_params(self):
        observed = []
        def opener(request, timeout):
            observed.append((request.full_url, json.loads(request.data), timeout))
            return io.BytesIO(json.dumps([{"jsonrpc": "2.0", "id": 0, "result": "0x1"}]).encode())
        client = snapshot.JsonRpcClient(9.0, clock=lambda: 5.0, opener=opener)
        self.assertEqual(client([("eth_getBalance", [address(2), "0x64"])]), ["0x1"])
        self.assertEqual(observed[0][0], "http://127.0.0.1:18555")
        self.assertEqual(observed[0][1][0]["params"], [address(2), "0x64"])
        self.assertEqual(observed[0][2], 4.0)

    def test_client_budget_after_response_and_before_request(self):
        clock = [0.0]
        def opener(request, timeout):
            clock[0] = 2.0
            return io.BytesIO(b'[{"jsonrpc":"2.0","id":0,"result":"0x1"}]')
        client = snapshot.JsonRpcClient(1.0, clock=lambda: clock[0], opener=opener)
        with self.assertRaises(TimeoutError):
            client([("eth_blockNumber", [])])
        with patch.object(client, "opener", side_effect=AssertionError("must not connect")):
            with self.assertRaises(TimeoutError):
                client([("eth_blockNumber", [])])

    def test_invalid_options_rejected(self):
        options = [(0, 200, 4), (-1, 200, 4), (float("nan"), 200, 4), (float("inf"), 200, 4),
                   (181, 200, 4), (True, 200, 4), (180, 0, 4), (180, -1, 4), (180, 201, 4),
                   (180, True, 4), (180, 1.5, 4), (180, 200, 0), (180, 200, -1),
                   (180, 200, 5), (180, 200, True), (180, 200, 1.5)]
        for args in options:
            with self.subTest(args=args), self.assertRaises(ValueError):
                snapshot.validate_options(*args)

    def test_invalid_cli_options_fail_before_network_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "should-not-exist"
            for args in (["--workers", "0"], ["--batch-size", "201"], ["--max-seconds", "nan"]):
                with self.subTest(args=args), patch.object(snapshot, "collect_snapshot", side_effect=AssertionError("must not acquire")):
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                        snapshot.main(["--root", str(root), *args])
                    self.assertEqual(error.exception.code, 2)
                    self.assertFalse(root.exists())

    def test_atomic_serialization_fsync_and_replace_failures_leave_old_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text('{"old":true}')
            before = path.read_bytes()
            with self.assertRaises(TypeError):
                snapshot.atomic_json(path, {"bad": set()})
            self.assertEqual(path.read_bytes(), before)
            for operation in ("fsync", "replace"):
                with self.subTest(operation=operation), patch.object(snapshot.os, operation, side_effect=OSError("disk failure")):
                    with self.assertRaises(OSError):
                        snapshot.atomic_json(path, {"new": True})
                self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
