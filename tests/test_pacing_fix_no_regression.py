"""
Golden-master regression test: the intraday Vol_Ratio pacing fix (2026-09-03)
must not change ANY existing backtest or `live_now_et`-omitted result.

WHY THIS EXISTS
----------------
utils.pace_adjust_volume_ratio() and its wiring into scanner.py's six live-
cascade detectors are gated on a new `live_now_et` kwarg that defaults to
None. The whole design promise (see CLAUDE.md's own §29 lookahead-bias
standard, and the docstrings on pace_adjust_volume_ratio /
orchestrator._scan_symbol) is that omitting it — exactly how every backtest
script calls BreakoutDetector — leaves behavior byte-identical to before the
fix. test_live_volume_pacing_wiring.py already proves this structurally (no
backtest script ever references live_now_et) and proves the WIRING works
behaviorally (pacing recovers a real signal). This file proves the inverse
and more directly answers "did this harm existing results": it loads the
actual pre-fix scanner.py from git (HEAD, before this change was made) and
diffs its output against the current code, call for call, signal for
signal, across every live-cascade detector and a battery of realistic price
series — the exact interface backtest_regime_compare.py's
`collect_signals_*()` functions use (`detector.detect(df_slice, symbol,
mode, '1 day', spy_perf, use_scoring=True, ...)`, no live_now_et).

If this file ever fails, the fix changed something it wasn't supposed to —
which is a real regression to backtest-reproduced numbers, not a design
choice to accept.
"""
from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

ROOT = Path(__file__).resolve().parent.parent

# ─── Load the pre-fix scanner.py straight from git, as an independent module ──

