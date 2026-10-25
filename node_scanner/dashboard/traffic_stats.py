"""Private, cached Baidu Tongji pageview statistics for the node dashboard.

``safe_summary()`` never performs network or credential-file I/O in the request
thread. A single daemon per bound instance refreshes every ten minutes by default.
This module must only be mounted on the private node dashboard, not the public
ranking website.

Configuration is a user-owned JSON file with permissions 0600, by default
``~/.config/marschain-dashboard/analytics.json``. OAuth configuration::

    {"mode": "oauth", "client_id": "...", "client_secret": "...",
     "refresh_token": "...", "access_token": "...", "expires_at": 0}

``access_token`` and ``expires_at`` are optional when a refresh token is present.
The optional ``site_id`` must be the numeric report ID, not the tracker hash.
Commercial accounts can instead use ``mode: commercial``, ``user_name`` and
``access_token``. Credentials and raw API responses are never returned or logged.

Official references:
https://tongji.baidu.com/api/manual/Chapter2/openapi.html
https://tongji.baidu.com/api/manual/Chapter2/drapi.html
https://tongji.baidu.com/api/manual/Chapter1/getSiteList.html
https://tongji.baidu.com/api/manual/Chapter1/getData.html
https://tongji.baidu.com/api/manual/Chapter1/trend_time_a.html
"""

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import ssl
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


BEIJING = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")
DOMAINS = frozenset(("marschainrank.com", "www.marschainrank.com"))
OAUTH_BASE = "https://openapi.baidu.com/rest/2.0/tongji/"
COMMERCIAL_BASE = "https://api.baidu.com/json/tongji/v1/ReportService/"
TOKEN_ENDPOINT = "https://openapi.baidu.com/oauth/2.0/token"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_CONFIG_BYTES = 64 * 1024


class _SafeError(Exception):
    """Only fixed, locally authored messages may cross the API boundary."""

    def __init__(self, state, message):
        self.state = state
        self.message = message
        super().__init__(state)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a credential-bearing request to another location.
        raise _SafeError("unavailable", "统计服务暂时无法连接，稍后自动重试。")


def _timestamp(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _count(value):
    if value in (None, "", "--", "-"):
        return None
    if isinstance(value, bool):
        raise _SafeError("unavailable", "统计数据格式暂时无法识别，已保留上次数据。")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and math.isfinite(value) and value >= 0 and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\d+(?:,\d{3})*", value.strip()):
        return int(value.replace(",", ""))
    raise _SafeError("unavailable", "统计数据格式暂时无法识别，已保留上次数据。")


def _site_id(value):
    # In particular, do not interpret the 32-character tracker hash as an ID.
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9]\d{0,18}", value):
        return int(value)
    return None


def _domain(value):
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    parsed = urllib.parse.urlsplit(candidate if "://" in candidate else "https://" + candidate)
    if parsed.username or parsed.password or parsed.port or parsed.path not in ("", "/"):
        return None
    if parsed.query or parsed.fragment or parsed.scheme not in ("http", "https"):
        return None
    hostname = (parsed.hostname or "").lower()
    return hostname if hostname in DOMAINS else None


def _read_private_json(path):
    """Read an existing regular file without following a credential symlink."""
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK)
    except FileNotFoundError:
        raise _SafeError("not_configured", "尚未连接百度统计报表，请先完成读取授权。") from None
    except OSError:
        raise _SafeError("invalid_config", "统计授权配置无法读取，请检查私有配置。") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise _SafeError("invalid_config", "统计授权配置需由当前用户持有，且权限为 600。")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError("size")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("object")
        return payload, hashlib.sha256(raw).digest()
    except (ValueError, UnicodeError):
        raise _SafeError("invalid_config", "统计授权配置格式有误，请检查私有配置。") from None
    finally:
        if fd is not None:
            os.close(fd)


def _atomic_json(path, payload, create_parent=False):
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".analytics-", suffix=".tmp", dir=str(path.parent))
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _config(path):
    payload, digest = _read_private_json(path)
    mode = payload.get("mode", "oauth")
    if mode not in ("oauth", "commercial"):
        raise _SafeError("invalid_config", "统计授权方式无法识别，请检查私有配置。")
    if "site_id" in payload and _site_id(payload["site_id"]) is None:
        raise _SafeError("invalid_config", "报表站点编号需为数字，不能使用网页统计代码编号。")
    if mode == "commercial":
        required = ("user_name", "access_token")
        valid = all(isinstance(payload.get(key), str) and payload[key].strip() for key in required)
    else:
        has_access = isinstance(payload.get("access_token"), str) and bool(payload["access_token"].strip())
        can_refresh = all(isinstance(payload.get(key), str) and payload[key].strip()
                          for key in ("client_id", "client_secret", "refresh_token"))
        valid = has_access or can_refresh
    if not valid:
        raise _SafeError("not_configured", "百度统计读取授权尚未完成，暂不展示浏览量。")
    return payload, digest


