from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .formulas import evaluate_pour
from .schemas import LocationInfo, PourReadinessRequest, PourReadinessResponse
from .weather import fetch_hourly, resolve_location

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

app = FastAPI(
    title="Pour Intelligence",
    version="0.1.0",
    description=(
        "Go / no-go pour-readiness for concrete and masonry from public weather "
        "forecasts and ACI 305R / 306R guidance. Advisory only."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "pour-intelligence"}


@app.post("/v1/pour-readiness", response_model=PourReadinessResponse)
async def pour_readiness(request: PourReadinessRequest) -> PourReadinessResponse:
    lat, lon, name = await resolve_location(
        request.latitude,
        request.longitude,
        request.zip_code,
        request.address,
    )
    hourly, tz_name = await fetch_hourly(lat, lon, request.pour_date)
    try:
        result = evaluate_pour(
            pour_time=request.pour_date,
            hourly=hourly,
            mix=request.mix_design,
            concrete_temp_f=request.concrete_temp_f,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return PourReadinessResponse(
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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/style.css")
def stylesheet() -> FileResponse:
    return FileResponse(FRONTEND / "style.css", media_type="text/css")


@app.get("/app.js")
def javascript() -> FileResponse:
    return FileResponse(FRONTEND / "app.js", media_type="application/javascript")
