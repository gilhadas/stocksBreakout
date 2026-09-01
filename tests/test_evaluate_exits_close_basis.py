"""
Regression test: orchestrator.evaluate_exits() must decide "Trend broken" (and
every other exit_evaluator.py rule) off a close-based history for daily bars,
not off whatever partial/live bar the data source hands back mid-session.

WHY THIS EXISTS
---------------
Reported 2026-08-03: ARKX (a live swing position) fired "Trend broken: 31.46
< 32.53" while the stock was still trending up. `evaluate_exits` fetches data
via `MarketDataHandler.get_historical_data`, whose yfinance fallback returns
today's *partial* daily bar mid-session (Close = live price) with a disk
cache that can serve a snapshot up to 4 hours stale on top of that. The rule
`price < trend_line` (exit_evaluator.py) was deciding off that snapshot with
no trimming at all — the same partial-bar problem `auto_portfolio.
refresh_prices` already had and fixed via `_close_basis_history` (2026-07-22),
but that fix never reached this code path.

This test pins the port: `close_basis_history` (utils.py, shared with
auto_portfolio) is applied to daily-bar positions before exit_evaluator sees
the data, and is skipped for intraday (daytrade/scalping) positions where the
concept doesn't apply.

Run:
    python -m pytest tests/test_evaluate_exits_close_basis.py -v
"""
import asyncio
import sys
from datetime import datetime as real_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import orchestrator as orchestrator_module
from orchestrator import ScannerOrchestrator

_NY = ZoneInfo('America/New_York')


class _FakeDatetime(real_datetime):
    """Stand-in for orchestrator's `datetime` so `.now(tz)` is deterministic."""
    _fixed = None  # must be NY-tz-aware

    @classmethod
    def now(cls, tz=None):
        return cls._fixed if tz is None else cls._fixed.astimezone(tz)


def _freeze(monkeypatch, year, month, day, hour, minute):
    """Freeze orchestrator's `datetime.now(...)` to this NY wall-clock time."""
    _FakeDatetime._fixed = real_datetime(year, month, day, hour, minute, tzinfo=_NY)
    monkeypatch.setattr(orchestrator_module, 'datetime', _FakeDatetime)


def _flat_daily_history(today: pd.Timestamp, today_close: float,
                         n: int = 151, flat_close: float = 100.0) -> pd.DataFrame:
    """n bars ending exactly on `today`, all flat at `flat_close` except the
    last bar (today), whose close is `today_close` — simulating an intraday
    dip on an otherwise flat SMA150."""
    dates = pd.bdate_range(end=today, periods=n)
    closes = [flat_close] * (n - 1) + [today_close]
    return pd.DataFrame({
        'open':   closes,
        'high':   [c + 1 for c in closes],
        'low':    [c - 1 for c in closes],
        'close':  closes,
        'volume': [1_000_000] * n,
    }, index=dates)


def _make_orchestrator(monkeypatch, hist: pd.DataFrame, timeframe: str):
    orch = ScannerOrchestrator(ib_connection=None, use_level2=False, yf_fallback=False)

    async def fake_get_historical_data(symbol, tf, exchange='SMART', currency='USD'):
        assert tf == timeframe
        return hist.copy()

    monkeypatch.setattr(orch.market_data, 'get_historical_data', fake_get_historical_data)
    return orch


def _swing_position(entry=85.0, stop=70.0, target=150.0):
    return {
        'symbol': 'ARKX', 'mode': 'swing', 'timeframe': '1 day',
        'entry': entry, 'stop': stop, 'target': target,
        'entry_date': '', 'signal_type': 'TREND_CONFIRM',
    }


def _run(coro):
    return asyncio.run(coro)


