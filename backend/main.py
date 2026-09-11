from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from .admin import router as admin_router
from .config import get_settings
from .db import check_exists, get_client, insert_pour_check, insert_pour_outcome, lookup_api_key
from .formulas import evaluate_pour
from .formulas_masonry import evaluate_masonry
from .rate_limit import limiter
from .schemas import (
    LocationInfo,
    PourOutcomeRequest,
    PourOutcomeResponse,
    PourReadinessRequest,
    PourReadinessResponse,
    ProductType,
)
from .weather import fetch_hourly, resolve_location

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("pourintelligence")

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
settings = get_settings()

app = FastAPI(
    title="Pour Intelligence",
    version="0.1.0",
    description=(
        "Go / no-go pour-readiness for concrete and masonry from public weather "
        "forecasts and ACI 305R / 306R guidance. Advisory only. "
        "Public demo is rate-limited. Vendor callers should send X-API-Key."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax", https_only=False)
app.include_router(admin_router)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


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
        if api_key:
            limit = int(api_key.get("rate_limit_per_hour") or settings.api_rate_limit_per_hour)
            bucket = f"key:{api_key['id']}"
        else:
            limit = settings.demo_rate_limit_per_hour
            bucket = f"ip:{_client_ip(request)}"
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
    hourly, tz_name = await fetch_hourly(lat, lon, body.pour_date)
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

    api_key = getattr(request.state, "api_key", None)
    referer = request.headers.get("referer") or ""
    source = "demo" if "pourintelligence" in referer or "localhost" in referer or "127.0.0.1" in referer else "api"

    if body.product == ProductType.MASONRY:
        stored_mix = body.masonry_design.model_dump(mode="json")
    else:
        stored_mix = body.mix_design.model_dump(mode="json")
    stored_mix["product"] = body.product.value

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
            "predictions": result["predictions"].model_dump(mode="json"),
            "recommended_mitigation": result["recommended_mitigation"],
            "location": {"name": name, "latitude": lat, "longitude": lon, "timezone": tz_name},
        }
    )

    logger.info(
        "pour_readiness request_id=%s status=%s check_id=%s",
        _request_id(request),
        result["go_no_go_status"].value,
        check_id,
    )

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
    )


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
    return FileResponse(FRONTEND / "index.html")


@app.get("/terms")
def terms() -> FileResponse:
    return FileResponse(FRONTEND / "terms.html")


@app.get("/style.css")
def stylesheet() -> FileResponse:
    return FileResponse(FRONTEND / "style.css", media_type="text/css")


@app.get("/app.js")
def javascript() -> FileResponse:
    return FileResponse(FRONTEND / "app.js", media_type="application/javascript")
