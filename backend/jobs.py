"""Server-side watch tick and daily digest. Auth: X-Jobs-Secret or Bearer."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from .config import get_settings
from .db import (
    WATCH_TICK_BATCH,
    get_client,
    list_open_watches,
    list_watching_subscribed,
    record_watch_poll,
    unsubscribe_by_token,
)
from .formulas import evaluate_pour
from .formulas_masonry import evaluate_masonry
from .notify import group_digest_sites, send_daily_digest, send_watch_alerts
from .schemas import MasonryDesign, MixDesign, PourReadinessRequest, ProductType
from .watch import diff_forecast, is_watch_open, jobsite_now, pending_alert_events, watch_until
from .weather import fetch_hourly

logger = logging.getLogger("pourintelligence")

router = APIRouter()

UNSUB_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Unsubscribed — Monolith</title>
  <link rel="stylesheet" href="/style.css?v=27"/>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand">
        <a class="wordmark" href="/">Monolith</a>
      </div>
    </div>
  </header>
  <div class="page">
    <section class="panel">
      <h2>Unsubscribed</h2>
      <p>Alerts and the daily digest are off for this address.</p>
    </section>
  </div>
</body>
</html>
"""

UNSUB_MISSING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Unsubscribe — Monolith</title>
  <link rel="stylesheet" href="/style.css?v=27"/>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand">
        <a class="wordmark" href="/">Monolith</a>
      </div>
    </div>
  </header>
  <div class="page">
    <section class="panel">
      <h2>Link not valid</h2>
      <p>This unsubscribe link is unknown or already used.</p>
    </section>
  </div>
