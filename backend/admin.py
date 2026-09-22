from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator

from .auth import hash_password, new_session_nonce, require_admin, verify_admin
from .config import get_settings
from .db import (
    create_account,
    create_api_key,
    get_account_by_email,
    list_api_keys,
    list_checks,
    list_org_accounts,
    revoke_api_key,
    set_watching,
    summarize_checks,
)
from .schemas import _EMAIL_RE

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
router = APIRouter()


class LoginBody(BaseModel):
    username: str = "admin"
    password: str


class CreateKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    rate_limit_per_hour: int = Field(default=300, ge=1, le=10000)


class OnboardOrgBody(BaseModel):
    company_name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("company_name", mode="before")
    @classmethod
    def normalize_company(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Provide a company name.")
        return text

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if not text or len(text) > 254 or not _EMAIL_RE.fullmatch(text):
            raise ValueError("Provide a valid email.")
        return text


@router.get("/admin/login")
def admin_login_page() -> FileResponse:
    return FileResponse(FRONTEND / "login.html", headers={"Cache-Control": "no-cache"})


@router.get("/admin")
def admin_page(request: Request):
    if not request.session.get("admin"):
        return RedirectResponse("/admin/login", status_code=302)
    return FileResponse(FRONTEND / "admin.html", headers={"Cache-Control": "no-cache"})


@router.get("/admin.js")
def admin_js() -> FileResponse:
    return FileResponse(
        FRONTEND / "admin.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/admin/api/login")
def admin_login(request: Request, body: LoginBody) -> dict[str, object]:
    settings = get_settings()
    if not settings.admin_configured:
        raise HTTPException(
            status_code=503,
            detail="Set ADMIN_PASSWORD on the server before using /admin.",
        )
    if not verify_admin(body.username.strip(), body.password):
        raise HTTPException(status_code=401, detail="Bad username or password.")
    request.session["admin"] = True
    return {"ok": True}


@router.post("/admin/api/logout")
def admin_logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


@router.get("/admin/api/me")
def admin_me(request: Request) -> dict[str, object]:
    require_admin(request)
    return {"ok": True, "username": get_settings().admin_username}


@router.get("/admin/api/checks")
def admin_checks(request: Request) -> dict[str, object]:
    require_admin(request)
    rows = list_checks()
    return {"checks": rows, "summary": summarize_checks(rows)}


@router.post("/admin/api/checks/{check_id}/close")
def admin_close_watch(request: Request, check_id: UUID) -> dict[str, object]:
    require_admin(request)
    if not set_watching(
        check_id,
        False,
        {
            "code": "WATCH_CLOSED",
            "label": "Watch closed",
            "detail": "Monitoring stopped from admin.",
            "previous": "watching",
            "current": "closed",
        },
    ):
        raise HTTPException(status_code=404, detail="Unknown check.")
    return {"ok": True, "watching": False}


@router.get("/admin/api/orgs")
def admin_list_orgs(request: Request) -> dict[str, object]:
    require_admin(request)
    return {"orgs": list_org_accounts()}


@router.post("/admin/api/orgs")
def admin_onboard_org(request: Request, body: OnboardOrgBody) -> dict[str, object]:
    require_admin(request)
    if get_account_by_email(body.email):
        raise HTTPException(status_code=409, detail="An account with that email already exists.")
    nonce = new_session_nonce()
    account = create_account(
        body.email,
        hash_password(body.password),
        session_nonce=nonce,
        display_name=body.company_name,
        kind="org",
    )
    if account is None:
        raise HTTPException(status_code=503, detail="Could not create this org.")
    return {
        "id": account.get("id"),
        "email": account.get("email"),
        "display_name": account.get("display_name"),
        "kind": "org",
        "plan": account.get("plan") or "free",
        "created_at": account.get("created_at"),
        "trial_ends_at": account.get("trial_ends_at"),
        "password": body.password,
        "warning": "Copy this password now. It will not be shown again.",
    }


@router.get("/admin/api/keys")
def admin_list_keys(request: Request) -> dict[str, object]:
    require_admin(request)
    return {"keys": list_api_keys()}


@router.post("/admin/api/keys")
def admin_create_key(request: Request, body: CreateKeyBody) -> dict[str, object]:
    require_admin(request)
    try:
        raw, created = create_api_key(body.name, body.rate_limit_per_hour)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not create API key.") from exc
    return {
        "id": created.get("id"),
        "name": created.get("name"),
        "key_prefix": created.get("key_prefix"),
        "rate_limit_per_hour": created.get("rate_limit_per_hour"),
        "key": raw,
        "warning": "Copy this key now. It will not be shown again.",
    }


@router.post("/admin/api/keys/{key_id}/revoke")
def admin_revoke_key(request: Request, key_id: UUID) -> dict[str, bool]:
    require_admin(request)
    try:
        ok = revoke_api_key(str(key_id))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not revoke API key.") from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Unknown API key.")
    return {"ok": True}
