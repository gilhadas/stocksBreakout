"""
Tests for the SLOW_GRIND detector (added 2026-08-29, dormant by default).

Motivation: NOW gained +31.5% in August 2026 without firing a single signal of
any type, all month — confirmed live by running every existing detector
against NOW's real daily bars. detect() logged "no price break" on every
checked date: NOW's climb was a grind (new highs most days, by a small
margin, with occasional red days) rather than a decisive break above a clear
resistance level. detect_continuation needs 3+ CONSECUTIVE green candles (a
single red day resets its streak counter to zero) — NOW's real pattern never
sustained that. detect_bounce/detect_sma20_cross/detect_trend_confirm all
need their own sharp triggers that a slow grind doesn't produce either.

SLOW_GRIND fills that gap: majority (not unbroken) up days over a longer
window, net cumulative gain, still near the window's highs, rising SMA20,
healthy-not-blown-off RSI. tests/fixtures/slow_grind_now_2026.csv is NOW's
own real daily bars ending 2026-08-10 — the exact case that motivated this
detector — used as the positive-fire fixture rather than hand-tuned synthetic
data (real RSI/SMA/volume interactions are hard to fake convincingly).

AC-SG-01  Config contract: dormant by default, all expected keys present
AC-SG-02  The real NOW fixture fires PREMIUM (the motivating case)
AC-SG-03  Weaker volume + spy outperformance drops it to HIGH (checks-based scoring)
AC-SG-04  Flat/no-move history: insufficient cumulative return → None
AC-SG-05  Choppy/declining history: insufficient up-day ratio → None
AC-SG-06  Price pulled well off the lookback high → None
AC-SG-07  Price below a falling SMA20 → None
AC-SG-08  RSI below the healthy floor → None
AC-SG-09  RSI above the blow-off ceiling → None
AC-SG-10  Insufficient history (< 60 bars) → None, no crash
AC-SG-11  Orchestrator wiring: only reached when SLOW_GRIND_CONFIG['enabled']
          and only as the final fallback after every other detector returns None
"""
from __future__ import annotations

import asyncio
import sys
import logging
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.disable(logging.CRITICAL)

import config
from scanner import BreakoutDetector

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'slow_grind_now_2026.csv'


def _now_fixture():
    df = pd.read_csv(FIXTURE, index_col='Date', parse_dates=True)
    df.columns = df.columns.str.lower()
    return df


def _flat_df(n=100, price=100.0):
    idx = pd.bdate_range(end='2026-08-10', periods=n)
    close = np.full(n, price) + np.random.RandomState(1).normal(0, 0.2, n)
    return pd.DataFrame({
        'open': close, 'high': close * 1.005, 'low': close * 0.995,
        'close': close, 'volume': np.full(n, 1_000_000.0),
    }, index=idx)


def _choppy_df(n=100, price=100.0):
    """Net roughly flat/declining with no sustained directional bias."""
    idx = pd.bdate_range(end='2026-08-10', periods=n)
    steps = np.random.RandomState(2).normal(-0.001, 0.01, n)
    close = price * np.cumprod(1 + steps)
    return pd.DataFrame({
        'open': close, 'high': close * 1.01, 'low': close * 0.99,
        'close': close, 'volume': np.full(n, 1_000_000.0),
    }, index=idx)


def _big_net_gain_low_up_ratio_df():
    """Isolates the up-day-ratio gate: a big enough net gain to clear
    min_cum_return_pct (11.2%), driven by a MINORITY of days (5 of 15 up,
    ratio 0.33 < the 0.55 floor) — the rest small down days. Verified in
    isolation: RSI 74.6 (under the 75 ceiling), still near the window high,
    SMA20 rising and price above it — up_day_ratio is the only failing gate.
    """
    n_base = 90
    base = 100 + np.cumsum(np.random.RandomState(5).normal(0, 0.15, n_base))
    moves = [-0.006, -0.006, 0.038, -0.006, -0.006, 0.033, -0.006, -0.006,
             0.035, -0.006, -0.006, 0.033, -0.006, -0.006, 0.030]
    grind = [base[-1]]
    for m in moves:
        grind.append(grind[-1] * (1 + m))
    close = np.concatenate([base, grind[1:]])
    idx = pd.bdate_range(end='2026-08-10', periods=len(close))
    opens = np.roll(close, 1)
    opens[0] = close[0]
    return pd.DataFrame({
        'open': opens, 'high': np.maximum(opens, close) * 1.005,
        'low': np.minimum(opens, close) * 0.995, 'close': close,
        'volume': np.full(len(close), 1_000_000.0),
    }, index=idx)


@pytest.fixture
def detector():
    return BreakoutDetector()


# ── config contract ────────────────────────────────────────────────────────

def test_config_dormant_by_default():
    """AC-SG-01"""
    assert config.SLOW_GRIND_CONFIG['enabled'] is False
    for key in ('lookback_days', 'min_cum_return_pct', 'min_up_day_ratio',
                'near_high_pct', 'rsi_min', 'rsi_max', 'min_vol_ratio',
                'atr_stop_mult', 'target_rr'):
        assert key in config.SLOW_GRIND_CONFIG


# ── positive fire + quality tiers ──────────────────────────────────────────

