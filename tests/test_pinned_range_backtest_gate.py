"""
Tests for backtest_regime_compare._apply_pinned_range_gate() (CLAUDE.md §27
"best lead out of the session" — the --reject-pinned-range ablation).

AC-PRG-01  Signal on a stock pinned in a tight, low-ATR range at the signal date is gated
AC-PRG-02  Signal on a normal trending stock passes
AC-PRG-03  Applies to ANY signal type (not just BOUNCE — HOLX-style TREND_CONFIRM too)
AC-PRG-04  <lookback_days bars of history passes ungated (permissive default)
AC-PRG-05  Custom thresholds are respected
"""
import numpy as np
import pandas as pd

from backtest_regime_compare import _apply_pinned_range_gate


def _pinned_df(n=90, price=50.0, range_pct=4.0, start='2024-01-02'):
    """A merger-arb-style pinned stock: tiny absolute range AND tiny day-to-day
    true range (a smooth wave, not i.i.d. noise — i.i.d. jumps inflate the
    close-to-close term in True Range even when the high-low band is tiny)."""
    idx = pd.bdate_range(start=start, periods=n)
    amp = price * range_pct / 100 / 2
    close = price + amp * np.sin(np.linspace(0, 6 * np.pi, n))
    return pd.DataFrame({
        'open': close, 'high': close + amp * 0.05, 'low': close - amp * 0.05,
        'close': close, 'volume': np.full(n, 1e6),
    }, index=idx)


def _trending_df(n=90, start_px=50.0, end_px=100.0, start='2024-01-02'):
    """A normal uptrend — real range, real per-bar true range."""
    idx = pd.bdate_range(start=start, periods=n)
    close = np.linspace(start_px, end_px, n)
    return pd.DataFrame({
        'open': close, 'high': close * 1.03, 'low': close * 0.97,
        'close': close, 'volume': np.full(n, 1e6),
    }, index=idx)


def _sig(sym, date, typ='BOUNCE', quality='PREMIUM'):
    return {'symbol': sym, 'date': pd.Timestamp(date), 'type': typ, 'quality': quality}


def test_pinned_stock_is_gated():
    df = _pinned_df(range_pct=4.0)
    d = df.index[-1]
    kept, gated = _apply_pinned_range_gate([_sig('HOLX', d)], {'HOLX': df})
    assert gated == 1 and kept == []


def test_normal_trending_stock_passes():
    df = _trending_df()
    d = df.index[-1]
    kept, gated = _apply_pinned_range_gate([_sig('NVDA', d)], {'NVDA': df})
    assert gated == 0 and len(kept) == 1


def test_applies_to_any_signal_type_not_just_bounce():
    """The merger-arb failure mode wasn't BOUNCE-specific (§27: TREND_CONFIRM
    fired too) — unlike the SMA200 gate, this must gate every type."""
    df = _pinned_df(range_pct=4.0)
    d = df.index[-1]
    sigs = [_sig('HOLX', d, typ='TREND_CONFIRM'), _sig('HOLX', d, typ='BOUNCE'),
            _sig('HOLX', d, typ='SMA20_CROSS')]
    kept, gated = _apply_pinned_range_gate(sigs, {'HOLX': df})
    assert gated == 3 and kept == []


def test_short_history_passes_ungated():
    df = _pinned_df(n=30, range_pct=4.0)   # < default 60-day lookback
    d = df.index[-1]
    kept, gated = _apply_pinned_range_gate([_sig('HOLX', d)], {'HOLX': df})
    assert gated == 0 and len(kept) == 1


def test_missing_symbol_passes_ungated():
    kept, gated = _apply_pinned_range_gate([_sig('GHOST', pd.Timestamp('2024-06-01'))], {})
    assert gated == 0 and len(kept) == 1


def test_custom_thresholds_respected():
    df = _pinned_df(range_pct=4.0)
    d = df.index[-1]
    # Thresholds tightened below the fixture's actual range/ATR → no longer gated.
    kept, gated = _apply_pinned_range_gate(
        [_sig('HOLX', d)], {'HOLX': df}, max_range_pct=0.5, max_atr_pct=0.05
    )
    assert gated == 0 and len(kept) == 1


def test_gate_only_looks_back_from_signal_date_not_future():
    """A stock pinned AFTER the signal date but trending normally as-of the
    signal date must NOT be gated — the gate must not look ahead."""
    trend = _trending_df(n=90, start_px=50.0, end_px=100.0)
    pin = _pinned_df(n=30, price=float(trend['close'].iloc[-1]), range_pct=4.0,
                     start=trend.index[-1] + pd.Timedelta(days=1))
    df = pd.concat([trend, pin])
    d = trend.index[-1]   # signal fired at the end of the trending phase
    kept, gated = _apply_pinned_range_gate([_sig('X', d)], {'X': df})
    assert gated == 0 and len(kept) == 1
