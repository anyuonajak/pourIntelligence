from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from .admin import router as admin_router
from .config import get_settings
from .db import (
    check_exists,
    get_check_for_watch,
    get_client,
    insert_pour_check,
    insert_pour_outcome,
    lookup_api_key,
    record_watch_poll,
    set_watching,
    upsert_subscriber,
)
from .formulas import evaluate_pour
from .formulas_masonry import evaluate_masonry
from .jobs import router as jobs_router
from .notify import send_watch_alerts, send_watch_started
from .rate_limit import limiter
from .schemas import (
    LocationInfo,
    PourOutcomeRequest,
    PourOutcomeResponse,
    PourReadinessRequest,
    PourReadinessResponse,
    PourWatchRequest,
    PourWatchResponse,
    ProductType,
)
from .watch import diff_forecast, is_watch_open, jobsite_now, watch_until
from .weather import WeatherUnavailable, fetch_hourly, resolve_location

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("pourintelligence")

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
settings = get_settings()

app = FastAPI(
    title="Monolith",
    version="0.1.0",
    description=(
        "Go / no-go tickets for concrete and masonry, then a live watch "
        "that restamps only on material forecast changes. Advisory only. "
        "Public demo is rate-limited. Vendor callers should send X-API-Key."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID", "X-Jobs-Secret"],
)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax", https_only=False)
app.include_router(admin_router)
app.include_router(jobs_router)


@app.exception_handler(WeatherUnavailable)
async def weather_unavailable_handler(_request: Request, exc: WeatherUnavailable) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "message": exc.message},
    )


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def _jobsite_now(tz_name: str | None) -> datetime:
    return jobsite_now(tz_name)


async def _evaluate(body: PourReadinessRequest, lat: float, lon: float, cache_ttl_minutes: int | None = None):
    hourly, tz_name = await fetch_hourly(lat, lon, body.pour_date, cache_ttl_minutes=cache_ttl_minutes)
    try:
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
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result, tz_name


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.middleware("http")
async def rate_limit_api(request: Request, call_next):
    if request.method == "POST" and request.url.path.startswith("/v1/"):
        raw_key = request.headers.get("x-api-key")
        api_key = lookup_api_key(raw_key) if raw_key else None
        if raw_key and api_key is None:
            return JSONResponse(status_code=401, content={"detail": "Invalid API key."})
        # Watch polls are cheap and repetitive, so they get their own quota.
        # Customer close must always persist, even if the poll quota is exhausted.
        path = request.url.path
        closing = path.startswith("/v1/pour-watch/") and path.rstrip("/").endswith("/close")
        watching = path.startswith("/v1/pour-watch") and not closing
        if closing:
            request.state.api_key = api_key
            return await call_next(request)
        if api_key:
            limit = int(api_key.get("rate_limit_per_hour") or settings.api_rate_limit_per_hour)
            bucket = f"key:{api_key['id']}"
        else:
            limit = settings.watch_rate_limit_per_hour if watching else settings.demo_rate_limit_per_hour
            bucket = f"ip:{_client_ip(request)}"
        if watching:
            bucket = f"watch:{bucket}"
        allowed, retry_after = limiter.allow(bucket, limit)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
                headers={"Retry-After": str(retry_after)},
            )
        request.state.api_key = api_key
    else:
        request.state.api_key = None
    return await call_next(request)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "pour-intelligence",
        "api_version": "v1",
        "supabase": settings.supabase_configured,
        "environment": settings.environment,
    }


