#!/usr/bin/env python3
"""
Regression test: Portfolio.get_positions_as_exit_format() must carry entry_date,
because the max-hold exit rule is derived from it and silently defaults to 0.

WHY THIS EXISTS
---------------
Found 2026-07-30 from a daily Telegram exit alert. 14 positions in
portfolio.json opened 2026-05-07 — 84 calendar days earlier — were reported
with `DaysHeld 0` in every run, so "Max hold period reached" could never fire
for that book no matter how long a position was held. In the *same* log,
positions sourced from auto_portfolio.json correctly showed 92/113/99 days.

The asymmetry was the tell. Both books feed one exit evaluation, but their
position dicts are built in different places:

  * auto_portfolio -> breakout_scanner.py, which sets 'entry_date' explicitly
  * portfolio.json -> Portfolio.get_positions_as_exit_format(), which did not

`orchestrator.evaluate_exits` does `pos.get('entry_date', '')` and leaves
days_held at 0 when the key is missing or unparseable, so the omission was
invisible: no error, no warning, just a rule that never fired for half the
input.

The test therefore asserts the *consequence* (a real day count reaches the
rule) rather than merely that a key is present — a presence check would pass
on an empty string, which is exactly the value that caused the bug.

Run:
    python -m pytest tests/test_portfolio_exit_entry_date.py -v
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import portfolio as portfolio_module


def _fresh_portfolio(tmp_path, monkeypatch):
    """Isolated Portfolio instance backed by a tmp dir instead of scanner_output/."""
    portfolio_dir = tmp_path / 'portfolio'
    monkeypatch.setattr(portfolio_module, 'PORTFOLIO_DIR', str(portfolio_dir))
    monkeypatch.setattr(portfolio_module, 'SNAPSHOTS_DIR', str(portfolio_dir / 'snapshots'))
    monkeypatch.setattr(portfolio_module, 'PORTFOLIO_FILE', str(portfolio_dir / 'portfolio.json'))
    return portfolio_module.Portfolio(capital=100000)


def _days_held_as_orchestrator_computes_it(pos: dict) -> int:
    """Mirror of orchestrator.evaluate_exits' derivation, kept in lockstep.

    If that logic changes shape (different key, different format), this helper
    must change with it — which is the point: the coupling is what broke.
    """
    entry_date_str = pos.get('entry_date', '')
    if not entry_date_str:
        return 0
    try:
        entry_dt = datetime.strptime(entry_date_str, '%Y-%m-%d')
    except ValueError:
        return 0
    now = datetime.now(ZoneInfo('America/New_York')).replace(tzinfo=None)
    return (now - entry_dt).days


class TestExitFormatCarriesEntryDate:
    def test_a_long_held_position_reports_its_real_age(self, tmp_path, monkeypatch):
        """The production shape: opened months ago, must not report 0 days."""
        p = _fresh_portfolio(tmp_path, monkeypatch)
        p.add_position({'Symbol': 'AMZN', 'Price': 262.83, 'Stop': 255.65,
                        'Target': 300.0, 'Mode': 'swing'}, shares=30)

        opened = (datetime.now(ZoneInfo('America/New_York')).replace(tzinfo=None)
                  - timedelta(days=84)).strftime('%Y-%m-%d')
        p._data['positions']['AMZN']['entry_date'] = opened
        p._save()

        row = next(r for r in p.get_positions_as_exit_format() if r['symbol'] == 'AMZN')

        assert row.get('entry_date') == opened
        assert _days_held_as_orchestrator_computes_it(row) == 84, (
            'days_held collapsed to 0 — the max-hold exit rule cannot fire')

    def test_the_date_survives_the_csv_round_trip(self, tmp_path, monkeypatch):
        """breakout_scanner writes these rows to a temp CSV before evaluating.

        `csv.DictWriter(..., extrasaction='ignore')` silently DROPS any key not
        in its fieldnames list, so a field can be produced correctly here and
        still never reach the evaluator.
        """
        import csv
        import io

        p = _fresh_portfolio(tmp_path, monkeypatch)
        p.add_position({'Symbol': 'ASTS', 'Price': 66.92, 'Stop': 65.44,
                        'Target': 80.0, 'Mode': 'swing'}, shares=75)
        opened = (datetime.now(ZoneInfo('America/New_York')).replace(tzinfo=None)
                  - timedelta(days=40)).strftime('%Y-%m-%d')
        p._data['positions']['ASTS']['entry_date'] = opened
        p._save()

        rows = p.get_positions_as_exit_format()
        fieldnames = ['symbol', 'mode', 'entry', 'entry_date', 'stop',
                      'target', 'timeframe', 'quality']
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
        buf.seek(0)

        back = next(r for r in csv.DictReader(buf) if r['symbol'] == 'ASTS')
        assert back['entry_date'] == opened, 'entry_date lost in the CSV round trip'
        assert _days_held_as_orchestrator_computes_it(back) == 40

    def test_a_missing_date_still_degrades_to_zero_rather_than_raising(
            self, tmp_path, monkeypatch):
        """Older saved books predate the field; they must not crash the run."""
        p = _fresh_portfolio(tmp_path, monkeypatch)
        p.add_position({'Symbol': 'CCL', 'Price': 27.99, 'Stop': 27.18,
                        'Target': 32.0, 'Mode': 'swing'}, shares=250)
        p._data['positions']['CCL'].pop('entry_date', None)
        p._save()

        row = next(r for r in p.get_positions_as_exit_format() if r['symbol'] == 'CCL')
        assert row.get('entry_date') == ''
        assert _days_held_as_orchestrator_computes_it(row) == 0
