"""
The auto-swap policy IS the experiment's treatment. It must apply to exactly
one book, at exactly the configured rate, and never break the scan.

WHY THIS EXISTS
---------------
The whole point of running two books is that ONE of them acts. If the control
book ever auto-executes, there is no control and the experiment silently
measures nothing — with no error to notice, because both books would simply look
similar again.

The rate cap matters for a different reason: every swap pays slippage and
commission on BOTH legs. CLAUDE.md §11 measured auto swap-on-skip in backtest at
−4.68 Sharpe, so an uncapped swap loop is the known-worst configuration.

Failure isolation matters most of all. This code runs as a tail of
`scan_and_add`, AFTER the scan has already found and saved the day's signals. A
raise here would take down the thing that produced the signals in the first
place — trading a real, working feature for a speculative one.
"""
from __future__ import annotations

import json

import pytest

import auto_portfolio as ap
import utils


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, '_is_cloud', lambda: False)
    monkeypatch.setattr(utils, '_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setattr(utils, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(ap, '_append_swap_ledger', lambda rec: LEDGER.append(rec))
    LEDGER.clear()
    return tmp_path


LEDGER: list = []


def _swap(close='OLD', open_='NEW'):
    return {'close_symbol': close, 'open_symbol': open_, 'close_pnl_pct': -5.0,
            'open_quality': 'PREMIUM', 'open_rr': 2.0, 'open_win_prob': 0.0,
            'open_momentum': 0.0, 'score_improvement': 25.0, 'open_priority': 65.0}


def _book_with_state():
    d = ap._empty()
    d['positions'] = [{'symbol': 'OLD', 'entry_price': 10.0, 'stop': 9.0,
                       'target': 12.0, 'shares': 10, 'cost': 100.0,
                       'date_added': '2026-08-01', 'quality': 'GOLD'}]
    d['skipped_cash'] = [{'symbol': 'NEW', 'date_added': '2026-08-05',
                          'priority_score': 65.0, 'entry_price': 20.0,
                          'stop': 18.0, 'target': 26.0}]
    return d


# ── Treatment assignment ─────────────────────────────────────────────────────

def test_control_book_never_executes(sandbox, monkeypatch):
    calls = []
    monkeypatch.setattr(ap, 'suggest_swaps', lambda **k: [_swap()])
    monkeypatch.setattr(ap, 'execute_swap', lambda *a, **k: calls.append(a) or {'ok': True})
    monkeypatch.setattr(ap, '_save', lambda *a, **k: None)
    monkeypatch.setattr('notifier.Notifier', lambda *a, **k: _Silent())

    ap._run_swap_stage(_book_with_state(), user_id='U1', book='control')
    assert calls == [], "the control arm must only advise, never trade"


def test_autoswap_book_executes(sandbox, monkeypatch):
    calls = []

    def _exec(close, open_, **k):
        calls.append((close, open_, k.get('book'), k.get('price_basis')))
        return {'ok': True, 'closed': {'exit_price': 11.0}, 'opened': {'entry_price': 20.0}}

    monkeypatch.setattr(ap, 'suggest_swaps', lambda **k: [_swap()])
    monkeypatch.setattr(ap, 'execute_swap', _exec)
    monkeypatch.setattr(ap, 'load', lambda *a, **k: _book_with_state())
    monkeypatch.setattr(ap, '_save', lambda *a, **k: None)
    monkeypatch.setattr('notifier.Notifier', lambda *a, **k: _Silent())

    ap._run_swap_stage(_book_with_state(), user_id='U1', book='autoswap')
    assert calls == [('OLD', 'NEW', 'autoswap', 'close')]


# ── Rate cap ─────────────────────────────────────────────────────────────────

def test_executions_are_capped_at_the_configured_daily_rate(sandbox, monkeypatch):
    cap = ap.BOOKS['autoswap']['max_swaps_per_day']
    calls = []
    monkeypatch.setattr(ap, 'suggest_swaps',
                        lambda **k: [_swap(f'O{i}', f'N{i}') for i in range(cap + 4)])
    monkeypatch.setattr(ap, 'execute_swap',
                        lambda c, o, **k: calls.append(c) or
                        {'ok': True, 'closed': {'exit_price': 1.0}, 'opened': {'entry_price': 2.0}})
    monkeypatch.setattr(ap, 'load', lambda *a, **k: _book_with_state())
    monkeypatch.setattr(ap, '_save', lambda *a, **k: None)
    monkeypatch.setattr('notifier.Notifier', lambda *a, **k: _Silent())

    ap._run_swap_stage(_book_with_state(), user_id='U1', book='autoswap')
    assert len(calls) <= cap, f"executed {len(calls)} swaps against a cap of {cap}"


def test_a_declined_swap_does_not_consume_the_budget_silently(sandbox, monkeypatch):
    """A refusal must be reported as still-pending, not swallowed."""
    monkeypatch.setattr(ap, 'suggest_swaps', lambda **k: [_swap()])
    monkeypatch.setattr(ap, 'execute_swap',
                        lambda c, o, **k: {'ok': False, 'reason': 'live price unavailable'})
    monkeypatch.setattr(ap, 'load', lambda *a, **k: _book_with_state())
    saved = {}
    monkeypatch.setattr(ap, '_save', lambda d, **k: saved.update(d))
    monkeypatch.setattr('notifier.Notifier', lambda *a, **k: _Silent())

    ap._run_swap_stage(_book_with_state(), user_id='U1', book='autoswap')
    assert saved['swap_advice']['executed'] == 0
    assert [s['close_symbol'] for s in saved['swap_advice']['swaps']] == ['OLD']


# ── Failure isolation ────────────────────────────────────────────────────────

def test_a_raising_swap_does_not_abort_the_batch(sandbox, monkeypatch):
    seen = []

    def _exec(c, o, **k):
        seen.append(c)
        if c == 'O0':
            raise RuntimeError('yfinance exploded')
        return {'ok': True, 'closed': {'exit_price': 1.0}, 'opened': {'entry_price': 2.0}}

    monkeypatch.setattr(ap, 'suggest_swaps',
                        lambda **k: [_swap('O0', 'N0'), _swap('O1', 'N1')])
    monkeypatch.setattr(ap, 'execute_swap', _exec)
    monkeypatch.setattr(ap, 'load', lambda *a, **k: _book_with_state())
    monkeypatch.setattr(ap, '_save', lambda *a, **k: None)
    monkeypatch.setattr('notifier.Notifier', lambda *a, **k: _Silent())

    ap._run_swap_stage(_book_with_state(), user_id='U1', book='autoswap')
    assert seen == ['O0', 'O1'], "one bad symbol must not stop the rest of the batch"


def _scan_that_skips_for_cash(monkeypatch):
    """Drive scan_and_add down a path that really populates `skipped_cash`.

    The swap stage is guarded by `if notify and (skipped_cap or skipped_cash)`,
    so a scan with no signal files never reaches it — an isolation test built on
    an empty scan passes no matter what the error handling does. (It did: this
    test was vacuous until a mutation that removed the try/except failed to fail
    it.) A book with almost no capital makes the first admitted signal
    unaffordable, which is the cheapest honest way in.
    """
    import pandas as pd
    from datetime import datetime as _dt

    day = _dt.now(ap._NY_TZ).strftime('%Y%m%d')
    monkeypatch.setattr(utils, 'list_files',
                        lambda *a, **k: [f'signals_swing_{day}_090000.csv'])
    monkeypatch.setattr(utils, 'load_data', lambda p: pd.DataFrame([{
        'Symbol': 'NEW', 'Price': 20.0, 'Stop': 18.0, 'Target': 26.0,
        'Quality': 'PREMIUM', 'MinerviniScore': 8, 'Type': 'BOUNCE',
        'Vol': 2.0, 'R:R': 3.0, 'WinProb': 0.0, 'Mode': 'swing',
    }]))
    monkeypatch.setattr(ap, '_fetch_entry_and_current', lambda s, d, p: (20.0, 20.0))
    monkeypatch.setattr(ap, '_detect_split_factor', lambda s, d: 1.0)
    monkeypatch.setattr(ap, '_compute_atr_adjustment', lambda s: 1.0)
    monkeypatch.setattr(ap, '_check_portfolio_balance',
                        lambda *a, **k: {'blocked': False, 'sizing_mult': 1.0})

    book = ap._empty()
    book['capital'] = 1.0            # cannot afford even one share
    monkeypatch.setattr(ap, 'load', lambda *a, **k: book)
    monkeypatch.setattr(ap, '_save', lambda *a, **k: None)
    return book


def test_the_scan_path_under_test_really_reaches_the_swap_stage(sandbox, monkeypatch):
    """Fixture guard — if this stops firing, the isolation test below is vacuous."""
    _scan_that_skips_for_cash(monkeypatch)
    reached = []
    monkeypatch.setattr(ap, '_run_swap_stage', lambda *a, **k: reached.append(1) or [])

    result = ap.scan_and_add(user_id='U1', book='autoswap', notify=True)
    assert result['skipped_cash'] >= 1, "fixture no longer skips for cash"
    assert reached == [1], "the swap stage was never invoked"


def test_a_scan_that_skipped_nothing_does_not_advise(sandbox, monkeypatch):
    """The trigger is "the cap or cash just turned signals away", not "a scan ran".

    A book keeps `skipped_cash` entries for days, so without this guard every
    scan — including the 4 daily ones that admit everything they see — would
    re-enter the swap stage and re-advise on stale candidates. That is precisely
    the shape of the §13 bug: a never-refreshed list re-alerting on a schedule.

    The scan must really RUN for this to mean anything: `scan_and_add` returns
    early at auto_portfolio.py:459 when there are no files at all, which would
    make the stage unreachable for a reason that has nothing to do with the
    guard. So this feeds a real file whose only signal is already an open
    position — it is deduped, nothing is skipped, and the loop still completes.
    """
    import pandas as pd
    from datetime import datetime as _dt

    day = _dt.now(ap._NY_TZ).strftime('%Y%m%d')
    monkeypatch.setattr(utils, 'list_files',
                        lambda *a, **k: [f'signals_swing_{day}_090000.csv'])
    monkeypatch.setattr(utils, 'load_data', lambda p: pd.DataFrame([{
        'Symbol': 'OLD',              # already held → deduped, not skipped
        'Price': 10.0, 'Stop': 9.0, 'Target': 12.0,
        'Quality': 'PREMIUM', 'MinerviniScore': 8, 'Type': 'BOUNCE',
        'Vol': 2.0, 'R:R': 3.0, 'WinProb': 0.0, 'Mode': 'swing',
    }]))
    monkeypatch.setattr(ap, 'load', lambda *a, **k: _book_with_state())
    monkeypatch.setattr(ap, '_save', lambda *a, **k: None)
    reached = []
    monkeypatch.setattr(ap, '_run_swap_stage', lambda *a, **k: reached.append(1) or [])

    result = ap.scan_and_add(user_id='U1', book='autoswap', notify=True)
    assert result['files_scanned'] == 1, "the scan must actually have run a file"
    assert result['skipped_cash'] == 0
    assert reached == [], "nothing was skipped, so there is nothing to advise about"


def test_notify_false_never_advises(sandbox, monkeypatch):
    """recalculate() calls scan_and_add(notify=False) — a rebuild must be silent."""
    _scan_that_skips_for_cash(monkeypatch)
    reached = []
    monkeypatch.setattr(ap, '_run_swap_stage', lambda *a, **k: reached.append(1) or [])

    ap.scan_and_add(user_id='U1', book='autoswap', notify=False)
    assert reached == []


def test_scan_and_add_survives_a_swap_stage_explosion(sandbox, monkeypatch):
    """The scan already saved the day's signals — a swap bug must not undo that."""
    _scan_that_skips_for_cash(monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError('swap stage is broken')
    monkeypatch.setattr(ap, '_run_swap_stage', _boom)

    result = ap.scan_and_add(user_id='U1', book='autoswap', notify=True)
    assert isinstance(result, dict) and 'added' in result
    assert result['skipped_cash'] >= 1, "the scan's own work must still be reported"


# ── Ledger ───────────────────────────────────────────────────────────────────

def test_every_execution_is_recorded_with_both_legs(sandbox, monkeypatch):
    monkeypatch.setattr(ap, 'suggest_swaps', lambda **k: [_swap()])
    monkeypatch.setattr(ap, 'execute_swap', lambda c, o, **k: {
        'ok': True, 'closed': {'exit_price': 11.5, 'pnl': 15.0, 'pnl_pct': 1.5},
        'opened': {'entry_price': 20.5, 'stop': 18.0, 'target': 26.0}})
    monkeypatch.setattr(ap, 'load', lambda *a, **k: _book_with_state())
    monkeypatch.setattr(ap, '_save', lambda *a, **k: None)
    monkeypatch.setattr('notifier.Notifier', lambda *a, **k: _Silent())

    ap._run_swap_stage(_book_with_state(), user_id='U1', book='autoswap')

    assert len(LEDGER) == 1
    rec = LEDGER[0]
    # Both legs at both prices — without all four the counterfactual in
    # book_compare.swap_attribution cannot be computed at all.
    for key in ('close_symbol', 'close_price', 'open_symbol', 'open_price', 'book'):
        assert rec.get(key) is not None, f"ledger row is missing {key}"
    assert rec['book'] == 'autoswap'


def test_ledger_write_failure_never_costs_a_trade(tmp_path, monkeypatch):
    """The trade already happened; losing an audit line must not raise."""
    monkeypatch.setattr(utils, '_to_local_abs',
                        lambda p: (_ for _ in ()).throw(OSError('disk full')))
    ap._append_swap_ledger({'anything': 1})   # must not raise


# ── Dedup ────────────────────────────────────────────────────────────────────

def test_identical_advice_is_not_resent_the_same_day(sandbox, monkeypatch):
    """scan_and_add_all_users fires up to 4x/weekday — the stamp is what stops
    the same advice re-notifying all day."""
    sent = []
    monkeypatch.setattr(ap, 'suggest_swaps', lambda **k: [_swap()])
    monkeypatch.setattr('notifier.Notifier', lambda *a, **k: _Recorder(sent))
    monkeypatch.setattr(ap, 'load', lambda *a, **k: _book_with_state())
    monkeypatch.setattr(ap, '_save', lambda *a, **k: None)

    data = _book_with_state()
    ap._run_swap_stage(data, user_id='U1', book='control')
    assert len(sent) == 1

    # Second run of the day, same advice → stamped, so silent.
    import datetime as _dt
    from auto_portfolio import _NY_TZ
    today = _dt.datetime.now(_NY_TZ).strftime('%Y-%m-%d')
    data['swap_advice'] = {'date': today, 'key': 'OLD>NEW', 'sends': 1}
    ap._run_swap_stage(data, user_id='U1', book='control')
    assert len(sent) == 1, "identical advice must not re-send within the same day"


def test_yesterdays_stamp_does_not_suppress_today(sandbox, monkeypatch):
    sent = []
    monkeypatch.setattr(ap, 'suggest_swaps', lambda **k: [_swap()])
    monkeypatch.setattr('notifier.Notifier', lambda *a, **k: _Recorder(sent))
    monkeypatch.setattr(ap, 'load', lambda *a, **k: _book_with_state())
    monkeypatch.setattr(ap, '_save', lambda *a, **k: None)

    data = _book_with_state()
    data['swap_advice'] = {'date': '2020-01-01', 'key': 'OLD>NEW', 'sends': 9}
    ap._run_swap_stage(data, user_id='U1', book='control')
    assert len(sent) == 1


def test_no_positions_or_no_skipped_is_a_silent_noop(sandbox, monkeypatch):
    called = []
    monkeypatch.setattr(ap, 'suggest_swaps', lambda **k: called.append(1) or [])

    empty = ap._empty()
    assert ap._run_swap_stage(empty, user_id='U1', book='autoswap') == []

    only_pos = _book_with_state(); only_pos['skipped_cash'] = []
    assert ap._run_swap_stage(only_pos, user_id='U1', book='autoswap') == []
    assert called == [], "must not even ask for suggestions with nothing to swap"


class _Silent:
    def send_all(self, **kw):
        return True


class _Recorder:
    def __init__(self, sink):
        self.sink = sink

    def send_all(self, **kw):
        self.sink.append(kw)
        return True
