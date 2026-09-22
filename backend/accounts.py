from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from .auth import (
    clear_account_session,
    get_account_session,
    hash_password,
    new_session_nonce,
    set_account_session,
    verify_password,
)
from .db import create_account, get_account_by_email, set_account_nonce
from .schemas import _EMAIL_RE

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
router = APIRouter()


class AuthCredentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=80)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if not text or len(text) > 254 or not _EMAIL_RE.fullmatch(text):
            raise ValueError("Provide a valid email.")
        return text

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


def _me_payload(account: dict) -> dict[str, object]:
    return {
        "email": account.get("email"),
        "plan": account.get("plan") or "free",
        "trial_ends_at": account.get("trial_ends_at"),
        "kind": account.get("kind") or "individual",
        "display_name": account.get("display_name"),
    }


@router.get("/login")
def login_page() -> FileResponse:
    return FileResponse(FRONTEND / "signin.html", headers={"Cache-Control": "no-cache"})


@router.get("/signup")
def signup_page() -> FileResponse:
    return FileResponse(FRONTEND / "signup.html", headers={"Cache-Control": "no-cache"})


@router.post("/v1/auth/signup")
def signup(request: Request, body: AuthCredentials) -> dict[str, object]:
    if get_account_by_email(body.email):
        raise HTTPException(status_code=409, detail="An account with that email already exists.")
    nonce = new_session_nonce()
    account = create_account(
        body.email,
        hash_password(body.password),
        session_nonce=nonce,
        display_name=body.display_name,
    )
    if account is None:
        raise HTTPException(status_code=503, detail="Could not create this account.")
    set_account_session(request, str(account["id"]), nonce)
    return _me_payload(account)


@router.post("/v1/auth/login")
def login(request: Request, body: AuthCredentials) -> dict[str, object]:
    account = get_account_by_email(body.email)
    if account is None or not verify_password(body.password, str(account.get("password_hash") or "")):
        raise HTTPException(status_code=401, detail="Bad email or password.")
    if (account.get("kind") or "individual") == "org":
        nonce = str(account.get("session_nonce") or "") or new_session_nonce()
        if not account.get("session_nonce") and not set_account_nonce(str(account["id"]), nonce):
            raise HTTPException(status_code=503, detail="Could not start a session.")
    else:
        nonce = new_session_nonce()
        if not set_account_nonce(str(account["id"]), nonce):
            raise HTTPException(status_code=503, detail="Could not start a session.")
        account["session_nonce"] = nonce
    set_account_session(request, str(account["id"]), nonce)
    return _me_payload(account)


@router.post("/v1/auth/logout")
def logout(request: Request) -> dict[str, bool]:
    account = get_account_session(request)
    if account and (account.get("kind") or "individual") != "org":
        set_account_nonce(str(account["id"]), new_session_nonce())
    clear_account_session(request)
    return {"ok": True}


@router.get("/v1/auth/me")
def me(request: Request) -> dict[str, object]:
    account = get_account_session(request)
    if account is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return _me_payload(account)
