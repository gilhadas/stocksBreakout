#!/usr/bin/env python3
"""The 15-minute monitor must never write stops into either book.

WHY THIS EXISTS
---------------
`run_monitor_mode` used to trail stops itself: a mode-aware LAST-PRICE trail
(swing 3%, longterm 5%, daytrade/scalping 1%) persisted straight into the book
on every run. `docker/crontab` fires it at 09:45, every 15 min from 10:00-15:45,
and at 16:00 — roughly 25 writes per trading day — so it had quietly become the
de-facto stop policy for both books, and nothing reported it. Stops just moved.

Neither book wants that:

  * auto_portfolio.json — the validated champion exit is a CLOSE-based ATR x2.0
    ratchet written only by refresh_prices() at 10:00 and 15:45 ET. An intraday
    last-price trail can only tighten, so it stopped positions out ahead of the
    trail that was actually backtested (CLAUDE.md §7 values that trail at
    ~+97 pts compound / +0.45 Sharpe).

  * portfolio.json — a MANUAL book whose stops are hand-set by
    /manual-portfolio/compute-stops with a deliberately wider ATR x3.0 /
    20-day-swing-low rule. Portfolio.update_prices' docstring forbids
    auto-trailing it (issue #7, decided 2026-08-11) — but that guard named
    refresh_prices as the thing not to wire in, and the monitor was already
    doing it by another route.

These tests pin the invariant from both directions: behaviourally (a run that
would previously have moved a stop leaves it alone) and structurally (no stop
write can be reintroduced into the function without failing a test).

Run:
    python -m pytest tests/test_monitor_never_writes_stops.py -v
"""
import asyncio
import inspect
import re
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── helpers ───────────────────────────────────────────────────────────────────

def _orchestrator(price_map):
    """Minimal stand-in exposing the one call run_monitor_mode makes."""
    async def get_current_price(symbol):
        return price_map.get(symbol)

    return types.SimpleNamespace(
        market_data=types.SimpleNamespace(get_current_price=get_current_price)
    )


def _args():
    return types.SimpleNamespace(monitor=None)


def _run(orch, from_portfolio=False, from_auto_portfolio=False):
    import breakout_scanner as bs
    return asyncio.run(bs.run_monitor_mode(
        orch, _args(), notifier=types.SimpleNamespace(),
        from_portfolio=from_portfolio,
        from_auto_portfolio=from_auto_portfolio,
    ))


# ── the auto book ─────────────────────────────────────────────────────────────

def test_auto_book_stop_is_not_written(monkeypatch, tmp_path):
    """A price far above entry would previously have ratcheted the stop up."""
    monkeypatch.chdir(tmp_path)
    import auto_portfolio as ap

    book = {'positions': [{
        'symbol': 'AAA', 'mode': 'swing',
        'entry_price': 100.0, 'stop': 90.0, 'target': 130.0,
    }]}

    saves = []
    monkeypatch.setattr(ap, 'load', lambda *a, **k: book)
    monkeypatch.setattr(ap, '_save', lambda *a, **k: saves.append(a))

    # 150 vs a 3% trail => the old code would have proposed 145.50, then clamped
    # it to entry*1.005 = 100.50 — still a move from 90.00.
    _run(_orchestrator({'AAA': 150.0}), from_auto_portfolio=True)

    assert book['positions'][0]['stop'] == 90.0, (
        'the monitor moved a stop in auto_portfolio.json; only '
        'refresh_prices() may write stops there')
    assert saves == [], (
        'the monitor persisted the auto book; it is alert-only and must not '
        'call auto_portfolio._save()')


def test_auto_book_untouched_even_when_price_below_stop(monkeypatch, tmp_path):
    """A breach is an ALERT here, never a write (and never a close)."""
    monkeypatch.chdir(tmp_path)
    import auto_portfolio as ap

    book = {'positions': [{
        'symbol': 'BBB', 'mode': 'swing',
        'entry_price': 100.0, 'stop': 90.0, 'target': 130.0,
    }]}
    saves = []
    monkeypatch.setattr(ap, 'load', lambda *a, **k: book)
    monkeypatch.setattr(ap, '_save', lambda *a, **k: saves.append(a))

    _run(_orchestrator({'BBB': 85.0}), from_auto_portfolio=True)

    assert book['positions'][0]['stop'] == 90.0
    assert saves == []
    assert len(book['positions']) == 1, 'the monitor must not close positions'


# ── the manual book ───────────────────────────────────────────────────────────

class _FakePortfolio:
    """Records any stop write; mirrors the two methods the monitor used."""
    instances = []

    def __init__(self):
        self.stop_writes = []
        self.tp_marks = []
        _FakePortfolio.instances.append(self)

    def get_positions_as_exit_format(self):
        return [{
            'symbol': 'CCC', 'mode': 'swing', 'entry': 100.0,
            'stop': 90.0, 'target': 130.0, 'timeframe': '1 day',
        }]

    def update_stop(self, symbol, new_stop):
        self.stop_writes.append((symbol, new_stop))

    def mark_tp_reached(self, symbol):
        self.tp_marks.append(symbol)


def test_manual_book_stop_is_not_written(monkeypatch, tmp_path):
    """portfolio.json is hand-set (ATR x3.0) and alert-only by design."""
    monkeypatch.chdir(tmp_path)
    import portfolio as pf

    _FakePortfolio.instances = []
    monkeypatch.setattr(pf, 'Portfolio', _FakePortfolio)

    _run(_orchestrator({'CCC': 150.0}), from_portfolio=True)

    writes = [w for inst in _FakePortfolio.instances for w in inst.stop_writes]
    assert writes == [], (
        f'the monitor wrote stops into the manual book ({writes}); '
        "Portfolio.update_prices' docstring forbids auto-trailing it")


def test_manual_book_not_written_when_target_exceeded(monkeypatch, tmp_path):
    """The old TP branch fired its own ATR x2.0 write — that path is gone too."""
    monkeypatch.chdir(tmp_path)
    import portfolio as pf

    _FakePortfolio.instances = []
    monkeypatch.setattr(pf, 'Portfolio', _FakePortfolio)

    # 200 is above the 130 target, which is what used to trigger the TP trail.
    _run(_orchestrator({'CCC': 200.0}), from_portfolio=True)

    writes = [w for inst in _FakePortfolio.instances for w in inst.stop_writes]
    assert writes == [], f'TP branch still writes a stop: {writes}'


# ── alerting still works (the fix must not silence the monitor) ───────────────

def test_breach_against_stored_stop_is_still_reported(monkeypatch, tmp_path):
    """Removing the writes must not remove the alert — that is the whole job."""
    monkeypatch.chdir(tmp_path)
    import auto_portfolio as ap

    book = {'positions': [{
        'symbol': 'DDD', 'mode': 'swing',
        'entry_price': 100.0, 'stop': 90.0, 'target': 130.0,
    }]}
    monkeypatch.setattr(ap, 'load', lambda *a, **k: book)
    monkeypatch.setattr(ap, '_save', lambda *a, **k: None)

    _run(_orchestrator({'DDD': 85.0}), from_auto_portfolio=True)

    # The monitor records what it alerted on for same-day dedup.
    hist = list((tmp_path / 'scanner_output' / 'state').glob('.monitor_alerts_*.txt'))
    assert hist, 'no alert-history file written — the monitor reported nothing'
    assert 'DDD' in hist[0].read_text(), (
        'a position trading below its stored stop was not reported; the fix '
        'must remove the WRITES, not the alerts')


# ── structural guard ──────────────────────────────────────────────────────────

def test_no_stop_writes_in_monitor_source():
    """Stops the write being reintroduced by a future edit.

    Comment lines are stripped first: the explanatory note in this function
    talks about stops at length, and a raw substring search would match prose
    rather than code (the trap CLAUDE.md §23.1 records — a substring assertion
    cannot tell a name being USED from a name merely mentioned).
    """
    import breakout_scanner as bs

    src = inspect.getsource(bs.run_monitor_mode)
    code = '\n'.join(line for line in src.splitlines()
                     if not line.lstrip().startswith('#'))

    # The persistence calls are the real teeth. The assignment patterns are
    # belt-and-braces for an in-memory mutation that a later edit might persist;
    # they are scoped to position-shaped targets so that formatting a display
    # column (df_display['stop'] = ...) does not read as a stop write.
    forbidden = {
        r"\.update_stop\(": 'Portfolio.update_stop() — writes the manual book',
        r"_ap\._save\(": 'auto_portfolio._save() — persists the auto book',
        r"\bpos\[['\"]stop['\"]\]\s*=\s*": "assignment to pos['stop']",
        r"_pos_map\[[^\]]+\]\[['\"]stop['\"]\]\s*=\s*": "assignment to _pos_map[...]['stop']",
    }
    for pattern, why in forbidden.items():
        assert not re.search(pattern, code), (
            f'run_monitor_mode contains {why}. This monitor is alert-only: '
            'refresh_prices() owns the auto books\' ATR x2.0 trail and a human '
            'owns the manual book\'s stops.')


def test_refresh_prices_is_still_the_auto_book_stop_writer():
    """The other half of the invariant: the legitimate writer still exists.

    Without this, deleting the trail everywhere would also pass the test above.
    """
    import auto_portfolio as ap

    src = inspect.getsource(ap.refresh_prices)
    assert '_raise_atr_trail' in src or 'trail' in src, (
        'refresh_prices no longer appears to trail stops — the champion exit '
        'must keep exactly one writer, not zero')
