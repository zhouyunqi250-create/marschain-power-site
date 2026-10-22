# MarsChain incremental collector

`incremental_collector.py` is a user-level systemd one-shot job intended to run
once per minute on the Ubuntu node host. It uses the existing SQLite database
and advances `meta.live_next_block` only after each range is saved.

It refreshes power and native MARS balance for addresses seen as transaction
senders/recipients, block producers, top-level contract creations, or indexed
POWER contract log topics. Six confirmations are left behind the current head.

This is the live incremental pass. It does not discover old addresses that only
appear in internal calls or historical traces. The address coverage metric is a
comparison with the explorer's last recorded total, not proof of completeness.

The CSV is an export of the SQLite database and omits the raw `wei` field. The
SQLite database remains the working state; GitHub snapshots should be published
in batches instead of committing a full CSV every minute.

The systemd service and timer templates are in `systemd/`.

## On the Ubuntu node host

- Progress page: `http://127.0.0.1:18765/`
- Timer status: `systemctl --user status marschain-incremental.timer`
- Recent collector log: `journalctl --user -u marschain-incremental.service -n 30 --no-pager`
- Follow live logs: `journalctl --user -u marschain-incremental.service -f`

The dashboard listens only on loopback. From another computer, use an SSH
local port forward to view it without exposing the dashboard or node RPC to the
LAN.
