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
- `POST /v1/pour-watch` — re-checks a submitted ticket and reports only material forecast moves
- Watch roster on the demo and in `/admin` (watching / paused / closed)
- `POST /v1/pour-outcomes` — success / cracked / delayed / other, tied to a check
- Supabase Postgres (checks, outcomes, weather cache, API keys)
- Demo rate limit (30/hour/IP) and optional `X-API-Key` for vendors
- Terms at `/terms`
- Admin at `/admin` (checks vs outcomes, API key mint/revoke)
- Two products: concrete slabs (ACI 305R/306R) and masonry (TMS 602 / ACI 530.1)

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

## Monitoring

Submitting a ticket opens a watch. The demo polls `POST /v1/pour-watch`, which re-runs the same evaluation and returns
only changes big enough to matter: a status flip, a risk factor appearing or clearing, a crossing of the 32°F or 40°F
line, or a swing of 5°F air, 5 mph wind, 12% RH, 0.05 lb/ft²/hr evaporation, or 0.03 in rain. Everything else is a
heartbeat (`last checked …, no material change`).

The watch closes at pour time plus the protection period (24–48h). Changes are appended to `watch_events` on the check
and shown in `/admin`.

Browser polling only runs while the page is open. Push delivery (SMS, email, webhooks) and a server-side scheduler are
not built yet — Render's free web service sleeps, so background monitoring needs a worker or a Supabase cron.

`WATCH_POLL_SECONDS` (default 180) sets the heartbeat, `WATCH_WEATHER_CACHE_MINUTES` (default 10) how fresh the forecast
must be on a watch poll, and `WATCH_RATE_LIMIT_PER_HOUR` (default 240) the demo poll quota.

## Deploy

The FastAPI app serves both the API and the demo page, so one web service is enough.

**Render:** connect this GitHub repo and use `render.yaml`, or set the start command to `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`.

**Railway:** deploy from the repo. `Dockerfile` / `railway.toml` / `Procfile` are included.

Health check: `GET /health`.

## Supabase

1. Run `supabase/migrations/001_init.sql`, then `002_product.sql`, then `003_watch.sql` in the Supabase SQL editor.
2. Set env vars (Render already has `project_url` and `service_role`; those names work). Preferred names: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.
3. Optional: `ALLOWED_ORIGINS=https://pourintelligence.onrender.com`
4. Set `ADMIN_PASSWORD` (and optionally `ADMIN_USERNAME`, `SESSION_SECRET`) on Render.

Sign in at `/admin`. Mint vendor keys there, then call the API as a second client:

```bash
export POUR_API_URL=https://pourintelligence.onrender.com
export POUR_API_KEY='pi_live_...'
python scripts/vendor_check.py
```
