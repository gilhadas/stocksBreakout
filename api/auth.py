"""
JWT authentication for the portfolio API.
Supports email/password users and Google OAuth users (stored in SQLite).
Legacy: APP_PASSWORD single-user login kept for backward compat (30-day token TTL).
"""
import os
import time
from pathlib import Path

import bcrypt
import jwt
from dotenv import load_dotenv

_root = Path(__file__).resolve().parent.parent
load_dotenv(_root / '.env')

_SECRET = os.getenv('API_SECRET_KEY', 'change-me-in-production')
_ALGORITHM = 'HS256'
_EXPIRY_DAYS = 30


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_user_token(user_id: str, email: str) -> str:
    now = int(time.time())
    payload = {
        'sub':   user_id,
        'email': email,
        'iat':   now,
        'exp':   now + _EXPIRY_DAYS * 86400,
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ── Legacy single-password login (kept until all 30-day tokens expire) ────────

def create_legacy_token(password: str) -> str | None:
    """Verify against APP_PASSWORD, return JWT with the default user's UUID."""
    load_dotenv(_root / '.env', override=True)
    expected = os.getenv('APP_PASSWORD', '')
    if not expected or password != expected:
        return None
    user_id = os.getenv('DEFAULT_USER_ID', 'portfolio_user')
    email = os.getenv('DEFAULT_USER_EMAIL', '')
    return create_user_token(user_id, email)


# ── Kept for any non-server callers ───────────────────────────────────────────

def create_token(password: str) -> str | None:
    """Alias for create_legacy_token — kept for backward compat."""
    return create_legacy_token(password)


def verify_token(token: str) -> bool:
    return decode_token(token) is not None
