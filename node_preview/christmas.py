"""Public equation burn scenarios using the site's existing emission model."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import math


TARGET_TIMESTAMP = int(datetime(2026, 12, 25, tzinfo=timezone.utc).timestamp())
DAY_SECONDS = 86_400
EVENT_DAYS = 8
EVENT_SECONDS = EVENT_DAYS * DAY_SECONDS
CYCLE_DAYS = 448
CYCLE_SECONDS = CYCLE_DAYS * DAY_SECONDS
FIRST_CYCLE_TOKENS = Decimal(100_000_000_000)
WEI_PER_TOKEN = Decimal(10) ** 18
DISPLAY_KEYS = (
    "circulation", "remaining_emission", "projected_circulation", "event_emission",
    "settlement_circulation", "burn_pool",
    "per_100m_power", "daily_emission", "remaining_days",
)
ORACLE_DISPLAY_KEYS = (
    "circulation", "source_gap_emission", "projected_circulation", "event_emission",
    "settlement_circulation", "burn_pool", "per_100m_power", "daily_emission",
)


def _integer(value):
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = Decimal(str(value))
        if (number.is_finite() and number.adjusted() <= 77
                and number == number.to_integral_value()):
            return int(number)
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        pass
    return None


def _amount(number: Decimal) -> str:
    for threshold, unit in ((Decimal(10) ** 12, "万亿"),
                            (Decimal(10) ** 8, "亿"),
                            (Decimal(10) ** 4, "万")):
        if abs(number) >= threshold:
            return f"{number / threshold:.3f}{unit}"
    return f"{number:,.3f}".rstrip("0").rstrip(".")


def _daily_emission(timestamp: int, genesis: int) -> Decimal:
    cycle = (timestamp - genesis) // CYCLE_SECONDS
    return FIRST_CYCLE_TOKENS * (Decimal("0.5") ** cycle) / CYCLE_DAYS


def _future_emission(start: int, end: int, genesis: int) -> Decimal:
    """Integrate daily model output, including a halving inside the interval."""
    total = Decimal(0)
    cursor = start
    while cursor < end:
        cycle = (cursor - genesis) // CYCLE_SECONDS
        boundary = genesis + (cycle + 1) * CYCLE_SECONDS
        segment_end = min(boundary, end)
        total += _daily_emission(cursor, genesis) * Decimal(segment_end - cursor) / DAY_SECONDS
        cursor = segment_end
    return total


def _lag_display(lag: int) -> str:
    return (f"{lag // DAY_SECONDS}天前" if lag >= DAY_SECONDS else
            f"{lag // 3600}小时前" if lag >= 3600 else
            f"{lag // 60}分钟前" if lag >= 60 else "刚刚")


def oracle_burn_forecast(meta: dict) -> dict:
    """Estimate a rolling eight-day burn scenario at the current data timestamp.

    ``circulation`` stays the last observed balance. If that observation is old,
    emission from its timestamp to ``generated_at`` is explicitly modelled in
    ``source_gap_emission`` rather than presented as another observation. The
    eight-day ``event_emission`` starts at ``generated_at`` and cannot overlap
    that gap. Future burns are assumed zero; the existing power conversion is
    retained without applying the mechanism's multiplier a second time.
    """
    result = {
        "state": "unavailable", "reason": "测算数据暂不可用",
        "event_days": EVENT_DAYS, "event_start_timestamp": None,
        "settlement_timestamp": None, "source_timestamp": None,
        "source_lag_seconds": None, "source_lag_display": "—",
        "display": {key: "—" for key in ORACLE_DISPLAY_KEYS},
        "assumptions": [
            "未来新增销毁按0计算，实际销毁随流通量更新。",
            "流通量记录滞后时，至当前时点的产量单列模型补算。",
            "本轮产量从当前数据时间起算8天，跨减半周期分段计算。",
            "按当前全网总算力及35%参数估算，沿用原有算力换算口径。",
        ],
    }
    if not isinstance(meta, dict):
        return result
    now = _integer(meta.get("generated_at"))
    if now is None or now <= 0:
        result["reason"] = "缺少有效数据时间"
        return result
    genesis = _integer(meta.get("emission_genesis_timestamp"))
    circulation_wei = _integer(meta.get("network_total_circulation_tokens"))
    power = _integer(meta.get("network_total_power"))
    if genesis is None or genesis <= 0 or now < genesis:
        result["reason"] = "缺少有效产量周期"
        return result
    if circulation_wei is None or circulation_wei < 0:
        result["reason"] = "缺少有效流通量"
        return result
    if power is None or power <= 0:
        result["reason"] = "缺少有效全网算力"
        return result

    sources = meta.get("preview_metric_sources")
    source = sources.get("network_total_circulation_tokens") if isinstance(sources, dict) else None
    fetched_at = _integer(source.get("fetched_at")) if isinstance(source, dict) else None
    if isinstance(source, dict) and source.get("status") == "unavailable" and fetched_at is None:
        result["reason"] = "缺少有效流通量记录时间"
        return result
    anchor = fetched_at if fetched_at is not None and 0 < fetched_at <= now else now
    if anchor < genesis:
        result["reason"] = "流通量采样早于产量周期"
        return result

    with localcontext() as context:
        context.prec = 60
        circulation = Decimal(circulation_wei) / WEI_PER_TOKEN
        source_gap = _future_emission(anchor, now, genesis)
        projected = circulation + source_gap
        event = _future_emission(now, now + EVENT_SECONDS, genesis)
        settlement = projected + event
        pool = settlement * Decimal("0.35")
        burn_per_power = pool / Decimal(power)
        daily = _daily_emission(now, genesis)
        numbers = {
            "circulation": circulation, "source_gap_emission": source_gap,
            "projected_circulation": projected, "event_emission": event,
            "settlement_circulation": settlement, "burn_pool": pool,
            "burn_per_power": burn_per_power,
            "per_100m_power": burn_per_power * Decimal(100_000_000),
            "current_daily_emission": daily,
        }
        numeric = {key: float(value) for key, value in numbers.items()}
        if not all(math.isfinite(value) for value in numeric.values()):
            result["reason"] = "测算数据超出有效范围"
            return result
        display = {key: _amount(value) for key, value in numbers.items()
                   if key not in {"current_daily_emission", "burn_per_power"}}
        display["daily_emission"] = _amount(daily)

    lag = now - anchor
    result.update(numeric)
    result.update(
        state="ready", reason="", current_power=power, source_timestamp=anchor,
        event_start_timestamp=now, settlement_timestamp=now + EVENT_SECONDS,
        source_lag_seconds=lag, source_lag_display=_lag_display(lag),
        display=display,
    )
    return result


def christmas_forecast(meta: dict) -> dict:
    """Estimate Christmas supply, then add the following eight days of emission.

    Supply is an observed amount in wei. Its existing burns are already included;
    adding future model emission once avoids deducting today's burn twice.
    Future burns are assumed zero. No private source information is returned.
    """
    result = {
        "state": "unavailable", "reason": "测算数据暂不可用",
        "target_timestamp": TARGET_TIMESTAMP, "source_timestamp": None,
        "event_days": EVENT_DAYS, "event_start_timestamp": TARGET_TIMESTAMP,
        "settlement_timestamp": TARGET_TIMESTAMP + EVENT_SECONDS,
        "source_lag_seconds": None, "source_lag_display": "—",
        "display": {key: "—" for key in DISPLAY_KEYS},
        "assumptions": [
            "未来新增销毁按0计算，实际销毁随流通量更新。",
            "未来产量沿用现有448天减半模型，跨周期分段计算。",
            "圣诞起算后另加8天产量，与距圣诞产量不重叠。",
            "按当前全网总算力及35%参数估算，沿用原有算力换算口径。",
        ],
    }
    if not isinstance(meta, dict):
        return result

    now = _integer(meta.get("generated_at"))
    if now is None or now <= 0:
        result["reason"] = "缺少有效数据时间"
        return result
    if now >= TARGET_TIMESTAMP:
        result.update(state="ended", reason="本期预测已结束")
        return result

    genesis = _integer(meta.get("emission_genesis_timestamp"))
    circulation_wei = _integer(meta.get("network_total_circulation_tokens"))
    power = _integer(meta.get("network_total_power"))
    if genesis is None or genesis <= 0 or now < genesis:
        result["reason"] = "缺少有效产量周期"
        return result
    if circulation_wei is None or circulation_wei < 0:
        result["reason"] = "缺少有效流通量"
        return result
    if power is None or power <= 0:
        result["reason"] = "缺少有效全网算力"
        return result

    sources = meta.get("preview_metric_sources")
    source = sources.get("network_total_circulation_tokens") if isinstance(sources, dict) else None
    fetched_at = _integer(source.get("fetched_at")) if isinstance(source, dict) else None
    if isinstance(source, dict) and source.get("status") == "unavailable" and fetched_at is None:
        result["reason"] = "缺少有效流通量记录时间"
        return result
    anchor = fetched_at if fetched_at is not None and 0 < fetched_at <= now else now
    if anchor < genesis:
        result["reason"] = "流通量采样早于产量周期"
        return result

    with localcontext() as context:
        context.prec = 60
        circulation = Decimal(circulation_wei) / WEI_PER_TOKEN
        remaining = _future_emission(anchor, TARGET_TIMESTAMP, genesis)
        projected = circulation + remaining
        event = _future_emission(TARGET_TIMESTAMP, TARGET_TIMESTAMP + EVENT_SECONDS, genesis)
        settlement = projected + event
        pool = settlement * Decimal("0.35")
        burn_per_power = pool / Decimal(power)
        per_power = burn_per_power * Decimal(100_000_000)
        daily = _daily_emission(now, genesis)
        days = Decimal(TARGET_TIMESTAMP - now) / DAY_SECONDS
        numbers = {
            "circulation": circulation, "remaining_emission": remaining,
            "projected_circulation": projected, "event_emission": event,
            "settlement_circulation": settlement, "burn_pool": pool,
            "burn_per_power": burn_per_power,
            "per_100m_power": per_power, "current_daily_emission": daily,
            "remaining_days": days,
        }
        numeric = {key: float(value) for key, value in numbers.items()}
        if not all(math.isfinite(value) for value in numeric.values()):
            result["reason"] = "测算数据超出有效范围"
            return result
        display = {key: _amount(value) for key, value in numbers.items()
                   if key not in {"current_daily_emission", "remaining_days", "burn_per_power"}}
        display["daily_emission"] = _amount(daily)
        display["remaining_days"] = f"{days:.2f}天"

    lag = now - anchor
    result.update(numeric)
    result.update(
        state="ready", reason="", current_power=power, source_timestamp=anchor,
        source_lag_seconds=lag,
        source_lag_display=_lag_display(lag),
        display=display,
    )
    return result
