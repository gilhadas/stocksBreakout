"""
trading_api_kit — reusable FastAPI auth + push layer for trading mobile apps.

Provides:
  - JWT auth (email/password + Google OAuth)
  - SQLite/PostgreSQL user management
  - Expo push token registry
  - Admin dashboard
  - Base FastAPI app factory

Usage:
    from trading_api_kit import create_app
    from trading_api_kit.auth_routes import router as auth_router
    from trading_api_kit.admin_routes import router as admin_router

    app = create_app(title="MyScanner", version="1.0")
    app.include_router(auth_router)
    app.include_router(admin_router)

    @app.get("/portfolio")
    def get_portfolio():
        ...  # your scanner-specific logic
"""

from trading_api_kit.factory import create_app
from trading_api_kit.deps import get_current_user
from trading_api_kit.database import get_db, create_tables

__all__ = ["create_app", "get_current_user", "get_db", "create_tables"]
__version__ = "1.0.0"
