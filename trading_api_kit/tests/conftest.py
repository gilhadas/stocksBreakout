"""
Shared fixtures for trading_api_kit tests.

Uses StaticPool so all SQLAlchemy sessions share the same in-memory SQLite
connection — tables created in the fixture are visible to route handlers.
Uses app.dependency_overrides to inject the test DB session cleanly.
"""
import os

# Must run before trading_api_kit.config is imported (create_app fail-closes
# on a missing/default API_SECRET_KEY).
os.environ.setdefault("API_SECRET_KEY", "kit-test-secret-not-for-production")
if os.environ.get("API_SECRET_KEY") == "change-me-in-production":
    os.environ["API_SECRET_KEY"] = "kit-test-secret-not-for-production"
os.environ.setdefault("APP_PASSWORD", "")
if os.environ.get("APP_PASSWORD") == "breakout2026":
    os.environ["APP_PASSWORD"] = ""
os.environ.setdefault("CORS_ORIGINS", "")
if os.environ.get("CORS_ORIGINS", "").strip() == "*":
    os.environ["CORS_ORIGINS"] = ""

import datetime
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from trading_api_kit import create_app, get_db
from trading_api_kit.database import Base
from trading_api_kit.models import User
from trading_api_kit.auth import hash_password
import trading_api_kit.admin_routes as _admin_mod


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="session")
def TestSession(test_engine):
    return sessionmaker(bind=test_engine, autocommit=False, autoflush=False)


@pytest.fixture(scope="session")
def app(TestSession):
    _app = create_app(title="Test", version="0.1")

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    _app.dependency_overrides[get_db] = override_get_db
    return _app


@pytest.fixture(scope="session")
def client(app):
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(scope="session")
def seeded_user(TestSession):
    """Create a test user in the DB, return (email, password)."""
    db = TestSession()
    existing = db.query(User).filter(User.email == "fixture@kit.com").first()
    if not existing:
        db.add(User(
            id="fixture-u1",
            email="fixture@kit.com",
            password_hash=hash_password("fixture-pass"),
            created_at=datetime.datetime.now(datetime.timezone.utc),
        ))
        db.commit()
    db.close()
    return "fixture@kit.com", "fixture-pass"


@pytest.fixture
def admin_secret(monkeypatch):
    """Patch ADMIN_SECRET for a single test."""
    secret = "test-admin-secret"
    monkeypatch.setattr(_admin_mod, "ADMIN_SECRET", secret)
    return secret
