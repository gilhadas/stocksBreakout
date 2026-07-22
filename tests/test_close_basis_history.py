"""
Tests for auto_portfolio._close_basis_history() — the close-based exit basis
for refresh_prices() (design-mismatch fix, 2026-07-22).

The champion exit is CLOSE-based: an intraday dip below the trail must NOT
exit (low-based triggering gave 2022 −24.8% in the restore isolation), yet
the 10:00 ET refresh cron was deciding exits on the live intraday price via
today's partial yfinance bar. These tests pin the corrected semantics:

AC-CBH-01  Morning run (before 15:30 ET): today's partial bar is dropped —
           exit/trail decisions use the last COMPLETED daily close
AC-CBH-02  Late window (>= 15:30 ET): today's near-close bar is kept as a
           fair close proxy (the 15:45 cron behavior, unchanged)
AC-CBH-03  After the close (>= 16:00 ET): today's bar is final and kept
AC-CBH-04  Last bar from a prior session (market closed / weekend): kept
           regardless of wall-clock time
AC-CBH-05  End-to-end: a morning intraday dip below the stop does not make
           the basis close breach the stop when yesterday closed above it
"""
from datetime import datetime

import pandas as pd
import pytest

from auto_portfolio import _close_basis_history


def _hist(dates, closes):
    return pd.DataFrame({
        'Open':  closes,
        'High':  [c + 1 for c in closes],
        'Low':   [c - 1 for c in closes],
        'Close': closes,
        'Volume': [1_000_000] * len(closes),
    }, index=pd.DatetimeIndex(pd.to_datetime(dates)))


TODAY = '2026-07-22'          # a Wednesday
YDAY  = '2026-07-21'
DAYS  = ['2026-07-17', '2026-07-20', YDAY, TODAY]


# ─── AC-CBH-01: morning run drops today's partial bar ────────────────────────
def test_morning_run_drops_todays_partial_bar():
    hist = _hist(DAYS, [100.0, 101.0, 102.0, 95.0])   # 95 = live dip
    now = datetime(2026, 7, 22, 10, 0)
    basis = _close_basis_history(hist, now)
    assert len(basis) == len(hist) - 1
    assert float(basis['Close'].iloc[-1]) == 102.0    # yesterday's close


# ─── AC-CBH-02: late window keeps the near-close bar ─────────────────────────
def test_late_window_keeps_todays_bar():
    hist = _hist(DAYS, [100.0, 101.0, 102.0, 95.0])
    for hh, mm in [(15, 30), (15, 45), (15, 59)]:
        basis = _close_basis_history(hist, datetime(2026, 7, 22, hh, mm))
        assert len(basis) == len(hist)
        assert float(basis['Close'].iloc[-1]) == 95.0


# ─── AC-CBH-03: after the close today's bar is final ─────────────────────────
def test_after_close_keeps_todays_bar():
    hist = _hist(DAYS, [100.0, 101.0, 102.0, 95.0])
    basis = _close_basis_history(hist, datetime(2026, 7, 22, 16, 30))
    assert len(basis) == len(hist)


# ─── AC-CBH-04: prior-session last bar kept at any hour ──────────────────────
def test_prior_session_bar_kept_any_hour():
    hist = _hist(DAYS[:-1], [100.0, 101.0, 102.0])    # no bar for today
    for hh in (9, 10, 12, 15, 20):
        basis = _close_basis_history(hist, datetime(2026, 7, 22, hh, 0))
        assert len(basis) == len(hist)


def test_none_and_empty_pass_through():
    assert _close_basis_history(None, datetime(2026, 7, 22, 10, 0)) is None
    empty = _hist([], [])
    assert _close_basis_history(empty, datetime(2026, 7, 22, 10, 0)) is empty


# ─── AC-CBH-05: morning dip does not breach the stop via the basis ───────────
def test_morning_dip_does_not_breach_stop():
    stop = 98.0
    hist = _hist(DAYS, [100.0, 101.0, 102.0, 96.0])   # live dip below stop
    basis = _close_basis_history(hist, datetime(2026, 7, 22, 10, 0))
    basis_close = float(basis['Close'].dropna().iloc[-1])
    assert basis_close > stop                          # 102 > 98 → no exit
    # Same frame in the late window WOULD breach — the intended 15:45 behavior
    late = _close_basis_history(hist, datetime(2026, 7, 22, 15, 45))
    assert float(late['Close'].dropna().iloc[-1]) <= stop
