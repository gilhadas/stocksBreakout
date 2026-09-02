"""Security lockdown for the public FastAPI (trading_api_kit).

Covers the fail-closed JWT secret, Google allowlist, and CORS defaults.
Does not touch notifier send paths (keep notify=False / pytest choke intact).
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trading_api_kit.config import (
    DEFAULT_CORS_ORIGINS,
    INSECURE_DEFAULT_API_SECRET,
    INSECURE_DEFAULT_APP_PASSWORD,
    parse_cors_origins,
)
from trading_api_kit.database import Base, get_db
from trading_api_kit.models import User


ROOT = Path(__file__).resolve().parent.parent


def _id_token(email: str, sub: str = "google-sub-1", name: str = "Test User") -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"email": email, "sub": sub, "name": name}).encode()
    ).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{payload}.sig"


class _FakeGoogleToken:
    def __init__(self, email: str, sub: str = "google-sub-1"):
        self._email = email
        self._sub = sub

    @property
    def is_success(self) -> bool:
        return True

    def json(self):
        return {"id_token": _id_token(self._email, self._sub)}


def _kit_client(monkeypatch, extra_env: dict | None = None):
    """Isolated FastAPI app + in-memory DB for OAuth tests."""
    import trading_api_kit.config as cfg
    import trading_api_kit.auth_routes as auth_routes
    from trading_api_kit.factory import create_app

    monkeypatch.setattr(cfg, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(cfg, "GOOGLE_CLIENT_SECRET", "test-client-secret")
    for k, v in (extra_env or {}).items():
        monkeypatch.setenv(k, v)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    app = create_app(title="SecurityTest", version="0")

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, raise_server_exceptions=True), Session, auth_routes


# ── API_SECRET_KEY ────────────────────────────────────────────────────────────

def test_create_app_refuses_default_api_secret(monkeypatch):
    import trading_api_kit.config as cfg
    from trading_api_kit.factory import create_app

    monkeypatch.setattr(cfg, "API_SECRET_KEY", INSECURE_DEFAULT_API_SECRET)
    with pytest.raises(RuntimeError, match="API_SECRET_KEY"):
        create_app(title="nope")


def test_create_app_refuses_missing_api_secret(monkeypatch):
    import trading_api_kit.config as cfg
    from trading_api_kit.factory import create_app

    monkeypatch.setattr(cfg, "API_SECRET_KEY", "")
    with pytest.raises(RuntimeError, match="API_SECRET_KEY"):
        create_app(title="nope")


def test_create_app_refuses_documented_default_password(monkeypatch):
    import trading_api_kit.config as cfg
    from trading_api_kit.factory import create_app

    monkeypatch.setattr(cfg, "APP_PASSWORD", INSECURE_DEFAULT_APP_PASSWORD)
    with pytest.raises(RuntimeError, match="APP_PASSWORD"):
        create_app(title="nope")


def test_leftover_api_auth_has_no_fail_open_default():
    src = (ROOT / "api" / "auth.py").read_text()
    assert "change-me-in-production" not in src
    assert "from trading_api_kit.auth import" in src


def test_create_user_token_refuses_default_secret(monkeypatch):
    import trading_api_kit.config as cfg
    from trading_api_kit.auth import create_user_token

    monkeypatch.setattr(cfg, "API_SECRET_KEY", INSECURE_DEFAULT_API_SECRET)
    with pytest.raises(RuntimeError, match="API_SECRET_KEY"):
        create_user_token("uid", "a@b.com")


# ── CORS ──────────────────────────────────────────────────────────────────────

def test_cors_default_is_not_wildcard_with_credentials():
    origins = parse_cors_origins("")
    assert "*" not in origins
    assert "https://gilhadas-stocks.com" in origins
    assert "https://api.gilhadas-stocks.com" in origins
    assert "http://127.0.0.1:8501" in origins
    assert list(DEFAULT_CORS_ORIGINS) == origins


def test_cors_wildcard_env_is_rejected():
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        parse_cors_origins("*")


def test_cors_preflight_does_not_echo_unknown_origin():
    from trading_api_kit.factory import create_app

    app = create_app(title="CorsCheck", version="0")
    client = TestClient(app)
    evil = client.options(
        "/auth/login",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    acao = evil.headers.get("access-control-allow-origin")
    assert acao != "*"
    assert acao != "https://evil.example"

    allowed = client.options(
        "/auth/login",
        headers={
            "Origin": "https://gilhadas-stocks.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert allowed.headers.get("access-control-allow-origin") == "https://gilhadas-stocks.com"
    assert allowed.headers.get("access-control-allow-credentials") == "true"


# ── Google allowlist ──────────────────────────────────────────────────────────

def test_unallowlisted_google_user_is_403_and_creates_no_row(monkeypatch):
    client, Session, auth_routes = _kit_client(monkeypatch)
    auth_routes._oauth_states["st"] = "web"
    monkeypatch.setattr(
        auth_routes.httpx, "post",
        lambda *a, **k: _FakeGoogleToken("attacker@gmail.com"),
    )

    r = client.get("/auth/google/callback", params={"code": "x", "state": "st"},
                   follow_redirects=False)
    assert r.status_code == 403
    assert r.json()["detail"] == "Google account is not allowlisted"

    db = Session()
    try:
        assert db.query(User).filter(User.email == "attacker@gmail.com").first() is None
        assert db.query(User).count() == 0
    finally:
        db.close()


def test_allowlisted_google_email_can_authenticate(monkeypatch):
    client, Session, auth_routes = _kit_client(monkeypatch)
    auth_routes._oauth_states["st"] = "web"
    monkeypatch.setattr(
        auth_routes.httpx, "post",
        lambda *a, **k: _FakeGoogleToken("gil.hadas@gmail.com"),
    )

    r = client.get("/auth/google/callback", params={"code": "x", "state": "st"},
                   follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    location = r.headers.get("location", "")
    assert "?token=" not in location
    assert "#token=" in location

    db = Session()
    try:
        user = db.query(User).filter(User.email == "gil.hadas@gmail.com").first()
        assert user is not None
        assert user.google_id == "google-sub-1"
    finally:
        db.close()


def test_allowlist_env_extra_email_can_authenticate(monkeypatch):
    client, Session, auth_routes = _kit_client(
        monkeypatch, extra_env={"GOOGLE_ALLOWLIST": "ops@example.com"}
    )
    auth_routes._oauth_states["st"] = "web"
    monkeypatch.setattr(
        auth_routes.httpx, "post",
        lambda *a, **k: _FakeGoogleToken("ops@example.com"),
    )

    r = client.get("/auth/google/callback", params={"code": "x", "state": "st"},
                   follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    db = Session()
    try:
        assert db.query(User).filter(User.email == "ops@example.com").first() is not None
    finally:
        db.close()


def test_existing_allowlisted_row_can_link_google(monkeypatch):
    client, Session, auth_routes = _kit_client(monkeypatch)
    db = Session()
    db.add(User(id="existing-1", email="gil.hadas@gmail.com", name="Gil"))
    db.commit()
    db.close()

    auth_routes._oauth_states["st"] = "web"
    monkeypatch.setattr(
        auth_routes.httpx, "post",
        lambda *a, **k: _FakeGoogleToken("gil.hadas@gmail.com", sub="new-google"),
    )
    r = client.get("/auth/google/callback", params={"code": "x", "state": "st"},
                   follow_redirects=False)
    assert r.status_code in (302, 303, 307)

    db = Session()
    try:
        rows = db.query(User).filter(User.email == "gil.hadas@gmail.com").all()
        assert len(rows) == 1
        assert rows[0].id == "existing-1"
        assert rows[0].google_id == "new-google"
    finally:
        db.close()


def test_dashboard_oauth_sets_httponly_cookie_not_query_token(monkeypatch):
    client, _Session, auth_routes = _kit_client(
        monkeypatch,
        extra_env={"DASHBOARD_PUBLIC_URL": "https://dashboard.gilhadas-stocks.com"},
    )
    auth_routes._oauth_states["st"] = "dashboard"
    monkeypatch.setattr(
        auth_routes.httpx, "post",
        lambda *a, **k: _FakeGoogleToken("gil.hadas@gmail.com"),
    )
    r = client.get("/auth/google/callback", params={"code": "x", "state": "st"},
                   follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers.get("location") == "https://dashboard.gilhadas-stocks.com"
    assert "?token=" not in r.headers.get("location", "")
    cookie = r.headers.get("set-cookie", "")
    assert "sb_oauth_token=" in cookie
    assert "httponly" in cookie.lower()


def test_http_oauth_redirect_does_not_put_jwt_in_query_string():
    src = (ROOT / "trading_api_kit" / "auth_routes.py").read_text()
    assert "/?token=" not in src
    assert "#token=" in src
