import re

from fastapi.testclient import TestClient

from backend.auth import verify_admin
from backend.config import get_settings
from backend.db import serialize_check, summarize_checks
from backend.main import app


def test_admin_login_page():
    client = TestClient(app)
    response = client.get("/admin/login")
    assert response.status_code == 200
    assert "Sign in" in response.text


def test_admin_api_requires_session():
    client = TestClient(app)
    response = client.get("/admin/api/checks")
    assert response.status_code == 401


def test_verify_admin(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse")
    assert verify_admin("admin", "correct-horse")
    assert not verify_admin("admin", "wrong")
    assert not verify_admin("nope", "correct-horse")
    get_settings.cache_clear()


def test_summarize_go_vs_outcome():
    rows = [
        {"go_no_go_status": "GO", "outcome": "success"},
        {"go_no_go_status": "GO", "outcome": "cracked"},
        {"go_no_go_status": "WARNING", "outcome": None},
    ]
    summary = summarize_checks(rows)
    assert summary["total_checks"] == 3
    assert summary["with_outcome"] == 2
    assert summary["pending_outcome"] == 1
    assert summary["go_and_success"] == 1
    assert summary["go_and_failed"] == 1
    assert summary["by_status"]["GO"] == 2


def test_serialize_check_includes_product_calculations():
    row = {
        "id": "check-1",
        "created_at": "2026-09-11T17:26:00Z",
        "source": "demo",
        "product": "masonry",
        "zip_code": "94612",
        "go_no_go_status": "WARNING",
        "risk_factors": ["COLD_WEATHER_MASONRY"],
        "location": {"name": "Oakland, CA 94612"},
        "pour_date": "2026-09-12T08:00:00",
        "mix_design": {"unit_type": "CMU", "mortar_type": "Type_N", "product": "masonry"},
        "metrics": {"ambient_temp_f": 38, "concrete_temp_f": 45},
        "predictions": {"protection_period_hours": 24},
        "recommended_mitigation": "Heat mixing water.",
        "concrete_temp_f": 45,
        "pour_outcomes": [],
    }
    out = serialize_check(row)
    assert out["product"] == "masonry"
    assert out["mix_design"]["unit_type"] == "CMU"
    assert out["metrics"]["ambient_temp_f"] == 38
    assert out["predictions"]["protection_period_hours"] == 24
    assert out["recommended_mitigation"] == "Heat mixing water."
    assert out["risk_factor_details"][0]["code"] == "COLD_WEATHER_MASONRY"
    assert "Cold-weather" in out["risk_factor_details"][0]["label"]


def test_serialize_check_defaults_product_from_mix():
    row = {
        "mix_design": {"product": "masonry", "unit_type": "brick"},
        "risk_factors": [],
        "location": {},
    }
    out = serialize_check(row)
    assert out["product"] == "masonry"


def test_demo_assets_are_cache_busted():
    client = TestClient(app)
    page = client.get("/")
    assert re.search(r'href="/style\.css\?v=\d+"', page.text)
    assert re.search(r'src="/app\.js\?v=\d+"', page.text)
    assert 'id="empty-kicker"' in page.text
    admin = client.get("/admin/login")
    assert re.search(r'href="/style\.css\?v=\d+"', admin.text)


def test_demo_js_isolates_products():
    client = TestClient(app)
    js = client.get("/app.js")
    assert "lastByProduct" in js.text
    assert "selectProduct" in js.text
    assert "pour-watch" in js.text
    assert js.headers.get("cache-control") == "no-cache"
