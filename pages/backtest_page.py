"""Backtest Viewer — display backtest comparison results."""
import streamlit as st
import pandas as pd
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
BACKTESTS_DIR = PROJECT_ROOT / 'scanner_output' / 'backtests'


def _load_latest_backtest():
    """Load the most recent backtest JSON."""
    if not BACKTESTS_DIR.exists():
        return None, None
    jsons = sorted(BACKTESTS_DIR.glob('multi_config_vs_spy_*.json'), reverse=True)
    if not jsons:
        # Try other backtest files
        jsons = sorted(BACKTESTS_DIR.glob('*.json'), reverse=True)
    if not jsons:
        return None, None
    with open(jsons[0]) as f:
        data = json.load(f)
    return data, jsons[0].name


def _format_pct(val):
    """Format percentage value."""
    if isinstance(val, str):
        return val
    return f"{val:+.2f}%" if val else "N/A"


def _highlight_best(s):
    """Highlight the best return row."""
    if s.name == 'Return':
        best = s.str.replace('%', '').str.replace('+', '').astype(float).idxmax()
        return ['font-weight: bold; background-color: #1a5c1a' if i == best else '' for i in s.index]
    return ['' for _ in s]


def render_backtest_page():
    st.header("Backtest Results")

    data, filename = _load_latest_backtest()

    if data is None:
        st.warning("No backtest results found. Run a backtest first:")
        st.code("python enhanced_backtest.py", language="bash")
        return

    st.caption(f"Source: {filename}")

    # Parse results
    if isinstance(data, dict) and 'results' in data:
        results = data['results']
    elif isinstance(data, list):
        results = data
    else:
        st.error("Unexpected backtest format")
        st.json(data)
        return

    if not results:
        st.warning("No backtest configs in results.")
        return

    # Build comparison table
    rows = []
    for r in results:
        rows.append({
            'Config': r.get('config', r.get('name', 'Unknown')),
            'Signals': r.get('signals_count', r.get('total_signals', 'N/A')),
            'Trades': r.get('trades', r.get('total_trades', 'N/A')),
            'Return': _format_pct(r.get('return_pct', r.get('total_return', 0))),
            'Win Rate': _format_pct(r.get('win_rate', 0)),
            'Sharpe': f"{r.get('sharpe', r.get('sharpe_ratio', 0)):.2f}",
            'Max DD': _format_pct(r.get('max_drawdown', 0)),
            'W/L': f"{r.get('win_loss_ratio', r.get('wl_ratio', 0)):.2f}",
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, height=500)

    # Highlight best config
    if rows:
        best = max(rows, key=lambda r: float(str(r.get('Return', '0')).replace('%', '').replace('+', '').replace('N/A', '0')))
        st.success(f"Best config: **{best['Config']}** ({best['Return']} return, {best['Win Rate']} win rate)")

    # SPY benchmark
    spy_data = data.get('spy_benchmark') if isinstance(data, dict) else None
    if spy_data:
        st.markdown("---")
        cols = st.columns(4)
        cols[0].metric("SPY Return", _format_pct(spy_data.get('return_pct', 0)))
        cols[1].metric("SPY Sharpe", f"{spy_data.get('sharpe', 0):.2f}")
        cols[2].metric("SPY Max DD", _format_pct(spy_data.get('max_drawdown', 0)))
        cols[3].metric("Period", spy_data.get('period', 'N/A'))

    # Raw JSON expander
    with st.expander("Raw JSON"):
        st.json(data)
