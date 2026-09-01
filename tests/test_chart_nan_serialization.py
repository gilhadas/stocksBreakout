"""
Chart payloads must be valid JSON — no bare NaN tokens.

WHY THIS EXISTS
---------------
yfinance emits a **trailing placeholder bar** for the most recent period with NaN
Open/High/Low/Close, usually with Volume already populated — so neither `.empty` nor a
Volume check catches it. Measured 2026-08-04 at 07:10 ET, `period='1y'`: SPY, BP, GSAT
and CAPR each carried exactly one such row, always the last of 251.

`json.dumps` serialises NaN as a bare `NaN` token (`allow_nan=True` is the default),
which is not valid JSON. The browser threw

    SyntaxError: Unexpected token 'N', ..."open": NaN, "high"... is not valid JSON

and the chart never rendered — for *every* symbol, not just thin or delisted ones
(issue #4).

The candlestick series was built straight from the frame while the SMA series in the
same function filtered with `pd.notna`, so the bug hid in plain sight next to its own
fix. Guarding at fetch time covers both render sites instead of requiring the
duplicated builders to be fixed twice.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from utils import drop_incomplete_bars


def _frame(rows: int = 5, *, trailing_nan_ohlc: bool = False,
           nan_volume_row: int | None = None) -> pd.DataFrame:
    idx = pd.date_range('2026-07-01', periods=rows, freq='B', tz='America/New_York')
    df = pd.DataFrame({
        'Open': np.linspace(100, 104, rows),
        'High': np.linspace(101, 105, rows),
        'Low': np.linspace(99, 103, rows),
        'Close': np.linspace(100.5, 104.5, rows),
        'Volume': np.full(rows, 1_000_000.0),
    }, index=idx)
    df.index.name = 'Date'
    if trailing_nan_ohlc:
        # The exact production shape: OHLC gone, Volume still present.
        df.loc[df.index[-1], ['Open', 'High', 'Low', 'Close']] = np.nan
    if nan_volume_row is not None:
        df.loc[df.index[nan_volume_row], 'Volume'] = np.nan
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 1. The fetch-time guard
# ─────────────────────────────────────────────────────────────────────────────


def test_trailing_placeholder_bar_is_dropped():
    df = _frame(trailing_nan_ohlc=True)
    out = drop_incomplete_bars(df)

    assert len(out) == len(df) - 1, "trailing NaN-OHLC bar survived"
    assert not out[['Open', 'High', 'Low', 'Close']].isna().any().any()


def test_complete_bars_are_untouched():
    df = _frame()
    assert len(drop_incomplete_bars(df)) == len(df)


def test_nan_volume_alone_does_not_drop_the_bar():
    """Volume is not part of the OHLC completeness test — the bar is still
    plottable as a candle, and the volume series skips the point itself."""
    df = _frame(nan_volume_row=2)
    assert len(drop_incomplete_bars(df)) == len(df)


@pytest.mark.parametrize('bad', [None, pd.DataFrame()])
def test_empty_and_none_are_passed_through(bad):
    out = drop_incomplete_bars(bad)
    assert out is None or out.empty


def test_frame_without_ohlc_columns_is_passed_through():
    df = pd.DataFrame({'value': [1.0, np.nan]})
    assert len(drop_incomplete_bars(df)) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 2. The property that actually broke: serialisable payload
# ─────────────────────────────────────────────────────────────────────────────


def _candles(df: pd.DataFrame) -> list[dict]:
    """Mirror _build_chart_config / _build_mini_chart's candle construction."""
    df = df.reset_index()
    df['time'] = df['Date'].dt.strftime('%Y-%m-%d')
    return df[['time', 'Open', 'High', 'Low', 'Close']].rename(columns={
        'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close'}).to_dict('records')


def test_unguarded_payload_really_does_emit_bare_nan():
    """Pin the failure, so the guard below is proven to be doing the work."""
    payload = json.dumps(_candles(_frame(trailing_nan_ohlc=True)))
    assert 'NaN' in payload, "premise broken — the bug no longer reproduces"


def test_guarded_payload_is_valid_json():
    payload = json.dumps(_candles(drop_incomplete_bars(_frame(trailing_nan_ohlc=True))))

    assert 'NaN' not in payload, f"bare NaN token in chart payload: {payload}"
    # The real check: this is what the browser does, and it is what threw.
    assert json.loads(payload)
    # And strict encoding — which the browser effectively enforces — succeeds.
    json.dumps(_candles(drop_incomplete_bars(_frame(trailing_nan_ohlc=True))),
               allow_nan=False)


# ─────────────────────────────────────────────────────────────────────────────
# 3. The latent second failure mode
# ─────────────────────────────────────────────────────────────────────────────


def test_nan_volume_would_crash_int_conversion():
    """int(nan) raises ValueError — a server-side crash from the same root
    cause, with a completely different presentation from the JSON one."""
    df = _frame(nan_volume_row=1)
    with pytest.raises(ValueError):
        [int(v) for v in df['Volume']]


def test_volume_series_skips_nan_rows():
    """Mirror the guarded volume loop in both builders."""
    df = drop_incomplete_bars(_frame(nan_volume_row=1)).reset_index()
    df['time'] = df['Date'].dt.strftime('%Y-%m-%d')

    volume_data = []
    for _, row in df.iterrows():
        if pd.isna(row['Volume']):
            continue
        volume_data.append({'time': row['time'], 'value': int(row['Volume'])})

    assert len(volume_data) == len(df) - 1
    assert 'NaN' not in json.dumps(volume_data)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Both render paths are guarded, not just one
# ─────────────────────────────────────────────────────────────────────────────


class _FakeTicker:
    """Stands in for yf.Ticker, returning the production failure shape."""

    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, *a, **k):
        return _frame(trailing_nan_ohlc=True)


@pytest.mark.parametrize('module_name,fetch_name', [
    ('pages.chart_page', '_fetch_chart_data'),
    ('pages.scan_page', '_fetch_mini_chart'),
])
def test_both_fetch_helpers_apply_the_guard(module_name, fetch_name, monkeypatch):
    """The builders are duplicated; the guard must be in both *fetch* paths or
    one of the two charts stays broken.

    Asserted behaviourally, not by grepping the source. A source check here
    passed with the call deleted, because chart_page imported the helper under
    an alias and the import line matched the substring — the same trap that bit
    tests/test_user_delete_cleanup.py. A name appearing in a file does not mean
    it is called.
    """
    import importlib
    mod = importlib.import_module(module_name)
    monkeypatch.setattr(mod.yf, 'Ticker', _FakeTicker)

    fetch = getattr(mod, fetch_name)
    if hasattr(fetch, 'clear'):        # st.cache_data wrapper
        fetch.clear()

    out = fetch('SPY')

    assert out is not None and not out.empty
    assert not out[['Open', 'High', 'Low', 'Close']].isna().any().any(), (
        f"{module_name}.{fetch_name} returned a NaN-OHLC bar — chart payload "
        f"will contain a bare NaN token")
