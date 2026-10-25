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

## Private website traffic panel

The node dashboard includes today's and yesterday's pageviews (PV), plus the
last seven completed calendar days. Dates use Beijing time. It reads existing
Baidu Tongji reports; repeated pageviews count separately from visitors.

`dashboard/traffic_stats.py` fetches reports in a background thread every ten
minutes. `/api/traffic` returns only cached counts and safe status messages, so
a slow reporting provider cannot block node monitoring. The browser reads the
cache every minute. Missing data stays blank; zero is displayed only when the
provider returns zero. Dated cached values remain visible during an outage.

Report access must be configured separately from the public tracking script.
The tracking hash is not a report credential or the numeric report site ID.
Store authorized credentials only on the Ubuntu host in the current user's
`~/.config/marschain-dashboard/analytics.json`, owned by that user with mode
`0600`, inside a private directory. Never commit this file or copy it to the
public website. The backend does not create an empty credential file.

Personal OAuth configuration fields are `mode: "oauth"`, `client_id`,
`client_secret`, and `refresh_token`; `access_token` and Unix `expires_at` are
optional. Successful token renewal is saved atomically to the same private
file. Commercial report accounts can use `mode: "commercial"`, `user_name`,
and `access_token`, with manual renewal when the provider requires it. Optional
`site_id` is a positive numeric report ID. The backend verifies the authorized
site list and accepts only `marschainrank.com` or `www.marschainrank.com`.

Private cache location: `~/.cache/marschain-dashboard/analytics-cache.json`.
No analytics secrets are served to the browser. Keep this panel on the existing
loopback-only dashboard; it is not part of the public ranking site.
