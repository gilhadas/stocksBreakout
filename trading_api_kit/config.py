"""
trading_api_kit.config — single source of truth for all env-driven settings.
All fields fall back to safe defaults so the lib works with zero .env setup.

Override via environment variables or a .env file in the project root.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the process's working directory (project root)
load_dotenv(Path.cwd() / ".env", override=False)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── Auth ──────────────────────────────────────────────────────────────────────
API_SECRET_KEY: str = _env("API_SECRET_KEY", "change-me-in-production")
"""JWT signing secret. MUST be overridden in production."""

JWT_ALGORITHM: str = "HS256"
JWT_EXPIRY_DAYS: int = 30

APP_PASSWORD: str = _env("APP_PASSWORD", "")
"""Legacy single-password login (kept for backward compat)."""

DEFAULT_USER_EMAIL: str = _env("DEFAULT_USER_EMAIL", "")
DEFAULT_USER_ID: str = _env("DEFAULT_USER_ID", "")

# ── Google OAuth ──────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID: str = _env("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET: str = _env("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI: str = _env("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")

# ── Database ──────────────────────────────────────────────────────────────────
DB_URL: str = _env("DATABASE_URL", "")
"""
Full SQLAlchemy URL, e.g.:
  sqlite:///./scanner_output/users.db   (default, auto-created)
  postgresql://user:pass@host/dbname
If empty, defaults to SQLite at scanner_output/users.db.
"""

DB_DEFAULT_PATH: Path = Path(_env("DB_PATH", "scanner_output/users.db"))

# ── Push notifications ────────────────────────────────────────────────────────
PUSH_TOKEN_FILE: Path = Path(_env("PUSH_TOKEN_FILE", "scanner_output/.expo_push_tokens.json"))

# ── Admin ─────────────────────────────────────────────────────────────────────
ADMIN_SECRET: str = _env("ADMIN_SECRET", "")
"""Required header value for /admin/* routes. Must be set to enable admin."""

# ── CORS ──────────────────────────────────────────────────────────────────────
CORS_ORIGINS: list[str] = _env("CORS_ORIGINS", "*").split(",")
"""Comma-separated allowed origins. Defaults to wildcard (dev-friendly)."""
