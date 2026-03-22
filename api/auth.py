"""
JWT authentication for the portfolio API.
Uses APP_PASSWORD from .env for login, API_SECRET_KEY for signing.
"""

import os
import time
import jwt
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_root = Path(__file__).resolve().parent.parent
load_dotenv(_root / '.env')

_SECRET = os.getenv('API_SECRET_KEY', 'change-me-in-production')
_ALGORITHM = 'HS256'
_EXPIRY_DAYS = 30


def create_token(password: str) -> str | None:
    """Verify password against APP_PASSWORD and return a signed JWT, or None."""
    expected = os.getenv('APP_PASSWORD', '')
    if not expected or password != expected:
        return None
    payload = {
        'sub': 'portfolio_user',
        'iat': int(time.time()),
        'exp': int(time.time()) + _EXPIRY_DAYS * 86400,
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALGORITHM)


def verify_token(token: str) -> bool:
    """Return True if the token is valid and not expired."""
    try:
        jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
        return True
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return False
