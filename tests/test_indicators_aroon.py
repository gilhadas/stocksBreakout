"""
Tests for Aroon Up/Down/Oscillator indicator.

Acceptance criteria:
  AC-AROON-01  All-rising prices → AroonUp=100, AroonDown=0, Osc=+100 on last bar
  AC-AROON-02  All-falling prices → AroonUp=0, AroonDown=100, Osc=-100 on last bar
  AC-AROON-03  First n bars produce NaN (warmup); bar n+1 is the first valid value
  AC-AROON-04  Output DataFrame has correct shape and columns
  AC-AROON-05  N=14 and N=25 both supported (non-default period)
"""
import numpy as np
import pandas as pd
import pytest
from indicators import calculate_aroon


def _make_df(closes, high_offset=0.5, low_offset=0.5):
    n = len(closes)
    idx = pd.date_range('2025-01-01', periods=n, freq='B')
    return pd.DataFrame({
        'open':   closes,
        'high':   [c + high_offset for c in closes],
        'low':    [c - low_offset  for c in closes],
        'close':  closes,
        'volume': [1_000_000] * n,
    }, index=idx)


# ─── AC-AROON-01: steadily rising → AroonUp=100, AroonDown=0 ────────────────
def test_all_rising_prices():
    closes = list(range(100, 140))        # 40 bars, monotonically rising
    df = _make_df(closes)
    aroon = calculate_aroon(df, n=25)
    last = aroon.iloc[-1]
    assert last['aroon_up']   == pytest.approx(100.0), "All-rising: aroon_up must be 100"
    assert last['aroon_down'] == pytest.approx(0.0),   "All-rising: aroon_down must be 0"
    assert last['aroon_osc']  == pytest.approx(100.0), "All-rising: aroon_osc must be +100"


# ─── AC-AROON-02: steadily falling → AroonUp=0, AroonDown=100 ───────────────
def test_all_falling_prices():
    closes = list(range(140, 100, -1))    # 40 bars, monotonically falling
    df = _make_df(closes)
    aroon = calculate_aroon(df, n=25)
    last = aroon.iloc[-1]
    assert last['aroon_up']   == pytest.approx(0.0),    "All-falling: aroon_up must be 0"
    assert last['aroon_down'] == pytest.approx(100.0),  "All-falling: aroon_down must be 100"
    assert last['aroon_osc']  == pytest.approx(-100.0), "All-falling: aroon_osc must be -100"


# ─── AC-AROON-03: warmup — first n bars NaN, bar n+1 is first valid ─────────
def test_warmup_produces_nan():
    n = 25
    closes = list(range(100, 100 + n + 5))
    df = _make_df(closes)
    aroon = calculate_aroon(df, n=n)
    # First n rows should be NaN (rolling window n+1 needs n+1 bars)
    assert aroon['aroon_osc'].iloc[:n].isna().all(), \
        f"First {n} bars should be NaN (warmup)"
    assert not pd.isna(aroon['aroon_osc'].iloc[n]), \
        f"Bar {n+1} (index {n}) should be the first valid value"


# ─── AC-AROON-04: output shape and columns ───────────────────────────────────
def test_output_shape_and_columns():
    closes = list(range(100, 140))
    df = _make_df(closes)
    aroon = calculate_aroon(df, n=25)
    assert list(aroon.columns) == ['aroon_up', 'aroon_down', 'aroon_osc']
    assert len(aroon) == len(df), "Output must have same length as input"
    assert aroon.index.equals(df.index), "Output index must match input"


# ─── AC-AROON-05: non-default periods (N=14, N=25) ───────────────────────────
def test_non_default_periods():
    closes = list(range(100, 160))
    df = _make_df(closes)
    for n in [14, 25]:
        aroon = calculate_aroon(df, n=n)
        assert aroon['aroon_osc'].iloc[-1] == pytest.approx(100.0), \
            f"N={n}: monotone-rising should yield osc=100"
        # Warmup: first n bars NaN
        assert aroon['aroon_osc'].iloc[:n].isna().all(), \
            f"N={n}: first {n} bars should be NaN"
