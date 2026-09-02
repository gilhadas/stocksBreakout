"""
trading_api_kit.auth — JWT creation/verification + bcrypt password helpers.

Supports:
  - Email + password (hashed with bcrypt)
  - Google OAuth (token is created after id_token verification in auth_routes)
  - Legacy single-password mode (APP_PASSWORD env var)

All config values are resolved lazily at call time so monkeypatching in tests
works correctly without needing to patch the auth module itself.
"""
import time

import bcrypt
import jwt

import trading_api_kit.config as _cfg


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Hash a plain-text password with bcrypt."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against its bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ── JWT ───────────────────────────────────────────────────────────────────────

def _require_api_secret() -> str:
    """Refuse to sign or verify with a missing/documented-default secret."""
    secret = (_cfg.API_SECRET_KEY or "").strip()
    if not secret or secret == _cfg.INSECURE_DEFAULT_API_SECRET:
        raise RuntimeError(
            "API_SECRET_KEY is missing or still the documented default. "
            "Set a unique secret in .env."
        )
    return secret


def create_user_token(user_id: str, email: str) -> str:
    """Create a signed JWT for a user. Expires in JWT_EXPIRY_DAYS days."""
    secret = _require_api_secret()
    now = int(time.time())
    payload = {
        "sub":   user_id,
        "email": email,
        "iat":   now,
        "exp":   now + _cfg.JWT_EXPIRY_DAYS * 86_400,
    }
    return jwt.encode(payload, secret, algorithm=_cfg.JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    """
    Decode and verify a JWT. Returns the payload dict or None if
    the token is expired / invalid.
    """
    try:
        return jwt.decode(token, _require_api_secret(), algorithms=[_cfg.JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ── Legacy single-password login ──────────────────────────────────────────────

def create_legacy_token(password: str) -> str | None:
    """
    Verify against APP_PASSWORD, return a JWT for the default user.
    Kept for backward compat with old mobile clients that only send a password.
    Config values resolved lazily so test monkeypatching works.
    """
    app_pw = _cfg.APP_PASSWORD
    if not app_pw or password != app_pw:
        return None
    user_id = _cfg.DEFAULT_USER_ID or "default_user"
    email   = _cfg.DEFAULT_USER_EMAIL or ""
    return create_user_token(user_id, email)


# Alias kept for any non-server callers
create_token = create_legacy_token


def verify_token(token: str) -> bool:
    """Return True if the token is valid and not expired."""
    return decode_token(token) is not None
