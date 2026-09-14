"""Detect material forecast shifts on a watched pour or lay-up."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

TEMP_SWING_F = 5.0
WIND_SWING_MPH = 5.0
RH_SWING_PCT = 12.0
EVAP_SWING = 0.05
RAIN_SWING_IN = 0.03
MIN_TEMP_SWING_F = 5.0

# A watch runs until the placement has had its protection period, then closes.
WATCH_TAIL_HOURS_MIN = 24
WATCH_TAIL_HOURS_MAX = 48


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
    """How long after placement we keep watching, from the protection period."""
    predictions = predictions or {}
    candidates = [
        predictions.get("protection_period_hours"),
        predictions.get("estimated_time_to_500_psi_hours"),
    ]
    hours = WATCH_TAIL_HOURS_MIN
    for value in candidates:
        if value is None:
            continue
        try:
            hours = max(hours, int(round(float(value))))
        except (TypeError, ValueError):
            continue
    return min(hours, WATCH_TAIL_HOURS_MAX)


def watch_until(pour_date: datetime, predictions: dict[str, Any] | None) -> datetime:
    return pour_date + timedelta(hours=watch_tail_hours(predictions))


def is_watch_open(pour_date: datetime, predictions: dict[str, Any] | None, now: datetime) -> bool:
    return now < watch_until(pour_date, predictions)


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
