"""
trading_api_kit.push_registry — Expo push token storage.

File-based, deduplicated. Token file path is configurable via
PUSH_TOKEN_FILE env var (defaults to scanner_output/.expo_push_tokens.json).

Usage:
    from trading_api_kit.push_registry import register_token, get_all_tokens
"""
import json

from trading_api_kit.config import PUSH_TOKEN_FILE


def register_token(token: str) -> bool:
    """
    Register an Expo push token (deduplicated).
    Returns True if the token was new, False if already registered.
    """
    tokens = get_all_tokens()
    if token in tokens:
        return False
    tokens.append(token)
    PUSH_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    PUSH_TOKEN_FILE.write_text(json.dumps(tokens))
    return True


def get_all_tokens() -> list[str]:
    """Return all registered Expo push tokens."""
    try:
        if PUSH_TOKEN_FILE.exists():
            return json.loads(PUSH_TOKEN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return []


def remove_token(token: str) -> bool:
    """Remove a push token (e.g., when a device unregisters). Returns True if found."""
    tokens = get_all_tokens()
    if token not in tokens:
        return False
    tokens.remove(token)
    PUSH_TOKEN_FILE.write_text(json.dumps(tokens))
    return True