def _load_pre_fix_scanner():
    src = subprocess.run(
        ['git', 'show', 'HEAD:scanner.py'],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    assert 'live_now_et' not in src, (
        "HEAD:scanner.py already contains live_now_et — this test needs a "
        "commit hash from BEFORE the pacing fix to be a meaningful control. "
        "Pass an explicit pre-fix rev instead of HEAD."
    )
    spec = importlib.util.spec_from_loader('scanner_pre_pacing_fix', loader=None)
    module = importlib.util.module_from_spec(spec)
    sys.modules['scanner_pre_pacing_fix'] = module
    exec(compile(src, '<HEAD:scanner.py>', 'exec'), module.__dict__)
    return module


_pre = _load_pre_fix_scanner()
OldBreakoutDetector = _pre.BreakoutDetector

import scanner as _new_scanner_module  # current working tree
NewBreakoutDetector = _new_scanner_module.BreakoutDetector


# ─── Realistic price-series fixtures (varied enough to actually fire signals) ─

def _series(seed: int, n: int, start: float, drift: float, vol_pct: float,
            vol_mean: float = 1_000_000, vol_std_pct: float = 0.05,
            end_date: str = '2026-07-27') -> pd.DataFrame:
    """A seeded random-walk OHLCV series — deterministic, reused identically
    for both the old and new detector."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift, vol_pct, n)
    closes = start * np.cumprod(1 + steps)
    highs = closes * (1 + np.abs(rng.normal(0, vol_pct * 0.5, n)))
    lows = closes * (1 - np.abs(rng.normal(0, vol_pct * 0.5, n)))
    opens = np.concatenate([[closes[0]], closes[:-1]]) * (1 + rng.normal(0, vol_pct * 0.2, n))
    volumes = np.abs(rng.normal(vol_mean, vol_mean * vol_std_pct, n))
    dates = pd.bdate_range(end=end_date, periods=n)
    return pd.DataFrame({
        'open': opens, 'high': highs, 'low': lows, 'close': closes, 'volume': volumes,
    }, index=dates)


def _bounce_setup_series(seed: int = 7, today_volume: float = 900_000) -> pd.DataFrame:
    """Mirrors test_live_volume_pacing_wiring.py's fixture shape (decline,
    then a genuine bounce day) but with NORMAL (non-partial) volume — this
    one should fire identically old vs new regardless of live_now_et."""
    rng = np.random.default_rng(seed)
    n_pre = 50
    closes = np.linspace(150, 95, n_pre) + rng.normal(0, 0.4, n_pre)
    highs, lows, opens = closes + 1.0, closes - 1.0, closes - 0.2
    vols = rng.normal(1_000_000, 50_000, n_pre)
    y_close, y_high, y_low, y_open, y_vol = 90.0, 91.0, 89.0, 90.5, 900_000
    t_open, t_close, t_high, t_low = 91.0, 93.6, 94.0, 90.8
    dates = pd.bdate_range(end='2026-07-27', periods=n_pre + 2)
    return pd.DataFrame({
        'open':   list(opens)  + [y_open, t_open],
        'high':   list(highs)  + [y_high, t_high],
        'low':    list(lows)   + [y_low, t_low],
        'close':  list(closes) + [y_close, t_close],
        'volume': list(vols)   + [y_vol, today_volume],
    }, index=dates)


SERIES_FIXTURES = {
    'uptrend_120d':      lambda: _series(seed=1, n=120, start=50, drift=0.006, vol_pct=0.018),
    'downtrend_120d':    lambda: _series(seed=2, n=120, start=200, drift=-0.005, vol_pct=0.02),
    'choppy_120d':       lambda: _series(seed=3, n=120, start=80, drift=0.0002, vol_pct=0.03),
    'strong_uptrend_200d': lambda: _series(seed=4, n=200, start=30, drift=0.008, vol_pct=0.015),
    'high_vol_swings_120d': lambda: _series(seed=5, n=120, start=60, drift=0.001, vol_pct=0.06),
    'slow_grind_60d':    lambda: _series(seed=6, n=60, start=100, drift=0.004, vol_pct=0.008),
    'bounce_setup':       _bounce_setup_series,
}

NOW_FIXTURE = ROOT / 'tests' / 'fixtures' / 'slow_grind_now_2026.csv'


def _load_now_fixture() -> pd.DataFrame:
    df = pd.read_csv(NOW_FIXTURE, index_col=0, parse_dates=True)
    df.columns = df.columns.str.lower()
    return df


MODES = ['swing', 'daytrade', 'longterm']
DETECT_CALLS = [
    ('detect', lambda det, df, mode: det.detect(
        df.copy(), 'TEST', mode, '1 day', 0.02, use_scoring=True,
        use_legacy_momentum=False, use_v4_overextension=True,
    )),
    ('detect_bounce', lambda det, df, mode: det.detect_bounce(
        df.copy(), 'TEST', mode, '1 day',
    )),
    ('detect_continuation', lambda det, df, mode: det.detect_continuation(
        df.copy(), 'TEST', mode, '1 day', 0.02,
    )),
    ('detect_trend_confirm', lambda det, df, mode: det.detect_trend_confirm(
        df.copy(), 'TEST', mode, '1 day', 0.02,
    )),
    ('detect_sma20_cross', lambda det, df, mode: det.detect_sma20_cross(
        df.copy(), 'TEST', mode, '1 day', 0.02,
    )),
    ('detect_slow_grind', lambda det, df, mode: det.detect_slow_grind(
        df.copy(), 'TEST', mode, '1 day', 0.02,
    )),
]


def _normalize(signal):
    """Round-trip through the same float/NaN handling for a stable comparison."""
    if signal is None:
        return None
    out = {}
    for k, v in signal.items():
        if isinstance(v, float) and pd.isna(v):
            out[k] = 'NaN'
        else:
            out[k] = v
    return out


@pytest.mark.parametrize('detect_name,call', DETECT_CALLS, ids=[c[0] for c in DETECT_CALLS])
@pytest.mark.parametrize('series_name', SERIES_FIXTURES.keys())
@pytest.mark.parametrize('mode', MODES)
def test_no_live_now_et_call_is_byte_identical_to_pre_fix(detect_name, call, series_name, mode):
    """
    The core claim: with live_now_et omitted (the only way backtest ever
    calls these), old and new code must produce the exact same signal (or
    the exact same None) for every detector, every mode, and a spread of
    realistic price shapes.
    """
    df = SERIES_FIXTURES[series_name]()
    old_det = OldBreakoutDetector()
    new_det = NewBreakoutDetector()

    old_signal = _normalize(call(old_det, df, mode))
    new_signal = _normalize(call(new_det, df, mode))

    assert old_signal == new_signal, (
        f"{detect_name}/{mode}/{series_name}: pre-fix vs post-fix signal diverged "
        f"with live_now_et omitted.\nOLD: {old_signal}\nNEW: {new_signal}"
    )


@pytest.mark.parametrize('detect_name,call', DETECT_CALLS, ids=[c[0] for c in DETECT_CALLS])
@pytest.mark.parametrize('mode', MODES)
def test_real_now_ohlcv_fixture_unaffected_without_live_now_et(detect_name, call, mode):
    """
    Same comparison against NOW's real August 2026 OHLCV (the fixture §30's
    SLOW_GRIND detector was validated against) — real market data, not a
    synthetic random walk, run through every detector.
    """
    if not NOW_FIXTURE.exists():
        pytest.skip(f"{NOW_FIXTURE} not present")
    df = _load_now_fixture()
    old_det = OldBreakoutDetector()
    new_det = NewBreakoutDetector()

    old_signal = _normalize(call(old_det, df, mode))
    new_signal = _normalize(call(new_det, df, mode))

    assert old_signal == new_signal, (
        f"{detect_name}/{mode} on the real NOW fixture diverged.\n"
        f"OLD: {old_signal}\nNEW: {new_signal}"
    )


def test_at_least_one_fixture_actually_fires_a_signal():
    """
    Sanity guard on the fixtures themselves: if every series/mode/detector
    combination above returns None on both sides, the byte-identical
    assertions are vacuously true and prove nothing (the exact trap
    CLAUDE.md's §22.1/§23.1 call out repeatedly — a passing suite that never
    exercises the interesting code path). At least one combination must
    produce a real signal.
    """
    new_det = NewBreakoutDetector()
    fired = 0
    for series_name, factory in SERIES_FIXTURES.items():
        df = factory()
        for mode in MODES:
            for _, call in DETECT_CALLS:
                if call(new_det, df, mode) is not None:
                    fired += 1
    assert fired > 0, (
        "No detector/mode/series combination produced a signal — the "
        "byte-identical comparisons above never exercised real signal "
        "construction (only the 'return None' early-outs)."
    )
