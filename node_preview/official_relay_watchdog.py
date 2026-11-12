"""Request a hosted Explorer refresh when the verified public receipt ages out."""
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
REQUEST_REF = f"refs/heads/{REQUEST_BRANCH}"
MAX_RECEIPT_AGE = 7 * 60
MIN_ATTEMPT_INTERVAL = 5 * 60


def _request_refresh(transport: GitTransport, now: int) -> str:
    transport._git(["fetch", "--no-tags", "--depth=1", "origin", "refs/heads/main"])
    main = _sha(transport._git(["rev-parse", "FETCH_HEAD"]).decode().strip())
    tree = _sha(transport._git(["rev-parse", f"{main}^{{tree}}"]).decode().strip())
    previous = transport._head()
    identity = dict(transport.env,
                    GIT_AUTHOR_NAME="MarsChain Explorer watchdog",
                    GIT_AUTHOR_EMAIL="watchdog@marschainrank.com",
                    GIT_COMMITTER_NAME="MarsChain Explorer watchdog",
                    GIT_COMMITTER_EMAIL="watchdog@marschainrank.com")
    message = f"Request a public Explorer refresh ({now})\n".encode()
    commit = _sha(transport._git(["commit-tree", tree, "-p", main], message, env=identity).decode().strip())
    # The request branch follows main's current tree and never replaces a
    # different writer's tip. The daily archive and receipt have separate refs.
    transport._git(["push", "--porcelain", f"--force-with-lease={REQUEST_REF}:{previous or ''}",
                    "origin", f"{commit}:{REQUEST_REF}"])
    return commit


def run(root: Path) -> dict:
    private = root / "state/official-relay-watchdog"
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
        receipt = OfficialRelay().read(now)
        ages = {name: now - receipt[name]["fetched_at"] for name in ("stats", "highest") if name in receipt}
        if len(ages) == 2 and max(ages.values()) <= MAX_RECEIPT_AGE:
            return {"state": "fresh", "receipt_ages": ages}

        try:
            previous_attempt = json.loads(attempt_path.read_text())["attempted_at"]
        except FileNotFoundError:
            previous_attempt = 0
        if type(previous_attempt) is not int or previous_attempt < 0:
            raise BackupError("Invalid Explorer watchdog attempt time")
        now = int(time.time())
        if now - previous_attempt < MIN_ATTEMPT_INTERVAL:
            return {"state": "throttled", "receipt_ages": ages, "last_attempt_at": previous_attempt}

        config = Config.environment()
        if config.repository != REPOSITORY or config.transport != "git":
            raise BackupError("Explorer watchdog requires the existing Git transport for the leaderboard repository")
        atomic_json(attempt_path, {"attempted_at": now})
        transport = GitTransport(Config(config.repository, branch=REQUEST_BRANCH, prefix="request"), private)
        commit = _request_refresh(transport, now)
        return {"state": "requested", "receipt_ages": ages, "commit": commit, "attempted_at": now}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run(args.root.expanduser().resolve())
    except (OSError, ValueError, KeyError, TypeError, BackupError) as error:
        result = {"state": "error", "error": str(error) if isinstance(error, BackupError) else type(error).__name__}
    print(json.dumps(result), flush=True)
    return 1 if result["state"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
