from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from .auth import require_admin, verify_admin
from .config import get_settings
from .db import create_api_key, list_api_keys, list_checks, revoke_api_key, set_watching, summarize_checks

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
router = APIRouter()


class LoginBody(BaseModel):
    username: str = "admin"
    password: str


class CreateKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    rate_limit_per_hour: int = Field(default=300, ge=1, le=10000)


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