@app.post("/v1/pour-readiness", response_model=PourReadinessResponse)
async def pour_readiness(request: Request, body: PourReadinessRequest) -> PourReadinessResponse:
    lat, lon, name = await resolve_location(
        body.latitude,
        body.longitude,
        body.zip_code,
        body.address,
    )
    result, tz_name = await _evaluate(body, lat, lon)

    predictions = result["predictions"].model_dump(mode="json")
    watching = is_watch_open(body.pour_date, predictions, _jobsite_now(tz_name))

    api_key = getattr(request.state, "api_key", None)
    referer = request.headers.get("referer") or ""
    source = "demo" if "pourintelligence" in referer or "localhost" in referer or "127.0.0.1" in referer else "api"

    if body.product == ProductType.MASONRY:
        stored_mix = body.masonry_design.model_dump(mode="json")
    else:
        stored_mix = body.mix_design.model_dump(mode="json")
    stored_mix["product"] = body.product.value

    subscriber = None
    subscriber_id = None
    if body.email:
        subscriber = upsert_subscriber(body.email)
        if subscriber:
            subscriber_id = subscriber.get("id")

    check_id = insert_pour_check(
        {
            "request_id": _request_id(request),
            "source": source,
            "product": body.product.value,
            "api_key_id": api_key["id"] if api_key else None,
            "client_ip": _client_ip(request),
            "latitude": lat,
            "longitude": lon,
            "zip_code": body.zip_code,
            "address": body.address,
            "pour_date": body.pour_date.isoformat(),
            "mix_design": stored_mix,
            "concrete_temp_f": body.concrete_temp_f,
            "go_no_go_status": result["go_no_go_status"].value,
            "risk_factors": result["risk_factors"],
            "metrics": result["metrics"].model_dump(mode="json"),
            "predictions": predictions,
            "recommended_mitigation": result["recommended_mitigation"],
            "location": {"name": name, "latitude": lat, "longitude": lon, "timezone": tz_name},
            "watching": watching,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
            "subscriber_id": subscriber_id,
        }
    )

    logger.info(
        "pour_readiness request_id=%s status=%s check_id=%s",
        _request_id(request),
        result["go_no_go_status"].value,
        check_id,
    )

    if check_id and watching and subscriber:
        try:
            await send_watch_started(
                check_id=check_id,
                subscriber=subscriber,
                watching=watching,
                location_name=name,
                product=body.product.value,
                status=result["go_no_go_status"].value,
                pour_date=body.pour_date,
            )
        except Exception:
            logger.exception("watch-started send failed check_id=%s", check_id)

    return PourReadinessResponse(
        product=body.product,
        check_id=check_id,
        go_no_go_status=result["go_no_go_status"],
        risk_factors=result["risk_factors"],
        risk_factor_details=result["risk_factor_details"],
        metrics=result["metrics"],
        predictions=result["predictions"],
        recommended_mitigation=result["recommended_mitigation"],
        location=LocationInfo(
            name=name,
            latitude=lat,
            longitude=lon,
            timezone=tz_name,
        ),
        hourly=result["hourly"],
        watching=watching,
        watch_until=watch_until(body.pour_date, predictions).isoformat(),
        next_check_seconds=settings.watch_poll_seconds,
    )


