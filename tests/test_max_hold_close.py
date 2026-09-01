"""
Tests for the MaxHold auto-close in refresh_prices (added 2026-08-28).

Context: exit_evaluator.py has always recommended closing a position once it
passes its mode's MAX_HOLD_BARS (calendar days) — this is the same MaxHold rule
the backtest's simulate() enforces (MAX_HOLD=30) and that the champion's own
validated numbers depend on. But refresh_prices() — the only function that
actually closes an auto_portfolio position — never implemented it, so a
"Max hold period reached" EXIT_FULL notification could recur every single day
indefinitely with no way to ever be acted on automatically (observed live:
FROG and CF both stuck past 30/30 days, re-notified daily). This is the same
"advisory recommendation with no execution path" shape already documented for
portfolio.json (CLAUDE.md §22.3) and the pre-2026-03 mock CSVs (§13) — except
this time the position genuinely belongs to the auto-managed book.

AC-MH-01  Under the cap: stays open
AC-MH-02  Exactly at the cap: closes (boundary is >=, not >)
AC-MH-03  One day under the cap: stays open (boundary check the other way)
AC-MH-04  Past the cap: closes
AC-MH-05  Mode-specific cap: longterm (60d) does not close what swing (30d) would
AC-MH-06  scalping (max_hold=0) never closes on hold length, any age
AC-MH-07  Stop-hit takes priority — a position that is BOTH past max_hold AND
          below its stop closes as 'atr_trail_stop', not 'max_hold'
AC-MH-08  Missing/malformed date_added does not crash and does not close
AC-MH-09  MaxHold close credits pnl/capital exactly like a stop-loss close
AC-MH-10  Mixed book: only the stale position closes, the fresh one survives
"""
from __future__ import annotations

import sys
import types
import datetime as _dt_module
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auto_portfolio as ap
from auto_portfolio import _NY_TZ

NOW = _dt_module.datetime(2026, 8, 28, 15, 45)


class _FixedDatetime(_dt_module.datetime):
    """Subclass (not a Mock) so strptime/other real datetime methods still work."""
    @classmethod
    def now(cls, tz=None):
        return cls(NOW.year, NOW.month, NOW.day, NOW.hour, NOW.minute, tzinfo=tz)


def _hist(close):
    """30 bars ending 'today', flat, enough for the ATR trail floor."""
    idx = pd.bdate_range(end=NOW.date().isoformat(), periods=30)
    return pd.DataFrame({
        'Open': close, 'High': close * 1.01, 'Low': close * 0.99,
        'Close': close, 'Volume': 1_000_000,
    }, index=idx)


def _added(days_ago):
    return (NOW.date() - _dt_module.timedelta(days=days_ago)).isoformat()


def _pos(symbol, entry=100.0, shares=10, stop=None, days_ago=10, mode='swing', **extra):
    # Stop defaults far BELOW entry so only MaxHold (not the stop check) can
    # fire, unless a test deliberately overrides it.
    stop = entry * 0.5 if stop is None else stop
    return {
        'symbol': symbol, 'date_added': _added(days_ago), 'mode': mode,
        'quality': 'GOLD', 'minervini_score': 0, 'entry_price': entry,
        'stop': stop, 'target': entry * 1.2, 'shares': shares,
        'cost': entry * shares, 'current_price': entry, 'type': 'TREND_CONFIRM',
        **extra,
    }


def _run(positions, live_prices, capital=100_000.0, **kwargs):
    """Drive refresh_prices with a fake yfinance and a fixed 'now'."""
    data = {'capital': capital, 'positions': positions, 'closed': [],
            'skipped_cash': [], 'processed_files': [], 'equity_history': []}

    class _Ticker:
        def __init__(self, sym):
            self.sym = sym

        def history(self, period=None):
            px = live_prices.get(self.sym)
            return _hist(px) if px is not None else pd.DataFrame()

    fake_yf = types.ModuleType('yfinance')
    fake_yf.Ticker = _Ticker

    saved = {}
    with patch.dict(sys.modules, {'yfinance': fake_yf}), \
         patch.object(ap, '_load_for_write', return_value=data), \
         patch.object(ap, '_save', side_effect=lambda d, **kw: saved.update(d)), \
         patch.object(ap, 'datetime', _FixedDatetime):
        result = ap.refresh_prices(**kwargs)
    return result, data


