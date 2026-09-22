"""Detect material forecast shifts on a watched pour or lay-up."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

TEMP_SWING_F = 5.0
WIND_SWING_MPH = 5.0
RH_SWING_PCT = 12.0
EVAP_SWING = 0.05
RAIN_SWING_IN = 0.03
MIN_TEMP_SWING_F = 5.0

# A watch closes 48 hours after the scheduled pour / lay-up time.
WATCH_CLOSE_HOURS_AFTER_POUR = 48
WATCH_TAIL_HOURS_MIN = 48
WATCH_TAIL_HOURS_MAX = 48

# Instant email: call changes and no-go risks only — not metric heartbeats.
CRITICAL_RISK_CODES = frozenset(
    {
        "EXTREME_EVAPORATION_RATE",
        "FREEZING_BEFORE_500_PSI",
        "HEAVY_RAIN_DURING_POUR",
        "MASONRY_BELOW_20F",
    }
)
ALERT_STATUS_CURRENT = frozenset({"NO_GO", "WARNING"})


def _num(metrics: Optional[dict[str, Any]], key: str) -> Optional[float]:
    if not metrics:
        return None
    value = metrics.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def watch_tail_hours(predictions: dict[str, Any] | None) -> int:
    """How long after the scheduled time we keep watching."""
    return WATCH_CLOSE_HOURS_AFTER_POUR


def watch_until(pour_date: datetime, predictions: dict[str, Any] | None) -> datetime:
    return pour_date + timedelta(hours=WATCH_CLOSE_HOURS_AFTER_POUR)


def is_watch_open(pour_date: datetime, predictions: dict[str, Any] | None, now: datetime) -> bool:
    return now < watch_until(pour_date, predictions)


def jobsite_now(tz_name: str | None) -> datetime:
    """Jobsite-local wall clock, to compare against a naive pour_date."""
    if tz_name:
        try:
            return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _event_risk_code(event: dict[str, Any]) -> str:
    code = str(event.get("code") or "")
    if code.startswith("RISK_ADDED_"):
        return code[len("RISK_ADDED_") :]
    if code.startswith("RISK_CLEARED_"):
        return code[len("RISK_CLEARED_") :]
    return code


def is_alert_worthy(event: dict[str, Any]) -> bool:
    """Critical-adjacent changes that should trigger an instant email."""
    code = str(event.get("code") or "")
    current = str(event.get("current") or "").upper().replace("-", "_")
    if code == "STATUS":
        return current in ALERT_STATUS_CURRENT
    if code == "CROSS_32":
        return True
    if code.startswith("RISK_CLEARED_"):
        return False
    if code.startswith("RISK_ADDED_") and _event_risk_code(event) in CRITICAL_RISK_CODES:
        return True
    return code in CRITICAL_RISK_CODES


def pending_alert_events(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Events stamped emailed=False that still need a critical alert."""
    pending: list[dict[str, Any]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if event.get("emailed") is not False:
            continue
        if is_alert_worthy(event):
            pending.append(event)
    return pending


def diff_forecast(
    *,
    previous_status: Optional[str],
    previous_risks: list[str] | None,
    previous_metrics: dict[str, Any] | None,
    status: str,
    risks: list[str],
    metrics: dict[str, Any],
) -> list[dict[str, str]]:
    """Return human-readable changes that are large enough to re-open the advisory."""
    changes: list[dict[str, str]] = []
    prev_status = previous_status or ""
    if prev_status and prev_status != status:
        changes.append(
            {
                "code": "STATUS",
                "label": "Advisory changed",
                "detail": f"{prev_status.replace('_', '-')} → {status.replace('_', '-')}",
                "previous": prev_status,
                "current": status,
            }
        )

    old_risks = set(previous_risks or [])
    new_risks = set(risks or [])
    for code in sorted(new_risks - old_risks):
        changes.append(
            {
                "code": f"RISK_ADDED_{code}",
                "label": "New risk factor",
                "detail": code.replace("_", " ").title(),
                "previous": "",
                "current": code,
            }
        )
    for code in sorted(old_risks - new_risks):
        changes.append(
            {
                "code": f"RISK_CLEARED_{code}",
                "label": "Risk factor cleared",
                "detail": code.replace("_", " ").title(),
                "previous": code,
                "current": "",
            }
        )

    def swing(key: str, label: str, unit: str, threshold: float, digits: int = 1) -> None:
        before = _num(previous_metrics, key)
        after = _num(metrics, key)
        if before is None or after is None:
            return
        delta = after - before
        if abs(delta) < threshold:
            return
        sign = "+" if delta > 0 else ""
        changes.append(
            {
                "code": f"METRIC_{key.upper()}",
                "label": label,
                "detail": f"{_fmt(before, digits)} → {_fmt(after, digits)} {unit} ({sign}{_fmt(delta, digits)})",
                "previous": _fmt(before, digits),
                "current": _fmt(after, digits),
            }
        )

    swing("ambient_temp_f", "Air temperature", "°F", TEMP_SWING_F)
    swing("wind_speed_mph", "Wind", "mph", WIND_SWING_MPH)
    swing("relative_humidity_pct", "Humidity", "%", RH_SWING_PCT)
    swing("calculated_evaporation_rate_lbs_sqft_hr", "Evaporation", "lb/ft²/hr", EVAP_SWING, 3)
    swing("precipitation_in", "Rain at placement", "in", RAIN_SWING_IN, 3)
    swing("min_temp_next_24h_f", "Min temp next 24h", "°F", MIN_TEMP_SWING_F)

    old_min = _num(previous_metrics, "min_temp_next_24h_f")
    new_min = _num(metrics, "min_temp_next_24h_f")
    for line, label in ((32.0, "freezing"), (40.0, "cold-weather 40°F")):
        if old_min is None or new_min is None:
            continue
        crossed = (old_min >= line) != (new_min >= line)
        if crossed:
            direction = "dropped below" if new_min < line else "rose above"
            changes.append(
                {
                    "code": f"CROSS_{int(line)}",
                    "label": f"Forecast crossed {label}",
                    "detail": f"Min next 24h {direction} {int(line)}°F ({_fmt(old_min)} → {_fmt(new_min)}).",
                    "previous": _fmt(old_min),
                    "current": _fmt(new_min),
                }
            )

    return changes
