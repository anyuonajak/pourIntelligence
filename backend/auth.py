from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from .config import get_settings


def _secret_eq(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def verify_admin(username: str, password: str) -> bool:
    settings = get_settings()
    if not settings.admin_password:
        return False
    return _secret_eq(username, settings.admin_username) and _secret_eq(password, settings.admin_password)


def require_admin(request: Request) -> None:
    if not request.session.get("admin"):
        raise HTTPException(status_code=401, detail="Not signed in.")