def test_under_the_cap_stays_open():
    """AC-MH-01"""
    positions = [_pos('KO', days_ago=20, mode='swing')]  # cap is 30
    result, data = _run(positions, live_prices={'KO': 62.0})

    assert result['closed'] == []
    assert [p['symbol'] for p in data['positions']] == ['KO']


def test_exactly_at_the_cap_closes():
    """AC-MH-02 — boundary is >=, matching simulate()'s days_held >= hold_cap."""
    positions = [_pos('KO', days_ago=30, mode='swing')]
    result, data = _run(positions, live_prices={'KO': 62.0})

    assert result['closed'] == ['KO']
    assert data['closed'][0]['close_reason'] == 'max_hold'


def test_one_day_under_the_cap_stays_open():
    """AC-MH-03 — the boundary the other direction."""
    positions = [_pos('KO', days_ago=29, mode='swing')]
    result, data = _run(positions, live_prices={'KO': 62.0})

    assert result['closed'] == []


def test_past_the_cap_closes():
    """AC-MH-04 — reproduces the live FROG/CF shape (32/30 days)."""
    positions = [_pos('FROG', days_ago=32, mode='swing')]
    result, data = _run(positions, live_prices={'FROG': 98.83})

    assert result['closed'] == ['FROG']
    assert data['closed'][0]['close_reason'] == 'max_hold'


def test_longterm_cap_is_not_the_swing_cap():
    """AC-MH-05 — 40 days: past swing's 30, nowhere near longterm's 60."""
    positions = [_pos('WAB', days_ago=40, mode='longterm')]
    result, data = _run(positions, live_prices={'WAB': 300.0})

    assert result['closed'] == []
    assert [p['symbol'] for p in data['positions']] == ['WAB']


def test_longterm_past_its_own_cap_closes():
    positions = [_pos('WAB', days_ago=61, mode='longterm')]
    result, data = _run(positions, live_prices={'WAB': 300.0})

    assert result['closed'] == ['WAB']


def test_scalping_never_closes_on_hold_length():
    """AC-MH-06 — MAX_HOLD_BARS['scalping']=0 means no cap, at any age."""
    positions = [_pos('KO', days_ago=500, mode='scalping')]
    result, data = _run(positions, live_prices={'KO': 62.0})

    assert result['closed'] == []


def test_stop_hit_takes_priority_over_max_hold():
    """AC-MH-07 — the two rules can both apply; the stop check runs first."""
    positions = [_pos('KO', entry=100.0, stop=95.0, days_ago=45, mode='swing')]
    result, data = _run(positions, live_prices={'KO': 90.0})  # below stop too

    assert result['closed'] == ['KO']
    assert data['closed'][0]['close_reason'] == 'atr_trail_stop', \
        "a stop breach must win over MaxHold, not the other way round"


@pytest.mark.parametrize('bad_added', ['', None, 'not-a-date', '2026-13-99'])
def test_malformed_date_added_does_not_close_or_crash(bad_added):
    """AC-MH-08"""
    positions = [_pos('KO', days_ago=45, mode='swing')]
    positions[0]['date_added'] = bad_added
    result, data = _run(positions, live_prices={'KO': 62.0})

    assert result['closed'] == []
    assert [p['symbol'] for p in data['positions']] == ['KO']


def test_max_hold_close_credits_pnl_and_capital():
    """AC-MH-09 — same accounting path as a stop-loss close."""
    positions = [_pos('KO', entry=60.0, shares=100, days_ago=35, mode='swing')]
    result, data = _run(positions, live_prices={'KO': 65.0})

    rec = data['closed'][0]
    assert rec['close_reason'] == 'max_hold'
    assert rec['exit_price'] == pytest.approx(65.0)
    assert rec['pnl'] == pytest.approx((65.0 - 60.0) * 100)
    assert data['capital'] == pytest.approx(100_000 + (65.0 - 60.0) * 100)


def test_mixed_book_only_the_stale_position_closes():
    """AC-MH-10"""
    positions = [_pos('KO', days_ago=5, mode='swing'),
                 _pos('FROG', days_ago=32, mode='swing')]
    result, data = _run(positions, live_prices={'KO': 62.0, 'FROG': 98.83})

    assert result['closed'] == ['FROG']
    assert [p['symbol'] for p in data['positions']] == ['KO']
