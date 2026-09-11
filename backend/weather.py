"""Open-Meteo forecast client plus ZIP / address geocoding."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import httpx
from fastapi import HTTPException

from .config import get_settings
from .db import cache_key_for_weather, get_weather_cache, set_weather_cache
from .formulas import WeatherHour

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
ZIP_URL = "https://api.zippopotam.us/us/{zip_code}"

HOURLY_VARS = "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation"
TIMEOUT = 20.0
US_ZIP = re.compile(r"^\d{5}$")


async def geocode_zip(zip_code: str) -> tuple[float, float, str]:
    cleaned = zip_code.strip()
    if not US_ZIP.match(cleaned):
        raise HTTPException(status_code=422, detail="zip_code must be a 5-digit US ZIP.")
    url = ZIP_URL.format(zip_code=cleaned)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.get(url)
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail=f"ZIP code {cleaned} was not found.")
    response.raise_for_status()
    payload = response.json()
    place = payload["places"][0]
    name = f"{place['place name']}, {place['state abbreviation']} {cleaned}"
    return float(place["latitude"]), float(place["longitude"]), name


async def geocode_address(address: str) -> tuple[float, float, str]:
    query = address.strip()
    if not query:
        raise HTTPException(status_code=422, detail="address is empty.")
    params = {"name": query, "count": 1, "language": "en", "format": "json"}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.get(GEOCODE_URL, params=params)
    response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        raise HTTPException(status_code=404, detail=f"Could not geocode '{query}'.")
    hit = results[0]
    parts = [hit.get("name"), hit.get("admin1"), hit.get("country_code")]
    name = ", ".join(part for part in parts if part)
    return float(hit["latitude"]), float(hit["longitude"]), name


async def resolve_location(
    latitude: float | None,
    longitude: float | None,
    zip_code: str | None,
    address: str | None,
) -> tuple[float, float, str]:
    if zip_code and zip_code.strip() and (latitude is None or longitude is None):
        return await geocode_zip(zip_code)
    if address and address.strip() and (latitude is None or longitude is None):
        return await geocode_address(address)
    if latitude is not None and longitude is not None:
        name = f"{latitude:.4f}, {longitude:.4f}"
        if zip_code and zip_code.strip():
            try:
                _, _, zip_name = await geocode_zip(zip_code)
                name = zip_name
            except HTTPException:
                pass
        elif address and address.strip():
            name = address.strip()
        return latitude, longitude, name
    raise HTTPException(status_code=422, detail="A location is required.")


def _parse_hourly(payload: dict) -> tuple[list[WeatherHour], str | None]:
    block = payload.get("hourly") or {}
    times = block.get("time") or []
    temps = block.get("temperature_2m") or []
    rhs = block.get("relative_humidity_2m") or []
    winds = block.get("wind_speed_10m") or []
    precips = block.get("precipitation") or []
    tz_name = payload.get("timezone")
    hours: list[WeatherHour] = []
    for i, stamp in enumerate(times):
        if i >= len(temps) or temps[i] is None:
            continue
        hours.append(
            WeatherHour(
                time=datetime.fromisoformat(stamp),
                temp_f=float(temps[i]),
                rh_pct=float(rhs[i]) if i < len(rhs) and rhs[i] is not None else 50.0,
                wind_mph=float(winds[i]) if i < len(winds) and winds[i] is not None else 0.0,
                precip_in=float(precips[i]) if i < len(precips) and precips[i] is not None else 0.0,
            )
        )
    return hours, tz_name


async def fetch_hourly(latitude: float, longitude: float, pour_date: datetime) -> tuple[list[WeatherHour], str | None]:
    pour_day = pour_date.date()
    today = date.today()
    too_far = today + timedelta(days=15)
    far_past = today - timedelta(days=5)

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": HOURLY_VARS,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "auto",
    }

    if pour_day > too_far:
        raise HTTPException(
            status_code=422,
            detail="Pour date is more than 16 days out. Open-Meteo forecast only covers ~16 days.",
        )

    use_archive = pour_day < far_past
    url = ARCHIVE_URL if use_archive else FORECAST_URL
    if use_archive:
        params["start_date"] = (pour_day - timedelta(days=1)).isoformat()
        params["end_date"] = (pour_day + timedelta(days=7)).isoformat()
    else:
        params["forecast_days"] = 16
        params["past_days"] = 3

    settings = get_settings()
    mode = "archive" if use_archive else "forecast"
    key = cache_key_for_weather(latitude, longitude, pour_day.isoformat(), mode)
    cached = get_weather_cache(key)
    if cached:
        hours, tz_name = _parse_hourly(cached)
        if hours:
            return hours, tz_name

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
            hours, tz_name = _parse_hourly(payload)
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Weather service error: {exc}") from exc

    if hours:
        set_weather_cache(key, payload, settings.weather_cache_minutes)

    if not hours:
        raise HTTPException(status_code=502, detail="Weather service returned no hourly data.")
    return hours, tz_name
