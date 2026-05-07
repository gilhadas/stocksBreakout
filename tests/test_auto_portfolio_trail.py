"""
Tests for ATR always-on trailing stop in auto_portfolio._raise_atr_trail().

Acceptance criteria:
  AC-TRAIL-01  Trail activates from bar 1 (not after TP) — stop rises when price rises
  AC-TRAIL-02  Fixed stop_loss is the absolute floor — trail never drops below it
  AC-TRAIL-03  Stop is monotonically non-decreasing across successive calls
  AC-TRAIL-04  ATR multiplier reads from config.ATR_TRAIL_MULT, not hardcoded
  AC-TRAIL-05  Insufficient history (< ATR_TRAIL_FLOOR_BARS) leaves stop unchanged
"""
import pandas as pd
import numpy as np
import pytest
from auto_portfolio import _raise_atr_trail
from config import ATR_TRAIL_MULT, ATR_TRAIL_FLOOR_BARS


def _make_hist(closes, high_offset=1.0, low_offset=1.0):
    """Build a minimal yfinance-style DataFrame with capitalized columns."""
    closes = list(closes)
    n = len(closes)
    highs  = [c + high_offset for c in closes]
    lows   = [c - low_offset  for c in closes]
    idx = pd.date_range('2025-01-01', periods=n, freq='B')
    return pd.DataFrame({
        'Open':  closes,
        'High':  highs,
        'Low':   lows,
        'Close': closes,
        'Volume': [1_000_000] * n,
    }, index=idx)


def _make_position(entry=100.0, stop=90.0):
    return {
        'symbol':      'TEST',
        'entry_price': entry,
        'stop':        stop,
        'shares':      10,
        'date_added':  '2025-01-01',
    }


# ─── AC-TRAIL-01: trail rises when price rises ────────────────────────────────
def test_trail_raises_stop_when_price_rises():
    p = _make_position(entry=100.0, stop=90.0)
    # Price rising steadily → trail candidate will exceed fixed stop
    closes = [100 + i * 2 for i in range(20)]   # 100 → 138
    hist = _make_hist(closes)
    _raise_atr_trail(p, hist)
    assert p['stop'] > 90.0, "Trail should have raised stop above fixed floor"


# ─── AC-TRAIL-02: fixed stop is the absolute floor ───────────────────────────
def test_trail_never_drops_below_fixed_stop():
    p = _make_position(entry=100.0, stop=90.0)
    # Flat price — trail candidate will be below initial stop
    closes = [100.0] * 20
    hist = _make_hist(closes, high_offset=0.5, low_offset=0.5)
    _raise_atr_trail(p, hist)
    assert p['stop'] >= 90.0, "Stop must never drop below fixed floor"


# ─── AC-TRAIL-03: stop is monotonically non-decreasing ───────────────────────
def test_stop_never_decreases_across_calls():
    p = _make_position(entry=100.0, stop=90.0)
    stops = []
    for peak in [110, 120, 115, 130, 125]:   # price rises then dips, then rises again
        closes = list(range(100, peak, 1)) + [peak] * (20 - (peak - 100))
        closes = closes[:20]
        hist = _make_hist(closes)
        _raise_atr_trail(p, hist)
        stops.append(p['stop'])
    for i in range(1, len(stops)):
        assert stops[i] >= stops[i - 1], f"Stop decreased at index {i}: {stops[i-1]} → {stops[i]}"


# ─── AC-TRAIL-04: multiplier comes from config, not hardcoded ─────────────────
def test_atr_mult_from_config():
    p1 = _make_position(entry=100.0, stop=80.0)
    p2 = _make_position(entry=100.0, stop=80.0)
    closes = [100 + i for i in range(20)]
    hist = _make_hist(closes, high_offset=2.0, low_offset=2.0)

    # Patch config value and verify stop changes proportionally
    import config as cfg
    original = cfg.ATR_TRAIL_MULT
    try:
        cfg.ATR_TRAIL_MULT = 1.0
        _raise_atr_trail(p1, hist)
        cfg.ATR_TRAIL_MULT = 3.0
        _raise_atr_trail(p2, hist)
    finally:
        cfg.ATR_TRAIL_MULT = original

    # Larger multiplier = larger ATR buffer = lower trail stop (more room)
    assert p1['stop'] >= p2['stop'], \
        "Smaller ATR multiplier should yield a tighter (higher) trail stop"


# ─── AC-TRAIL-05: insufficient history leaves stop unchanged ─────────────────
def test_insufficient_history_leaves_stop_unchanged():
    p = _make_position(entry=100.0, stop=90.0)
    closes = [105.0] * (ATR_TRAIL_FLOOR_BARS - 1)   # one bar short of warmup
    hist = _make_hist(closes)
    _raise_atr_trail(p, hist)
    assert p['stop'] == 90.0, "Stop should be unchanged when history < ATR_TRAIL_FLOOR_BARS"


def test_none_history_leaves_stop_unchanged():
    p = _make_position(entry=100.0, stop=90.0)
    _raise_atr_trail(p, None)
    assert p['stop'] == 90.0, "Stop should be unchanged when hist is None"


# ─── trail_stop field is exposed for mobile UI ───────────────────────────────
def test_trail_stop_field_set_after_raise():
    p = _make_position(entry=100.0, stop=85.0)
    closes = [100 + i * 2 for i in range(20)]
    hist = _make_hist(closes)
    _raise_atr_trail(p, hist)
    assert 'trail_stop' in p, "trail_stop field must be set for mobile UI display"
    assert p['trail_stop'] == p['stop']
