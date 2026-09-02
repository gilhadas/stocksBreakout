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
import json
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
    m = re.search(r'oauth_url\s*=\s*f"\{(\w+)\}/auth/google(?:\?[^"]*)?"', source)
    assert m, 'could not find the oauth_url construction in app.py'
    assert m.group(1) == 'public_api_base', (
        f'oauth_url is built from {m.group(1)!r}; the browser cannot resolve '
        'an internal hostname like http://api:8000')


def test_public_base_falls_back_to_api_base(source):
    """A single-host deploy (Streamlit Cloud) should need only API_BASE_URL."""
    assert re.search(
        r"public_api_base\s*=\s*_setting\(\s*['\"]PUBLIC_API_BASE_URL['\"]\s*,\s*api_base\s*\)",
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


def test_dashboard_oauth_link_tags_its_client_type(source):
    """Reported 2026-08-02: Google login on the dashboard landed back on the
    mobile web app's root (gilhadas-stocks.com) instead of the dashboard.

    The callback only knows which app to return to via the 'client' query
    param captured into _oauth_states — an untagged link falls into the
    'web' default and gets the mobile app's relative "/#token=" redirect,
    which resolves against the CALLBACK's host (gilhadas-stocks.com), not
    dashboard.gilhadas-stocks.com. Without this tag the bug is silent: the
    login still "succeeds", just on the wrong origin.
    """
    assert re.search(r'/auth/google\?client=dashboard', source), (
        "the dashboard's oauth_url must pass client=dashboard so the "
        "callback knows to redirect back to this app's own host")


def test_no_dead_shadow_auth_routes_file():
    """Reported 2026-08-02: the dashboard OAuth fix was applied to
    api/auth_routes.py, deployed, and verified in isolation — but that file
    was DEAD CODE. api/server.py builds its app via
    trading_api_kit.create_app(), which wires up trading_api_kit/auth_routes.py
    instead; api/auth_routes.py was never imported anywhere. The fix had zero
    effect on production for hours before this was caught. Guards against ever
    recreating a second, unwired copy of this file.
    """
    assert not (Path(__file__).parent.parent / 'api' / 'auth_routes.py').exists(), (
        'api/auth_routes.py exists again — the real /auth/* routes are served '
        'by trading_api_kit/auth_routes.py via api/server.py\'s create_app(); '
        'a second copy here is dead code that invites fixing the wrong file')


def test_dashboard_callback_redirect_is_absolute(monkeypatch):
    """Companion to the app.py tag above: the backend must special-case
    client_type == 'dashboard' with an ABSOLUTE redirect. A relative
    "/#token=" (the 'web' branch, correct for the mobile app which IS
    gilhadas-stocks.com) would resolve against this callback's own host
    and land back on the mobile app instead of the dashboard.

    Calls the real _deliver_token() instead of grepping source text for these
    strings — a substring check can't tell a name being USED from a name that
    only appears in a comment or a dead branch (see CLAUDE.md's own repeated
    lesson on this trap). This exercises the actual redirect + cookie built.
    """
    monkeypatch.setenv('DASHBOARD_PUBLIC_URL', 'https://dashboard.gilhadas-stocks.com')
    from trading_api_kit.auth_routes import _deliver_token
    from trading_api_kit.config import OAUTH_COOKIE_NAME

    resp = _deliver_token('tok123', 'dashboard')

    location = resp.headers['location']
    assert location == 'https://dashboard.gilhadas-stocks.com', (
        f'dashboard redirect must be absolute, got {location!r}')
    assert '#token=' not in location and '?token=' not in location, (
        'dashboard must not receive the JWT in the URL')

    set_cookie = resp.headers.get('set-cookie', '')
    assert f'{OAUTH_COOKIE_NAME}=tok123' in set_cookie, (
        'dashboard must receive the JWT via an httpOnly cookie, not the URL')
    assert 'HttpOnly' in set_cookie


def test_mobile_app_scheme_matches_app_json():
    """Found alongside the dashboard bug, same function, same root cause: the
    trading_api_kit extraction made MOBILE_APP_SCHEME configurable with a
    generic 'myapp' default, but no .env here was ever updated to override it
    — so native Google login redirects to myapp://oauth-callback, which
    mobile/app.json's registered scheme ("stocksbreakout") never intercepts.
    """
    app_json = json.loads((Path(__file__).parent.parent / 'mobile' / 'app.json').read_text())
    scheme = app_json['expo']['scheme']
    example = (Path(__file__).parent.parent / 'deploy' / '.env.example').read_text()
    assert re.search(rf'^MOBILE_APP_SCHEME={re.escape(scheme)}$', example, re.MULTILINE), (
        f"deploy/.env.example must set MOBILE_APP_SCHEME={scheme} to match "
        "mobile/app.json's scheme, or native Google login silently breaks")


def test_dashboard_public_url_documented():
    example = (Path(__file__).parent.parent / 'deploy' / '.env.example').read_text()
    assert 'DASHBOARD_PUBLIC_URL=' in example, (
        'DASHBOARD_PUBLIC_URL must be documented in deploy/.env.example — '
        "without it in .env, the dashboard OAuth branch falls back to the "
        "same relative fragment redirect as 'web' and the bug reappears silently")


def test_compose_gives_the_dashboard_a_public_url():
    """The container's api_base is internal, so the public one must be explicit."""
    compose = (Path(__file__).parent.parent / 'compose.yaml').read_text()
    assert 'PUBLIC_API_BASE_URL:' in compose, (
        'dashboard has API_BASE_URL=http://api:8000 with no public override, '
        'so its Google login link would point at an unresolvable host')


def test_settings_are_not_read_from_the_environment_alone(source):
    """Streamlit Cloud cannot set OS env vars — config lives in st.secrets.

    Reading only os.getenv() is what left API_BASE_URL unresolved there, so
    api_base fell back to http://127.0.0.1:8000 and login failed with
    'Connection refused' (Errno 111 — Linux, i.e. the Cloud container).
    """
    for name in ('API_BASE_URL', 'PUBLIC_API_BASE_URL', 'GOOGLE_CLIENT_ID'):
        assert not re.search(rf"=\s*os\.getenv\(\s*['\"]{name}['\"]", source), (
            f'{name} is read via os.getenv only; Streamlit Cloud sets secrets, '
            'not environment variables')
        assert re.search(rf"_setting\(\s*['\"]{name}['\"]", source), (
            f'{name} should be resolved through _setting()')


class TestSettingResolution:
    """_setting() must satisfy all three deployments at once."""

    @staticmethod
    def _load(monkeypatch, secrets, env):
        """Exec just _setting() against a stubbed streamlit + env."""
        import types

        fake = types.ModuleType('streamlit')

        class Secrets(dict):
            def __contains__(self, k):
                if secrets is None:      # no secrets file at all -> raises
                    raise RuntimeError('no secrets file')
                return dict.__contains__(self, k)

        fake.secrets = Secrets(secrets or {})
        monkeypatch.setitem(sys.modules, 'streamlit', fake)

        import os as _os
        for k in ('API_BASE_URL',):
            monkeypatch.delenv(k, raising=False)
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)

        src = APP.read_text()
        start = src.index('def _setting(')
        end = src.index('\n\n', src.index('return os.getenv(name, default)'))
        ns = {'st': fake, 'os': _os}
        exec(src[start:end], ns)
        return ns['_setting']

    DEFAULT = 'http://127.0.0.1:8000'

    def test_container_uses_the_env_var(self, monkeypatch):
        """No secrets.toml in the image, so st.secrets raises; env must win."""
        f = self._load(monkeypatch, None, {'API_BASE_URL': 'http://api:8000'})
        assert f('API_BASE_URL', self.DEFAULT) == 'http://api:8000'

    def test_streamlit_cloud_uses_the_secret(self, monkeypatch):
        """The reported bug: secret set, no env var. Must NOT fall back."""
        f = self._load(monkeypatch,
                       {'API_BASE_URL': 'https://api.gilhadas-stocks.com'}, {})
        assert f('API_BASE_URL', self.DEFAULT) == 'https://api.gilhadas-stocks.com'

    def test_secret_takes_precedence_over_env(self, monkeypatch):
        """A Cloud deploy must be able to override a value baked into .env."""
        f = self._load(monkeypatch,
                       {'API_BASE_URL': 'https://api.gilhadas-stocks.com'},
                       {'API_BASE_URL': self.DEFAULT})
        assert f('API_BASE_URL', self.DEFAULT) == 'https://api.gilhadas-stocks.com'

    def test_falls_back_to_default_when_nothing_is_configured(self, monkeypatch):
        f = self._load(monkeypatch, None, {})
        assert f('API_BASE_URL', self.DEFAULT) == self.DEFAULT
