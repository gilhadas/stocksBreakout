"""
trading_api_kit.config — single source of truth for all env-driven settings.

Override via environment variables or a .env file in the project root.

Boot is fail-closed for JWT signing: a missing or documented-default
API_SECRET_KEY must not mint tokens. See assert_boot_config().
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the process's working directory (project root)
load_dotenv(Path.cwd() / ".env", override=False)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# Documented insecure values that must never be used to boot or sign tokens.
INSECURE_DEFAULT_API_SECRET = "change-me-in-production"
INSECURE_DEFAULT_APP_PASSWORD = "breakout2026"

# Always-allowlisted Google accounts (production operators). Extra addresses
# come from GOOGLE_ALLOWLIST (comma-separated). Comparison is case-insensitive.
DEFAULT_GOOGLE_ALLOWLIST: tuple[str, ...] = (
    "gil.hadas@gmail.com",
    "gil.hadas+1@gmail.com",
)

# First-party web origins. Never include "*".
DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "https://gilhadas-stocks.com",
    "https://api.gilhadas-stocks.com",
    "https://dashboard.gilhadas-stocks.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:8501",
    "http://127.0.0.1:8501",
)


# ── Auth ──────────────────────────────────────────────────────────────────────
API_SECRET_KEY: str = _env("API_SECRET_KEY", "")
"""JWT signing secret. Required; empty or the documented default refuses boot."""

JWT_ALGORITHM: str = "HS256"
JWT_EXPIRY_DAYS: int = 30

APP_PASSWORD: str = _env("APP_PASSWORD", "")
"""Legacy single-password login. Blank disables it; the documented default is refused."""

DEFAULT_USER_EMAIL: str = _env("DEFAULT_USER_EMAIL", "")
DEFAULT_USER_ID: str = _env("DEFAULT_USER_ID", "")

# ── Google OAuth ──────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID: str = _env("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET: str = _env("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI: str = _env("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
def google_allowlist() -> set[str]:
    """Emails permitted to authenticate with Google. Default operators plus env extras."""
    emails = {e.lower() for e in DEFAULT_GOOGLE_ALLOWLIST}
    # Read env at call time so tests can monkeypatch GOOGLE_ALLOWLIST.
    for part in _env("GOOGLE_ALLOWLIST", "").split(","):
        extra = part.strip().lower()
        if extra:
            emails.add(extra)
    return emails


def google_email_allowed(email: str) -> bool:
    return bool(email) and email.strip().lower() in google_allowlist()


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
OAUTH_COOKIE_NAME = "sb_oauth_token"


def parse_cors_origins(raw: str | None = None) -> list[str]:
    """
    Explicit origin list. Empty/unset → DEFAULT_CORS_ORIGINS.
    A wildcard is never accepted: this app always sends credentials.
    """
    if raw is None:
        raw = _env("CORS_ORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if not origins:
        return list(DEFAULT_CORS_ORIGINS)
    if any(o == "*" for o in origins):
        raise RuntimeError(
            "CORS_ORIGINS cannot include '*' while allow_credentials=True. "
            "Set an explicit comma-separated origin list."
        )
    return origins


CORS_ORIGINS: list[str] = parse_cors_origins()


def assert_boot_config() -> None:
    """Refuse to start with a documented-default JWT secret, password, or wildcard CORS."""
    secret = (API_SECRET_KEY or "").strip()
    if not secret or secret == INSECURE_DEFAULT_API_SECRET:
        raise RuntimeError(
            "API_SECRET_KEY is missing or still the documented default "
            f"{INSECURE_DEFAULT_API_SECRET!r}. Set a unique secret in .env."
        )
    app_pw = (APP_PASSWORD or "").strip()
    if app_pw == INSECURE_DEFAULT_APP_PASSWORD:
        raise RuntimeError(
            "APP_PASSWORD is the documented default and cannot be used. "
            "Set a unique password or leave it blank to disable legacy login."
        )
    # Re-parse so a monkeypatched CORS_ORIGINS / env is honored, and "*" never slips through.
    origins = list(CORS_ORIGINS) if CORS_ORIGINS else parse_cors_origins()
    if not origins or any(o == "*" for o in origins):
        raise RuntimeError(
            "CORS_ORIGINS must be an explicit origin list; '*' + credentials is not allowed."
        )
