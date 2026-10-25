"""Loopback-only preview web server. Private state is outside its document root."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def status(root: Path, source_root: Path | None = None) -> dict:
    from node_preview.collector import read_status
    database = (source_root or root) / "state/preview.sqlite3"
    result = {"preview": True, "production_writes": False, "generated_at": int(time.time())}
    if database.exists():
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=3) as db:
            result["collector"] = read_status(db)
    for name in ("publisher-status", "bootstrap-status", "freezer-status", "observations-status",
                 "cloud-publish-status", "github-backup-status", "official-address-sampler-status"):
        path = root / f"state/{name}.json"
        try:
            result[name.replace("-status", "")] = json.loads(path.read_text())
        except (OSError, ValueError):
            result[name.replace("-status", "")] = None
    result["snapshot_archives"] = len(list((root / "archives").glob("*/manifest.json")))
    now = int(time.time())
    collector = result.get("collector") or {}
    publication = result.get("publisher") or {}
    try:
        public_snapshot = json.loads((root / "site/data/latest.json").read_text())
        last_generated = public_snapshot.get("meta", {}).get("node_preview", {}).get("generated_at")
    except (OSError, ValueError, AttributeError):
        last_generated = publication.get("generated_at")
    result["freshness"] = {
        "collector_age_seconds": now - int(collector.get("last_success") or 0) if collector.get("last_success") else None,
        "preview_age_seconds": now - int(last_generated or 0) if last_generated else None,
    }
    result["freshness"]["collector_delayed"] = result["freshness"]["collector_age_seconds"] is None or result["freshness"]["collector_age_seconds"] > 180
    result["freshness"]["preview_delayed"] = result["freshness"]["preview_age_seconds"] is None or result["freshness"]["preview_age_seconds"] > 180
    if source_root:
        result["isolated_review"] = True
        result["source_publication"] = {}
        for key in ("cloud-publish-status", "github-backup-status"):
            try:
                result["source_publication"][key] = json.loads((source_root / "state" / f"{key}.json").read_text())
            except (OSError, ValueError):
                result["source_publication"][key] = None
    return result


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, root: Path, source_root: Path | None = None, **kwargs):
        self.preview_root = root
        self.source_root = source_root
        super().__init__(*args, directory=str(root / "site"), **kwargs)

    def list_directory(self, path):
        self.send_error(403, "Directory listings are disabled")
        return None

    def snapshot(self, send_body=True):
        try:
            body = (self.preview_root / "site/data/latest.json").read_bytes()
        except OSError:
            self.send_error(503, "The first checked snapshot is not ready")
            return
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        compressed = "gzip" in self.headers.get("Accept-Encoding", "")
        if compressed:
            body = gzip.compress(body, compresslevel=5, mtime=0)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("ETag", etag)
        self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def do_HEAD(self):
        if urlsplit(self.path).path == "/data/latest.json":
            self.snapshot(send_body=False)
            return
        super().do_HEAD()

    def do_GET(self):
        if urlsplit(self.path).path in {"/admin", "/admin/"}:
            body = Path(__file__).with_name("admin.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if urlsplit(self.path).path == "/data/latest.json":
            self.snapshot()
            return
        if urlsplit(self.path).path == "/api/status":
            try:
                payload = status(self.preview_root, self.source_root)
                body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
                self.send_response(200)
            except Exception as exc:
                body = json.dumps({"ok": False, "error": str(exc)}).encode()
                self.send_response(503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18880)
    parser.add_argument("--source-root", type=Path, help="Optional read-only collector status source for an isolated review")
    args = parser.parse_args()
    root = args.root.resolve()
    (root / "site").mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, root=root,
                                 source_root=args.source_root.resolve() if args.source_root else None))
    print(f"Preview: http://127.0.0.1:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
