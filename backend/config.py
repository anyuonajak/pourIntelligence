import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

DEFAULT_ORIGINS = (
    "https://pourintelligence.onrender.com,"
    "http://localhost:8000,"
    "http://127.0.0.1:8000"
)


def _first(*keys: str) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return None


@dataclass(frozen=True)
class Settings:
    supabase_url: str | None
    supabase_key: str | None
    allowed_origins: list[str]
    environment: str
    demo_rate_limit_per_hour: int
    api_rate_limit_per_hour: int
    weather_cache_minutes: int

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)


@lru_cache
def get_settings() -> Settings:
    origins = _first("ALLOWED_ORIGINS") or DEFAULT_ORIGINS
    return Settings(
        supabase_url=_first("SUPABASE_URL", "project_url"),
        supabase_key=_first("SUPABASE_SERVICE_ROLE_KEY", "service_role"),
        allowed_origins=[item.strip() for item in origins.split(",") if item.strip()],
        environment=_first("ENVIRONMENT", "APP_ENV") or "production",
        demo_rate_limit_per_hour=int(_first("DEMO_RATE_LIMIT_PER_HOUR") or "30"),
        api_rate_limit_per_hour=int(_first("API_RATE_LIMIT_PER_HOUR") or "300"),
        weather_cache_minutes=int(_first("WEATHER_CACHE_MINUTES") or "45"),
    )
