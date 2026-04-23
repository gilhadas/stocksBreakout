"""
FastAPI dependency: get_current_user
Decodes JWT, looks up User in DB.
Handles legacy tokens (sub='portfolio_user') for 30-day backward compat.
"""
import os
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from api.auth import decode_token
from api.database import get_db
from api.models import User

_bearer = HTTPBearer()

_LEGACY_SUB = "portfolio_user"


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_token(creds.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    sub = payload.get("sub", "")
    email = payload.get("email", os.getenv("DEFAULT_USER_EMAIL", ""))

    if sub == _LEGACY_SUB:
        # Old token without user_id — resolve by email
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            raise HTTPException(status_code=401, detail="User not found — please log in again")
        return user

    user = db.query(User).filter(User.id == sub).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user
