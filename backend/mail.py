"""Thin Resend HTTP client and factual HTML for watch mail."""

from __future__ import annotations

import html
import logging
from typing import Any

import httpx

from .config import get_settings

logger = logging.getLogger("pourintelligence")

RESEND_URL = "https://api.resend.com/emails"


def public_base_url() -> str:
    settings = get_settings()
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    for origin in settings.allowed_origins:
        if "localhost" not in origin and "127.0.0.1" not in origin:
            return origin.rstrip("/")
    return (settings.allowed_origins[0] if settings.allowed_origins else "").rstrip("/")


def unsubscribe_url(token: str | None) -> str:
    base = public_base_url()
    token = token or ""
    if not base:
        return f"/v1/unsubscribe?token={token}"
    return f"{base}/v1/unsubscribe?token={token}"


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def wrap_email(title: str, body: str, unsub: str) -> str:
    return (
        '<!DOCTYPE html><html><body style="margin:0;background:#f3f3f0;padding:24px;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #d8d8d4;'
        "border-collapse:collapse;font-family:Helvetica,Arial,sans-serif;color:#1a1a1a;"
        'font-size:14px;line-height:1.45;">'
        f'<tr><td style="padding:14px 16px;border-bottom:1px solid #d8d8d4;font-size:11px;'
        f'letter-spacing:0.06em;text-transform:uppercase;color:#555;">{_esc(title)}</td></tr>'
        f'<tr><td style="padding:16px;">{body}</td></tr>'
        '<tr><td style="padding:12px 16px;border-top:1px solid #d8d8d4;font-size:12px;color:#777;">'
        f'<a href="{_esc(unsub)}" style="color:#555;">Unsubscribe</a></td></tr>'
        "</table></body></html>"
    )


def _fact_row(label: str, value: Any) -> str:
    return (
        "<tr>"
        f'<td style="padding:6px 8px;border-bottom:1px solid #ecece8;color:#666;width:38%;">{_esc(label)}</td>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #ecece8;">{_esc(value)}</td>'
        "</tr>"
    )


def render_alert_html(
    *,
    location: str,
    product: str,
    status: str,
    pour_date: str,
    events: list[dict[str, Any]],
    unsub_token: str | None,
) -> str:
    rows = "".join(
        _fact_row(
            event.get("label") or event.get("code") or "Change",
            event.get("detail") or event.get("current") or "",
        )
        for event in events
    )
    body = (
        f'<p style="margin:0 0 12px;">{_esc(location)} · {_esc(product)} · {_esc(status)}</p>'
        f'<p style="margin:0 0 12px;color:#555;">Window {_esc(pour_date)}</p>'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="border-collapse:collapse;border:1px solid #ecece8;">'
        f"{rows}</table>"
    )
    return wrap_email("Pour Intelligence", body, unsubscribe_url(unsub_token))


def render_digest_html(*, sites: list[dict[str, Any]], unsub_token: str | None) -> str:
    header = (
        "<tr>"
        '<th align="left" style="padding:6px 8px;border-bottom:1px solid #d8d8d4;font-weight:600;font-size:12px;">Site</th>'
        '<th align="left" style="padding:6px 8px;border-bottom:1px solid #d8d8d4;font-weight:600;font-size:12px;">Status</th>'
        '<th align="left" style="padding:6px 8px;border-bottom:1px solid #d8d8d4;font-weight:600;font-size:12px;">Window</th>'
        '<th align="left" style="padding:6px 8px;border-bottom:1px solid #d8d8d4;font-weight:600;font-size:12px;">Last</th>'
        "</tr>"
    )
    rows = []
    for site in sites:
        last = site.get("last_event") or site.get("last_checked_at") or "—"
        rows.append(
            "<tr>"
            f'<td style="padding:6px 8px;border-bottom:1px solid #ecece8;">{_esc(site.get("location"))}'
            f'<br><span style="color:#777;font-size:12px;">{_esc(site.get("product"))}</span></td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #ecece8;">{_esc(site.get("status"))}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #ecece8;">{_esc(site.get("pour_date"))}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #ecece8;">{_esc(last)}</td>'
            "</tr>"
        )
    body = (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="border-collapse:collapse;border:1px solid #ecece8;">'
        f"{header}{''.join(rows)}</table>"
    )
    return wrap_email("Pour Intelligence daily", body, unsubscribe_url(unsub_token))


async def send_email(to: str, subject: str, html_body: str) -> bool:
    settings = get_settings()
    if not settings.resend_api_key:
        logger.info("email skipped: RESEND_API_KEY not set")
        return False
    if not settings.alert_from_email:
        logger.info("email skipped: ALERT_FROM_EMAIL not set")
        return False
    if not to:
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                RESEND_URL,
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.alert_from_email,
                    "to": [to],
                    "subject": subject,
                    "html": html_body,
                },
            )
        if response.status_code >= 400:
            logger.warning("resend failed status=%s body=%s", response.status_code, response.text)
            return False
        return True
    except Exception:
        logger.exception("resend request failed")
        return False