class _BaiduClient:
    def __init__(self, config_path, config, digest):
        self.config_path = config_path
        self.config = config
        self.digest = digest
        self.mode = config.get("mode", "oauth")
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def _post(self, url, data, json_body=False):
        content_type = "application/json" if json_body else "application/x-www-form-urlencoded"
        encoded = (json.dumps(data).encode("utf-8") if json_body
                   else urllib.parse.urlencode(data).encode("utf-8"))
        request = urllib.request.Request(url, data=encoded, headers={
            "Content-Type": content_type, "Accept": "application/json",
            "User-Agent": "MarsChain-Private-Dashboard/1.0",
        }, method="POST")
        try:
            with self.opener.open(request, timeout=12) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("size")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("object")
            return payload
        except urllib.error.HTTPError as exc:
            state = "auth_required" if exc.code in (401, 403) else "unavailable"
            message = ("百度统计读取授权需要更新。" if state == "auth_required"
                       else "统计服务暂时无法连接，稍后自动重试。")
            raise _SafeError(state, message) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeError):
            raise _SafeError("unavailable", "统计服务暂时无法连接，稍后自动重试。") from None

    def _refresh_token(self):
        required = ("client_id", "client_secret", "refresh_token")
        if not all(isinstance(self.config.get(key), str) and self.config[key].strip() for key in required):
            raise _SafeError("auth_required", "百度统计读取授权需要更新。")
        result = self._post(TOKEN_ENDPOINT, {
            "grant_type": "refresh_token", "client_id": self.config["client_id"],
            "client_secret": self.config["client_secret"], "refresh_token": self.config["refresh_token"],
        })
        access = result.get("access_token")
        if result.get("error") or not isinstance(access, str) or not access:
            raise _SafeError("auth_required", "百度统计读取授权需要更新。")
        refreshed = dict(self.config)
        refreshed["access_token"] = access
        refresh = result.get("refresh_token")
        if isinstance(refresh, str) and refresh:
            refreshed["refresh_token"] = refresh
        lifetime = _timestamp(result.get("expires_in"))
        if lifetime:
            refreshed["expires_at"] = int(time.time()) + lifetime
        # Never create credentials implicitly or overwrite a concurrent user edit.
        _, current_digest = _read_private_json(self.config_path)
        if current_digest != self.digest:
            raise _SafeError("invalid_config", "读取授权配置已更改，将在下次刷新时重新读取。")
        try:
            _atomic_json(self.config_path, refreshed)
        except OSError:
            raise _SafeError("invalid_config", "无法安全保存更新后的统计授权。") from None
        self.config, self.digest = _read_private_json(self.config_path)

    def _request(self, endpoint, params=None, retry=True):
        params = dict(params or {})
        if self.mode == "commercial":
            expiry = _timestamp(self.config.get("expires_at"))
            if expiry and expiry <= time.time():
                raise _SafeError("auth_required", "百度统计读取授权需要更新。")
            body = {"header": {"userName": self.config["user_name"],
                                "accessToken": self.config["access_token"]}}
            if params:
                body["body"] = params
            result = self._post(COMMERCIAL_BASE + endpoint, body, json_body=True)
            header = result.get("header")
            if not isinstance(header, dict) or header.get("status") != 0 or header.get("failures"):
                raise _SafeError("auth_required", "百度统计报表读取失败，请检查账号授权及配额。")
            records = result.get("body", {}).get("data", [])
            if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
                raise _SafeError("unavailable", "统计数据格式暂时无法识别，已保留上次数据。")
            return records[0]
        expiry = _timestamp(self.config.get("expires_at"))
        if not self.config.get("access_token") or (expiry and expiry <= time.time() + 120):
            self._refresh_token()
        path = "config/getSiteList" if endpoint == "getSiteList" else "report/getData"
        result = self._post(OAUTH_BASE + path, dict(params, access_token=self.config["access_token"]))
        code = result.get("error_code")
        if code is not None or result.get("error"):
            if str(code) in ("110", "111") and retry:
                self._refresh_token()
                return self._request(endpoint, params, retry=False)
            state = "auth_required" if str(code) in ("110", "111") or result.get("error") else "unavailable"
            raise _SafeError(state, "百度统计报表读取失败，请检查读取授权及配额。")
        return result

    def fetch(self, today):
        listing = self._request("getSiteList").get("list")
        if not isinstance(listing, list):
            raise _SafeError("unavailable", "统计站点列表暂时无法读取。")
        candidates = {}
        for item in listing:
            if not isinstance(item, dict):
                continue
            try:
                domain = _domain(item.get("domain"))
            except ValueError:
                domain = None
            identifier = _site_id(item.get("site_id"))
            if domain and identifier and item.get("status", 0) in (0, "0"):
                candidates[identifier] = domain
        wanted = _site_id(self.config.get("site_id"))
        if wanted is not None:
            candidates = {key: value for key, value in candidates.items() if key == wanted}
        if len(candidates) != 1:
            message = ("账号下有多个排行榜统计站点，需要指定数字报表站点编号。" if candidates
                       else "授权账号下未找到排行榜的完整站点，请检查站点权限。")
            raise _SafeError("site_not_found", message)
        identifier, domain = next(iter(candidates.items()))
        start = today - dt.timedelta(days=7)
        response = self._request("getData", {
            "site_id": identifier, "method": "trend/time/a", "metrics": "pv_count",
            "start_date": start.strftime("%Y%m%d"), "end_date": today.strftime("%Y%m%d"),
            "gran": "day", "max_results": 0,
        })
        rows = _report_rows(response.get("result"), start, today)
        return {"schema": 1, "site_id": identifier, "domain": domain,
                "updated_at": int(time.time()), "rows": rows}


