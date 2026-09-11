from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from .config import get_settings

logger = logging.getLogger("pourintelligence")

_client = None


def get_client():
    global _client
    settings = get_settings()
    if not settings.supabase_configured:
        return None
    if _client is None:
        from supabase import create_client

        _client = create_client(settings.supabase_url, settings.supabase_key)
    return _client


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def lookup_api_key(raw: str) -> Optional[dict[str, Any]]:
    client = get_client()
    if client is None or not raw:
        return None
    try:
        digest = hash_api_key(raw)
        result = (
            client.table("api_keys")
            .select("id,name,active,rate_limit_per_hour")
            .eq("key_hash", digest)
            .eq("active", True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None
    except Exception:
        logger.exception("api_key lookup failed")
        return None


def cache_key_for_weather(latitude: float, longitude: float, pour_day: str, mode: str) -> str:
    return f"{round(latitude, 2)}:{round(longitude, 2)}:{pour_day}:{mode}"


def get_weather_cache(key: str) -> Optional[dict[str, Any]]:
    client = get_client()
    if client is None:
        return None
    try:
        now = datetime.now(timezone.utc).isoformat()
        result = (
            client.table("weather_cache")
            .select("payload")
            .eq("cache_key", key)
            .gt("expires_at", now)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        return rows[0].get("payload")
    except Exception:
        logger.exception("weather cache read failed")
        return None


def set_weather_cache(key: str, payload: dict[str, Any], ttl_minutes: int) -> None:
    client = get_client()
    if client is None:
        return
    try:
        now = datetime.now(timezone.utc)
        client.table("weather_cache").upsert(
            {
                "cache_key": key,
                "payload": payload,
                "fetched_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
            }
        ).execute()
    except Exception:
        logger.exception("weather cache write failed")


def insert_pour_check(row: dict[str, Any]) -> Optional[UUID]:
    client = get_client()
    if client is None:
        return None
    try:
        check_id = row.get("id") or uuid4()
        row = {**row, "id": str(check_id)}
        client.table("pour_checks").insert(row).execute()
        return UUID(str(check_id))
    except Exception:
        logger.exception("pour_check insert failed")
        return None


def insert_pour_outcome(check_id: UUID, outcome: str, notes: Optional[str]) -> None:
    client = get_client()
    if client is None:
        raise RuntimeError("Database is not configured.")
    client.table("pour_outcomes").insert(
        {
            "check_id": str(check_id),
            "outcome": outcome,
            "notes": notes,
        }
    ).execute()


def check_exists(check_id: UUID) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        result = client.table("pour_checks").select("id").eq("id", str(check_id)).limit(1).execute()
        return bool(result.data)
    except Exception:
        logger.exception("pour_check lookup failed")
        return False
