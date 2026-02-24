"""Backtest Viewer — display backtest comparison results."""
import streamlit as st
import pandas as pd
from pathlib import Path

from utils import load_json, list_files

PROJECT_ROOT = Path(__file__).parent.parent
BACKTESTS_DIR = str(PROJECT_ROOT / 'scanner_output' / 'backtests')


def _list_backtest_files() -> list[str]:
    """Return all backtest JSON filenames sorted newest-first (multi_config first, then others)."""
    all_names = list_files(BACKTESTS_DIR, '*.json')
    if not all_names:
        return []
    multi = sorted([n for n in all_names if n.startswith('multi_config_vs_spy_')], reverse=True)
    others = sorted([n for n in all_names if not n.startswith('multi_config_vs_spy_')], reverse=True)
    return multi + others


def _load_backtest(filename: str):
    """Load a single backtest JSON file."""
    return load_json(f"{BACKTESTS_DIR}/{filename}")


def _format_pct(val):
    """Format a percentage value (already in %, e.g. 141.77 → '+141.77%')."""
    if isinstance(val, str):
        return val
    if val is None:
        return 'N/A'
    return f"{val:+.2f}%"


def _wl_ratio(r: dict) -> str:
    """Compute win/loss ratio from avg_win / abs(avg_loss)."""
    avg_win  = r.get('avg_win', 0) or 0
    avg_loss = abs(r.get('avg_loss', 0) or 1)
    if avg_loss == 0:
        return 'N/A'
    return f"{avg_win / avg_loss:.2f}"


def render_backtest_page():
    st.header("Backtest Results")

    all_files = _list_backtest_files()
    if not all_files:
        st.warning("No backtest results found. Run a backtest first:")
        st.code("python enhanced_backtest.py", language="bash")
        return

    selected = st.selectbox("Backtest file", all_files, index=0)

    data = _load_backtest(selected)
    if data is None:
        st.error(f"Could not load {selected}")
        return
    filename = selected

    st.caption(f"Source: {filename}")

    # ── Parse results ────────────────────────────────────────────────────────
    # JSON schema: { 'test': {...}, 'spy': {...}, 'configs': [...] }
    # Fallback: legacy schemas that used 'results' key or a bare list.
    if isinstance(data, dict):
        results = data.get('configs', data.get('results', []))
    elif isinstance(data, list):
        results = data
    else:
        data_type = type(data).__name__
        preview = str(data)[:200] if data else '(empty)'
        st.error(f"Unexpected backtest format: type={data_type}, preview: {preview}")
        return

    if not results:
        st.warning("No backtest configs in results.")
        return

    # ── Build comparison table ────────────────────────────────────────────────
    rows = []
    for r in results:
        rows.append({
            'Config':    r.get('config_name', r.get('config', r.get('name', 'Unknown'))),
            'Signals':   r.get('signal_count', r.get('signals_count', r.get('total_signals', 'N/A'))),
            'Trades':    r.get('total_trades',  r.get('trades', 'N/A')),
            'Return':    _format_pct(r.get('total_return',  r.get('return_pct',   0))),
            'Win Rate':  _format_pct(r.get('win_rate', 0)),
            'Sharpe':    f"{r.get('sharpe_ratio', r.get('sharpe', 0)):.2f}",
            'Max DD':    _format_pct(r.get('max_drawdown', 0)),
            'W/L':       _wl_ratio(r),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, height=500)

    # ── Best config highlight ─────────────────────────────────────────────────
    if rows:
        def _return_float(row):
            try:
                return float(str(row.get('Return', '0')).replace('%', '').replace('+', ''))
            except ValueError:
                return 0.0
        best = max(rows, key=_return_float)
        st.success(f"Best config: **{best['Config']}** ({best['Return']} return, {best['Win Rate']} win rate)")

    # ── SPY benchmark ─────────────────────────────────────────────────────────
    # Schema uses 'spy'; legacy may use 'spy_benchmark'.
    spy_data = None
    if isinstance(data, dict):
        spy_data = data.get('spy', data.get('spy_benchmark'))

    if spy_data:
        # Build period string from test metadata if available
        test_meta = data.get('test', {}) if isinstance(data, dict) else {}
        period = (
            f"{test_meta.get('start', '')} → {test_meta.get('end', '')}"
            if test_meta.get('start')
            else spy_data.get('period', 'N/A')
        )

        st.markdown("---")
        cols = st.columns(4)
        cols[0].metric("SPY Return",  _format_pct(spy_data.get('total_return', spy_data.get('return_pct', 0))))
        cols[1].metric("SPY Sharpe",  f"{spy_data.get('sharpe_ratio', spy_data.get('sharpe', 0)):.2f}")
        cols[2].metric("SPY Max DD",  _format_pct(spy_data.get('max_drawdown', 0)))
        cols[3].metric("Period",      period)

    # ── Minervini Screen benchmark ────────────────────────────────────────────
    mb_data = data.get('minervini_benchmark') if isinstance(data, dict) else None
    if mb_data and mb_data.get('total_return') is not None:
        mb_n = mb_data.get('num_stocks', '?')
        st.markdown(f"**Minervini Screen ({mb_n} qualifying stocks, buy-and-hold)**")
        mb_cols = st.columns(3)
        mb_cols[0].metric("Minervini Return",  _format_pct(mb_data.get('total_return', 0)))
        mb_cols[1].metric("Minervini Sharpe",  f"{mb_data.get('sharpe_ratio', 0):.2f}")
        mb_cols[2].metric("Minervini Max DD",  _format_pct(mb_data.get('max_drawdown', 0)))

    # ── Raw JSON expander ─────────────────────────────────────────────────────
    with st.expander("Raw JSON"):
        st.json(data)
