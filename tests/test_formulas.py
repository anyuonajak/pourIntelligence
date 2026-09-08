from datetime import datetime, timedelta

from backend.formulas import (
    WeatherHour,
    evaporation_rate_lb_ft2_hr,
    evaluate_pour,
    hours_to_maturity,
)
from backend.schemas import MixDesign


def test_uno_example_matches_nomograph_ballpark():
    """ACI example: 87°F concrete, 80°F air, 50% RH, 12 mph → ~0.25 lb/ft²/hr."""
    rate = evaporation_rate_lb_ft2_hr(87, 80, 50, 12)
    assert 0.22 <= rate <= 0.27


def test_hot_dry_wind_is_warning_or_nogo():
    start = datetime(2026, 7, 15, 14, 0, 0)
    hourly = [
        WeatherHour(start + timedelta(hours=i), 100.0, 12.0, 18.0, 0.0) for i in range(72)
    ]
    result = evaluate_pour(start, hourly, MixDesign())
    assert result["go_no_go_status"].value in {"WARNING", "NO_GO"}
    assert "HIGH_EVAPORATION_RATE" in result["risk_factors"] or "EXTREME_EVAPORATION_RATE" in result["risk_factors"]


def test_mild_day_is_go():
    start = datetime(2026, 5, 12, 8, 0, 0)
    hourly = [
        WeatherHour(start + timedelta(hours=i), 68.0, 60.0, 4.0, 0.0) for i in range(72)
    ]
    result = evaluate_pour(start, hourly, MixDesign())
    assert result["go_no_go_status"].value == "GO"
    assert result["risk_factors"] == []


def test_freezing_before_set_is_nogo():
    start = datetime(2026, 1, 8, 9, 0, 0)
    hourly = [
        WeatherHour(start + timedelta(hours=i), 22.0, 70.0, 6.0, 0.0) for i in range(72)
    ]
    result = evaluate_pour(start, hourly, MixDesign())
    assert result["go_no_go_status"].value == "NO_GO"
    assert "FREEZING_BEFORE_500_PSI" in result["risk_factors"]


def test_maturity_reaches_500_psi_around_half_a_day_at_73f():
    start = datetime(2026, 6, 1, 8, 0, 0)
    hourly = [
        WeatherHour(start + timedelta(hours=i), 73.0, 50.0, 5.0, 0.0) for i in range(48)
    ]
    hours = hours_to_maturity(0, hourly, 500.0)
    assert hours is not None
    assert 10 <= hours <= 14


def test_trace_rain_does_not_warn():
    start = datetime(2026, 5, 12, 8, 0, 0)
    hourly = [
        WeatherHour(start + timedelta(hours=i), 68.0, 60.0, 4.0, 0.004) for i in range(72)
    ]
    result = evaluate_pour(start, hourly, MixDesign())
    assert result["go_no_go_status"].value == "GO"
    assert "RAIN_DURING_POUR" not in result["risk_factors"]
