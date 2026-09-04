"""
The fork must produce two identical books, once.

WHY THIS EXISTS
---------------
An A/B whose arms do not start identical measures the starting difference, not
the treatment. `fork_books.py` clones the control book into the variant so the
only thing that can ever separate them is the swap policy.

The overwrite guard is the more important half. Re-running the fork after the
experiment is underway would silently reset the variant to a copy of control,
destroying however many weeks of divergence had accumulated — and it would look
like a successful run. There is no way to detect that afterwards: a freshly
re-forked book is, by construction, indistinguishable from one that simply never
diverged.

`last_swap` and `swap_advice` are explicitly NOT carried over: they describe the
control book's history. A cloned `swap_advice` stamp would make the variant's
very first scan believe it had already advised (or already executed) today.
"""
from __future__ import annotations

import json

import pytest

import auto_portfolio as ap
import fork_books
import utils


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, '_is_cloud', lambda: False)
    monkeypatch.setattr(utils, '_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setattr(utils, 'PROJECT_ROOT', tmp_path)
    return tmp_path


def _seed_control(user_id='U1'):
    d = ap._empty()
    d['capital'] = 98765.43
    d['positions'] = [{'symbol': 'AAA', 'entry_price': 10.0, 'stop': 9.0,
                       'target': 12.0, 'shares': 5, 'cost': 50.0,
                       'date_added': '2026-08-01', 'quality': 'GOLD'}]
    d['closed'] = [{'symbol': 'ZZZ', 'pnl': 12.0, 'date_closed': '2026-07-30'}]
    d['skipped_cash'] = [{'symbol': 'NEW', 'priority_score': 65.0}]
    d['processed_files'] = ['signals_swing_20260801_090000.csv']
    d['last_swap'] = {'close_symbol': 'Q', 'open_symbol': 'R'}
    d['swap_advice'] = {'date': '2026-08-01', 'key': 'Q>R', 'sends': 2}
    ap._save(d, user_id=user_id, book='control')
    return d


def test_fork_clones_the_trading_state_exactly(sandbox):
    src = _seed_control()
    fork_books.fork_user('U1', 'u@x', force=False, dry_run=False)

    var = ap.load(user_id='U1', book='autoswap')
    for key in ('capital', 'positions', 'closed', 'skipped_cash', 'processed_files'):
        assert var[key] == src[key], f"{key} diverged at the fork"


def test_fork_stamps_both_books_with_the_same_date(sandbox):
    _seed_control()
    fork_books.fork_user('U1', 'u@x', force=False, dry_run=False)

    ctrl = ap.load(user_id='U1', book='control')
    var = ap.load(user_id='U1', book='autoswap')
    assert ctrl['fork']['date'] == var['fork']['date']
    assert var['fork']['source'] == 'control'


def test_fork_does_not_carry_over_advice_or_undo_state(sandbox):
    """These describe control's history, not the variant's."""
    _seed_control()
    fork_books.fork_user('U1', 'u@x', force=False, dry_run=False)

    var = ap.load(user_id='U1', book='autoswap')
    assert var.get('last_swap') is None
    assert var.get('swap_advice') == {}


def test_refork_is_refused_without_force(sandbox):
    _seed_control()
    fork_books.fork_user('U1', 'u@x', force=False, dry_run=False)

    # The variant now diverges — a position the control book never had.
    var = ap.load(user_id='U1', book='autoswap')
    var['positions'].append({'symbol': 'DIVERGED', 'entry_price': 1.0, 'shares': 1,
                             'cost': 1.0, 'stop': 0.9, 'target': 2.0})
    ap._save(var, user_id='U1', book='autoswap')

    out = fork_books.fork_user('U1', 'u@x', force=False, dry_run=False)
    # Registry-driven, not a hardcoded list: adding a book to BOOKS must not
    # make this test fail for a reason that has nothing to do with re-forking.
    assert 'autoswap' in out['skipped']
    assert 'autoswap' not in out['forked']

    after = ap.load(user_id='U1', book='autoswap')
    assert 'DIVERGED' in [p['symbol'] for p in after['positions']], \
        "a second fork silently reset a running experiment"


def test_force_does_overwrite(sandbox):
    _seed_control()
    fork_books.fork_user('U1', 'u@x', force=False, dry_run=False)
    var = ap.load(user_id='U1', book='autoswap')
    var['positions'] = []
    ap._save(var, user_id='U1', book='autoswap')

    out = fork_books.fork_user('U1', 'u@x', force=True, dry_run=False)
    assert 'autoswap' in out['forked']
    assert len(ap.load(user_id='U1', book='autoswap')['positions']) == 1


def test_dry_run_writes_nothing(sandbox):
    _seed_control()
    out = fork_books.fork_user('U1', 'u@x', force=False, dry_run=True)
    assert 'autoswap' in out['forked']
    assert ap.load(user_id='U1', book='autoswap')['positions'] == [], \
        "--dry-run must not create the variant"
    assert ap.load(user_id='U1', book='control').get('fork') is None, \
        "--dry-run must not stamp the control book either"


def test_fork_of_an_empty_book_is_harmless(sandbox):
    out = fork_books.fork_user('U1', 'u@x', force=False, dry_run=False)
    assert 'autoswap' in out['forked']
    assert ap.load(user_id='U1', book='autoswap')['positions'] == []


def test_variant_books_excludes_the_control_book():
    assert ap.DEFAULT_BOOK not in fork_books.VARIANT_BOOKS
    assert set(fork_books.VARIANT_BOOKS) == set(ap.BOOKS) - {ap.DEFAULT_BOOK}
