"""
trading_api_kit.auth_routes — login + Google OAuth endpoints.

Routes:
  POST /auth/login           — email+password (new) or password-only (legacy)
  GET  /auth/google          — redirect to Google consent screen (web only)
  GET  /auth/google/callback — exchange code → upsert user → return JWT
  GET  /auth/me              — return current user info

Usage:
    from trading_api_kit.auth_routes import router as auth_router
    app.include_router(auth_router)
"""
import base64
import json as _json
import secrets
import uuid
import os
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from trading_api_kit.auth import (
    create_user_token,
    create_legacy_token,
    hash_password,
    verify_password,
)
import trading_api_kit.config as _cfg
from trading_api_kit.database import get_db
from trading_api_kit.deps import get_current_user
from trading_api_kit.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory CSRF state store (fine for single-process personal tool)
_oauth_states: dict[str, str] = {}  # state_token -> client_type ('web' | 'mobile')


# ── Request / response models ─────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str
    email: str | None = None


class LoginResponse(BaseModel):
    token: str
    user_id: str | None = None
    email: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """
    Login with email + password (new-style) or password-only (legacy).
    Returns a signed JWT valid for 30 days.
    """
    if req.email:
        # New-style: email + password
        user = db.query(User).filter(User.email == req.email.lower()).first()
        if not user or not user.password_hash:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if not verify_password(req.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        user.last_login = datetime.now(timezone.utc)
        db.commit()
        token = create_user_token(user.id, user.email)
        return LoginResponse(token=token, user_id=user.id, email=user.email)

    # Legacy: password-only (old mobile clients that predated multi-user)
    token = create_legacy_token(req.password)
    if not token:
        raise HTTPException(status_code=401, detail="Wrong password")
    return LoginResponse(token=token)


@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    """Return current authenticated user info."""
    return {
        "id":         current_user.id,
        "email":      current_user.email,
        "name":       current_user.name or "",
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
        "last_login": current_user.last_login.isoformat() if current_user.last_login else None,
    }


@router.get("/google")
def google_redirect(client: str = "web"):
    """
    Redirect to Google consent screen.
    client='web'    → POST-login redirect to /#token=... (fragment, not query)
    client='mobile' → redirect to stocksbreakout://oauth-callback?token=...
    """
    if not _cfg.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=501,
            detail="Google OAuth not configured — add GOOGLE_CLIENT_ID to .env",
        )
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = client

    params = {
        "client_id":     _cfg.GOOGLE_CLIENT_ID,
        "redirect_uri":  _cfg.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         state,
        "access_type":   "offline",
        "prompt":        "select_account",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return RedirectResponse(url)


@router.get("/google/callback")
def google_callback(code: str, state: str, db: Session = Depends(get_db)):
    """
    Exchange Google auth code for id_token, upsert user, return JWT.

    Web: redirects to /#token=... (URL fragment — not sent in Referer or logs).
    Dashboard: httpOnly cookie + redirect with no token in the URL.
    Mobile: custom-scheme query (not an HTTP URL, so not Referer-able).
    """
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid OAuth state — please try again")
    client_type = _oauth_states.pop(state)

    if not _cfg.GOOGLE_CLIENT_ID or not _cfg.GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")

    # Exchange code for Google tokens
    token_resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code":          code,
            "client_id":     _cfg.GOOGLE_CLIENT_ID,
            "client_secret": _cfg.GOOGLE_CLIENT_SECRET,
            "redirect_uri":  _cfg.GOOGLE_REDIRECT_URI,
            "grant_type":    "authorization_code",
        },
        timeout=10,
    )
    if not token_resp.is_success:
        raise HTTPException(status_code=400, detail="Google token exchange failed")

    id_token_str = token_resp.json().get("id_token", "")
    if not id_token_str:
        raise HTTPException(status_code=400, detail="No id_token in Google response")

    # Decode id_token payload (received directly from Google — no sig verify needed)
    parts = id_token_str.split(".")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    claims = _json.loads(base64.urlsafe_b64decode(padded))

    google_id = claims.get("sub")
    email = (claims.get("email") or "").lower()
    name = claims.get("name") or (email.split("@")[0] if email else "")

    if not google_id or not email:
        raise HTTPException(status_code=400, detail="Incomplete Google profile")

    if not _cfg.google_email_allowed(email):
        raise HTTPException(
            status_code=403,
            detail="Google account is not allowlisted",
        )

    # Upsert: find by google_id first, then by email (account linking).
    # Linking is only reached for allowlisted emails (checked above).
    user = (
        db.query(User).filter(User.google_id == google_id).first()
        or db.query(User).filter(User.email == email).first()
    )

    if user is None:
        user = User(
            id=str(uuid.uuid4()),
            email=email,
            google_id=google_id,
            name=name,
            created_at=datetime.now(timezone.utc),
        )
        db.add(user)
    else:
        if user.google_id is None:
            user.google_id = google_id
        user.name = name

    user.last_login = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    token = create_user_token(user.id, user.email)
    return _deliver_token(token, client_type)


def _cookie_domain_for(url: str) -> str | None:
    """Parent-domain cookie so api.* can hand a token to dashboard.*.

    Configurable via COOKIE_DOMAIN for deployments other than the default
    (e.g. a multi-part TLD like .co.uk, or where api/dashboard don't share a
    domain suffix). Blank COOKIE_DOMAIN derives the parent domain from this
    URL's own host (last two labels) — correct for gilhadas-stocks.com and
    any other simple single-part-TLD domain without hardcoding one name.
    """
    host = (urlparse(url).hostname or "").lower()
    override = _cfg.COOKIE_DOMAIN.strip()
    if override:
        dom = override if override.startswith(".") else f".{override}"
        return dom if (host == dom.lstrip(".") or host.endswith(dom)) else None
    labels = host.split(".")
    return "." + ".".join(labels[-2:]) if len(labels) >= 2 else None


def _deliver_token(token: str, client_type: str) -> RedirectResponse:
    """Hand the JWT to the client without putting it in an HTTP query string.

    Fragment (#token=) is used for the mobile web SPA (JS can read it; browsers
    do not send fragments in Referer or to the server). The Streamlit dashboard
    cannot see fragments, so it gets a short-lived httpOnly cookie instead.
    Native mobile keeps a custom-scheme query param — that is not an HTTP URL
    and is not Referer-able; existing app builds parse searchParams.
    """
    app_scheme = os.getenv("MOBILE_APP_SCHEME", "stocksbreakout")

    if client_type == "mobile":
        return RedirectResponse(f"{app_scheme}://oauth-callback?token={token}")

    if client_type == "dashboard":
        dashboard_url = os.getenv("DASHBOARD_PUBLIC_URL", "").rstrip("/")
        if not dashboard_url:
            return _fragment_redirect("/", token)
        resp = RedirectResponse(dashboard_url)
        resp.set_cookie(
            _cfg.OAUTH_COOKIE_NAME,
            token,
            max_age=120,
            httponly=True,
            secure=dashboard_url.startswith("https://"),
            samesite="lax",
            path="/",
            domain=_cookie_domain_for(dashboard_url),
        )
        return resp

    return _fragment_redirect("/", token)


def _fragment_redirect(path: str, token: str) -> RedirectResponse:
    # Token is in the fragment so it never appears in server logs or Referer.
    return RedirectResponse(f"{path}#token={token}")
