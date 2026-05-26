"""Unit tests — push_registry.py."""
import tempfile
from pathlib import Path
import pytest
import trading_api_kit.config as cfg
import trading_api_kit.push_registry as pr


@pytest.fixture(autouse=True)
def temp_token_file(tmp_path, monkeypatch):
    """Redirect token storage to a temp file for each test."""
    f = tmp_path / "push_tokens.json"
    monkeypatch.setattr(cfg, "PUSH_TOKEN_FILE", f)
    monkeypatch.setattr(pr, "PUSH_TOKEN_FILE", f)
    return f


def test_register_new_token():
    assert pr.register_token("ExponentPushToken[abc]") is True


def test_register_duplicate_token():
    pr.register_token("ExponentPushToken[abc]")
    assert pr.register_token("ExponentPushToken[abc]") is False


def test_get_all_tokens():
    pr.register_token("tok-1")
    pr.register_token("tok-2")
    tokens = pr.get_all_tokens()
    assert "tok-1" in tokens
    assert "tok-2" in tokens


def test_remove_existing_token():
    pr.register_token("tok-del")
    assert pr.remove_token("tok-del") is True
    assert "tok-del" not in pr.get_all_tokens()


def test_remove_nonexistent_token():
    assert pr.remove_token("does-not-exist") is False


def test_empty_file_returns_empty_list(temp_token_file):
    # File doesn't exist yet
    assert pr.get_all_tokens() == []
