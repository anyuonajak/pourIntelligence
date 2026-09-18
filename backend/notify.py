"""Subscriber alerts and digest grouping."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from .db import mark_events_emailed, set_last_digest_at
from .mail import render_alert_html, render_digest_html, render_watch_started_html, send_email
from .watch import pending_alert_events

logger = logging.getLogger("pourintelligence")

DIGEST_MIN_AGE = timedelta(hours=20)
_WATCH_STARTED_SENT: set[str] = set()


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def digest_due(last_digest_at: Any, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    last = _parse_dt(last_digest_at)
    if last is None:
        return True
    return now - last >= DIGEST_MIN_AGE


def fmt_window(value: Any) -> str:
    if not value:
        return "—"
    text = str(value).replace("T", " ")
    return text[:16]


def product_label(product: str | None) -> str:
    return "Masonry" if product == "masonry" else "Concrete"


def last_event_line(events: list[dict[str, Any]] | None) -> str:
    if not events:
        return "—"
    event = events[-1] if isinstance(events, list) else None
    if not isinstance(event, dict):
        return "—"
    return event.get("detail") or event.get("label") or event.get("code") or "—"


def site_snapshot(row: dict[str, Any]) -> dict[str, str]:
    return {
        "location": row.get("location_name") or row.get("zip_code") or "Site",
        "product": product_label(row.get("product")),
        "status": str(row.get("go_no_go_status") or "—"),
        "pour_date": fmt_window(row.get("pour_date")),
        "last_event": last_event_line(row.get("watch_events")),
        "last_checked_at": fmt_window(row.get("last_checked_at")),
    }


def group_digest_sites(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group watching checks by subscriber id. Skip digest_enabled=false and empty inboxes."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        sub = row.get("subscriber") if isinstance(row.get("subscriber"), dict) else None
        subscriber_id = str(row.get("subscriber_id") or (sub or {}).get("id") or "")
        if not subscriber_id or not sub:
            continue
        if sub.get("digest_enabled") is False:
            continue
        if not sub.get("email"):
            continue
        bucket = grouped.setdefault(
            subscriber_id,
            {"subscriber": sub, "sites": []},
        )
        bucket["sites"].append(site_snapshot(row))
    return {key: value for key, value in grouped.items() if value["sites"]}


async def send_watch_started(
    *,
    check_id: UUID | str | None,
    subscriber: dict[str, Any] | None,
    watching: bool,
    location_name: str,
    product: str,
    status: str,
    pour_date: Any,
    watch_events: list[dict[str, Any]] | None = None,
) -> bool:
    """Confirm a new open watch. One send per check_id; never raises to the caller."""
    if not watching or not check_id or not subscriber:
        return False
    email = subscriber.get("email")
    if not email:
        return False
    key = str(check_id)
    if key in _WATCH_STARTED_SENT:
        return False
    for event in watch_events or []:
        if isinstance(event, dict) and event.get("code") == "WATCH_STARTED" and event.get("emailed") is True:
            _WATCH_STARTED_SENT.add(key)
            return False
    location = location_name or "site"
    html_body = render_watch_started_html(
        location=location,
        product=product_label(product),
        status=str(status or "—"),
        pour_date=fmt_window(pour_date),
        unsub_token=subscriber.get("unsub_token"),
    )
    try:
        sent = await send_email(email, f"Watching · {location}", html_body)
    except Exception:
        logger.exception("watch-started send failed check_id=%s", check_id)
        return False
    if sent:
        _WATCH_STARTED_SENT.add(key)
    return sent


async def send_watch_alerts(
    *,
    check_id: UUID | str | None,
    stored: dict[str, Any] | None,
    events: list[dict[str, Any]],
    status: str,
    location_name: str,
    product: str,
    pour_date: Any,
) -> bool:
    if not check_id or not stored:
        return False
    pending = pending_alert_events(events)
    if not pending:
        return False
    sub = stored.get("subscriber") if isinstance(stored.get("subscriber"), dict) else None
    if not sub or not sub.get("email") or sub.get("alerts_enabled") is False:
        return False
    subject_status = str(status or pending[0].get("current") or "UPDATE")
    location = location_name or stored.get("location_name") or stored.get("zip_code") or "site"
    html_body = render_alert_html(
        location=location,
        product=product_label(product),
        status=subject_status,
        pour_date=fmt_window(pour_date),
        events=pending,
        unsub_token=sub.get("unsub_token"),
    )
    sent = await send_email(sub["email"], f"{subject_status} · {location}", html_body)
    if sent:
        mark_events_emailed(UUID(str(check_id)), pending)
    return sent


async def send_daily_digest(grouped: dict[str, dict[str, Any]], now: Optional[datetime] = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    emailed = 0
    skipped = 0
    errors = 0
    for subscriber_id, payload in grouped.items():
        sub = payload["subscriber"]
        sites = payload["sites"]
        if not digest_due(sub.get("last_digest_at"), now=now):
            skipped += 1
            continue
        count = len(sites)
        noun = "site" if count == 1 else "sites"
        subject = f"Pour Intelligence daily · {count} {noun}"
        html_body = render_digest_html(sites=sites, unsub_token=sub.get("unsub_token"))
        try:
            sent = await send_email(sub.get("email"), subject, html_body)
        except Exception:
            logger.exception("digest send failed subscriber=%s", subscriber_id)
            errors += 1
            continue
        if sent:
            set_last_digest_at(subscriber_id, now)
            emailed += 1
        else:
            skipped += 1
    return {"emailed": emailed, "skipped": skipped, "errors": errors}
