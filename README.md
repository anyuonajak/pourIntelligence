# Pour Intelligence

Go / no-go pour-readiness for concrete and masonry. The API turns a public weather forecast and ACI 305R / 306R guidance into a status, risk factors, cure-time estimate, and a mitigation note.

This is an advisory tool, not a substitute for project specifications or the engineer of record.

## What is in this MVP

- `POST /v1/pour-readiness` — location + pour time + mix → readiness score
- Open-Meteo hourly forecast (no API key)
- Uno / Menzel evaporation rate (ACI 305R nomograph equation)
- Cold-weather and freezing checks (ACI 306R)
- Simplified Nurse-Saul maturity for time-to-500-psi and 70% strength
- A single-page demo UI served from the same app

No auth and no database yet. Those are Week 1.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000). Interactive API docs: [http://localhost:8000/docs](http://localhost:8000/docs).

```bash
pytest
```

## Example request

```bash
curl -s http://localhost:8000/v1/pour-readiness \
  -H 'Content-Type: application/json' \
  -d '{
    "latitude": 37.8044,
    "longitude": -122.2712,
    "pour_date": "2026-09-10T08:00:00",
    "mix_design": {
      "cement_type": "Type_I",
      "target_psi": 4000,
      "thickness_inches": 4
    }
  }'
```

`zip_code` (US 5-digit) or `address` can replace lat/long. `pour_date` is jobsite local time.

## Status logic

| Status | When |
| --- | --- |
| `GO` | No risk factors |
| `WARNING` | Manageable issues (evaporation ≥ 0.2 lb/ft²/hr, air < 40°F in 48h, light rain, high wind/heat) |
| `NO_GO` | Freezing before ~500 psi, evaporation ≥ 0.5 lb/ft²/hr, or heavy rain at placement |

## Deploy

The FastAPI app serves both the API and the demo page, so one web service is enough.

**Render:** connect this GitHub repo and use `render.yaml`, or set the start command to `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`.

**Railway:** deploy from the repo. `Dockerfile` / `railway.toml` / `Procfile` are included.

Health check: `GET /health`.
