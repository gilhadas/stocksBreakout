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
from datetime import datetime, timezone
from urllib.parse import urlencode

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
from trading_api_kit.config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
)
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
    client='web'    → POST-login redirect to /?token=...
    client='mobile' → redirect to stocksbreakout://oauth-callback?token=...
    """
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=501,
            detail="Google OAuth not configured — add GOOGLE_CLIENT_ID to .env",
        )
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = client

    params = {
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  GOOGLE_REDIRECT_URI,
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

    Web: redirects to /?token=...
    Mobile: redirects to <APP_SCHEME>://oauth-callback?token=...
    """
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid OAuth state — please try again")
    client_type = _oauth_states.pop(state)

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")

    # Exchange code for Google tokens
    token_resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code":          code,
            "client_id":     GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri":  GOOGLE_REDIRECT_URI,
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

    # Upsert: find by google_id first, then by email (account linking)
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

    import os
    app_scheme = os.getenv("MOBILE_APP_SCHEME", "myapp")

    if client_type == "mobile":
        return RedirectResponse(f"{app_scheme}://oauth-callback?token={token}")
    # Web: redirect to root — frontend picks up ?token= in useEffect
    return RedirectResponse(f"/?token={token}")