@app.post("/v1/pour-watch", response_model=PourWatchResponse)
async def pour_watch(request: Request, body: PourWatchRequest) -> PourWatchResponse:
    """Re-run a submitted ticket and report only forecast moves big enough to matter."""
    lat, lon, name = await resolve_location(
        body.latitude,
        body.longitude,
        body.zip_code,
        body.address,
    )
    result, tz_name = await _evaluate(body, lat, lon, cache_ttl_minutes=settings.watch_weather_cache_minutes)

    status = result["go_no_go_status"].value
    risk_factors = result["risk_factors"]
    metrics = result["metrics"].model_dump(mode="json")
    predictions = result["predictions"].model_dump(mode="json")
    watching = is_watch_open(body.pour_date, predictions, _jobsite_now(tz_name))

    stored = get_check_for_watch(body.check_id) if body.check_id else None
    if stored is not None and stored.get("watching") is False:
        watching = False
    if stored:
        previous_status = stored.get("go_no_go_status")
        previous_risks = stored.get("risk_factors") or []
        previous_metrics = stored.get("metrics") or {}
        prior_events = stored.get("watch_events") or []
    elif body.baseline:
        previous_status = body.baseline.go_no_go_status.value if body.baseline.go_no_go_status else None
        previous_risks = body.baseline.risk_factors
        previous_metrics = body.baseline.metrics.model_dump(mode="json") if body.baseline.metrics else {}
        prior_events = []
    else:
        previous_status, previous_risks, previous_metrics, prior_events = None, [], {}, []

    changes = diff_forecast(
        previous_status=previous_status,
        previous_risks=previous_risks,
        previous_metrics=previous_metrics,
        status=status,
        risks=risk_factors,
        metrics=metrics,
    )

    stamped: list = []
    if body.check_id and stored is not None:
        stamped = record_watch_poll(
            body.check_id,
            status=status,
            risk_factors=risk_factors,
            metrics=metrics,
            predictions=predictions,
            recommended_mitigation=result["recommended_mitigation"],
            new_events=changes,
            prior_events=prior_events,
            watching=watching,
        ) or []
        try:
            await send_watch_alerts(
                check_id=body.check_id,
                stored=stored,
                events=[*prior_events, *stamped],
                status=status,
                location_name=name,
                product=body.product.value,
                pour_date=body.pour_date,
            )
        except Exception:
            logger.exception("watch alert send failed check_id=%s", body.check_id)

    if changes:
        logger.info(
            "pour_watch request_id=%s check_id=%s status=%s changes=%d",
            _request_id(request),
            body.check_id,
            status,
            len(changes),
        )

    return PourWatchResponse(
        product=body.product,
        check_id=body.check_id,
        watching=watching,
        watch_until=watch_until(body.pour_date, predictions).isoformat(),
        checked_at=datetime.now(timezone.utc).isoformat(),
        next_check_seconds=settings.watch_poll_seconds,
        changed=bool(changes),
        changes=changes,
        go_no_go_status=result["go_no_go_status"],
        risk_factors=risk_factors,
        risk_factor_details=result["risk_factor_details"],
        metrics=result["metrics"],
        predictions=result["predictions"],
        recommended_mitigation=result["recommended_mitigation"],
        location=LocationInfo(name=name, latitude=lat, longitude=lon, timezone=tz_name),
        hourly=result["hourly"],
    )


@app.post("/v1/pour-watch/{check_id}/close")
def close_watch(check_id: UUID) -> dict[str, object]:
    """Stop monitoring a ticket. Does not delete the check or its stamp."""
    if get_client() is None:
        return {"ok": True, "watching": False}
    stored = get_check_for_watch(check_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Unknown check_id.")
    closed = set_watching(
        check_id,
        False,
        {
            "code": "WATCH_CLOSED",
            "label": "Watch closed",
            "detail": "Monitoring stopped. The stamp is unchanged.",
            "previous": "watching",
            "current": "closed",
        },
    )
    if not closed:
        raise HTTPException(status_code=503, detail="Could not close this watch.")
    return {"ok": True, "watching": False}


@app.post("/v1/pour-outcomes", response_model=PourOutcomeResponse)
def pour_outcomes(body: PourOutcomeRequest) -> PourOutcomeResponse:
    if get_client() is None:
        raise HTTPException(status_code=503, detail="Outcome storage is not configured.")
    if not check_exists(body.check_id):
        raise HTTPException(status_code=404, detail="Unknown check_id.")
    try:
        insert_pour_outcome(body.check_id, body.outcome.value, body.notes)
    except Exception as exc:
        logger.exception("pour_outcome insert failed")
        raise HTTPException(status_code=409, detail="Outcome already recorded for this check.") from exc
    return PourOutcomeResponse(recorded=True, check_id=body.check_id, outcome=body.outcome)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/terms")
def terms() -> FileResponse:
    return FileResponse(FRONTEND / "terms.html", headers={"Cache-Control": "no-cache"})


@app.get("/style.css")
def stylesheet() -> FileResponse:
    return FileResponse(
        FRONTEND / "style.css",
        media_type="text/css",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/app.js")
def javascript() -> FileResponse:
    return FileResponse(
        FRONTEND / "app.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )
