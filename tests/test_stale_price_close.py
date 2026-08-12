"""
Tests for the stale-price escape hatch in refresh_prices (added 2026-08-12).

Context: when yfinance returned no history for a symbol, refresh_prices fell back
to the STORED current_price for BOTH the display mark and the exit basis. That
made the position permanently unexitable — basis_close == the frozen mark, the
trail could not move (an empty frame is below ATR_TRAIL_FLOOR_BARS), and the
frozen mark sat above the stop, so `basis_close <= p['stop']` could never become
true. Measured on the live book: PRA/JHG/HOLX/STEL, four completed-merger names
that had stopped trading, held $24,905 (24.9% of capital) in that state,
guaranteed to return 0% forever.

Same shape as CLAUDE.md §22.3 — a rule that silently never fires for part of its
input, producing no error and no warning.

AC-SP-01  A successful fetch clears any existing stale stamp
AC-SP-02  The first failed fetch stamps the date but does NOT close
AC-SP-03  A failure still inside the window does not close
AC-SP-04  A failure at/beyond STALE_PRICE_MAX_DAYS closes
AC-SP-05  max_days=0 closes on the first failure (the operator settle path)
AC-SP-06  A malformed stamp restarts the clock instead of closing on bad data
AC-SP-07  Recovery: data returning after a stamp clears it — no close
AC-SP-08  ⚠ A WHOLESALE fetch failure closes NOTHING (infrastructure fault, not
          14 simultaneous delistings — the difference between settling a dead
          name and liquidating the book on a bad afternoon)
AC-SP-09  Mixed book: the dead symbol settles, live symbols are untouched
AC-SP-10  The settled position frees its capital back into available cash
AC-SP-11  The closed record carries the right price, P&L and close_reason
AC-SP-12  A live position that has NOT gone stale still exits on its stop
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auto_portfolio as ap
from auto_portfolio import _apply_stale_price


TODAY = '2026-08-12'


# ── unit: _apply_stale_price ─────────────────────────────────────────────────

def test_successful_fetch_clears_the_stamp():
    """AC-SP-01"""
    p = {'symbol': 'KO', 'stale_since': '2026-08-01'}
    assert _apply_stale_price(p, has_data=True, today=TODAY) is False
    assert 'stale_since' not in p, "a live symbol must not carry a stale stamp"


def test_first_failure_stamps_but_does_not_close():
    """AC-SP-02 — one flaky fetch must never close a position."""
    p = {'symbol': 'KO'}
    assert _apply_stale_price(p, has_data=False, today=TODAY) is False
    assert p['stale_since'] == TODAY


def test_failure_inside_the_window_does_not_close():
    """AC-SP-03 — 4 days < STALE_PRICE_MAX_DAYS (5)."""
    p = {'symbol': 'KO', 'stale_since': '2026-08-08'}
    assert _apply_stale_price(p, has_data=False, today=TODAY) is False
    assert p['stale_since'] == '2026-08-08', "the original stamp must not be reset"


@pytest.mark.parametrize('since', ['2026-08-07', '2026-08-01'])
def test_failure_at_or_beyond_the_window_closes(since):
    """AC-SP-04 — 5 days and 11 days both settle."""
    p = {'symbol': 'PRA', 'stale_since': since}
    assert _apply_stale_price(p, has_data=False, today=TODAY) is True


def test_max_days_zero_closes_immediately():
    """AC-SP-05 — the operator path for settling known-dead symbols."""
    p = {'symbol': 'PRA'}
    assert _apply_stale_price(p, has_data=False, today=TODAY, max_days=0) is True


@pytest.mark.parametrize('bad', ['not-a-date', '', 12345, None, '2026-13-99'])
def test_malformed_stamp_restarts_the_clock(bad):
    """AC-SP-06 — never close a position because of a corrupt field."""
    p = {'symbol': 'KO', 'stale_since': bad}
    assert _apply_stale_price(p, has_data=False, today=TODAY) is False
    assert p['stale_since'] == TODAY


def test_recovery_clears_a_previous_stamp():
    """AC-SP-07 — a symbol that comes back must not carry stale state forward."""
    p = {'symbol': 'KO'}
    _apply_stale_price(p, has_data=False, today='2026-08-10')
    assert p['stale_since'] == '2026-08-10'
    assert _apply_stale_price(p, has_data=True, today=TODAY) is False
    assert 'stale_since' not in p
    # ...and the clock genuinely restarted rather than resuming the old gap.
    assert _apply_stale_price(p, has_data=False, today=TODAY) is False


# ── integration: refresh_prices ──────────────────────────────────────────────

def _hist(close):
    """30 bars, flat, enough for the ATR trail floor."""
    idx = pd.bdate_range(end='2026-08-11', periods=30)
    return pd.DataFrame({
        'Open': close, 'High': close * 1.01, 'Low': close * 0.99,
        'Close': close, 'Volume': 1_000_000,
    }, index=idx)


def _pos(symbol, entry=100.0, shares=10, stop=None, **extra):
    # Stop defaults BELOW entry relative to it — an absolute default would put
    # low-priced fixtures under their stop and close them for the wrong reason.
    stop = entry * 0.9 if stop is None else stop
    return {
        'symbol': symbol, 'date_added': '2026-08-04', 'mode': 'swing',
        'quality': 'GOLD', 'minervini_score': 0, 'entry_price': entry,
        'stop': stop, 'target': entry * 1.2, 'shares': shares,
        'cost': entry * shares, 'current_price': entry, 'type': 'TREND_CONFIRM',
        **extra,
    }


def _run(positions, live_prices, capital=100_000.0, **kwargs):
    """Drive refresh_prices with a fake yfinance; return (result, saved_data)."""
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
         patch.object(ap, '_save', side_effect=lambda d, **kw: saved.update(d)):
        result = ap.refresh_prices(**kwargs)
    return result, data


def test_wholesale_fetch_failure_closes_nothing():
    """AC-SP-08 — the safety property. Every symbol failing is an outage."""
    positions = [_pos('KO', stale_since='2026-08-01'),
                 _pos('F',  stale_since='2026-08-01'),
                 _pos('PRA', stale_since='2026-08-01')]
    result, data = _run(positions, live_prices={})   # nothing fetches

    assert result['closed'] == [], "an outage must not liquidate the book"
    assert len(data['positions']) == 3
    assert data['closed'] == []


def test_dead_symbol_settles_while_live_ones_survive():
    """AC-SP-09 + AC-SP-10 — the production shape."""
    positions = [_pos('KO', entry=60.0, shares=100),
                 _pos('PRA', entry=24.99, shares=200,
                      current_price=25.00, stale_since='2026-08-01')]
    result, data = _run(positions, live_prices={'KO': 62.0})

    assert result['closed'] == ['PRA']
    assert [p['symbol'] for p in data['positions']] == ['KO']
    # Capital is credited and the cost is released back into buying power.
    assert data['capital'] == pytest.approx(100_000 + (25.00 - 24.99) * 200)
    assert ap.available_cash(data) == pytest.approx(
        data['capital'] - 60.0 * 100)


def test_settled_record_shape_and_pnl():
    """AC-SP-11"""
    positions = [_pos('KO', entry=60.0, shares=100),
                 _pos('HOLX', entry=76.02, shares=65,
                      current_price=76.02, stale_since='2026-08-01')]
    _, data = _run(positions, live_prices={'KO': 62.0})

    rec = data['closed'][0]
    assert rec['symbol'] == 'HOLX'
    assert rec['close_reason'] == 'no_market_data'
    assert rec['exit_price'] == pytest.approx(76.02), \
        "settle at the last known mark, not the entry price"
    assert rec['pnl'] == pytest.approx(0.0)
    assert rec['date_closed']


def test_first_quiet_run_only_stamps():
    """AC-SP-02 end-to-end: a newly-quiet symbol is held, not closed."""
    positions = [_pos('KO', entry=60.0, shares=100), _pos('PRA')]
    result, data = _run(positions, live_prices={'KO': 62.0})

    assert result['closed'] == []
    pra = next(p for p in data['positions'] if p['symbol'] == 'PRA')
    assert pra['stale_since'], "must record when the symbol went quiet"


def test_operator_settle_closes_known_dead_immediately():
    """AC-SP-05 end-to-end — stale_max_days=0, the release path."""
    positions = [_pos('KO', entry=60.0, shares=100), _pos('PRA'), _pos('JHG')]
    result, data = _run(positions, live_prices={'KO': 62.0}, stale_max_days=0)

    assert sorted(result['closed']) == ['JHG', 'PRA']
    assert [p['symbol'] for p in data['positions']] == ['KO']


def test_live_position_still_exits_on_its_stop():
    """AC-SP-12 — the new branch must not shadow the existing stop check."""
    positions = [_pos('KO', entry=100.0, shares=10, stop=95.0)]
    result, data = _run(positions, live_prices={'KO': 90.0})

    assert result['closed'] == ['KO']
    assert data['closed'][0]['close_reason'] == 'atr_trail_stop'


def test_live_symbol_never_accumulates_a_stamp():
    """AC-SP-01 end-to-end."""
    positions = [_pos('KO', entry=60.0, shares=100, stale_since='2026-08-01')]
    result, data = _run(positions, live_prices={'KO': 62.0})

    assert result['closed'] == []
    assert 'stale_since' not in data['positions'][0]
