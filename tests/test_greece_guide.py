"""Public /greece trip guide — FileResponse before the SPA mount.

The Expo StaticFiles mount at "/" would swallow /greece if registered first.
These tests pin the route, the tracked HTML path, and the import source.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "api" / "server.py"
GREECE_HTML = ROOT / "docs" / "greece" / "index.html"


@pytest.fixture(scope="module")
def source() -> str:
    return SERVER.read_text(encoding="utf-8")


def test_fileresponse_imported_from_fastapi_responses(source):
    tree = ast.parse(source)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "fastapi.responses":
            names = {a.name for a in node.names}
            if "FileResponse" in names:
                found = True
    assert found, "FileResponse must be imported from fastapi.responses"


def test_greece_routes_registered_before_spa_mount(source):
    greece = source.index("def greece_trip_guide")
    mount = source.index('app.mount("/", StaticFiles')
    assert greece < mount
    assert '@app.get("/greece")' in source
    assert '@app.get("/greece/")' in source


def test_tracked_html_is_valid_hebrew_guide():
    assert GREECE_HTML.is_file()
    raw = GREECE_HTML.read_bytes()
    assert 50_000 < len(raw) < 80_000
    text = raw.decode("utf-8")
    assert text.startswith("<!DOCTYPE html>")
    assert 'lang="he"' in text
    assert 'dir="rtl"' in text


def test_get_greece_and_trailing_slash_return_html():
    import api.server as srv

    with TestClient(srv.app) as client:
        for path in ("/greece", "/greece/"):
            r = client.get(path, follow_redirects=True)
            assert r.status_code == 200, path
            ctype = r.headers.get("content-type", "")
            assert ctype.startswith("text/html"), (path, ctype)
            assert r.content.startswith(b"<!DOCTYPE html>")


def test_missing_file_is_404(monkeypatch):
    import api.server as srv

    monkeypatch.setattr(srv, "_GREECE_HTML", Path("/nonexistent/greece/index.html"))
    with TestClient(srv.app) as client:
        r = client.get("/greece")
        assert r.status_code == 404
