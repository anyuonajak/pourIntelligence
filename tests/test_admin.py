from fastapi.testclient import TestClient

from backend.auth import verify_admin
from backend.config import get_settings
from backend.db import summarize_checks
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
