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
    lat = round(latitude, 2)
    lon = round(longitude, 2)
    # Forecast payloads are a rolling 16-day window; share one entry per site.
    if mode == "forecast":
        return f"{lat}:{lon}:forecast"
    return f"{lat}:{lon}:{pour_day}:{mode}"


def get_weather_cache(
    key: str,
    max_age_minutes: Optional[int] = None,
    stale_within_minutes: Optional[int] = None,
    any_age: bool = False,
) -> Optional[dict[str, Any]]:
    client = get_client()
    if client is None:
        return None
    try:
        now = datetime.now(timezone.utc)
        query = client.table("weather_cache").select("payload,fetched_at").eq("cache_key", key)
        if any_age:
            pass
        elif stale_within_minutes is not None:
            cutoff = now - timedelta(minutes=stale_within_minutes)
            query = query.gt("fetched_at", cutoff.isoformat())
        else:
            query = query.gt("expires_at", now.isoformat())
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
            payload.pop("account_id", None)
            try:
                client.table("pour_checks").insert(payload).execute()
            except Exception:
                payload.pop("subscriber_id", None)
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
    "id,created_at,source,zip_code,address,latitude,longitude,go_no_go_status,risk_factors,"
    "location,pour_date,mix_design,metrics,predictions,recommended_mitigation,concrete_temp_f,"
    "pour_outcomes(outcome,notes,created_at)"
)
_SUBSCRIBER_JOIN = "watch_subscribers(id,email,unsub_token,alerts_enabled,digest_enabled,last_digest_at)"
# Newest columns first; each fallback drops a migration the database may not have run yet.
CHECK_SELECTS = (
    f"{_CHECK_CORE},product,watching,last_checked_at,watch_events,subscriber_id,account_id,{_SUBSCRIBER_JOIN}",
    f"{_CHECK_CORE},product,watching,last_checked_at,watch_events,subscriber_id,{_SUBSCRIBER_JOIN}",
    f"{_CHECK_CORE},product,watching,last_checked_at,watch_events,subscriber_id",
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


def _subscriber_record(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    sub = row.get("watch_subscribers")
    if isinstance(sub, list):
        sub = sub[0] if sub else None
    if isinstance(sub, dict) and sub:
        return sub
    return None


def serialize_check(row: dict[str, Any]) -> dict[str, Any]:
    outcome = _flatten_outcome(row)
    location = row.get("location") if isinstance(row.get("location"), dict) else {}
    mix = row.get("mix_design") if isinstance(row.get("mix_design"), dict) else {}
    product = row.get("product") or mix.get("product") or "concrete"
    subscriber = _subscriber_record(row)
    latitude = row.get("latitude")
    longitude = row.get("longitude")
    if latitude is None:
        latitude = location.get("latitude")
    if longitude is None:
        longitude = location.get("longitude")
    return {
        "id": row.get("id"),
        "created_at": row.get("created_at"),
        "source": row.get("source"),
        "product": product,
        "zip_code": row.get("zip_code"),
        "address": row.get("address"),
        "latitude": latitude,
        "longitude": longitude,
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
        "subscriber_id": row.get("subscriber_id") or (subscriber or {}).get("id"),
        "subscriber_email": (subscriber or {}).get("email"),
    }


def hydrate_watch_row(row: dict[str, Any]) -> dict[str, Any]:
    """serialize_check plus the subscriber record used for mail (includes unsub_token)."""
    out = serialize_check(row)
    out["subscriber"] = _subscriber_record(row)
    return out


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
        return hydrate_watch_row(rows[0])
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
) -> list[dict[str, Any]]:
    """Store the latest evaluation and append any material changes."""
    client = get_client()
    if client is None:
        return []
    checked_at = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "go_no_go_status": status,
        "risk_factors": risk_factors,
        "metrics": metrics,
        "predictions": predictions,
        "recommended_mitigation": recommended_mitigation,
        "last_checked_at": checked_at,
    }
    # Polls may close a watch when the protection window ends, but must not
    # reopen one the customer already closed (including an in-flight poll).
    if not watching:
        payload["watching"] = False
    if new_events:
        stamped = [{**event, "at": checked_at, "emailed": False} for event in new_events]
        payload["watch_events"] = ([*prior_events, *stamped])[-50:]
    written = False
    for attempt in (payload, {key: payload[key] for key in payload if key in _CHECK_WRITE_FALLBACK}):
        try:
            client.table("pour_checks").update(attempt).eq("id", str(check_id)).execute()
            written = True
            break
        except Exception:
            logger.warning("watch update failed, retrying without watch columns")
    if not written:
        logger.error("record_watch_poll failed")
    return stamped if new_events else []


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
    # watching=false must stick even if watch_events / last_checked_at cannot be written.
    for attempt in (payload, {"watching": watching, "last_checked_at": checked_at}, {"watching": watching}):
        try:
            client.table("pour_checks").update(attempt).eq("id", str(check_id)).execute()
            return True
        except Exception:
            logger.warning("set_watching failed, retrying without optional columns")
    logger.error("set_watching failed")
    return False


