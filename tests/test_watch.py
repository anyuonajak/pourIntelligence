from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.formulas import WeatherHour
from backend.main import app
from backend.watch import diff_forecast, is_watch_open, watch_tail_hours


def _metrics(**overrides):
    base = {
        "ambient_temp_f": 70.0,
        "wind_speed_mph": 4.0,
        "relative_humidity_pct": 50.0,
        "calculated_evaporation_rate_lbs_sqft_hr": 0.05,
        "precipitation_in": 0.0,
        "min_temp_next_24h_f": 55.0,
    }
    base.update(overrides)
    return base


def test_no_change_when_forecast_is_stable():
    metrics = _metrics()
    changes = diff_forecast(
        previous_status="GO",
        previous_risks=[],
        previous_metrics=metrics,
        status="GO",
        risks=[],
        metrics=metrics,
    )
    assert changes == []


def test_status_flip_is_material():
    changes = diff_forecast(
        previous_status="GO",
        previous_risks=[],
        previous_metrics=_metrics(),
        status="WARNING",
        risks=["HIGH_WIND"],
        metrics=_metrics(wind_speed_mph=16.0),
    )
    codes = {item["code"] for item in changes}
    assert "STATUS" in codes
    assert "RISK_ADDED_HIGH_WIND" in codes
    assert "METRIC_WIND_SPEED_MPH" in codes


def test_small_temp_wiggle_is_ignored():
    changes = diff_forecast(
        previous_status="GO",
        previous_risks=[],
        previous_metrics=_metrics(ambient_temp_f=70.0),
        status="GO",
        risks=[],
        metrics=_metrics(ambient_temp_f=72.0),
    )
    assert changes == []


def test_watch_tail_uses_protection_period_within_bounds():
    assert watch_tail_hours(None) == 24
    assert watch_tail_hours({"protection_period_hours": 12}) == 24
    assert watch_tail_hours({"estimated_time_to_500_psi_hours": 30}) == 30
    assert watch_tail_hours({"estimated_time_to_500_psi_hours": 200}) == 48


def test_watch_closes_after_the_protection_period():
    pour = datetime(2026, 5, 12, 8, 0, 0)
    assert is_watch_open(pour, None, pour - timedelta(hours=6))
    assert is_watch_open(pour, None, pour + timedelta(hours=23))
    assert not is_watch_open(pour, None, pour + timedelta(hours=25))


@pytest.fixture
def stub_weather(monkeypatch):
    """Serve a fixed forecast so the watch endpoint never touches the network."""

    def _install(temp_f: float):
        async def fake_resolve(*_args, **_kwargs):
            return 37.8, -122.27, "Oakland, CA 94612"

        async def fake_hourly(_lat, _lon, pour_date, **_kwargs):
            hours = [
                WeatherHour(pour_date + timedelta(hours=i), temp_f, 50.0, 4.0, 0.0) for i in range(72)
            ]
            return hours, "America/Los_Angeles"

        monkeypatch.setattr(main, "resolve_location", fake_resolve)
        monkeypatch.setattr(main, "fetch_hourly", fake_hourly)

    return _install


def _watch_body(**overrides):
    pour_date = (datetime.now() + timedelta(days=1)).replace(microsecond=0)
    body = {"zip_code": "94612", "pour_date": pour_date.isoformat(), "product": "concrete"}
    body.update(overrides)
    return body


def test_watch_reports_no_change_against_a_matching_baseline(stub_weather):
    stub_weather(70.0)
    client = TestClient(app)
    first = client.post("/v1/pour-readiness", json=_watch_body())
    assert first.status_code == 200
    stamp = first.json()
    assert stamp["watching"] is True

    second = client.post(
        "/v1/pour-watch",
        json=_watch_body(
            baseline={
                "go_no_go_status": stamp["go_no_go_status"],
                "risk_factors": stamp["risk_factors"],
                "metrics": stamp["metrics"],
            }
        ),
    )
    assert second.status_code == 200
    payload = second.json()
    assert payload["changed"] is False
    assert payload["changes"] == []
    assert payload["watching"] is True
    assert payload["next_check_seconds"] > 0


def test_watch_reports_a_status_flip_when_the_forecast_moves(stub_weather):
    stub_weather(25.0)
    client = TestClient(app)
    response = client.post(
        "/v1/pour-watch",
        json=_watch_body(
            baseline={
                "go_no_go_status": "GO",
                "risk_factors": [],
                "metrics": {
                    "ambient_temp_f": 70.0,
                    "relative_humidity_pct": 50.0,
                    "wind_speed_mph": 4.0,
                    "precipitation_in": 0.0,
                    "concrete_temp_f": 75.0,
                    "calculated_evaporation_rate_lbs_sqft_hr": 0.05,
                    "min_temp_next_24h_f": 60.0,
                    "min_temp_next_48h_f": 60.0,
                },
            }
        ),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["changed"] is True
    codes = {change["code"] for change in payload["changes"]}
    assert "STATUS" in codes
    assert "CROSS_32" in codes
    assert payload["go_no_go_status"] == "NO_GO"


def test_freeze_line_crossing_is_material():
    changes = diff_forecast(
        previous_status="WARNING",
        previous_risks=["COLD_WEATHER_CONDITIONS"],
        previous_metrics=_metrics(min_temp_next_24h_f=36.0),
        status="NO_GO",
        risks=["FREEZING_BEFORE_500_PSI"],
        metrics=_metrics(min_temp_next_24h_f=28.0),
    )
    assert any(item["code"] == "CROSS_32" for item in changes)
    assert any(item["code"] == "STATUS" for item in changes)
