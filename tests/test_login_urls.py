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
