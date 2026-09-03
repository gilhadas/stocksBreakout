"""
Regression tests for the intraday Vol_Ratio pacing fix (2026-09-03).

WHY THIS EXISTS
----------------
Live intraday scans (docker/crontab's 9:35 ET swing scan, 5 minutes after the
open) fetch a daily bar whose last row is still forming — Vol_Ratio computed
off it (raw volume / 20-day average) compares a few minutes of trading
against a denominator built from full trading days. Every detector decides
its signal off exactly this number
(`vol_confirm`/`vol_ok = latest['Vol_Ratio'] >= threshold`), so an early scan
can silently suppress a real signal.

utils.pace_adjust_volume_ratio() fixes the number; this file guards the
*wiring* around it, in the same spirit as test_crontab_parity.py (CLAUDE.md
§13/§24: a fix with no test binding it to every call site drifts back to
broken the next time someone touches the file) and mirrors test_pinned_range
.py's own lesson — a suite that only re-derives the decision table in Python
can pass while the real code path never fires; these tests exercise the
actual detector functions and the actual orchestrator source, not a mirror
of their logic.

Three levels:
  1. Structural — every one of orchestrator.py's `self.detector.detect*(`
     call sites passes `live_now_et=`. Catches a future call site (or a
     careless edit to an existing one) silently losing the wire-up.
  2. Structural — no backtest script ever passes `live_now_et`, preserving
     the "historical replay is untouched by construction" guarantee as a
     testable invariant rather than a docstring claim.
  3. Behavioral — an actual detect_bounce() call, with a real 52-bar
     synthetic OHLCV frame, flips from None (raw partial-bar volume trips
     the 0.15 hard floor) to a real HIGH-quality BOUNCE signal (pace-adjusted
     volume clears it) purely by passing `live_now_et`. This is the
     regression this fix exists to prevent: proof the wiring changes what a
     live scan actually admits, not just that the helper function is
     internally correct (see test_pace_adjust_volume_ratio.py for that).
"""
from __future__ import annotations

import ast
import asyncio
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Python 3.14 event-loop quirk (CLAUDE.md §2) — required before importing
# scanner/orchestrator, which pull in ib_insync transitively.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from scanner import BreakoutDetector

ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR_SRC = (ROOT / 'orchestrator.py').read_text()

BACKTEST_SCRIPTS = [
    'backtest_regime_compare.py',
    'enhanced_backtest.py',
    'backtest_validation.py',
    'mock_trader.py',
    'daytrade_tension_backtest.py',
    'scalp_supertrend_backtest.py',
    'scalp_supertrend_exit_backtest.py',
]

DETECT_METHODS = {
    'detect', 'detect_bounce', 'detect_continuation',
    'detect_trend_confirm', 'detect_sma20_cross', 'detect_slow_grind',
}


# ─── 1. Every live call site is wired ─────────────────────────────────────

def _detector_call_sites(src: str) -> list[ast.Call]:
    """AST-parse orchestrator.py and return every self.detector.detect*(...) call."""
    tree = ast.parse(src)
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr in DETECT_METHODS
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == 'detector'):
            calls.append(node)
    return calls


def test_orchestrator_has_the_expected_detector_call_sites():
    """
    Sanity check on the AST walk itself — if this count ever changes, the
    tests below need a look, not a silent pass. Locks in the 10 call sites
    wired in the 2026-09-03 fix (1 main detect() + 9 cascade calls across
    the bear_macro/BEARISH/normal-regime branches).
    """
    calls = _detector_call_sites(ORCHESTRATOR_SRC)
    assert len(calls) == 10, (
        f"Expected 10 self.detector.detect*() call sites in orchestrator.py, "
        f"found {len(calls)}. If a call site was added or removed, update "
        f"this test AND verify live_now_et is threaded through it."
    )


def test_every_detector_call_site_passes_live_now_et():
    """
    The actual regression guard: every self.detector.detect*() call in
    orchestrator.py must pass live_now_et as a keyword argument. A call site
    that loses this silently reverts to judging signals off today's raw,
    unpaced Vol_Ratio during intraday scans — the exact bug this fix closes.
    """
    calls = _detector_call_sites(ORCHESTRATOR_SRC)
    assert calls, "AST walk found no detector call sites — parser or source path is broken"
    missing = []
    for node in calls:
        kw_names = {kw.arg for kw in node.keywords}
        if 'live_now_et' not in kw_names:
            missing.append(f"line {node.lineno}: {node.func.attr}(...)")
    assert not missing, (
        "These self.detector.detect*() calls in orchestrator.py do not pass "
        "live_now_et:\n" + "\n".join(missing)
    )


def test_live_now_et_is_computed_before_first_use():
    """
    live_now_et must be assigned before the first detector call that
    references it — otherwise every call site 'passing' it is passing an
    undefined name (a NameError at runtime, not a silent bug, but worth
    pinning so a refactor can't reorder this into breakage).
    """
    define_at = ORCHESTRATOR_SRC.index('live_now_et = (')
    first_use_at = ORCHESTRATOR_SRC.index('live_now_et=live_now_et')
    assert define_at < first_use_at


def test_live_now_et_is_none_for_intraday_timeframes():
    """
    A 5-min/1-hour bar is already complete once it closes — there is no
    'partial bar' concept to pace-adjust. orchestrator.py must gate
    live_now_et to None for those timeframes rather than applying daily-bar
    pacing logic to an intraday series.
    """
    assert (
        "if 'min' not in timeframe.lower() and 'hour' not in timeframe.lower()"
        in ORCHESTRATOR_SRC
    )


