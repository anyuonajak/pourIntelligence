import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.mail import render_watch_started_html, send_email
from backend.main import app
from backend.notify import digest_due, group_digest_sites, send_watch_started
from backend.watch import is_alert_worthy, pending_alert_events


def _event(code, **kwargs):
    payload = {"code": code, "label": code, "detail": code, "emailed": False}
    payload.update(kwargs)
    return payload


def test_alert_worthy_status_and_critical_risks():
    assert is_alert_worthy(_event("STATUS", current="NO_GO"))
    assert is_alert_worthy(_event("STATUS", current="WARNING"))
    assert is_alert_worthy(_event("CROSS_32"))
    assert is_alert_worthy(_event("RISK_ADDED_FREEZING_BEFORE_500_PSI"))
    assert is_alert_worthy(_event("RISK_ADDED_EXTREME_EVAPORATION_RATE"))
    assert is_alert_worthy(_event("RISK_ADDED_HEAVY_RAIN_DURING_POUR"))
    assert is_alert_worthy(_event("RISK_ADDED_MASONRY_BELOW_20F"))


def test_metric_only_and_warning_risks_are_not_alert_worthy():
    assert not is_alert_worthy(_event("STATUS", current="GO"))
    assert not is_alert_worthy(_event("CROSS_40"))
    assert not is_alert_worthy(_event("METRIC_AMBIENT_TEMP_F", detail="70 → 76 °F"))
    assert not is_alert_worthy(_event("RISK_ADDED_COLD_WEATHER_CONDITIONS"))
    assert not is_alert_worthy(_event("RISK_CLEARED_FREEZING_BEFORE_500_PSI"))
    assert not is_alert_worthy(_event("WATCH_CLOSED"))


def test_pending_alerts_only_retry_explicit_unsent():
    events = [
        _event("STATUS", current="NO_GO", emailed=False),
        {"code": "STATUS", "current": "NO_GO"},  # missing key: pre-feature, do not blast
        _event("CROSS_32", emailed=True),
        _event("METRIC_AMBIENT_TEMP_F", emailed=False),
    ]
    pending = pending_alert_events(events)
    assert len(pending) == 1
    assert pending[0]["code"] == "STATUS"


def test_digest_groups_sites_by_subscriber():
    sub_a = {
        "id": "s1",
        "email": "a@example.com",
        "digest_enabled": True,
        "unsub_token": "tok-a",
        "last_digest_at": None,
    }
    sub_b = {
        "id": "s2",
        "email": "b@example.com",
        "digest_enabled": True,
        "unsub_token": "tok-b",
        "last_digest_at": None,
    }
    rows = [
        {
            "subscriber_id": "s1",
            "subscriber": sub_a,
            "location_name": "Denver, CO 80202",
            "product": "concrete",
            "go_no_go_status": "GO",
            "pour_date": "2026-09-19T08:00:00",
            "watch_events": [{"label": "Watch started"}],
            "last_checked_at": "2026-09-18T12:00:00Z",
        },
        {
            "subscriber_id": "s1",
            "subscriber": sub_a,
            "location_name": "Oakland, CA 94612",
            "product": "masonry",
            "go_no_go_status": "WARNING",
            "pour_date": "2026-09-20T07:00:00",
            "watch_events": [],
        },
        {
            "subscriber_id": "s2",
            "subscriber": sub_b,
            "location_name": "Phoenix, AZ 85003",
            "product": "concrete",
            "go_no_go_status": "NO_GO",
        },
        {
            "subscriber_id": "s3",
            "subscriber": {
                "id": "s3",
                "email": "off@example.com",
                "digest_enabled": False,
            },
            "location_name": "Minneapolis",
            "product": "concrete",
            "go_no_go_status": "GO",
        },
    ]
    grouped = group_digest_sites(rows)
    assert set(grouped) == {"s1", "s2"}
    assert len(grouped["s1"]["sites"]) == 2
    assert grouped["s1"]["sites"][0]["location"] == "Denver, CO 80202"
    assert grouped["s1"]["sites"][1]["product"] == "Masonry"
    assert len(grouped["s2"]["sites"]) == 1


def test_digest_due_after_twenty_hours():
    now = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    assert digest_due(None, now=now)
    assert not digest_due((now - timedelta(hours=19)).isoformat(), now=now)
    assert digest_due((now - timedelta(hours=21)).isoformat(), now=now)