WATCH_TICK_BATCH = 50


def list_open_watches(limit: int = WATCH_TICK_BATCH) -> list[dict[str, Any]]:
    """Open watches, oldest last_checked_at first, for the server-side tick."""
    client = get_client()
    if client is None:
        return []
    for select in CHECK_SELECTS:
        try:
            result = (
                client.table("pour_checks")
                .select(select)
                .eq("watching", True)
                .order("last_checked_at", desc=False, nullsfirst=True)
                .limit(limit)
                .execute()
            )
        except Exception:
            logger.warning("list_open_watches select failed, trying an older column set")
            continue
        return [hydrate_watch_row(row) for row in result.data or []]
    logger.error("list_open_watches failed for every known column set")
    return []


def list_watching_subscribed(limit: int = 500) -> list[dict[str, Any]]:
    """Open watches that have a subscriber, for the daily digest."""
    client = get_client()
    if client is None:
        return []
    for select in CHECK_SELECTS:
        try:
            result = (
                client.table("pour_checks")
                .select(select)
                .eq("watching", True)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
        except Exception:
            logger.warning("list_watching_subscribed select failed, trying an older column set")
            continue
        rows = []
        for row in result.data or []:
            hydrated = hydrate_watch_row(row)
            if hydrated.get("subscriber_id") and hydrated.get("subscriber"):
                rows.append(hydrated)
        return rows
    logger.error("list_watching_subscribed failed for every known column set")
    return []


def upsert_subscriber(email: str) -> Optional[dict[str, Any]]:
    client = get_client()
    if client is None or not email:
        return None
    cleaned = email.strip().lower()
    columns = "id,email,unsub_token,alerts_enabled,digest_enabled,last_digest_at"

    def _lookup() -> Optional[dict[str, Any]]:
        found = (
            client.table("watch_subscribers").select(columns).eq("email", cleaned).limit(1).execute()
        )
        rows = found.data or []
        return rows[0] if rows else None

    try:
        existing = _lookup()
        if existing:
            return existing
        row = {
            "email": cleaned,
            "unsub_token": secrets.token_urlsafe(24),
            "alerts_enabled": True,
            "digest_enabled": True,
        }
        created = client.table("watch_subscribers").insert(row).execute()
        return (created.data or [row])[0]
    except Exception:
        logger.exception("subscriber upsert failed")
        try:
            return _lookup()
        except Exception:
            logger.exception("subscriber re-select failed")
            return None


def unsubscribe_by_token(token: str) -> Optional[dict[str, Any]]:
    client = get_client()
    if client is None or not token:
        return None
    try:
        result = (
            client.table("watch_subscribers")
            .update({"alerts_enabled": False, "digest_enabled": False})
            .eq("unsub_token", token)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None
    except Exception:
        logger.exception("unsubscribe failed")
        return None


def mark_events_emailed(check_id: UUID, pending: list[dict[str, Any]]) -> None:
    if not pending:
        return
    client = get_client()
    if client is None:
        return
    stored = get_check_for_watch(check_id)
    if stored is None:
        return
    keys = {(event.get("code"), event.get("at")) for event in pending}
    events = []
    for event in stored.get("watch_events") or []:
        if not isinstance(event, dict):
            continue
        if (event.get("code"), event.get("at")) in keys:
            events.append({**event, "emailed": True})
        else:
            events.append(event)
    try:
        client.table("pour_checks").update({"watch_events": events}).eq("id", str(check_id)).execute()
    except Exception:
        logger.warning("mark_events_emailed failed")


def set_last_digest_at(subscriber_id: str, when: datetime) -> None:
    client = get_client()
    if client is None:
        return
    try:
        client.table("watch_subscribers").update({"last_digest_at": when.isoformat()}).eq(
            "id", str(subscriber_id)
        ).execute()
    except Exception:
        logger.warning("set_last_digest_at failed")


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


INDIVIDUAL_WATCH_LIMIT = 5
INDIVIDUAL_TRIAL_DAYS = 30
ORG_TRIAL_DAYS = 14
UNLIMITED_WATCHES = 1_000_000
FREE_WATCH_LIMIT = INDIVIDUAL_WATCH_LIMIT


def account_trial_open(account: Optional[dict[str, Any]]) -> bool:
    if not account:
        return False
    raw = account.get("trial_ends_at")
    if not raw:
        return False
    try:
        ends = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < ends


def watch_limit_for_account(account: Optional[dict[str, Any]]) -> int:
    if account and (account.get("kind") or "individual") == "org":
        return UNLIMITED_WATCHES if account_trial_open(account) else 0
    return INDIVIDUAL_WATCH_LIMIT


_ACCOUNT_COLUMNS = (
    "id,created_at,email,password_hash,kind,plan,trial_ends_at,session_nonce,display_name"
)
_ACCOUNTS_BY_ID: dict[str, dict[str, Any]] = {}
_ACCOUNTS_BY_EMAIL: dict[str, str] = {}
_MEMORY_WATCHING: dict[str, int] = {}


def clear_account_memory() -> None:
    _ACCOUNTS_BY_ID.clear()
    _ACCOUNTS_BY_EMAIL.clear()
    _MEMORY_WATCHING.clear()


def _public_account(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id")),
        "created_at": row.get("created_at"),
        "email": row.get("email"),
        "password_hash": row.get("password_hash"),
        "kind": row.get("kind") or "individual",
        "plan": row.get("plan") or "free",
        "trial_ends_at": row.get("trial_ends_at"),
        "session_nonce": row.get("session_nonce"),
        "display_name": row.get("display_name"),
    }


def _memory_account_by_email(email: str) -> Optional[dict[str, Any]]:
    account_id = _ACCOUNTS_BY_EMAIL.get(email)
    if not account_id:
        return None
    row = _ACCOUNTS_BY_ID.get(account_id)
    return dict(row) if row else None


def get_account_by_email(email: str) -> Optional[dict[str, Any]]:
    cleaned = (email or "").strip().lower()
    if not cleaned:
        return None
    client = get_client()
    if client is None:
        return _memory_account_by_email(cleaned)
    try:
        result = (
            client.table("accounts")
            .select(_ACCOUNT_COLUMNS)
            .eq("email", cleaned)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return _public_account(rows[0]) if rows else None
    except Exception:
        logger.warning("account email lookup failed")
        return _memory_account_by_email(cleaned)


def get_account_by_id(account_id: str) -> Optional[dict[str, Any]]:
    if not account_id:
        return None
    client = get_client()
    if client is None:
        row = _ACCOUNTS_BY_ID.get(str(account_id))
        return dict(row) if row else None
    try:
        result = (
            client.table("accounts")
            .select(_ACCOUNT_COLUMNS)
            .eq("id", str(account_id))
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return _public_account(rows[0]) if rows else None
    except Exception:
        logger.warning("account id lookup failed")
        row = _ACCOUNTS_BY_ID.get(str(account_id))
        return dict(row) if row else None


def create_account(
    email: str,
    password_hash: str,
    *,
    session_nonce: str,
    display_name: Optional[str] = None,
    kind: str = "individual",
) -> Optional[dict[str, Any]]:
    cleaned = email.strip().lower()
    account_kind = "org" if kind == "org" else "individual"
    now = datetime.now(timezone.utc)
    trial_days = ORG_TRIAL_DAYS if account_kind == "org" else INDIVIDUAL_TRIAL_DAYS
    trial_ends = now + timedelta(days=trial_days)
    row = {
        "id": str(uuid4()),
        "created_at": now.isoformat(),
        "email": cleaned,
        "password_hash": password_hash,
        "kind": account_kind,
        "plan": "free",
        "trial_ends_at": trial_ends.isoformat(),
        "session_nonce": session_nonce,
        "display_name": (display_name or "").strip() or None,
    }
    client = get_client()
    if client is None:
        if cleaned in _ACCOUNTS_BY_EMAIL:
            return None
        _ACCOUNTS_BY_ID[row["id"]] = dict(row)
        _ACCOUNTS_BY_EMAIL[cleaned] = row["id"]
        return dict(row)
    try:
        created = client.table("accounts").insert(row).execute()
        stored = (created.data or [row])[0]
        return _public_account(stored)
    except Exception:
        logger.warning("account insert failed")
        return None


def _org_list_row(row: dict[str, Any]) -> dict[str, Any]:
    public = _public_account(row)
    public.pop("password_hash", None)
    public.pop("session_nonce", None)
    return public


def list_org_accounts() -> list[dict[str, Any]]:
    client = get_client()
    if client is None:
        rows = [_org_list_row(row) for row in _ACCOUNTS_BY_ID.values() if row.get("kind") == "org"]
        return sorted(rows, key=lambda item: str(item.get("created_at") or ""), reverse=True)
    try:
        result = (
            client.table("accounts")
            .select(_ACCOUNT_COLUMNS)
            .eq("kind", "org")
            .order("created_at", desc=True)
            .limit(200)
            .execute()
        )
        return [_org_list_row(row) for row in (result.data or [])]
    except Exception:
        logger.warning("org account list failed")
        rows = [_org_list_row(row) for row in _ACCOUNTS_BY_ID.values() if row.get("kind") == "org"]
        return sorted(rows, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def set_account_nonce(account_id: str, session_nonce: str) -> bool:
    client = get_client()
    if client is None:
        row = _ACCOUNTS_BY_ID.get(str(account_id))
        if not row:
            return False
        row["session_nonce"] = session_nonce
        return True
    try:
        result = (
            client.table("accounts")
            .update({"session_nonce": session_nonce})
            .eq("id", str(account_id))
            .execute()
        )
        if result.data:
            return True
        row = _ACCOUNTS_BY_ID.get(str(account_id))
        if not row:
            return False
        row["session_nonce"] = session_nonce
        return True
    except Exception:
        logger.warning("account nonce update failed")
        row = _ACCOUNTS_BY_ID.get(str(account_id))
        if not row:
            return False
        row["session_nonce"] = session_nonce
        return True


def count_watching_for_account(account_id: str) -> int:
    if not account_id:
        return 0
    client = get_client()
    if client is None:
        return _MEMORY_WATCHING.get(str(account_id), 0)
    try:
        result = (
            client.table("pour_checks")
            .select("id", count="exact")
            .eq("account_id", str(account_id))
            .eq("watching", True)
            .execute()
        )
        if getattr(result, "count", None) is not None:
            return int(result.count)
        return len(result.data or [])
    except Exception:
        logger.warning("account watch count failed")
        return _MEMORY_WATCHING.get(str(account_id), 0)


def record_memory_watch(account_id: str) -> None:
    """Count a watching stamp when pour_checks is not persisted."""
    if not account_id or get_client() is not None:
        return
    key = str(account_id)
    _MEMORY_WATCHING[key] = _MEMORY_WATCHING.get(key, 0) + 1
