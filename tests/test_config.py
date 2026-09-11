from backend.config import get_settings


def test_supabase_aliases(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("project_url", "https://example.supabase.co")
    monkeypatch.setenv("service_role", "secret-role-key")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    settings = get_settings()
    assert settings.supabase_url == "https://example.supabase.co"
    assert settings.supabase_key == "secret-role-key"
    assert settings.supabase_configured
    get_settings.cache_clear()