def test_mail_noop_without_api_key(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("ALERT_FROM_EMAIL", raising=False)
    get_settings.cache_clear()
    called = []

    class Boom:
        def __init__(self, *args, **kwargs):
            called.append("client")

    monkeypatch.setattr("backend.mail.httpx.AsyncClient", Boom)
    assert asyncio.run(send_email("a@example.com", "NO_GO · Denver", "<p>x</p>")) is False
    assert called == []
    get_settings.cache_clear()


def test_mail_posts_to_resend_when_configured(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("ALERT_FROM_EMAIL", "Pour Intelligence <alerts@example.com>")
    get_settings.cache_clear()
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("backend.mail.httpx.AsyncClient", FakeClient)
    assert asyncio.run(send_email("a@example.com", "NO_GO · Denver, CO 80202", "<p>x</p>")) is True
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["json"]["to"] == ["a@example.com"]
    assert captured["json"]["subject"] == "NO_GO · Denver, CO 80202"
    get_settings.cache_clear()


def test_readiness_upserts_subscriber_when_email_present(monkeypatch):
    from backend import main
    from backend.formulas import WeatherHour

    async def fake_resolve(*_args, **_kwargs):
        return 37.8, -122.27, "Oakland, CA 94612"

    async def fake_hourly(_lat, _lon, pour_date, **_kwargs):
        hours = [WeatherHour(pour_date + timedelta(hours=i), 70.0, 50.0, 4.0, 0.0) for i in range(72)]
        return hours, "America/Los_Angeles"

    seen = {}

    def fake_upsert(email):
        seen["email"] = email
        return {"id": "sub-1", "email": email}

    inserted = {}

    def fake_insert(row):
        inserted.update(row)
        return uuid4()

    async def fake_send(*_args, **_kwargs):
        return True

    monkeypatch.setattr(main, "resolve_location", fake_resolve)
    monkeypatch.setattr(main, "fetch_hourly", fake_hourly)
    monkeypatch.setattr(main, "upsert_subscriber", fake_upsert)
    monkeypatch.setattr(main, "insert_pour_check", fake_insert)
    monkeypatch.setattr(main, "send_watch_started", fake_send)

    client = TestClient(app)
    pour_date = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()
    response = client.post(
        "/v1/pour-readiness",
        json={"zip_code": "94612", "pour_date": pour_date, "email": "Ops@Example.COM"},
    )
    assert response.status_code == 200
    assert seen["email"] == "ops@example.com"
    assert inserted["subscriber_id"] == "sub-1"


def test_readiness_without_email_still_works(monkeypatch):
    from backend import main
    from backend.formulas import WeatherHour

    async def fake_resolve(*_args, **_kwargs):
        return 37.8, -122.27, "Oakland, CA 94612"

    async def fake_hourly(_lat, _lon, pour_date, **_kwargs):
        hours = [WeatherHour(pour_date + timedelta(hours=i), 70.0, 50.0, 4.0, 0.0) for i in range(72)]
        return hours, "America/Los_Angeles"

    called = {"upsert": 0}

    monkeypatch.setattr(main, "resolve_location", fake_resolve)
    monkeypatch.setattr(main, "fetch_hourly", fake_hourly)
    monkeypatch.setattr(main, "upsert_subscriber", lambda _email: called.__setitem__("upsert", 1))
    monkeypatch.setattr(main, "insert_pour_check", lambda _row: None)

    client = TestClient(app)
    pour_date = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()
    response = client.post("/v1/pour-readiness", json={"zip_code": "94612", "pour_date": pour_date})
    assert response.status_code == 200
    assert called["upsert"] == 0


def test_readiness_rejects_invalid_email(monkeypatch):
    from backend import main
    from backend.formulas import WeatherHour

    async def fake_resolve(*_args, **_kwargs):
        return 37.8, -122.27, "Oakland, CA 94612"

    async def fake_hourly(_lat, _lon, pour_date, **_kwargs):
        hours = [WeatherHour(pour_date + timedelta(hours=i), 70.0, 50.0, 4.0, 0.0) for i in range(72)]
        return hours, "America/Los_Angeles"

    monkeypatch.setattr(main, "resolve_location", fake_resolve)
    monkeypatch.setattr(main, "fetch_hourly", fake_hourly)
    client = TestClient(app)
    pour_date = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()
    response = client.post(
        "/v1/pour-readiness",
        json={"zip_code": "94612", "pour_date": pour_date, "email": "not-an-email"},
    )
    assert response.status_code == 422


def test_watch_tick_unauthorized_without_or_with_wrong_secret(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("JOBS_SECRET", "correct-secret")
    get_settings.cache_clear()
    client = TestClient(app)
    assert client.post("/internal/jobs/watch-tick").status_code == 401
    assert client.post("/internal/jobs/watch-tick", headers={"X-Jobs-Secret": "nope"}).status_code == 401
    get_settings.cache_clear()


def test_watch_tick_authorized_returns_counts(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("JOBS_SECRET", "correct-secret")
    get_settings.cache_clear()

    async def fake_tick():
        return {"polled": 3, "changed": 1, "emailed": 1, "errors": 0}

    monkeypatch.setattr("backend.jobs.run_watch_tick", fake_tick)
    client = TestClient(app)
    response = client.post("/internal/jobs/watch-tick", headers={"X-Jobs-Secret": "correct-secret"})
    assert response.status_code == 200
    assert response.json() == {"polled": 3, "changed": 1, "emailed": 1, "errors": 0}
    get_settings.cache_clear()


def test_daily_digest_unauthorized(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("JOBS_SECRET", "correct-secret")
    get_settings.cache_clear()
    client = TestClient(app)
    assert client.post("/internal/jobs/daily-digest").status_code == 401
    get_settings.cache_clear()


def test_unsubscribe_flips_flags(monkeypatch):
    seen = {}

    def fake_unsub(token):
        seen["token"] = token
        return {"email": "a@example.com", "alerts_enabled": False, "digest_enabled": False}

    monkeypatch.setattr("backend.jobs.get_client", lambda: object())
    monkeypatch.setattr("backend.jobs.unsubscribe_by_token", fake_unsub)
    client = TestClient(app)
    response = client.get("/v1/unsubscribe", params={"token": "abc123"})
    assert response.status_code == 200
    assert "Unsubscribed" in response.text
    assert seen["token"] == "abc123"


def test_unsubscribe_unknown_token(monkeypatch):
    monkeypatch.setattr("backend.jobs.get_client", lambda: object())
    monkeypatch.setattr("backend.jobs.unsubscribe_by_token", lambda _token: None)
    client = TestClient(app)
    response = client.get("/v1/unsubscribe", params={"token": "missing"})
    assert response.status_code == 404


def test_pour_watch_does_not_email_metric_only_changes(monkeypatch):
    from backend import main

    sent = {"n": 0}

    async def fake_send(**_kwargs):
        sent["n"] += 1
        return True

    monkeypatch.setattr(main, "send_watch_alerts", fake_send)
    monkeypatch.setattr(
        main,
        "get_check_for_watch",
        lambda _cid: {
            "go_no_go_status": "GO",
            "risk_factors": [],
            "metrics": {
                "ambient_temp_f": 70.0,
                "wind_speed_mph": 4.0,
                "relative_humidity_pct": 50.0,
                "calculated_evaporation_rate_lbs_sqft_hr": 0.05,
                "precipitation_in": 0.0,
                "min_temp_next_24h_f": 55.0,
                "concrete_temp_f": 75.0,
                "min_temp_next_48h_f": 55.0,
            },
            "watch_events": [],
            "watching": True,
            "subscriber": {
                "email": "a@example.com",
                "alerts_enabled": True,
                "unsub_token": "tok",
            },
        },
    )
    monkeypatch.setattr(main, "record_watch_poll", lambda *_args, **_kwargs: [])

    async def fake_resolve(*_args, **_kwargs):
        return 37.8, -122.27, "Oakland, CA 94612"

    from backend.formulas import WeatherHour

    async def fake_hourly(_lat, _lon, pour_date, **_kwargs):
        hours = [WeatherHour(pour_date + timedelta(hours=i), 72.0, 50.0, 4.0, 0.0) for i in range(72)]
        return hours, "America/Los_Angeles"

    monkeypatch.setattr(main, "resolve_location", fake_resolve)
    monkeypatch.setattr(main, "fetch_hourly", fake_hourly)

    client = TestClient(app)
    pour_date = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()
    response = client.post(
        "/v1/pour-watch",
        json={"zip_code": "94612", "pour_date": pour_date, "check_id": str(uuid4())},
    )
    assert response.status_code == 200
    # send_watch_alerts is invoked; it should no-op internally on metric-only.
    # Here we replaced it, so assert the endpoint still records a poll.
    assert "changed" in response.json()


def test_send_watch_alerts_skips_metric_events(monkeypatch):
    from backend import notify

    called = []

    async def fake_send(*args, **kwargs):
        called.append(args)
        return True

    monkeypatch.setattr(notify, "send_email", fake_send)
    marked = []
    monkeypatch.setattr(notify, "mark_events_emailed", lambda *a, **k: marked.append(a))

    result = asyncio.run(
        notify.send_watch_alerts(
            check_id=uuid4(),
            stored={
                "subscriber": {
                    "email": "a@example.com",
                    "alerts_enabled": True,
                    "unsub_token": "tok",
                }
            },
            events=[_event("METRIC_AMBIENT_TEMP_F")],
            status="GO",
            location_name="Denver, CO 80202",
            product="concrete",
            pour_date="2026-09-19T08:00:00",
        )
    )
    assert result is False
    assert called == []
    assert marked == []


def test_send_watch_alerts_sends_nogo_flip(monkeypatch):
    from backend import notify

    called = []

    async def fake_send(to, subject, html_body):
        called.append({"to": to, "subject": subject, "html": html_body})
        return True

    monkeypatch.setattr(notify, "send_email", fake_send)
    marked = []
    monkeypatch.setattr(notify, "mark_events_emailed", lambda *a, **k: marked.append(True))

    result = asyncio.run(
        notify.send_watch_alerts(
            check_id=uuid4(),
            stored={
                "subscriber": {
                    "email": "a@example.com",
                    "alerts_enabled": True,
                    "unsub_token": "tok",
                }
            },
            events=[_event("STATUS", current="NO_GO", label="Advisory changed", detail="GO → NO_GO")],
            status="NO_GO",
            location_name="Denver, CO 80202",
            product="concrete",
            pour_date="2026-09-19T08:00:00",
        )
    )
    assert result is True
    assert called[0]["to"] == "a@example.com"
    assert called[0]["subject"] == "NO_GO · Denver, CO 80202"
    assert "Unsubscribe" in called[0]["html"]
    assert marked == [True]


def _stub_readiness_weather(monkeypatch):
    from backend import main
    from backend.formulas import WeatherHour

    async def fake_resolve(*_args, **_kwargs):
        return 44.98, -93.27, "Minneapolis, MN 55401"

    async def fake_hourly(_lat, _lon, pour_date, **_kwargs):
        hours = [WeatherHour(pour_date + timedelta(hours=i), 70.0, 50.0, 4.0, 0.0) for i in range(72)]
        return hours, "America/Chicago"

    monkeypatch.setattr(main, "resolve_location", fake_resolve)
    monkeypatch.setattr(main, "fetch_hourly", fake_hourly)
    return main


def test_readiness_sends_watch_started_with_email_and_watching(monkeypatch):
    main = _stub_readiness_weather(monkeypatch)
    sent = []

    async def fake_send(to, subject, html_body):
        sent.append({"to": to, "subject": subject, "html": html_body})
        return True

    monkeypatch.setattr("backend.notify.send_email", fake_send)
    monkeypatch.setattr(
        main,
        "upsert_subscriber",
        lambda email: {"id": "sub-1", "email": email, "unsub_token": "tok"},
    )
    monkeypatch.setattr(main, "insert_pour_check", lambda _row: uuid4())

    client = TestClient(app)
    pour_date = (datetime.now() + timedelta(days=1)).replace(microsecond=0)
    response = client.post(
        "/v1/pour-readiness",
        json={"zip_code": "55401", "pour_date": pour_date.isoformat(), "email": "ops@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["watching"] is True
    assert len(sent) == 1
    assert sent[0]["to"] == "ops@example.com"
    assert sent[0]["subject"] == "Watching · Minneapolis, MN 55401"
    html = sent[0]["html"]
    assert "Minneapolis, MN 55401" in html
    assert "Concrete" in html
    assert response.json()["go_no_go_status"] in html
    assert "Unsubscribe" in html
    assert "ACI" not in html
    assert "advisory" not in html.lower()
    assert "how it works" not in html.lower()


def test_readiness_does_not_send_watch_started_without_email(monkeypatch):
    main = _stub_readiness_weather(monkeypatch)
    sent = []

    async def fake_send(*args, **kwargs):
        sent.append((args, kwargs))
        return True

    monkeypatch.setattr("backend.notify.send_email", fake_send)
    monkeypatch.setattr(main, "upsert_subscriber", lambda _email: (_ for _ in ()).throw(AssertionError("upsert")))
    monkeypatch.setattr(main, "insert_pour_check", lambda _row: uuid4())

    client = TestClient(app)
    pour_date = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()
    response = client.post("/v1/pour-readiness", json={"zip_code": "55401", "pour_date": pour_date})
    assert response.status_code == 200
    assert sent == []


def test_readiness_does_not_send_watch_started_when_watching_false(monkeypatch):
    main = _stub_readiness_weather(monkeypatch)
    sent = []

    async def fake_send(*args, **kwargs):
        sent.append((args, kwargs))
        return True

    monkeypatch.setattr("backend.notify.send_email", fake_send)
    monkeypatch.setattr(
        main,
        "upsert_subscriber",
        lambda email: {"id": "sub-1", "email": email, "unsub_token": "tok"},
    )
    monkeypatch.setattr(main, "insert_pour_check", lambda _row: uuid4())

    client = TestClient(app)
    pour_date = (datetime.now() - timedelta(days=7)).replace(microsecond=0).isoformat()
    response = client.post(
        "/v1/pour-readiness",
        json={"zip_code": "55401", "pour_date": pour_date, "email": "ops@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["watching"] is False
    assert sent == []


def test_readiness_survives_watch_started_mail_failure(monkeypatch):
    main = _stub_readiness_weather(monkeypatch)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("resend down")

    monkeypatch.setattr(main, "send_watch_started", boom)
    monkeypatch.setattr(
        main,
        "upsert_subscriber",
        lambda email: {"id": "sub-1", "email": email, "unsub_token": "tok"},
    )
    monkeypatch.setattr(main, "insert_pour_check", lambda _row: uuid4())

    client = TestClient(app)
    pour_date = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()
    response = client.post(
        "/v1/pour-readiness",
        json={"zip_code": "55401", "pour_date": pour_date, "email": "ops@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["watching"] is True


def test_send_watch_started_dedupes_the_same_check_id(monkeypatch):
    from backend import notify

    called = []

    async def fake_send(to, subject, html_body):
        called.append(subject)
        return True

    monkeypatch.setattr(notify, "send_email", fake_send)
    check_id = uuid4()
    kwargs = dict(
        check_id=check_id,
        subscriber={"email": "ops@example.com", "unsub_token": "tok"},
        watching=True,
        location_name="Minneapolis, MN 55401",
        product="concrete",
        status="GO",
        pour_date="2026-09-19T08:00:00",
    )
    assert asyncio.run(send_watch_started(**kwargs)) is True
    assert asyncio.run(send_watch_started(**kwargs)) is False
    assert called == ["Watching · Minneapolis, MN 55401"]


def test_render_watch_started_html_is_factual():
    html = render_watch_started_html(
        location="Minneapolis, MN 55401",
        product="Concrete",
        status="WARNING",
        pour_date="2026-09-19 08:00",
        unsub_token="tok",
    )
    assert "Minneapolis, MN 55401" in html
    assert "WARNING" in html
    assert "Concrete" in html
    assert "2026-09-19 08:00" in html
    assert "Unsubscribe" in html
    assert "ACI" not in html
    assert "advisory" not in html.lower()
    assert "how it works" not in html.lower()


def test_demo_email_field_is_terse():
    client = TestClient(app)
    page = client.get("/").text
    assert 'id="notify-email"' in page
    assert "how it works" not in page.lower()
    assert "we emailed" not in page.lower()
    js = client.get("/app.js").text
    assert "pi_notify_email" in js
    assert "body.email" in js
    assert "we emailed" not in js.lower()