def _report_date(value, start, end):
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if isinstance(value, dict):
        value = value.get("name")
    if not isinstance(value, str):
        return None
    text = value.strip()
    for pattern in ("%Y/%m/%d", "%Y-%m-%d", "%Y%m%d", "%Y年%m月%d日"):
        try:
            day = dt.datetime.strptime(text, pattern).date()
            return day if start <= day <= end else None
        except ValueError:
            pass
    # Some report accounts omit the year. Resolve only within this query window.
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})", text)
    if match:
        for offset in range((end - start).days + 1):
            day = start + dt.timedelta(days=offset)
            if (day.month, day.day) == (int(match[1]), int(match[2])):
                return day
    return None


def _report_rows(report, start, end):
    if not isinstance(report, dict):
        raise _SafeError("unavailable", "统计报表暂时无法读取。")
    fields, items = report.get("fields"), report.get("items")
    if (not isinstance(fields, list) or "pv_count" not in fields or fields.index("pv_count") != 1
            or not isinstance(items, list) or len(items) < 2
            or not isinstance(items[0], list) or not isinstance(items[1], list)
            or len(items[0]) != len(items[1])):
        raise _SafeError("unavailable", "统计数据格式暂时无法识别，已保留上次数据。")
    observed = {}
    for label, metrics in zip(items[0], items[1]):
        day = _report_date(label, start, end)
        if day is None or not isinstance(metrics, list) or len(metrics) != 1 or day in observed:
            raise _SafeError("unavailable", "统计日期无法核对，已保留上次数据。")
        observed[day] = _count(metrics[0])
    if not any(value is not None for value in observed.values()):
        raise _SafeError("unavailable", "百度统计尚未返回可用的每日浏览量。")
    return [{"date": (start + dt.timedelta(days=offset)).isoformat(),
             "pv": observed.get(start + dt.timedelta(days=offset)),
             "partial": start + dt.timedelta(days=offset) == end}
            for offset in range((end - start).days + 1)]


