#!/usr/bin/env python3
import json
import sqlite3
import shutil
import subprocess
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from traffic_stats import safe_summary as traffic_summary

ROOT = Path.home() / "marschain-node" / "dashboard"
EXPORT = Path.home() / "marschain-node" / "full-power-export"
DB_PATH = EXPORT / "scan.sqlite3"
RPC = "http://127.0.0.1:18555"

def rpc(method, params=None):
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1}).encode()
    req = urllib.request.Request(RPC, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        result = json.loads(response.read().decode())
    if "error" in result:
        raise RuntimeError(result["error"].get("message", "RPC 错误"))
    return result.get("result")

def service_value(prop):
    try:
        return subprocess.check_output(
            ["systemctl", "--user", "show", "marschain.service", f"--property={prop}", "--value"],
            text=True, stderr=subprocess.DEVNULL, timeout=3
        ).strip()
    except Exception:
        return "未知"

def unit_value(unit, prop):
    try:
        return subprocess.check_output(
            ["systemctl", "--user", "show", unit, f"--property={prop}", "--value"],
            text=True, stderr=subprocess.DEVNULL, timeout=3
        ).strip()
    except Exception:
        return "未知"

def read_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except Exception:
        return default

def collector_status():
    if not DB_PATH.exists():
        return {"ok": False, "error": "采集数据库还没有创建"}
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=3)
    conn.execute("PRAGMA busy_timeout=3000")
    try:
        counts = {
            "addresses": conn.execute("SELECT COUNT(*) FROM addresses").fetchone()[0],
            "powers": conn.execute("SELECT COUNT(*) FROM powers").fetchone()[0],
            "balances": conn.execute("SELECT COUNT(*) FROM balances").fetchone()[0],
            "powerFailures": conn.execute("SELECT COUNT(*) FROM lookup_failures").fetchone()[0],
            "balanceFailures": conn.execute("SELECT COUNT(*) FROM balance_failures").fetchone()[0],
        }
        next_block = read_meta(conn, "live_next_block")
        last_block = read_meta(conn, "live_last_processed_block")
        last_run = read_meta(conn, "live_last_run")
        scan_head = read_meta(conn, "tx_scan_head")
        historical_next = read_meta(conn, "tx_next_block")
        balance_block = read_meta(conn, "balance_block")
    finally:
        conn.close()
    try:
        summary = json.loads((EXPORT / "scan_summary.json").read_text(encoding="utf-8"))
    except Exception:
        summary = {}
    network_addresses = summary.get("explorer_total_addresses")
    return {
        "ok": True,
        **counts,
        "coveragePercent": round(counts["addresses"] / network_addresses * 100, 2) if network_addresses else None,
        "networkAddresses": network_addresses,
        "lastBlock": last_block,
        "nextBlock": next_block,
        "headBlock": int(rpc("eth_blockNumber"), 16),
        "lastRun": last_run,
        "historicalHead": scan_head,
        "historicalNext": historical_next,
        "balanceBlock": balance_block,
        "timer": unit_value("marschain-incremental.timer", "ActiveState"),
        "service": unit_value("marschain-incremental.service", "ActiveState"),
        "serviceResult": unit_value("marschain-incremental.service", "Result"),
        "csvUpdated": int((EXPORT / "marschain_full_power_rank.csv").stat().st_mtime) if (EXPORT / "marschain_full_power_rank.csv").exists() else None,
        "generatedAt": int(time.time()),
    }

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path == "/api/traffic":
            try:
                self.send_json(200, traffic_summary())
            except Exception:
                # Provider responses and authentication details stay out of HTTP
                # responses and the node dashboard's status text.
                self.send_json(503, {"ok": False, "state": "unavailable",
                                    "message": "访问统计暂时不可用，请稍后重试。"})
            return
        if self.path == "/api/collector":
            try:
                self.send_json(200, collector_status())
            except Exception as exc:
                self.send_json(503, {"ok": False, "error": str(exc)})
            return
        if self.path == "/api/status":
            try:
                syncing = rpc("eth_syncing")
                peers = int(rpc("net_peerCount"), 16)
                chain_id = int(rpc("eth_chainId"), 16)
                client = rpc("web3_clientVersion")
                sync_obj = None
                if syncing is not False:
                    current = int(syncing.get("currentBlock", "0x0"), 16)
                    highest = int(syncing.get("highestBlock", "0x0"), 16)
                    sync_obj = {
                        "current": current,
                        "highest": highest,
                        "percent": round(min(100, current / highest * 100), 2) if highest else 0
                    }
                block = int(rpc("eth_blockNumber"), 16)
                disk = shutil.disk_usage(Path.home())
                payload = {
                    "ok": True,
                    "state": "syncing" if sync_obj else "synced",
                    "sync": sync_obj,
                    "block": block,
                    "peers": peers,
                    "chainId": chain_id,
                    "client": client,
                    "service": service_value("ActiveState"),
                    "since": service_value("ActiveEnterTimestamp"),
                    "diskFreeGiB": round(disk.free / (1024 ** 3), 1)
                }
                self.send_json(200, payload)
            except Exception as exc:
                self.send_json(503, {
                    "ok": False,
                    "service": service_value("ActiveState"),
                    "error": str(exc),
                    "hint": "确认 marschain.service 正在运行；节点 RPC 仅监听本机 127.0.0.1:18555。"
                })
            return
        if self.path in ("/", "/index.html"):
            try:
                content = (ROOT / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except OSError:
                self.send_error(404)
            return
        self.send_error(404)

    def send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass

ThreadingHTTPServer(("127.0.0.1", 18765), Handler).serve_forever()
