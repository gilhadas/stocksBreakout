"""
Tests for backtest_regime_compare._apply_bounce_sma200_gate() (CLAUDE.md §12 Task 2).

AC-BSG-01  BOUNCE signal with stock close < own SMA200 at signal date is gated
AC-BSG-02  BOUNCE signal with stock close >= own SMA200 passes
AC-BSG-03  Non-BOUNCE signals always pass, even below SMA200
AC-BSG-04  <200 bars of history (SMA200 undefined) passes ungated
"""
import numpy as np
import pandas as pd

from backtest_regime_compare import _apply_bounce_sma200_gate


def _df(closes, start='2024-01-02'):
    idx = pd.bdate_range(start=start, periods=len(closes))
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        'open': closes, 'high': closes + 1, 'low': closes - 1,
        'close': closes, 'volume': np.full(len(closes), 1e6),
    }, index=idx)


def _sig(sym, date, typ='BOUNCE'):
    return {'symbol': sym, 'date': pd.Timestamp(date), 'type': typ,
            'quality': 'PREMIUM'}


def test_bounce_below_own_sma200_is_gated():
    # 250 bars: long flat 100s, then collapse to 50 → close far below SMA200
    closes = [100.0] * 220 + [50.0] * 30
    df = _df(closes)
    d = df.index[-1]
    kept, gated = _apply_bounce_sma200_gate([_sig('X', d)], {'X': df})
    assert gated == 1 and kept == []


def test_bounce_above_own_sma200_passes():
    # Rising series → close above SMA200
    closes = list(np.linspace(100, 200, 250))
    df = _df(closes)
    d = df.index[-1]
    kept, gated = _apply_bounce_sma200_gate([_sig('X', d)], {'X': df})
    assert gated == 0 and len(kept) == 1


def test_non_bounce_always_passes():
    closes = [100.0] * 220 + [50.0] * 30       # below SMA200
    df = _df(closes)
    d = df.index[-1]
    sigs = [_sig('X', d, typ='BREAKOUT'), _sig('X', d, typ='SMA20_CROSS')]
    kept, gated = _apply_bounce_sma200_gate(sigs, {'X': df})
    assert gated == 0 and len(kept) == 2


def test_short_history_passes_ungated():
    closes = [100.0] * 100 + [50.0] * 20        # only 120 bars — SMA200 NaN
    df = _df(closes)
    d = df.index[-1]
    kept, gated = _apply_bounce_sma200_gate([_sig('X', d)], {'X': df})
    assert gated == 0 and len(kept) == 1


# ─── AC-BSG-05: bear-only variant gates only on SPY-bear days ────────────────
def test_bear_only_variant_conditions_on_spy():
    closes = [100.0] * 220 + [50.0] * 30        # stock below own SMA200
    df = _df(closes)
    d = df.index[-1]
    sig = [_sig('X', d)]
    # SPY healthy that day → gate off, signal passes
    kept, gated = _apply_bounce_sma200_gate(sig, {'X': df}, spy_below_dates=set())
    assert gated == 0 and len(kept) == 1
    # SPY below its SMA200 that day → gate on, signal gated
    below = {pd.Timestamp(d).normalize()}
    kept, gated = _apply_bounce_sma200_gate([_sig('X', d)], {'X': df},
                                            spy_below_dates=below)
    assert gated == 1 and kept == []