</body>
</html>
"""


def require_jobs_secret(
    x_jobs_secret: Optional[str] = None,
    authorization: Optional[str] = None,
) -> None:
    expected = get_settings().jobs_secret or ""
    provided = (x_jobs_secret or "").strip()
    if not provided and authorization:
        scheme, _, rest = authorization.partition(" ")
        if scheme.lower() == "bearer":
            provided = rest.strip()
    if not expected or not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Unauthorized.")


def _parse_pour_date(value: Any) -> datetime:
    if isinstance(value, datetime):
        pour = value
    else:
        pour = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if pour.tzinfo is not None:
        return pour.replace(tzinfo=None)
    return pour


def request_from_check(stored: dict[str, Any]) -> PourReadinessRequest:
    mix = stored.get("mix_design") if isinstance(stored.get("mix_design"), dict) else {}
    product = stored.get("product") or mix.get("product") or "concrete"
    kwargs: dict[str, Any] = {
        "product": product,
        "zip_code": stored.get("zip_code"),
        "address": stored.get("address"),
        "latitude": stored.get("latitude"),
        "longitude": stored.get("longitude"),
        "pour_date": _parse_pour_date(stored.get("pour_date")),
        "concrete_temp_f": stored.get("concrete_temp_f"),
    }
    if product == ProductType.MASONRY.value or product == ProductType.MASONRY:
        kwargs["masonry_design"] = MasonryDesign(
            unit_type=mix.get("unit_type") or "CMU",
            mortar_type=mix.get("mortar_type") or "Type_N",
        )
    else:
        kwargs["mix_design"] = MixDesign(
            cement_type=mix.get("cement_type") or "Type_I",
            target_psi=int(mix.get("target_psi") or 4000),
            thickness_inches=float(mix.get("thickness_inches") or 4),
        )
    return PourReadinessRequest(**kwargs)


async def evaluate_stored(stored: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    body = request_from_check(stored)
    lat = stored.get("latitude")
    lon = stored.get("longitude")
    if lat is None or lon is None:
        raise ValueError("Stored check is missing coordinates.")
    hourly, tz_name = await fetch_hourly(
        float(lat),
        float(lon),
        body.pour_date,
        cache_ttl_minutes=get_settings().watch_weather_cache_minutes,
    )
    if body.product == ProductType.MASONRY:
        result = evaluate_masonry(
            pour_time=body.pour_date,
            hourly=hourly,
            design=body.masonry_design,
            mortar_temp_f=body.concrete_temp_f,
        )
    else:
        result = evaluate_pour(
            pour_time=body.pour_date,
            hourly=hourly,
            mix=body.mix_design,
            concrete_temp_f=body.concrete_temp_f,
        )
    return result, tz_name


async def poll_stored_check(stored: dict[str, Any]) -> dict[str, Any]:
    result, tz_name = await evaluate_stored(stored)
    status = result["go_no_go_status"].value
    risk_factors = result["risk_factors"]
    metrics = result["metrics"].model_dump(mode="json")
    predictions = result["predictions"].model_dump(mode="json")
    pour_date = _parse_pour_date(stored.get("pour_date"))
    watching = is_watch_open(pour_date, predictions, jobsite_now(tz_name))
    if stored.get("watching") is False:
        watching = False
    changes = diff_forecast(
        previous_status=stored.get("go_no_go_status"),
        previous_risks=stored.get("risk_factors") or [],
        previous_metrics=stored.get("metrics") or {},
        status=status,
        risks=risk_factors,
        metrics=metrics,
    )
    check_id = stored.get("id")
    stamped: list[dict[str, Any]] = []
    if check_id:
        stamped = record_watch_poll(
            UUID(str(check_id)),
            status=status,
            risk_factors=risk_factors,
            metrics=metrics,
            predictions=predictions,
            recommended_mitigation=result["recommended_mitigation"],
            new_events=changes,
            prior_events=stored.get("watch_events") or [],
            watching=watching,
        ) or []
    pending_source = [*(stored.get("watch_events") or []), *stamped]
    emailed = False
    try:
        emailed = await send_watch_alerts(
            check_id=check_id,
            stored=stored,
            events=pending_source,
            status=status,
            location_name=stored.get("location_name") or "",
            product=stored.get("product") or "concrete",
            pour_date=stored.get("pour_date"),
        )
    except Exception:
        logger.exception("watch alert send failed check_id=%s", check_id)
    return {
        "changed": bool(changes),
        "emailed": emailed,
        "watching": watching,
        "watch_until": watch_until(pour_date, predictions).isoformat(),
        "pending_alerts": len(pending_alert_events(pending_source)),
    }


async def run_watch_tick(limit: int = WATCH_TICK_BATCH) -> dict[str, int]:
    rows = list_open_watches(limit)
    polled = 0
    changed = 0
    emailed = 0
    errors = 0
    for stored in rows:
        polled += 1
        try:
            outcome = await poll_stored_check(stored)
        except Exception:
            logger.exception("watch-tick failed check_id=%s", stored.get("id"))
            errors += 1
            continue
        if outcome.get("changed"):
            changed += 1
        if outcome.get("emailed"):
            emailed += 1
    return {"polled": polled, "changed": changed, "emailed": emailed, "errors": errors}


async def run_daily_digest() -> dict[str, int]:
    rows = list_watching_subscribed()
    grouped = group_digest_sites(rows)
    result = await send_daily_digest(grouped)
    return {
        "subscribers": len(grouped),
        "emailed": result["emailed"],
        "skipped": result["skipped"],
        "errors": result["errors"],
    }


@router.post("/internal/jobs/watch-tick")
async def watch_tick(
    request: Request,
    x_jobs_secret: Optional[str] = Header(default=None),
) -> dict[str, int]:
    require_jobs_secret(x_jobs_secret, request.headers.get("authorization"))
    return await run_watch_tick()


@router.post("/internal/jobs/daily-digest")
async def daily_digest(
    request: Request,
    x_jobs_secret: Optional[str] = Header(default=None),
) -> dict[str, int]:
    require_jobs_secret(x_jobs_secret, request.headers.get("authorization"))
    return await run_daily_digest()


@router.get("/v1/unsubscribe")
def unsubscribe(token: str = "") -> HTMLResponse:
    if not token.strip():
        return HTMLResponse(UNSUB_MISSING_HTML, status_code=400)
    if get_client() is None:
        return HTMLResponse(UNSUB_MISSING_HTML, status_code=503)
    row = unsubscribe_by_token(token.strip())
    if row is None:
        return HTMLResponse(UNSUB_MISSING_HTML, status_code=404)
    return HTMLResponse(UNSUB_HTML)
