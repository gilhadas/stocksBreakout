"""
trading_api_kit.database — SQLAlchemy engine + session factory.

Supports SQLite (default, auto-created) and any SQLAlchemy-compatible DB
(PostgreSQL, MySQL, etc.) via DATABASE_URL env variable.

Usage:
    from trading_api_kit.database import Base, get_db, create_tables

    # In your own models:
    class MyModel(Base):
        __tablename__ = "my_table"
        ...

    # In lifespan / startup:
    create_tables()
"""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from trading_api_kit.config import DB_URL, DB_DEFAULT_PATH


def _build_engine():
    if DB_URL:
        url = DB_URL
        kwargs = {}
    else:
        # Default: SQLite, auto-create directory
        DB_DEFAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{DB_DEFAULT_PATH}"
        kwargs = {"connect_args": {"check_same_thread": False}}
    return create_engine(url, **kwargs)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    """Shared declarative base — import and subclass in your own models."""
    pass


def get_db():
    """
    FastAPI dependency that yields a DB session.
    Always resolves SessionLocal at call time so test patches work correctly.
    """
    import trading_api_kit.database as _self
    db = _self.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    """Create all tables registered on Base. Call at app startup."""
    from trading_api_kit import models as _  # noqa: F401 — triggers model registration
    Base.metadata.create_all(bind=engine)
