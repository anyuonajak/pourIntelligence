from datetime import datetime, timedelta

from backend.formulas import WeatherHour
from backend.formulas_masonry import evaluate_masonry
from backend.schemas import MasonryDesign


def _hours(start, n, temp, rh=50, wind=5, precip=0):
    return [WeatherHour(start + timedelta(hours=i), temp, rh, wind, precip) for i in range(n)]


def test_masonry_mild_day_is_go():
    start = datetime(2026, 5, 12, 8, 0, 0)
    result = evaluate_masonry(start, _hours(start, 72, 68.0), MasonryDesign())
    assert result["go_no_go_status"].value == "GO"


def test_masonry_below_20f_is_nogo():
    start = datetime(2026, 1, 8, 9, 0, 0)
    result = evaluate_masonry(start, _hours(start, 72, 15.0), MasonryDesign())
    assert result["go_no_go_status"].value == "NO_GO"
    assert "MASONRY_BELOW_20F" in result["risk_factors"]
