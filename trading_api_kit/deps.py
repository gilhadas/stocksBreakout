"""
trading_api_kit.deps — FastAPI dependency: get_current_user.

Decodes the Bearer JWT from the Authorization header, resolves the User
from the database, and handles legacy tokens (sub='portfolio_user').

Usage:
    from trading_api_kit.deps import get_current_user
    from trading_api_kit.models import User

    @app.get("/protected")
    def protected_route(user: User = Depends(get_current_user)):
        return {"email": user.email}
"""
import os

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from trading_api_kit.auth import decode_token
from trading_api_kit.database import get_db
from trading_api_kit.models import User

_bearer = HTTPBearer()

# Legacy sub value used by old tokens before multi-user support
_LEGACY_SUB = "portfolio_user"


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """
    FastAPI dependency — resolves a valid JWT to a User row.

    Raises 401 if:
      - Token is missing / invalid / expired
      - User not found in DB

    Handles legacy tokens (sub='portfolio_user') by resolving via email.
    """
    payload = decode_token(creds.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    sub = payload.get("sub", "")
    email = payload.get("email", os.getenv("DEFAULT_USER_EMAIL", ""))

    if sub == _LEGACY_SUB:
        # Old single-user tokens — resolve by email
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            raise HTTPException(
                status_code=401,
                detail="User not found — please log in again",
            )
        return user

    user = db.query(User).filter(User.id == sub).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user
