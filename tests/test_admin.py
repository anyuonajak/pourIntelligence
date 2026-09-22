import re
from datetime import datetime, timedelta, timezone

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
    assert client.get("/admin/api/orgs").status_code == 401
    assert client.post(
        "/admin/api/orgs",
        json={"company_name": "Acme", "email": "gc@example.com", "password": "password12"},
    ).status_code == 401


def _admin_client(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
    get_settings.cache_clear()
    client = TestClient(app)
    logged_in = client.post("/admin/api/login", json={"username": "admin", "password": "admin-pass"})
    assert logged_in.status_code == 200
    return client


def test_admin_onboard_org(monkeypatch):
    from backend import db

    db.clear_account_memory()
    monkeypatch.setattr(db, "get_client", lambda: None)
    client = _admin_client(monkeypatch)
    created = client.post(
        "/admin/api/orgs",
        json={"company_name": "Acme Builders", "email": "Gc@Acme.test", "password": "password12"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["email"] == "gc@acme.test"
    assert body["display_name"] == "Acme Builders"
    assert body["kind"] == "org"
    assert body["plan"] == "free"
    trial = datetime.fromisoformat(str(body["trial_ends_at"]).replace("Z", "+00:00"))
    if trial.tzinfo is None:
        trial = trial.replace(tzinfo=timezone.utc)
    delta = trial - datetime.now(timezone.utc)
    assert timedelta(days=13) < delta < timedelta(days=15)
    assert body["password"] == "password12"
    assert "$20" not in created.text
    assert "$100" not in created.text
    listed = client.get("/admin/api/orgs")
    assert listed.status_code == 200
    orgs = listed.json()["orgs"]
    assert len(orgs) == 1
    assert orgs[0]["email"] == "gc@acme.test"
    assert "password_hash" not in orgs[0]
    assert "password" not in orgs[0]
    assert "$20" not in listed.text
    again = client.post(
        "/admin/api/orgs",
        json={"company_name": "Acme Builders", "email": "gc@acme.test", "password": "password12"},
    )
    assert again.status_code == 409
    get_settings.cache_clear()
    db.clear_account_memory()


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


def test_serialize_check_watch_state_follows_customer():
    watching = serialize_check({"watching": True, "risk_factors": [], "location": {}, "mix_design": {}})
    closed = serialize_check({"watching": False, "risk_factors": [], "location": {}, "mix_design": {}})
    assert watching["watching"] is True
    assert closed["watching"] is False


def test_serialize_check_includes_subscriber_email():
    row = {
        "id": "check-2",
        "risk_factors": [],
        "location": {"name": "Denver, CO 80202"},
        "mix_design": {},
        "subscriber_id": "sub-1",
        "watch_subscribers": {"id": "sub-1", "email": "ops@example.com", "unsub_token": "secret-token"},
    }
    out = serialize_check(row)
    assert out["subscriber_email"] == "ops@example.com"
    assert out["subscriber_id"] == "sub-1"
    assert "unsub_token" not in out
    assert "subscriber" not in out


def test_admin_onboard_org_ui_has_no_prices():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "frontend"
    html = (root / "admin.html").read_text()
    js = (root / "admin.js").read_text()
    assert "Onboard org" in html
    assert 'data-tab="orgs"' in html
    assert "/admin/api/orgs" in js
    assert "$20" not in html
    assert "$100" not in html
    assert "$20" not in js
    assert "$100" not in js


def test_admin_js_does_not_close_watches():
    client = TestClient(app)
    js = client.get("/admin.js").text
    assert "Close watch" not in js
    assert "data-close-check" not in js


def test_demo_assets_are_cache_busted():
    client = TestClient(app)
    landing = client.get("/")
    assert re.search(r'href="/style\.css\?v=\d+"', landing.text)
    page = client.get("/app")
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
    assert "watchRoster" in js.text
    assert "isPersistedCheckId" in js.text
    assert "/v1/pour-watch/${checkId}/close" in js.text
    assert js.headers.get("cache-control") == "no-cache"