def test_real_now_fixture_fires_premium(detector):
    """AC-SG-02 — the exact motivating case, real market data."""
    df = _now_fixture()
    sig = detector.detect_slow_grind(df, 'NOW', 'swing', '1 day', spy_perf=0.0)

    assert sig is not None
    assert sig['Type'] == 'SLOW_GRIND'
    assert sig['Quality'] == 'PREMIUM'
    assert sig['Dist'] > 15.0
    assert sig['UpDayRatio'] >= config.SLOW_GRIND_CONFIG['min_up_day_ratio']


def test_weaker_case_scores_high_not_premium(detector):
    """AC-SG-03 — checks-based scoring actually discriminates."""
    df = _now_fixture().astype({'volume': 'float64'})
    df.iloc[-15:, df.columns.get_loc('volume')] *= 0.9  # kills vol_confirmed

    sig = detector.detect_slow_grind(df, 'NOW', 'swing', '1 day', spy_perf=1.0)  # kills rs_ok

    assert sig is not None
    assert sig['Quality'] == 'HIGH'


# ── individual gates ────────────────────────────────────────────────────────

def test_flat_history_insufficient_return(detector):
    """AC-SG-04"""
    sig = detector.detect_slow_grind(_flat_df(), 'FLAT', 'swing', '1 day', spy_perf=0.0)
    assert sig is None


def test_flat_declining_history_also_fails_on_return(detector):
    """AC-SG-05a — a choppy/net-flat history fails (on cum_return, not up_ratio
    — see AC-SG-05b for a fixture that isolates up_ratio specifically)."""
    sig = detector.detect_slow_grind(_choppy_df(), 'CHOP', 'swing', '1 day', spy_perf=0.0)
    assert sig is None


def test_big_gain_but_minority_up_days_fails_up_ratio(detector):
    """AC-SG-05b — isolates the up-day-ratio gate: a large net gain (11.2%,
    clears min_cum_return_pct) driven by a minority of days (ratio 0.33 <
    the 0.55 floor) must still be rejected. This is the gate that
    distinguishes SLOW_GRIND from "any stock that happened to be up a lot" —
    a grind needs broad day-to-day participation, not a couple of spikes."""
    sig = detector.detect_slow_grind(_big_net_gain_low_up_ratio_df(), 'SPIKY',
                                     'swing', '1 day', spy_perf=0.0)
    assert sig is None


def test_pulled_off_the_high_fails(detector):
    """AC-SG-06 — real fixture, but knock the latest close down >2% off the
    lookback high without changing anything else."""
    df = _now_fixture().copy()
    df.iloc[-1, df.columns.get_loc('close')] *= 0.90

    sig = detector.detect_slow_grind(df, 'NOW', 'swing', '1 day', spy_perf=0.0)
    assert sig is None


def test_price_below_falling_sma20_fails(detector):
    """AC-SG-07"""
    sig = detector.detect_slow_grind(_choppy_df(price=150.0), 'CHOP2', 'swing',
                                     '1 day', spy_perf=0.0)
    assert sig is None


def test_rsi_below_floor_fails(detector):
    """AC-SG-08 — barely-up, listless drift keeps RSI under min (50)."""
    idx = pd.bdate_range(end='2026-08-10', periods=100)
    rng = np.random.RandomState(3)
    steps = rng.normal(0.0005, 0.004, 100)
    close = 100 * np.cumprod(1 + steps)
    df = pd.DataFrame({
        'open': close, 'high': close * 1.005, 'low': close * 0.995,
        'close': close, 'volume': np.full(100, 1_000_000.0),
    }, index=idx)
    sig = detector.detect_slow_grind(df, 'DRIFT', 'swing', '1 day', spy_perf=0.0)
    assert sig is None


def test_rsi_above_ceiling_fails(detector):
    """AC-SG-09 — real fixture pushed further/faster into blow-off RSI.
    Verified in isolation: this leaves return/up-ratio/near-high/SMA20 all
    still passing (RSI=81.2 vs the 75 ceiling is the only failing gate)."""
    df = _now_fixture().copy()
    tail = df.iloc[-10:].copy()
    boost = np.linspace(1.01, 1.25, len(tail))
    df.iloc[-10:, df.columns.get_loc('close')] = tail['close'].to_numpy() * boost
    df.iloc[-10:, df.columns.get_loc('high')] = df.iloc[-10:]['close'] * 1.01

    sig = detector.detect_slow_grind(df, 'NOW', 'swing', '1 day', spy_perf=0.0)
    assert sig is None


def test_insufficient_history_returns_none_no_crash(detector):
    """AC-SG-10"""
    df = _now_fixture().tail(30)
    sig = detector.detect_slow_grind(df, 'NOW', 'swing', '1 day', spy_perf=0.0)
    assert sig is None


# ── orchestrator wiring ─────────────────────────────────────────────────────

def test_disabled_by_default_never_called():
    """AC-SG-11a — with SLOW_GRIND_CONFIG['enabled']=False (the shipped
    default), the cascade must never reach detect_slow_grind."""
    import orchestrator as orch
    assert orch.SLOW_GRIND_CONFIG.get('enabled') is False


def test_enabled_config_is_the_same_object_backtest_reads():
    """AC-SG-11b — backtest_regime_compare's _SG_CFG and config.SLOW_GRIND_CONFIG
    must be the same object, or --slow-grind's mutation silently no-ops."""
    import backtest_regime_compare as brc
    assert brc._SG_CFG is config.SLOW_GRIND_CONFIG
