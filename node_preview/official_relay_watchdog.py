"""Request a hosted Explorer refresh or a Beijing 08:00 cutoff sample."""
from __future__ import annotations

import argparse
import fcntl
import json
import time
from pathlib import Path

from node_preview.bootstrap import atomic_json
from node_preview.github_backup import BackupError, Config, GitTransport, _sha
from node_preview.official_relay import OfficialRelay


REPOSITORY = "zhouyunqi250-create/marschain-power-site"
REQUEST_BRANCH = "official-browser-request"
CUTOFF_REQUEST_BRANCH = "official-browser-cutoff-request"
MAX_RECEIPT_AGE = 7 * 60
MIN_ATTEMPT_INTERVAL = 5 * 60
DAY_SECONDS = 86400
CUTOFF_REQUEST_LEAD = 9 * 60


def _request_refresh(transport: GitTransport, now: int, *, cutoff: int | None = None) -> str:
    branch = REQUEST_BRANCH if cutoff is None else CUTOFF_REQUEST_BRANCH
    if transport.config.branch != branch:
        raise BackupError("Explorer request branch does not match the requested operation")
    ref = f"refs/heads/{branch}"
    transport._git(["fetch", "--no-tags", "--depth=1", "origin", "refs/heads/main"])
    main = _sha(transport._git(["rev-parse", "FETCH_HEAD"]).decode().strip())
    tree = _sha(transport._git(["rev-parse", f"{main}^{{tree}}"]).decode().strip())
    previous = transport._head()
    identity = dict(transport.env,
                    GIT_AUTHOR_NAME="MarsChain Explorer watchdog",
                    GIT_AUTHOR_EMAIL="watchdog@marschainrank.com",
                    GIT_COMMITTER_NAME="MarsChain Explorer watchdog",
                    GIT_COMMITTER_EMAIL="watchdog@marschainrank.com")
    message = (f"Request a public Explorer refresh ({now})\n" if cutoff is None
               else f"Request a public Explorer cutoff ({cutoff})\n").encode()
    if cutoff is not None and previous is not None:
        if transport._git(["show", "-s", "--format=%B", previous]).rstrip(b"\n") == message.rstrip(b"\n"):
            return previous
    commit = _sha(transport._git(["commit-tree", tree, "-p", main], message, env=identity).decode().strip())
    # The request branch follows main's current tree and never replaces a
    # different writer's tip. The daily archive and receipt have separate refs.
    transport._git(["push", "--porcelain", f"--force-with-lease={ref}:{previous or ''}",
                    "origin", f"{commit}:{ref}"])
    return commit


def run(root: Path, *, capture_cutoff: bool = False) -> dict:
    private = root / "state" / ("official-relay-cutoff" if capture_cutoff else "official-relay-watchdog")
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    if private.is_symlink():
        raise BackupError("Explorer watchdog state must not be a symlink")
    attempt_path = private / "last-attempt.json"
    with (private / "request.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"state": "busy"}

        now = int(time.time())
        try:
            last_attempt = json.loads(attempt_path.read_text())
        except FileNotFoundError:
            last_attempt = {}
        if not isinstance(last_attempt, dict):
            raise BackupError("Invalid Explorer watchdog attempt record")
        if capture_cutoff:
            cutoff = (now // DAY_SECONDS + 1) * DAY_SECONDS
            if not 0 < cutoff - now <= CUTOFF_REQUEST_LEAD:
                return {"state": "outside_cutoff_request_window", "cutoff_timestamp": cutoff}
            if last_attempt.get("cutoff_timestamp") == cutoff:
                return {"state": "already_requested", "cutoff_timestamp": cutoff}
            context = {"cutoff_timestamp": cutoff}
        else:
            cutoff = None
            receipt = OfficialRelay().read(now)
            ages = {name: now - receipt[name]["fetched_at"] for name in ("stats", "highest") if name in receipt}
            if len(ages) == 2 and max(ages.values()) <= MAX_RECEIPT_AGE:
                return {"state": "fresh", "receipt_ages": ages}
            previous_attempt = last_attempt.get("attempted_at", 0)
            if type(previous_attempt) is not int or previous_attempt < 0:
                raise BackupError("Invalid Explorer watchdog attempt time")
            if int(time.time()) - previous_attempt < MIN_ATTEMPT_INTERVAL:
                return {"state": "throttled", "receipt_ages": ages, "last_attempt_at": previous_attempt}
            context = {"receipt_ages": ages}

        now = int(time.time())
        config = Config.environment()
        if config.repository != REPOSITORY or config.transport != "git":
            raise BackupError("Explorer watchdog requires the existing Git transport for the leaderboard repository")
        branch = REQUEST_BRANCH if cutoff is None else CUTOFF_REQUEST_BRANCH
        if cutoff is None:
            atomic_json(attempt_path, {"attempted_at": now})
        transport = GitTransport(Config(config.repository, branch=branch, prefix="request"), private)
        commit = _request_refresh(transport, now, cutoff=cutoff)
        result = {"state": "requested", **context, "commit": commit, "attempted_at": now}
        if cutoff is not None:
            atomic_json(attempt_path, result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--cutoff", action="store_true", help="Request an upcoming Beijing 08:00 sample")
    args = parser.parse_args()
    try:
        result = run(args.root.expanduser().resolve(), capture_cutoff=args.cutoff)
    except (OSError, ValueError, BackupError) as error:
        result = {"state": "error", "error": str(error) if isinstance(error, BackupError) else type(error).__name__}
    print(json.dumps(result), flush=True)
    return 1 if result["state"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
