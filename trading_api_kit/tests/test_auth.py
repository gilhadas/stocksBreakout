"""Unit tests — auth.py: JWT, bcrypt, legacy token."""
from trading_api_kit.auth import (
    hash_password, verify_password,
    create_user_token, decode_token,
    create_legacy_token,
)
import trading_api_kit.config as cfg


# ── Password ──────────────────────────────────────────────────────────────────

def test_hash_and_verify_password():
    hashed = hash_password("secret123")
    assert verify_password("secret123", hashed)


def test_wrong_password_rejected():
    hashed = hash_password("correct")
    assert not verify_password("wrong", hashed)


# ── JWT ───────────────────────────────────────────────────────────────────────

def test_create_and_decode_token():
    token = create_user_token("uid-1", "user@example.com")
    payload = decode_token(token)
    assert payload is not None
    assert payload["sub"] == "uid-1"
    assert payload["email"] == "user@example.com"


def test_invalid_token_returns_none():
    assert decode_token("not.a.valid.token") is None


def test_tampered_token_returns_none():
    token = create_user_token("uid-1", "user@example.com")
    tampered = token[:-4] + "XXXX"
    assert decode_token(tampered) is None


# ── Legacy token ──────────────────────────────────────────────────────────────

def test_legacy_token_wrong_password(monkeypatch):
    monkeypatch.setattr(cfg, "APP_PASSWORD", "correct-password")
    assert create_legacy_token("wrong-password") is None


def test_legacy_token_no_app_password(monkeypatch):
    monkeypatch.setattr(cfg, "APP_PASSWORD", "")
    assert create_legacy_token("any") is None


def test_legacy_token_correct_password(monkeypatch):
    monkeypatch.setattr(cfg, "APP_PASSWORD", "mypass")
    monkeypatch.setattr(cfg, "DEFAULT_USER_ID", "legacy-id")
    monkeypatch.setattr(cfg, "DEFAULT_USER_EMAIL", "legacy@example.com")
    token = create_legacy_token("mypass")
    assert token is not None
    payload = decode_token(token)
    assert payload["sub"] == "legacy-id"