# ─── 2. Backtest replay is untouched by construction ──────────────────────

@pytest.mark.parametrize('script', BACKTEST_SCRIPTS)
def test_backtest_scripts_never_pass_live_now_et(script):
    """
    live_now_et must stay live-only. If a backtest script ever starts
    passing it, historical replay would start depending on the wall-clock
    time the backtest happens to be RUN at — silently non-deterministic
    results, and a violation of §29's lookahead-bias audit standard (every
    detector call must be bounded by simulation state, never real 'now').
    """
    path = ROOT / script
    if not path.exists():
        pytest.skip(f"{script} not present")
    assert 'live_now_et' not in path.read_text()


# ─── 3. Behavioral: the actual detector flips on the actual wiring ────────

def _make_bounce_setup_df(today: str = '2026-07-27',
                           today_volume: float = 90_000) -> pd.DataFrame:
    """
    52 bars: a decline from 150 -> ~95 (sets up the 20-day-high / >=15%
    drawdown gate), a quiet oversold low yesterday, then a genuine +4%
    bounce day today with recovering RSI and improving MACD — every
    detect_bounce() gate satisfied except volume, which is deliberately
    thin (today_volume) to simulate a live scan a few minutes after the
    open. Deterministic (seeded).
    """
    np.random.seed(7)
    n_pre = 50
    dates = pd.bdate_range(end=today, periods=n_pre + 2)

    closes = np.linspace(150, 95, n_pre) + np.random.normal(0, 0.4, n_pre)
    highs = closes + 1.0
    lows = closes - 1.0
    opens = closes - 0.2
    vols = np.random.normal(1_000_000, 50_000, n_pre)

    y_close, y_high, y_low, y_open, y_vol = 90.0, 91.0, 89.0, 90.5, 900_000
    t_open, t_close, t_high, t_low = 91.0, 93.6, 94.0, 90.8

    df = pd.DataFrame({
        'open':   list(opens)  + [y_open, t_open],
        'high':   list(highs)  + [y_high, t_high],
        'low':    list(lows)   + [y_low, t_low],
        'close':  list(closes) + [y_close, t_close],
        'volume': list(vols)   + [y_vol, today_volume],
    }, index=dates)
    assert df.index[-1] == pd.Timestamp(today)
    return df


def test_thin_partial_bar_volume_silently_blocks_bounce_without_pacing():
    """
    Control: the exact scenario below, called the way a backtest (or any
    caller that omits live_now_et) would — must stay None. If this ever
    starts firing, the synthetic fixture has drifted and the "with pacing"
    test below is no longer proving what it claims to.
    """
    df = _make_bounce_setup_df()
    detector = BreakoutDetector()
    signal = detector.detect_bounce(df.copy(), 'TEST', 'swing', '1 day')
    assert signal is None, (
        "Fixture regression: expected the thin-volume bar to trip the 0.15 "
        "hard floor and block the signal even before pacing is applied."
    )


def test_pacing_recovers_the_real_signal_a_live_scan_would_otherwise_miss():
    """
    THE regression test. Identical bar, identical detector, identical
    thresholds — the only difference is passing live_now_et=9:35 ET (what
    orchestrator._scan_symbol now does on every live call). Raw Vol_Ratio
    for the partial bar is ~0.093 (below the 0.15 hard floor in
    detect_bounce -> silent None, proven above); paced Vol_Ratio projects
    to ~0.36 (clears the floor), and every other BOUNCE gate (strong_move,
    was_beaten_down, RSI recovery, MACD improving, R:R) is already
    satisfied by the fixture, so a real HIGH-quality signal is produced.
    """
    df = _make_bounce_setup_df()
    detector = BreakoutDetector()
    now_et = datetime(2026, 7, 27, 9, 35)  # 5 minutes after the 9:30 ET open

    signal = detector.detect_bounce(
        df.copy(), 'TEST', 'swing', '1 day', live_now_et=now_et,
    )

    assert signal is not None, (
        "Pacing did not recover the signal — the live wiring regressed."
    )
    assert signal['Type'] == 'BOUNCE'
    assert signal['Quality'] in ('HIGH', 'PREMIUM')
    assert signal['Vol'] > 0.15   # cleared the hard floor
    assert signal['R:R'] >= 1.5


def test_pacing_is_a_noop_after_market_close_same_bar():
    """
    The same bar, but evaluated after the close (16:30 ET) — today's bar is
    final by then (utils.pace_adjust_volume_ratio's own late-window rule),
    so pacing must NOT invent a signal out of genuinely thin full-day
    volume. Guards against the pacing logic firing outside its intended
    intraday window.
    """
    df = _make_bounce_setup_df()
    detector = BreakoutDetector()
    now_et = datetime(2026, 7, 27, 16, 30)

    signal = detector.detect_bounce(
        df.copy(), 'TEST', 'swing', '1 day', live_now_et=now_et,
    )
    assert signal is None


def test_omitting_live_now_et_matches_backtest_call_shape():
    """
    Every backtest call site (collect_signals_old/new/hybrid, per §29) calls
    detect_bounce() positionally without live_now_et. Confirm that shape
    still works and still returns None on this fixture — i.e. the new kwarg
    is additive, not a breaking signature change.
    """
    df = _make_bounce_setup_df()
    detector = BreakoutDetector()
    signal = detector.detect_bounce(df.copy(), 'TEST', 'swing', '1 day')
    assert signal is None