class TrafficStats:
    """Bind one private config/cache to an independent background refresher."""

    def __init__(self, config_path=None, cache_dir=None, refresh_interval=600):
        self.config_path = Path(config_path) if config_path else Path.home() / ".config/marschain-dashboard/analytics.json"
        directory = Path(cache_dir) if cache_dir else Path.home() / ".cache/marschain-dashboard"
        self.cache_path = directory / "analytics-cache.json"
        self.interval = max(60, min(600, int(refresh_interval)))
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._cached = None
        self._state = "refreshing"
        self._message = "正在读取排行榜访问统计。"
        self._checked_at = None

    def close(self):
        self._stop.set()

    def _start(self):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="ranking-traffic", daemon=True)
                self._thread.start()

    def _load_cache(self):
        try:
            cached, _ = _read_private_json(self.cache_path)
            if cached.get("schema") != 1 or cached.get("domain") not in DOMAINS:
                return
            if not _site_id(cached.get("site_id")) or not _timestamp(cached.get("updated_at")):
                return
            rows = cached.get("rows")
            if not isinstance(rows, list) or len(rows) > 8:
                return
            clean = []
            seen = set()
            for row in rows:
                day = dt.date.fromisoformat(row["date"]).isoformat()
                if day in seen or not isinstance(row.get("partial"), bool):
                    return
                seen.add(day)
                clean.append({"date": day, "pv": _count(row.get("pv")), "partial": row["partial"]})
            cached["rows"] = clean
            with self._lock:
                self._cached = cached
                self._state = "stale"
                self._message = "正在更新统计，暂时显示上次成功数据。"
        except (OSError, ValueError, KeyError, TypeError, _SafeError):
            pass

    def _refresh(self):
        checked = int(time.time())
        try:
            config, digest = _config(self.config_path)
            today = dt.datetime.now(BEIJING).date()
            result = _BaiduClient(self.config_path, config, digest).fetch(today)
            try:
                _atomic_json(self.cache_path, result, create_parent=True)
            except OSError:
                # Current values remain usable in memory; avoid exposing file paths.
                pass
            with self._lock:
                self._cached = result
                self._state = "ready"
                self._message = "按北京时间自然日统计；今日数据尚未结束，百度统计可能存在延迟。"
                self._checked_at = checked
        except _SafeError as exc:
            with self._lock:
                self._state, self._message, self._checked_at = exc.state, exc.message, checked
        except Exception:
            # Third-party or filesystem exceptions can contain credentials/URLs.
            with self._lock:
                self._state = "unavailable"
                self._message = "访问统计暂时无法更新，稍后自动重试。"
                self._checked_at = checked

    def _config_stamp(self):
        try:
            info = self.config_path.lstat()
            return info.st_ino, info.st_mtime_ns, info.st_size, info.st_mode
        except OSError:
            return None

    def _run(self):
        self._load_cache()
        previous = object()
        next_refresh = 0.0
        previous_day = None
        while not self._stop.is_set():
            stamp = self._config_stamp()
            today = dt.datetime.now(BEIJING).date()
            if stamp != previous or time.monotonic() >= next_refresh or today != previous_day:
                self._refresh()
                previous = self._config_stamp()
                previous_day = today
                next_refresh = time.monotonic() + self.interval
            self._stop.wait(30)

    def safe_summary(self):
        self._start()
        now = int(time.time())
        today = dt.datetime.fromtimestamp(now, BEIJING).date()
        yesterday = today - dt.timedelta(days=1)
        with self._lock:
            cached = self._cached
            rows = {row["date"]: dict(row) for row in cached["rows"]} if cached else {}
            updated = cached["updated_at"] if cached else None
            state, message, checked = self._state, self._message, self._checked_at
        stale = bool(cached) and (state != "ready" or now - updated > self.interval * 2
                                   or today.isoformat() not in rows)
        if state == "ready" and stale:
            state, message = "stale", "统计更新延迟，当前显示上次成功数据。"
        today_row = rows.get(today.isoformat(), {"date": today.isoformat(), "pv": None, "partial": True})
        yesterday_row = rows.get(yesterday.isoformat(), {"date": yesterday.isoformat(), "pv": None, "partial": False})
        if yesterday_row.get("partial") and yesterday_row.get("pv") is not None:
            message += " 昨日数值尚未取得日终统计，待刷新确认。"
        history = []
        if cached:
            for offset in range(7, -1, -1):
                day = today - dt.timedelta(days=offset)
                history.append(rows.get(day.isoformat(), {
                    "date": day.isoformat(), "pv": None, "partial": offset == 0}))
        return {"ok": bool(cached), "state": state, "source": "百度统计",
                "timezone": "Asia/Shanghai", "metric": "pageviews", "site": "marschainrank.com",
                "today": today_row, "yesterday": yesterday_row, "history": history,
                "updatedAt": updated, "checkedAt": checked, "stale": stale,
                "refreshIntervalSeconds": self.interval, "message": message}


_INSTANCES = {}
_INSTANCES_LOCK = threading.Lock()


def safe_summary(config_path=None, cache_dir=None):
    """Return safe cached values, bound to the supplied private file locations."""
    key = (str(config_path) if config_path is not None else None,
           str(cache_dir) if cache_dir is not None else None)
    with _INSTANCES_LOCK:
        instance = _INSTANCES.get(key)
        if instance is None:
            instance = TrafficStats(config_path=config_path, cache_dir=cache_dir)
            _INSTANCES[key] = instance
    return instance.safe_summary()
