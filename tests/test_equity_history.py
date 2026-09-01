"""
One equity point per day, per book.

WHY THIS EXISTS
---------------
The auto book's schema is entirely point-in-time — `_empty()` has no time series
at all — so the A/B had no way to compute return, Sharpe or drawdown over the
experiment window. `_record_equity_point` adds one, written as a tail of
`refresh_prices` because that is the only place a full valuation already exists.

`refresh_prices` runs at least TWICE every weekday (docker/crontab 10:00 and
15:45), and `refresh_prices_all_users` now runs it once per book on top of that.
A naive append would therefore record 2+ points per calendar day, which does not
just clutter the series — it corrupts every metric derived from it. `sharpe()`
divides by the standard deviation of *daily* returns, so a duplicated day
injects a spurious ~0% return that deflates volatility and inflates Sharpe.
Getting this wrong makes the experiment's headline number wrong in the
flattering direction, which is the hardest kind of error to notice.
"""
from __future__ import annotations

import pytest

import auto_portfolio as ap
import book_compare as bc


def _book(positions=None, capital=100000.0):
    d = ap._empty()
    d['capital'] = capital
    d['positions'] = positions or []
    return d


def test_records_a_point_with_the_expected_shape():
    d = _book([{'symbol': 'A', 'entry_price': 10.0, 'current_price': 12.0,
                'shares': 10, 'cost': 100.0}])
    pt = ap._record_equity_point(d, when='2026-08-10')

    assert pt['date'] == '2026-08-10'
    assert pt['market_value'] == 120.0
    assert pt['positions_count'] == 1
    assert pt['total_value'] == round(pt['cash'] + pt['market_value'], 2)
    assert d['equity_history'] == [pt]


def test_same_day_overwrites_rather_than_appends():
    """The 10:00 and 15:45 runs are the same trading day."""
    d = _book([{'symbol': 'A', 'entry_price': 10.0, 'current_price': 11.0,
                'shares': 10, 'cost': 100.0}])
    ap._record_equity_point(d, when='2026-08-10')
    d['positions'][0]['current_price'] = 13.0
    ap._record_equity_point(d, when='2026-08-10')

    assert len(d['equity_history']) == 1, "two runs on one day must not book two points"
    assert d['equity_history'][0]['market_value'] == 130.0, "the later run should win"


def test_distinct_days_accumulate_in_order():
    d = _book()
    for day in ('2026-08-12', '2026-08-10', '2026-08-11'):
        ap._record_equity_point(d, when=day)
    assert [p['date'] for p in d['equity_history']] == \
        ['2026-08-10', '2026-08-11', '2026-08-12']


def test_a_position_without_a_current_price_falls_back_to_entry():
    """A freshly added position has no current_price until the next refresh —
    valuing it at 0 would show a phantom crash on the day it was opened."""
    d = _book([{'symbol': 'A', 'entry_price': 10.0, 'shares': 10, 'cost': 100.0}])
    pt = ap._record_equity_point(d, when='2026-08-10')
    assert pt['market_value'] == 100.0


def test_empty_book_still_records_its_cash():
    d = _book()
    pt = ap._record_equity_point(d, when='2026-08-10')
    assert pt['market_value'] == 0.0
    assert pt['total_value'] == 100000.0


# ── The metrics that consume the series ──────────────────────────────────────

def test_duplicated_days_would_distort_sharpe():
    """Pins WHY the idempotence above matters, not just that it holds."""
    clean = [{'date': f'2026-08-{d:02d}', 'total_value': v}
             for d, v in [(3, 100.0), (4, 102.0), (5, 101.0), (6, 104.0)]]
    dupes = [clean[0], dict(clean[0]), clean[1], dict(clean[1]),
             clean[2], dict(clean[2]), clean[3], dict(clean[3])]
    assert bc.sharpe(clean) != bc.sharpe(dupes)


def test_sharpe_and_drawdown_are_none_on_a_thin_series():
    """Never report a number off one point — it reads as a real result."""
    assert bc.sharpe([]) is None
    assert bc.sharpe([{'date': 'x', 'total_value': 100.0}]) is None
    assert bc.max_drawdown([{'date': 'x', 'total_value': 100.0}]) is None


def test_drawdown_is_the_worst_peak_to_trough():
    curve = [{'date': 'a', 'total_value': 100.0},
             {'date': 'b', 'total_value': 120.0},
             {'date': 'c', 'total_value': 90.0},
             {'date': 'd', 'total_value': 130.0}]
    assert bc.max_drawdown(curve) == pytest.approx(-25.0)


def test_flat_series_has_zero_drawdown_and_no_sharpe():
    curve = [{'date': f'd{i}', 'total_value': 100.0} for i in range(5)]
    assert bc.max_drawdown(curve) == 0.0
    assert bc.sharpe(curve) is None      # zero variance, not an infinite Sharpe


def test_book_metrics_counts_only_trades_since_the_fork():
    d = _book()
    d['fork'] = {'date': '2026-08-05'}
    d['closed'] = [
        {'symbol': 'OLD', 'pnl': 500.0, 'date_closed': '2026-08-01',
         'date_added': '2026-07-20'},                       # pre-fork, shared
        {'symbol': 'NEW', 'pnl': -100.0, 'date_closed': '2026-08-07',
         'date_added': '2026-08-06'},                       # post-fork
    ]
    m = bc.book_metrics(d)
    assert m['closed_since'] == 1
    assert m['realized_since'] == -100.0, \
        "pre-fork trades are shared by construction and must not count"
