from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any, Optional

from fastapi import HTTPException, Request

from .config import get_settings
from .db import get_account_by_id

PBKDF2_ITERATIONS = 210_000


def _secret_eq(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$", 3)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    try:
        rounds = int(iterations)
        expected = bytes.fromhex(digest)
        computed = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            rounds,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(computed, expected)


def new_session_nonce() -> str:
    return secrets.token_urlsafe(24)


def verify_admin(username: str, password: str) -> bool:
    settings = get_settings()
    if not settings.admin_password:
        return False
    return _secret_eq(username, settings.admin_username) and _secret_eq(password, settings.admin_password)


def require_admin(request: Request) -> None:
    if not request.session.get("admin"):
        raise HTTPException(status_code=401, detail="Not signed in.")


def get_account_session(request: Request) -> Optional[dict[str, Any]]:
    account_id = request.session.get("account_id")
    nonce = request.session.get("session_nonce")
    if not account_id or not nonce:
        return None
    row = get_account_by_id(str(account_id))
    if not row:
        return None
    stored = str(row.get("session_nonce") or "")
    provided = str(nonce)
    if not stored or len(stored) != len(provided):
        return None
    if not hmac.compare_digest(stored, provided):
        return None
    return row


def set_account_session(request: Request, account_id: str, session_nonce: str) -> None:
    request.session["account_id"] = str(account_id)
    request.session["session_nonce"] = session_nonce


def clear_account_session(request: Request) -> None:
    request.session.pop("account_id", None)
    request.session.pop("session_nonce", None)
