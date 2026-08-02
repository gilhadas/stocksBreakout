"""
Auth endpoints:
  POST /auth/login           — email+password (new) or password-only (legacy)
  GET  /auth/google          — redirect to Google consent screen (web only)
  GET  /auth/google/callback — exchange code → upsert user → return JWT
"""
import os
import secrets
import base64
import json as _json
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import User
from api.auth import create_user_token, create_legacy_token, hash_password, verify_password

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_root, '.env'))

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
GOOGLE_REDIRECT_URI = os.getenv(
    'GOOGLE_REDIRECT_URI',
    'https://gilhadas-stocks.com/auth/google/callback'
)

# In-memory CSRF state store (fine for single-process personal tool)
_oauth_states: dict[str, str] = {}  # state_token -> client_type ('web' | 'mobile')


# ── Request models ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str
    email: str | None = None


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    # New-style: email + password
    if req.email:
        user = db.query(User).filter(User.email == req.email.lower()).first()
        if not user or not user.password_hash:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if not verify_password(req.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        user.last_login = datetime.now(timezone.utc)
        db.commit()
        token = create_user_token(user.id, user.email)
        return {"token": token, "user_id": user.id, "email": user.email}

    # Legacy: password-only (old mobile clients)
    token = create_legacy_token(req.password)
    if not token:
        raise HTTPException(status_code=401, detail="Wrong password")
    return {"token": token}


# ── Google OAuth (web browser only) ──────────────────────────────────────────

@router.get("/google")
def google_redirect(client: str = "web"):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth not configured — add GOOGLE_CLIENT_ID to .env")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = client  # 'web' or 'mobile'

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
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid OAuth state — please try again")
    client_type = _oauth_states.pop(state)  # 'web' or 'mobile'

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

    # Decode id_token payload (no sig verification — received directly from Google)
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
        import uuid
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
    if client_type == 'mobile':
        # Native app: custom scheme intercepted by openAuthSessionAsync
        return RedirectResponse(f"stocksbreakout://oauth-callback?token={token}")
    if client_type == 'dashboard':
        # Streamlit dashboard is a SEPARATE origin (dashboard.gilhadas-stocks.com)
        # from this callback (gilhadas-stocks.com) — unlike the mobile web app's
        # relative "/?token=" below, a relative redirect here would resolve
        # against THIS host and land the user back on the mobile app instead.
        # Must be absolute. app.py's check_auth() picks up ?token= the same way
        # _layout.tsx does.
        dashboard_url = os.getenv('DASHBOARD_PUBLIC_URL', 'https://dashboard.gilhadas-stocks.com')
        return RedirectResponse(f"{dashboard_url}/?token={token}")
    # Web browser: redirect to root with token param; _layout.tsx useEffect catches it
    return RedirectResponse(f"/?token={token}")
