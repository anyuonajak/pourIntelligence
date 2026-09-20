import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import weather
from backend.db import cache_key_for_weather
from backend.main import app
from backend.weather import WEATHER_UNAVAILABLE_MESSAGE, WeatherUnavailable


def _payload():
    start = datetime.now().replace(minute=0, second=0, microsecond=0)
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(48)]
    return {
        "timezone": "America/Chicago",
        "hourly": {
            "time": times,
            "temperature_2m": [70.0] * 48,
            "relative_humidity_2m": [50.0] * 48,
            "wind_speed_10m": [4.0] * 48,
            "precipitation": [0.0] * 48,
        },
    }


def _pour_date():
    return (datetime.now() + timedelta(days=1)).replace(microsecond=0)


class _StatusClient:
    def __init__(self, status_code: int, body: str = "rate limited"):
        self.status_code = status_code
        self.body = body
        self.calls = 0

    def factory(self):
        parent = self

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, params=None):
                parent.calls += 1
                request = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast?latitude=44.98")
                return httpx.Response(parent.status_code, request=request, text=parent.body)

        return _Client


@pytest.fixture(autouse=True)
def _clean_weather_cache():
    weather.HTTP_COOLDOWN_SECONDS = 0
    weather.clear_memory_cache()
    yield
    weather.clear_memory_cache()
    weather.HTTP_COOLDOWN_SECONDS = 1.5


def test_429_with_cache_returns_cached_forecast(monkeypatch):
    pour = _pour_date()
    key = cache_key_for_weather(44.9835, -93.2683, pour.date().isoformat(), "forecast")
    weather._MEMORY[key] = (datetime.now(timezone.utc) - timedelta(minutes=50), _payload())
    boom = _StatusClient(429, "Client error '429 Too Many Requests' for url 'https://api.open-meteo.com/v1/forecast'")
    monkeypatch.setattr(weather.httpx, "AsyncClient", boom.factory())

    hours, tz_name = asyncio.run(weather.fetch_hourly(44.9835, -93.2683, pour, cache_ttl_minutes=10))

    assert boom.calls == 1
    assert hours
    assert tz_name == "America/Chicago"
    assert hours[0].temp_f == 70.0


def test_429_without_cache_is_generic(monkeypatch):
    boom = _StatusClient(
        429,
        "Client error '429 Too Many Requests' for url 'https://api.open-meteo.com/v1/forecast?latitude=44.98' "
        "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429",
    )
    monkeypatch.setattr(weather.httpx, "AsyncClient", boom.factory())

    with pytest.raises(WeatherUnavailable) as caught:
        asyncio.run(weather.fetch_hourly(44.9835, -93.2683, _pour_date()))

    assert caught.value.error == "weather_unavailable"
    assert caught.value.message == WEATHER_UNAVAILABLE_MESSAGE
    assert "open-meteo" not in str(caught.value).lower()
    assert "http" not in str(caught.value).lower()

    client = TestClient(app)
    response = client.post(
        "/v1/pour-readiness",
        json={
            "latitude": 44.9835,
            "longitude": -93.2683,
            "pour_date": _pour_date().isoformat(),
            "product": "concrete",
        },
    )
    assert response.status_code == 503
    body = response.json()
    assert body == {"error": "weather_unavailable", "message": WEATHER_UNAVAILABLE_MESSAGE}
    text = response.text.lower()
    assert "open-meteo" not in text
    assert "http://" not in text
    assert "https://" not in text
    assert "mozilla" not in text
    assert "api.open-meteo.com" not in text


def test_warm_cache_does_not_hit_upstream(monkeypatch):
    pour = _pour_date()
    key = cache_key_for_weather(44.9835, -93.2683, pour.date().isoformat(), "forecast")
    weather._memory_set(key, _payload())

    def _should_not_run(*_args, **_kwargs):
        raise AssertionError("warm cache should not call Open-Meteo")

    monkeypatch.setattr(weather.httpx, "AsyncClient", _should_not_run)
    hours, tz_name = asyncio.run(weather.fetch_hourly(44.9835, -93.2683, pour))
    assert hours
    assert tz_name == "America/Chicago"


def test_timeout_with_cache_returns_cached_forecast(monkeypatch):
    pour = _pour_date()
    key = cache_key_for_weather(44.9835, -93.2683, pour.date().isoformat(), "forecast")
    weather._MEMORY[key] = (datetime.now(timezone.utc) - timedelta(minutes=50), _payload())

    class _Timeout:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None):
            raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(weather.httpx, "AsyncClient", _Timeout)
    hours, tz_name = asyncio.run(weather.fetch_hourly(44.9835, -93.2683, pour, cache_ttl_minutes=10))
    assert hours
    assert tz_name == "America/Chicago"


def test_second_pour_watch_with_cache_does_not_http(monkeypatch):
    pour = _pour_date()
    key = cache_key_for_weather(44.9835, -93.2683, pour.date().isoformat(), "forecast")
    weather._memory_set(key, _payload())

    def _should_not_run(*_args, **_kwargs):
        raise AssertionError("pour_watch should read cache and not call Open-Meteo")

    monkeypatch.setattr(weather.httpx, "AsyncClient", _should_not_run)
    client = TestClient(app)
    first = client.post(
        "/v1/pour-watch",
        json={
            "latitude": 44.9835,
            "longitude": -93.2683,
            "pour_date": pour.isoformat(),
            "product": "concrete",
        },
    )
    assert first.status_code == 200
    second = client.post(
        "/v1/pour-watch",
        json={
            "latitude": 44.9835,
            "longitude": -93.2683,
            "pour_date": pour.isoformat(),
            "product": "concrete",
        },
    )
    assert second.status_code == 200


def test_pour_watch_429_without_cache_is_generic(monkeypatch):
    boom = _StatusClient(
        429,
        "Client error '429 Too Many Requests' for url 'https://api.open-meteo.com/v1/forecast'",
    )
    monkeypatch.setattr(weather.httpx, "AsyncClient", boom.factory())
    client = TestClient(app)
    response = client.post(
        "/v1/pour-watch",
        json={
            "latitude": 44.9835,
            "longitude": -93.2683,
            "pour_date": _pour_date().isoformat(),
            "product": "concrete",
        },
    )
    assert response.status_code == 503
    body = response.json()
    assert body == {"error": "weather_unavailable", "message": WEATHER_UNAVAILABLE_MESSAGE}
    text = response.text.lower()
    assert "open-meteo" not in text
    assert "http://" not in text
    assert "https://" not in text
    assert "$20" not in text
    assert "$100" not in text
