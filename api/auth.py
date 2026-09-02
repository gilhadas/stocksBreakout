"""
JWT authentication — thin shim over trading_api_kit.auth.

This module used to fail-open with a documented default API_SECRET_KEY.
It must not mint or verify tokens on its own. All signing goes through
the kit, which refuses a missing/default secret.
"""
from trading_api_kit.auth import (  # noqa: F401
    create_legacy_token,
    create_token,
    create_user_token,
    decode_token,
    hash_password,
    verify_password,
    verify_token,
)
