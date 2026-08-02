"""
Breakout Scanner Dashboard
Cross-platform web app for running scans, viewing TradingView charts, and backtesting.
"""
import streamlit as st
import sys
import os
from pathlib import Path
import logging
import json
import urllib.parse


# Ensure project root is on Python path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))
logging.basicConfig(level=logging.INFO)
logging.getLogger().setLevel(logging.INFO)  # force override Streamlit's pre-config


# Load .env
_env_file = PROJECT_ROOT / '.env'
if _env_file.exists():
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                if line.startswith('export '):
                    line = line[7:]
                key, _, value = line.partition('=')
                value = value.split('#')[0].strip().strip('"').strip("'")  # strip inline comments
                os.environ.setdefault(key.strip(), value)


def _setting(name: str, default: str) -> str:
    """Resolve a setting from Streamlit secrets, then the environment, then default.

    Order matters and both sources are required, because the three deployments
    configure themselves in three different ways:

      * Streamlit Cloud — there is no way to set an OS environment variable.
        Config lives in st.secrets, and .env does not exist in the repo checkout.
        Reading only os.getenv() is why login failed there with the default
        http://127.0.0.1:8000: nothing listens on that port inside the Streamlit
        Cloud container, giving 'Connection refused' (Errno 111).
      * the sb-dashboard container — real env vars via compose `env_file: .env`
        plus its `environment:` block; no secrets.toml exists in the image.
      * a local run — the .env loader above.

    Secrets are checked first so a Cloud deployment can override a value baked
    into a committed .env. Mirrors utils._is_cloud()'s precedence, including the
    try/except: st.secrets raises when no secrets file exists at all, which is
    the normal case in the container.
    """
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)

st.set_page_config(
    page_title="Breakout Scanner",
    page_icon="chart_with_upwards_trend",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Authentication ---
def check_auth():
    """Email/password + Google OAuth login. Stores JWT token in session."""
    import requests

    if st.session_state.get('authenticated'):
        return True

    # Check for token in URL query params (from Google OAuth redirect)
    query_params = st.query_params
    if 'token' in query_params:
        try:
            token = query_params['token']
            if isinstance(token, list):
                token = token[0]
            st.session_state.token = token
            st.session_state.authenticated = True
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Token error: {e}")

    st.markdown("## Breakout Scanner")

    # Two different URLs, because they are consumed by two different clients:
    #
    #   api_base        — server-side; this Python process calls it. In the
    #                     container that is http://api:8000 over the compose
    #                     network (fast, no tunnel round trip).
    #   public_api_base — handed to the BROWSER as a link. It must be publicly
    #                     resolvable, so http://api:8000 is useless here: a
    #                     Docker-internal hostname no browser can resolve.
    #
    # Conflating them broke Google login on the containerised dashboard: the
    # button rendered a link to http://api:8000/auth/google. The old code tried
    # to paper over it with api_base.replace('127.0.0.1:8000', ...), which only
    # fires when api_base is the DEFAULT — i.e. it worked only on the retired Mac
    # deployment and silently did nothing once API_BASE_URL was set. It also
    # assigned the result to a `redirect_uri` variable that was never used.
    #
    # Defaults to api_base so a single-host deployment (Streamlit Cloud pointing
    # straight at the public API) needs only API_BASE_URL set.
    api_base = _setting('API_BASE_URL', 'http://127.0.0.1:8000')
    public_api_base = _setting('PUBLIC_API_BASE_URL', api_base)

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Login with Email")
        email = st.text_input("Email", key="login_email", value="")
        password = st.text_input("Password", type="password", key="login_pw")
        if st.button("Login", key="email_login_btn"):
            try:
                resp = requests.post(f"{api_base}/auth/login",
                    json={"email": email, "password": password})
                if resp.status_code == 200:
                    token = resp.json().get('token')
                    st.session_state.token = token
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("Invalid credentials")
            except Exception as e:
                st.error(f"Login failed: {e}")

    with col2:
        st.subheader("Legacy Password")
        legacy_pw = st.text_input("App Password", type="password", key="login_legacy_pw")
        if st.button("Login", key="legacy_login_btn"):
            try:
                resp = requests.post(f"{api_base}/auth/login",
                    json={"password": legacy_pw})
                if resp.status_code == 200:
                    token = resp.json().get('token')
                    st.session_state.token = token
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("Invalid password")
            except Exception as e:
                st.error(f"Login failed: {e}")

    st.divider()
    google_client_id = _setting('GOOGLE_CLIENT_ID', '')
    if google_client_id:
        st.markdown("### Or login with Google")
        if st.button("🔑 Login with Google", use_container_width=True):
            # public_api_base, not api_base — the browser follows this link.
            # client=dashboard tags the OAuth state so the callback redirects
            # back to this app's own host, not the mobile web app's root
            # (the un-tagged 'web' default) — see
            # trading_api_kit/auth_routes.py's google_callback().
            oauth_url = f"{public_api_base}/auth/google?client=dashboard"
            st.markdown(f"[Click here to login with Google]({oauth_url})")
    else:
        st.info("Google OAuth not configured yet")

    return False


if not check_auth():
    st.stop()

# --- Navigation ---
st.sidebar.title("Breakout Scanner")

# Handle programmatic page switches (from Chart/Back buttons)
_pages = ["Portfolio", "Scan", "Signals", "Chart", "Backtest", "Watchlists"]
_next = st.session_state.pop('_next_page', None)
if _next and _next in _pages:
    st.session_state['nav_radio'] = _next

page = st.sidebar.radio("Navigate", _pages, key="nav_radio")

st.sidebar.markdown("---")
st.sidebar.caption("V6 | GOLD/PREMIUM/HIGH/STANDARD")

# --- Route to pages ---
if page == "Scan":
    from pages.scan_page import render_scan_page
    render_scan_page()
elif page == "Signals":
    from pages.signals_page import render_signals_page
    render_signals_page()
elif page == "Portfolio":
    from pages.portfolio_page import render_portfolio_page
    render_portfolio_page()
elif page == "Chart":
    from pages.chart_page import render_chart_page
    render_chart_page()
elif page == "Backtest":
    from pages.backtest_page import render_backtest_page
    render_backtest_page()
elif page == "Watchlists":
    from pages.watchlist_page import render_watchlist_page
    render_watchlist_page()
