#!/usr/bin/env python3
"""
The Google OAuth link must be publicly resolvable, not the internal API URL.

WHY THIS EXISTS
---------------
Reported 2026-07-30: "streamlit stopped working with google auth".

app.py used ONE url for two different consumers:

  * server-side  — this Python process POSTs to /auth/login. In the container
    that is http://api:8000, over the compose network.
  * browser-side — the "Login with Google" button renders a LINK the user's
    browser follows. http://api:8000 is a Docker-internal hostname that no
    browser can resolve.

So on the containerised dashboard the button pointed at
http://api:8000/auth/google and could never work. The old code tried to correct
this with `api_base.replace('127.0.0.1:8000', 'gilhadas-stocks.com')`, which
only fires when api_base is the DEFAULT — it worked on the retired Mac
deployment and silently became a no-op the moment API_BASE_URL was set. The
result was also assigned to a `redirect_uri` local that was never read.

These tests parse app.py rather than importing it: importing pulls in Streamlit
and the whole dashboard stack, and the bug is in URL construction, which is
what we can assert cheaply and directly.

Run:
    python -m pytest tests/test_login_urls.py -v
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

APP = Path(__file__).parent.parent / 'app.py'


@pytest.fixture(scope='module')
def source() -> str:
    return APP.read_text()


def test_oauth_link_uses_the_public_base_not_the_internal_one(source):
    """The browser follows this link, so it must not be built from api_base."""
    m = re.search(r'oauth_url\s*=\s*f"\{(\w+)\}/auth/google"', source)
    assert m, 'could not find the oauth_url construction in app.py'
    assert m.group(1) == 'public_api_base', (
        f'oauth_url is built from {m.group(1)!r}; the browser cannot resolve '
        'an internal hostname like http://api:8000')


def test_public_base_falls_back_to_api_base(source):
    """A single-host deploy (Streamlit Cloud) should need only API_BASE_URL."""
    assert re.search(
        r"public_api_base\s*=\s*os\.getenv\(\s*['\"]PUBLIC_API_BASE_URL['\"]\s*,\s*api_base\s*\)",
        source), 'PUBLIC_API_BASE_URL must default to api_base'


def test_the_brittle_string_replace_is_gone(source):
    """It only fired on the default value, so it silently did nothing once set.

    Checks executable lines only: the comment above the fix names the old hack
    on purpose, and a raw substring search would match that and fail forever.
    """
    code = '\n'.join(line for line in source.splitlines()
                     if not line.lstrip().startswith('#'))
    assert "replace('127.0.0.1:8000'" not in code, (
        'the host-rewrite hack is back; it no-ops whenever API_BASE_URL is set')


def test_server_side_login_still_uses_the_internal_base(source):
    """/auth/login is called by this process, so it should stay on api_base."""
    calls = re.findall(r'requests\.post\(f"\{(\w+)\}/auth/login"', source)
    assert calls, 'could not find the /auth/login calls'
    assert set(calls) == {'api_base'}, (
        f'/auth/login should be called via api_base, found {set(calls)}')


def test_compose_gives_the_dashboard_a_public_url():
    """The container's api_base is internal, so the public one must be explicit."""
    compose = (Path(__file__).parent.parent / 'compose.yaml').read_text()
    assert 'PUBLIC_API_BASE_URL:' in compose, (
        'dashboard has API_BASE_URL=http://api:8000 with no public override, '
        'so its Google login link would point at an unresolvable host')