class TestCloseBasisAppliedToDailyExits:
    def test_morning_intraday_dip_does_not_trigger_trend_broken(self, monkeypatch):
        """A live dip below SMA150 at 10:00 ET must not fire — yesterday's
        completed close (flat at 100, above SMA150=100 by definition) is
        what the decision must use."""
        today = pd.Timestamp('2026-08-03')  # a Monday
        hist = _flat_daily_history(today, today_close=90.0)  # live dip
        _freeze(monkeypatch, 2026, 8, 3, 10, 0)
        orch = _make_orchestrator(monkeypatch, hist, '1 day')

        results = _run(orch.evaluate_exits([_swing_position()]))

        assert len(results) == 1
        assert 'Trend broken' not in results[0]['Reason'], results[0]
        # A raised swing-low trail suggestion (priority 40, unrelated to this
        # bug) is a legitimate lower-priority signal at unrealized_r>=1.0 —
        # what matters here is that the false EXIT_FULL never fires.
        assert results[0]['Action'] != 'EXIT_FULL', results[0]

    def test_late_window_uses_todays_bar_as_close_proxy(self, monkeypatch):
        """At/after 15:30 ET, today's near-close bar is a fair proxy and the
        same dip legitimately fires — matches the validated auto_portfolio
        semantics (15:45 cron), not a regression."""
        today = pd.Timestamp('2026-08-03')
        hist = _flat_daily_history(today, today_close=90.0)
        _freeze(monkeypatch, 2026, 8, 3, 15, 45)
        orch = _make_orchestrator(monkeypatch, hist, '1 day')

        results = _run(orch.evaluate_exits([_swing_position()]))

        assert len(results) == 1
        assert results[0]['Action'] == 'EXIT_FULL'
        assert 'Trend broken' in results[0]['Reason']

    def test_after_close_uses_todays_final_bar(self, monkeypatch):
        today = pd.Timestamp('2026-08-03')
        hist = _flat_daily_history(today, today_close=90.0)
        _freeze(monkeypatch, 2026, 8, 3, 16, 30)
        orch = _make_orchestrator(monkeypatch, hist, '1 day')

        results = _run(orch.evaluate_exits([_swing_position()]))

        assert results[0]['Action'] == 'EXIT_FULL'
        assert 'Trend broken' in results[0]['Reason']

    def test_no_dip_never_fires_regardless_of_time(self, monkeypatch):
        """Sanity: a position that never dips below trend must never see a
        Trend-broken EXIT_FULL at any hour — the trim must not manufacture
        false exits either."""
        today = pd.Timestamp('2026-08-03')
        hist = _flat_daily_history(today, today_close=101.0)  # no dip at all
        for hh, mm in [(10, 0), (15, 45), (16, 30)]:
            _freeze(monkeypatch, 2026, 8, 3, hh, mm)
            orch = _make_orchestrator(monkeypatch, hist, '1 day')
            results = _run(orch.evaluate_exits([_swing_position()]))
            assert results[0]['Action'] != 'EXIT_FULL', (hh, mm, results[0])
            assert 'Trend broken' not in results[0]['Reason'], (hh, mm, results[0])


class TestCloseBasisSkippedForIntraday:
    def test_daytrade_timeframe_is_not_trimmed(self, monkeypatch):
        """Intraday positions (e.g. 15-min daytrade bars) have no analogous
        'partial daily bar' problem — the trim must not touch them."""
        today = pd.Timestamp('2026-08-03')
        # 40 bars is enough for daytrade's trend_period=9 (max(30, 9)=30)
        dates = pd.bdate_range(end=today, periods=40)
        closes = [100.0] * 39 + [90.0]
        hist = pd.DataFrame({
            'open': closes, 'high': [c + 1 for c in closes],
            'low': [c - 1 for c in closes], 'close': closes,
            'volume': [1_000_000] * 40,
        }, index=dates)

        _freeze(monkeypatch, 2026, 8, 3, 10, 0)
        orch = _make_orchestrator(monkeypatch, hist, '15 mins')

        called = {'close_basis_history': False}
        real_cbh = orchestrator_module.close_basis_history

        def spy_cbh(*a, **kw):
            called['close_basis_history'] = True
            return real_cbh(*a, **kw)

        monkeypatch.setattr(orchestrator_module, 'close_basis_history', spy_cbh)

        pos = {
            'symbol': 'XYZ', 'mode': 'daytrade', 'timeframe': '15 mins',
            'entry': 85.0, 'stop': 70.0, 'target': 150.0,
            'entry_date': '', 'signal_type': '',
        }
        _run(orch.evaluate_exits([pos]))

        assert not called['close_basis_history'], (
            'close_basis_history must not run for intraday timeframes')
