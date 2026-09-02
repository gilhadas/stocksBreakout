"""
trading_api_kit.factory — FastAPI app factory.

Creates a pre-configured FastAPI app with:
  - CORS middleware
  - Auth + admin routers mounted
  - Lifespan that creates DB tables and default user on first boot
  - Static file serving (optional)

Usage:
    from trading_api_kit import create_app

    app = create_app(title="MyScanner", version="1.0")

    @app.get("/my-endpoint")
    def my_endpoint():
        return {"ok": True}
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import trading_api_kit.config as _cfg
from trading_api_kit.config import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from trading_api_kit.database import create_tables
import trading_api_kit.database as _db  # lazy reference — never early-bind SessionLocal
from trading_api_kit.models import User
from trading_api_kit.auth_routes import router as auth_router
from trading_api_kit.admin_routes import router as admin_router


def _ensure_default_user():
    """Create the default user row on first startup so legacy JWT tokens resolve."""
    if not DEFAULT_USER_EMAIL or not DEFAULT_USER_ID:
        return
    db = _db.SessionLocal()  # always resolved at call time, never early-bound
    try:
        existing = db.query(User).filter(User.email == DEFAULT_USER_EMAIL).first()
        if existing is None:
            db.add(User(
                id=DEFAULT_USER_ID,
                email=DEFAULT_USER_EMAIL,
                name=DEFAULT_USER_EMAIL.split("@")[0],
                created_at=datetime.now(timezone.utc),
            ))
            db.commit()
    finally:
        db.close()


def create_app(
    title: str = "Trading API",
    version: str = "1.0.0",
    static_dir: str | Path | None = None,
    static_path: str = "/static",
    include_auth: bool = True,
    include_admin: bool = True,
) -> FastAPI:
    """
    Create and return a configured FastAPI application.

    Args:
        title:         OpenAPI title shown in /docs.
        version:       API version string.
        static_dir:    Path to a directory of static files to serve (e.g., Expo web build).
                       If None, or if the directory doesn't exist yet (e.g. the Expo
                       web build is gitignored and hasn't been run on this checkout),
                       no static files are served.
        static_path:   URL prefix for static files (default: "/static").
        include_auth:  Mount /auth/* routes (default: True).
        include_admin: Mount /admin/* routes (default: True).

    Returns:
        FastAPI app instance. Add your own routes after calling this.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        create_tables()
        _ensure_default_user()
        yield

    _cfg.assert_boot_config()

    app = FastAPI(title=title, version=version, lifespan=lifespan)

    origins = list(_cfg.CORS_ORIGINS)
    if not origins or "*" in origins:
        raise RuntimeError(
            "CORS_ORIGINS must be an explicit origin list; '*' + credentials is not allowed."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if include_auth:
        app.include_router(auth_router)

    if include_admin:
        app.include_router(admin_router)

    if static_dir is not None and Path(static_dir).exists():
        from fastapi.staticfiles import StaticFiles
        app.mount(static_path, StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
