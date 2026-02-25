"""
Breakout Scanner Dashboard
Cross-platform web app for running scans, viewing TradingView charts, and backtesting.
"""
import streamlit as st
import sys
import os
from pathlib import Path
import logging


# Ensure project root is on Python path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))
logging.basicConfig(level=logging.DEBUG) 


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
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key.strip(), value)

st.set_page_config(
    page_title="Breakout Scanner",
    page_icon="chart_with_upwards_trend",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Authentication ---
def check_auth():
    """Simple password gate for team access."""
    try:
        app_password = st.secrets["APP_PASSWORD"]
    except Exception:
        # No secrets configured — allow access (local dev)
        return True

    if st.session_state.get('authenticated'):
        return True

    st.markdown("## Breakout Scanner Login")
    password = st.text_input("Password", type="password", key="login_pw")
    if st.button("Login"):
        if password == app_password:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Wrong password")
    return False


if not check_auth():
    st.stop()

# --- Navigation ---
st.sidebar.title("Breakout Scanner")

# Handle programmatic page switches (from Chart/Back buttons)
_pages = ["Scan", "Signals", "Portfolio", "Chart", "Backtest", "Watchlists"]
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
