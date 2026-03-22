"""
Expo push token storage — file-based, deduplicated.
Tokens stored in scanner_output/.expo_push_tokens.json
"""

import json
from pathlib import Path

_TOKEN_FILE = Path('scanner_output/.expo_push_tokens.json')


def register_token(token: str) -> bool:
    """Add an Expo push token (deduplicated). Returns True if new."""
    tokens = get_all_tokens()
    if token in tokens:
        return False
    tokens.append(token)
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(json.dumps(tokens))
    return True


def get_all_tokens() -> list[str]:
    """Return all registered Expo push tokens."""
    try:
        if _TOKEN_FILE.exists():
            return json.loads(_TOKEN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return []
