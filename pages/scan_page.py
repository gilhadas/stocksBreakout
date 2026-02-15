"""Scan Dashboard — run scans and view signal results."""
import streamlit as st
import pandas as pd
import asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def _get_watchlist_files():
    """Get available watchlist files from input/ directory."""
    input_dir = PROJECT_ROOT / 'input'
    files = sorted(input_dir.glob('*.txt'))
    return {f.stem: str(f) for f in files if f.stem != 'email_recipients'}


def _load_latest_signals():
    """Load the most recent signals CSV."""
    signals_dir = PROJECT_ROOT / 'scanner_output' / 'signals'
    if not signals_dir.exists():
        return None
    csvs = sorted(signals_dir.glob('signals_*.csv'), reverse=True)
    if not csvs:
        return None
    return pd.read_csv(csvs[0]), csvs[0].name


def _run_scan(mode, watchlist_path):
    """Run the scanner and return results as DataFrame."""
    from orchestrator import ScannerOrchestrator
    from utils import get_watchlist_from_file, classify_market_regime
    from config import MODES

    symbols = get_watchlist_from_file(watchlist_path)
    if not symbols:
        return None, "No symbols loaded", None

    orchestrator = ScannerOrchestrator(None, yf_fallback=True)
    timeframe = MODES[mode]['default_timeframe']
    lookback = MODES[mode]['lookback']

    results = asyncio.run(orchestrator.scan_watchlist(
        watchlist=symbols,
        mode=mode,
        timeframe=timeframe,
        lookback=lookback,
        detect_bounces=True,
    ))

    # Get market regime
    spy_perf, spy_vol = asyncio.run(
        orchestrator.market_data.get_spy_performance(timeframe, lookback)
    )
    regime = classify_market_regime(spy_perf, spy_vol)

    # Save results
    output_file = orchestrator.save_results(results, mode, 'signals')

    return results, regime, f"SPY {spy_perf:+.2%}"


def _style_quality(val):
    """Color-code quality cells."""
    colors = {
        'PREMIUM': 'background-color: #1a5c1a; color: white',
        'HIGH': 'background-color: #1a3d5c; color: white',
        'STANDARD': 'background-color: #3d3d1a; color: white',
    }
    return colors.get(val, '')


def render_scan_page():
    st.header("Scan Dashboard")

    # --- Controls ---
    col1, col2, col3, col4 = st.columns([2, 3, 2, 2])

    with col1:
        mode = st.selectbox("Mode", ["swing", "longterm", "daytrade"], index=0)

    watchlists = _get_watchlist_files()
    with col2:
        watchlist_name = st.selectbox(
            "Watchlist",
            list(watchlists.keys()),
            index=list(watchlists.keys()).index('S&P_500') if 'S&P_500' in watchlists else 0,
        )

    with col3:
        quality_filter = st.selectbox("Quality Filter", ["PREMIUM only", "HIGH+", "ALL"], index=1)

    with col4:
        st.markdown("<br>", unsafe_allow_html=True)
        run_clicked = st.button("Run Scan", type="primary", use_container_width=True)

    # --- Run scan ---
    if run_clicked:
        with st.spinner(f"Scanning {watchlist_name} ({mode})..."):
            try:
                results, regime, spy_info = _run_scan(mode, watchlists[watchlist_name])
                if results:
                    st.session_state['scan_results'] = results
                    st.session_state['scan_regime'] = regime
                    st.session_state['scan_spy'] = spy_info
                    st.session_state['scan_mode'] = mode
                    st.success(f"{len(results)} signals found | Regime: {regime} | {spy_info}")
                else:
                    st.warning(f"No signals found | Regime: {regime}")
                    st.session_state['scan_results'] = []
            except Exception as e:
                st.error(f"Scan error: {e}")

    # --- Load latest or show cached results ---
    results = st.session_state.get('scan_results')

    if results is None:
        # Try loading latest CSV
        loaded = _load_latest_signals()
        if loaded:
            df, filename = loaded
            st.info(f"Showing latest saved scan: {filename}")
        else:
            st.info("No scan results yet. Click 'Run Scan' to start.")
            return
    else:
        if not results:
            st.info("No signals in last scan.")
            return
        df = pd.DataFrame(results)

    if df.empty:
        st.info("No signals to display.")
        return

    # --- Regime banner ---
    regime = st.session_state.get('scan_regime', '')
    spy_info = st.session_state.get('scan_spy', '')
    if regime:
        regime_colors = {'NORMAL': 'green', 'CHOPPY': 'orange', 'EXPANSION': 'blue'}
        color = regime_colors.get(regime, 'gray')
        st.markdown(
            f"**Market Regime:** :{color}[{regime}] | {spy_info}",
        )

    # --- Filter ---
    if 'Quality' in df.columns:
        if quality_filter == "PREMIUM only":
            df = df[df['Quality'] == 'PREMIUM']
        elif quality_filter == "HIGH+":
            df = df[df['Quality'].isin(['PREMIUM', 'HIGH'])]

    if df.empty:
        st.warning(f"No {quality_filter} signals found.")
        return

    # --- Metrics ---
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Signals", len(df))
    if 'Quality' in df.columns:
        col2.metric("PREMIUM", len(df[df['Quality'] == 'PREMIUM']))
        col3.metric("HIGH", len(df[df['Quality'] == 'HIGH']))
    if 'Type' in df.columns:
        bounces = len(df[df.get('Type', '') == 'BOUNCE']) if 'Type' in df.columns else 0
        col4.metric("Bounces", bounces)

    # --- Signal table ---
    display_cols = [c for c in [
        'Symbol', 'Price', 'Quality', 'SMA_Dist%', 'R:R', 'RR_Grade',
        'WinProb', 'Stop', 'Target', 'Vol', 'Patterns', 'Type',
    ] if c in df.columns]

    styled = df[display_cols].style.map(
        _style_quality, subset=['Quality'] if 'Quality' in display_cols else []
    )
    st.dataframe(styled, height=500)

    # --- Chart shortcut ---
    if 'Symbol' in df.columns:
        symbols = df['Symbol'].tolist()
        selected = st.selectbox("Select symbol for chart", symbols, key="chart_select")
        if st.button("View Chart"):
            st.session_state['chart_symbol'] = selected
            signal_row = df[df['Symbol'] == selected].iloc[0].to_dict()
            st.session_state['chart_signal'] = signal_row
            st.info(f"Go to the **Chart** page in the sidebar to view {selected}")

    # --- CSV download ---
    csv = df.to_csv(index=False)
    st.download_button("Download CSV", csv, "signals.csv", "text/csv")
