from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import db, main
from backend.formulas import WeatherHour
from backend.main import app


@pytest.fixture
def memory_accounts(monkeypatch):
    db.clear_account_memory()
    monkeypatch.setattr(db, "get_client", lambda: None)
    yield
    db.clear_account_memory()


@pytest.fixture
def stub_weather(monkeypatch):
    async def fake_resolve(*_args, **_kwargs):
        return 37.8, -122.27, "Oakland, CA 94612"

    async def fake_hourly(_lat, _lon, pour_date, **_kwargs):
        hours = [WeatherHour(pour_date + timedelta(hours=i), 70.0, 50.0, 4.0, 0.0) for i in range(72)]
        return hours, "America/Los_Angeles"

    monkeypatch.setattr(main, "resolve_location", fake_resolve)
    monkeypatch.setattr(main, "fetch_hourly", fake_hourly)


def _watch_body():
    pour_date = (datetime.now() + timedelta(days=1)).replace(microsecond=0)
    return {"zip_code": "94612", "pour_date": pour_date.isoformat(), "product": "concrete"}


def test_signup_login_me(memory_accounts):
    client = TestClient(app)
    created = client.post(
        "/v1/auth/signup",
        json={"email": "Pat@Example.com", "password": "password12"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["email"] == "pat@example.com"
    assert body["plan"] == "free"
    assert body["kind"] == "individual"
    assert body.get("trial_ends_at")
    assert "$" not in created.text
    assert "20" not in str(body.get("plan"))
    me = client.get("/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "pat@example.com"
    assert "$20" not in me.text
    assert "$100" not in me.text
    trial = datetime.fromisoformat(str(me.json()["trial_ends_at"]).replace("Z", "+00:00"))
    if trial.tzinfo is None:
        trial = trial.replace(tzinfo=timezone.utc)
    delta = trial - datetime.now(timezone.utc)
    assert timedelta(days=29) < delta < timedelta(days=31)
    logged_out = client.post("/v1/auth/logout")
    assert logged_out.status_code == 200
    assert client.get("/v1/auth/me").status_code == 401
    again = client.post(
        "/v1/auth/login",
        json={"email": "pat@example.com", "password": "password12"},
    )
    assert again.status_code == 200
    assert client.get("/v1/auth/me").status_code == 200


def test_org_login_allows_two_sessions(memory_accounts):
    from backend.auth import hash_password, new_session_nonce
    from backend.db import create_account

    create_account(
        "gc@acme.test",
        hash_password("password12"),
        session_nonce=new_session_nonce(),
        display_name="Acme Builders",
        kind="org",
    )
    first = TestClient(app)
    second = TestClient(app)
    assert first.post("/v1/auth/login", json={"email": "gc@acme.test", "password": "password12"}).status_code == 200
    assert second.post("/v1/auth/login", json={"email": "gc@acme.test", "password": "password12"}).status_code == 200
    assert first.get("/v1/auth/me").status_code == 200
    assert second.get("/v1/auth/me").status_code == 200
    assert first.get("/v1/auth/me").json()["kind"] == "org"
    first.post("/v1/auth/logout")
    assert first.get("/v1/auth/me").status_code == 401
    assert second.get("/v1/auth/me").status_code == 200


def test_second_login_invalidates_first(memory_accounts):
    first = TestClient(app)
    created = first.post(
        "/v1/auth/signup",
        json={"email": "one@example.com", "password": "password12"},
    )
    assert created.status_code == 200
    assert first.get("/v1/auth/me").status_code == 200
    second = TestClient(app)
    logged_in = second.post(
        "/v1/auth/login",
        json={"email": "one@example.com", "password": "password12"},
    )
    assert logged_in.status_code == 200
    assert second.get("/v1/auth/me").status_code == 200
    assert first.get("/v1/auth/me").status_code == 401


def test_sixth_watch_does_not_stay_watching(memory_accounts, stub_weather):
    client = TestClient(app)
    signup = client.post(
        "/v1/auth/signup",
        json={"email": "watches@example.com", "password": "password12"},
    )
    assert signup.status_code == 200
    for _ in range(5):
        response = client.post("/v1/pour-readiness", json=_watch_body())
        assert response.status_code == 200
        payload = response.json()
        assert payload["watching"] is True
        assert payload["watch_limit_reached"] is False
    sixth = client.post("/v1/pour-readiness", json=_watch_body())
    assert sixth.status_code == 200
    payload = sixth.json()
    assert payload["watching"] is False
    assert payload["watch_limit_reached"] is True
    assert "$20" not in sixth.text
    assert "$100" not in sixth.text


def test_landing_has_no_prices():
    client = TestClient(app)
    landing = client.get("/")
    assert landing.status_code == 200
    text = landing.text
    assert "Monolith" in text
    assert "Get started" in text
    assert "Sign in" in text
    assert "$20" not in text
    assert "$100" not in text
    assert "watch 3" not in text.lower()
    assert "watch 5" not in text.lower()
    assert "aci" not in text.lower()
    assert "how it works" not in text.lower()
    app_page = client.get("/app")
    assert app_page.status_code == 200
    assert "$20" not in app_page.text
    assert "$100" not in app_page.text
    js = client.get("/app.js").text
    assert "credentials: \"include\"" in js or "credentials: 'include'" in js
    assert "/v1/auth/me" in js
    assert "You can watch 5 sites." in app_page.text
    assert "$20" not in js
    assert "$100" not in js
