from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from .config import get_settings
from .formulas import RISK_COPY as CONCRETE_RISK_COPY
from .formulas_masonry import RISK_COPY as MASONRY_RISK_COPY

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


def get_weather_cache(key: str, max_age_minutes: Optional[int] = None) -> Optional[dict[str, Any]]:
    client = get_client()
    if client is None:
        return None
    try:
        now = datetime.now(timezone.utc)
        query = (
            client.table("weather_cache")
            .select("payload,fetched_at")
            .eq("cache_key", key)
            .gt("expires_at", now.isoformat())
        )
        if max_age_minutes is not None:
            # A watch needs fresher data than a one-off check, even on a warm cache entry.
            cutoff = now - timedelta(minutes=max_age_minutes)
            query = query.gt("fetched_at", cutoff.isoformat())
        result = query.limit(1).execute()
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
        payload = {**row, "id": str(check_id)}
        try:
            client.table("pour_checks").insert(payload).execute()
        except Exception:
            payload.pop("watching", None)
            payload.pop("last_checked_at", None)
            try:
                client.table("pour_checks").insert(payload).execute()
            except Exception:
                payload.pop("product", None)
                client.table("pour_checks").insert(payload).execute()
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


def _flatten_outcome(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    outcomes = row.get("pour_outcomes") or []
    if isinstance(outcomes, dict):
        outcomes = [outcomes]
    return outcomes[0] if outcomes else None


_CHECK_CORE = (
    "id,created_at,source,zip_code,go_no_go_status,risk_factors,location,pour_date,"
    "mix_design,metrics,predictions,recommended_mitigation,concrete_temp_f,"
    "pour_outcomes(outcome,notes,created_at)"
)
# Newest columns first; each fallback drops a migration the database may not have run yet.
CHECK_SELECTS = (
    f"{_CHECK_CORE},product,watching,last_checked_at,watch_events",
    f"{_CHECK_CORE},product",
    _CHECK_CORE,
)


def _risk_details(codes: Any, product: str) -> list[dict[str, str]]:
    source = MASONRY_RISK_COPY if product == "masonry" else CONCRETE_RISK_COPY
    details: list[dict[str, str]] = []
    for code in codes or []:
        if not isinstance(code, str):
            continue
        if code in source:
            label, detail = source[code]
        else:
            label, detail = code.replace("_", " ").title(), ""
        details.append({"code": code, "label": label, "detail": detail})
    return details


def serialize_check(row: dict[str, Any]) -> dict[str, Any]:
    outcome = _flatten_outcome(row)
    location = row.get("location") if isinstance(row.get("location"), dict) else {}
    mix = row.get("mix_design") if isinstance(row.get("mix_design"), dict) else {}
    product = row.get("product") or mix.get("product") or "concrete"
    return {
        "id": row.get("id"),
        "created_at": row.get("created_at"),
        "source": row.get("source"),
        "product": product,
        "zip_code": row.get("zip_code"),
        "location_name": location.get("name") if location else None,
        "pour_date": row.get("pour_date"),
        "go_no_go_status": row.get("go_no_go_status"),
        "risk_factors": row.get("risk_factors") or [],
        "risk_factor_details": _risk_details(row.get("risk_factors") or [], product),
        "mix_design": mix,
        "metrics": row.get("metrics") if isinstance(row.get("metrics"), dict) else {},
        "predictions": row.get("predictions") if isinstance(row.get("predictions"), dict) else {},
        "recommended_mitigation": row.get("recommended_mitigation"),
        "concrete_temp_f": row.get("concrete_temp_f"),
        "outcome": outcome.get("outcome") if outcome else None,
        "outcome_at": outcome.get("created_at") if outcome else None,
        "outcome_notes": outcome.get("notes") if outcome else None,
        "watching": bool(row.get("watching")),
        "last_checked_at": row.get("last_checked_at"),
        "watch_events": row.get("watch_events") if isinstance(row.get("watch_events"), list) else [],
    }


def list_checks(limit: int = 200) -> list[dict[str, Any]]:
    client = get_client()
    if client is None:
        return []
    for select in CHECK_SELECTS:
        try:
            result = (
                client.table("pour_checks")
                .select(select)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
        except Exception:
            logger.warning("list_checks select failed, trying an older column set")
            continue
        return [serialize_check(row) for row in result.data or []]
    logger.error("list_checks failed for every known column set")
    return []


def get_check_for_watch(check_id: UUID) -> Optional[dict[str, Any]]:
    """Load the stored baseline for a watched check."""
    client = get_client()
    if client is None:
        return None
    for select in CHECK_SELECTS:
        try:
            result = (
                client.table("pour_checks")
                .select(select)
                .eq("id", str(check_id))
                .limit(1)
                .execute()
            )
        except Exception:
            continue
        rows = result.data or []
        if not rows:
            return None
        return serialize_check(rows[0])
    logger.error("get_check_for_watch failed for every known column set")
    return None


_CHECK_WRITE_FALLBACK = {
    "go_no_go_status",
    "risk_factors",
    "metrics",
    "predictions",
    "recommended_mitigation",
}


def record_watch_poll(
    check_id: UUID,
    *,
    status: str,
    risk_factors: list[str],
    metrics: dict[str, Any],
    predictions: dict[str, Any],
    recommended_mitigation: str,
    new_events: list[dict[str, Any]],
    prior_events: list[dict[str, Any]],
    watching: bool,
) -> None:
    """Store the latest evaluation and append any material changes."""
    client = get_client()
    if client is None:
        return
    checked_at = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "go_no_go_status": status,
        "risk_factors": risk_factors,
        "metrics": metrics,
        "predictions": predictions,
        "recommended_mitigation": recommended_mitigation,
        "watching": watching,
        "last_checked_at": checked_at,
    }
    if new_events:
        stamped = [{**event, "at": checked_at} for event in new_events]
        payload["watch_events"] = ([*prior_events, *stamped])[-50:]
    for attempt in (payload, {key: payload[key] for key in payload if key in _CHECK_WRITE_FALLBACK}):
        try:
            client.table("pour_checks").update(attempt).eq("id", str(check_id)).execute()
            return
        except Exception:
            logger.warning("watch update failed, retrying without watch columns")
    logger.error("record_watch_poll failed")


def set_watching(check_id: UUID, watching: bool, event: dict[str, Any] | None = None) -> bool:
    """Open or close a watch without re-running the forecast."""
    client = get_client()
    if client is None:
        return False
    stored = get_check_for_watch(check_id)
    if stored is None:
        return False
    checked_at = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {"watching": watching, "last_checked_at": checked_at}
    if event:
        prior = stored.get("watch_events") if isinstance(stored.get("watch_events"), list) else []
        payload["watch_events"] = [*prior, {**event, "at": checked_at}][-50:]
    try:
        client.table("pour_checks").update(payload).eq("id", str(check_id)).execute()
        return True
    except Exception:
        logger.exception("set_watching failed")
        return False


def summarize_checks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    with_outcome = [row for row in rows if row.get("outcome")]
    by_status: dict[str, int] = {}
    by_outcome: dict[str, int] = {}
    by_product: dict[str, int] = {}
    go_success = 0
    go_bad = 0
    for row in rows:
        status = row.get("go_no_go_status") or "UNKNOWN"
        product = row.get("product") or "concrete"
        by_status[status] = by_status.get(status, 0) + 1
        by_product[product] = by_product.get(product, 0) + 1
        outcome = row.get("outcome")
        if outcome:
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        if status == "GO" and outcome == "success":
            go_success += 1
        if status == "GO" and outcome in {"cracked", "delayed"}:
            go_bad += 1
    return {
        "total_checks": total,
        "with_outcome": len(with_outcome),
        "pending_outcome": total - len(with_outcome),
        "by_status": by_status,
        "by_outcome": by_outcome,
        "by_product": by_product,
        "go_and_success": go_success,
        "go_and_failed": go_bad,
    }


def list_api_keys() -> list[dict[str, Any]]:
    client = get_client()
    if client is None:
        return []
    result = (
        client.table("api_keys")
        .select("id,created_at,name,key_prefix,active,rate_limit_per_hour")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


def create_api_key(name: str, rate_limit_per_hour: int) -> tuple[str, dict[str, Any]]:
    client = get_client()
    if client is None:
        raise RuntimeError("Database is not configured.")
    raw = "pi_live_" + secrets.token_urlsafe(24)
    prefix = raw[:16]
    row = {
        "name": name.strip(),
        "key_prefix": prefix,
        "key_hash": hash_api_key(raw),
        "active": True,
        "rate_limit_per_hour": rate_limit_per_hour,
    }
    result = client.table("api_keys").insert(row).execute()
    created = (result.data or [row])[0]
    return raw, created


def revoke_api_key(key_id: str) -> bool:
    client = get_client()
    if client is None:
        raise RuntimeError("Database is not configured.")
    result = client.table("api_keys").update({"active": False}).eq("id", key_id).execute()
    return bool(result.data)
