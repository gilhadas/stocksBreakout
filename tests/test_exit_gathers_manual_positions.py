#!/usr/bin/env python3
"""The exit-evaluator must include manually-bought positions, per user.

WHY THIS EXISTS
---------------
_gather_exit_positions_from_portfolios (extracted from main()'s
--exit-from-portfolio branch) used to call Portfolio() with no user_id,
reading the single unscoped scanner_output/portfolio/portfolio.json. But
/manual-portfolio/buy always writes to a genuinely per-user path (CLAUDE.md
§14) — so a manually-bought position was invisible to this function no
matter how far below its stop it traded. Fixed by looping over every
registered user's own Portfolio(user_id=...), mirroring the per-user loop
this function already used for auto_portfolio.

Run:
    python -m pytest tests/test_exit_gathers_manual_positions.py -v
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import breakout_scanner as bs
import portfolio as portfolio_module
import auto_portfolio as ap
import utils


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_module, 'PORTFOLIO_DIR', str(tmp_path / 'portfolio'))
    monkeypatch.setattr(portfolio_module, '_is_cloud', lambda: False)
    monkeypatch.setattr(utils, '_is_cloud', lambda: False)
    monkeypatch.setattr(utils, '_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setattr(utils, 'PROJECT_ROOT', tmp_path)
    return tmp_path


def _user(uid: str, email: str) -> SimpleNamespace:
    return SimpleNamespace(id=uid, email=email)


def _patch_users(monkeypatch, users):
    """_gather_exit_positions_from_portfolios imports api.database/api.models
    locally, so patch what next(get_db()).query(User).all() returns."""
    class _FakeQuery:
        def all(self):
            return users

    class _FakeDB:
        def query(self, model):
            return _FakeQuery()

    def _fake_get_db():
        yield _FakeDB()

    import api.database
    import api.models
    monkeypatch.setattr(api.database, 'get_db', _fake_get_db)


def test_a_manually_bought_position_is_included_and_attributed(sandbox, monkeypatch):
    _patch_users(monkeypatch, [_user('user-a', 'a@example.com')])

    p = portfolio_module.Portfolio(user_id='user-a')
    p.add_position({'Symbol': 'AAPL', 'Price': 100.0, 'Stop': 90.0, 'Target': 130.0}, shares=10)

    exit_positions, symbol_to_users = bs._gather_exit_positions_from_portfolios()

    symbols = [p['symbol'] for p in exit_positions]
    assert 'AAPL' in symbols, (
        'a manually-bought position must appear in the exit-evaluator list')
    assert any(u['email'] == 'a@example.com' for u in symbol_to_users.get('AAPL', [])), (
        "the position's user must be attributed so they receive the exit alert")


def test_two_users_manual_positions_do_not_collide(sandbox, monkeypatch):
    _patch_users(monkeypatch, [
        _user('user-a', 'a@example.com'),
        _user('user-b', 'b@example.com'),
    ])

    pa = portfolio_module.Portfolio(user_id='user-a')
    pa.add_position({'Symbol': 'AAPL', 'Price': 100.0, 'Stop': 90.0, 'Target': 130.0}, shares=10)
    pb = portfolio_module.Portfolio(user_id='user-b')
    pb.add_position({'Symbol': 'MSFT', 'Price': 400.0, 'Stop': 380.0, 'Target': 440.0}, shares=5)

    exit_positions, symbol_to_users = bs._gather_exit_positions_from_portfolios()

    symbols = {p['symbol'] for p in exit_positions}
    assert symbols == {'AAPL', 'MSFT'}
    assert [u['email'] for u in symbol_to_users['AAPL']] == ['a@example.com']
    assert [u['email'] for u in symbol_to_users['MSFT']] == ['b@example.com']


def test_manual_and_auto_positions_for_the_same_symbol_are_deduped(sandbox, monkeypatch):
    """Same symbol held manually by one user and automatically by another —
    exit_positions must not list it twice, but both users must still be
    notified (this is the exact contract the auto-only loop already had)."""
    _patch_users(monkeypatch, [
        _user('user-a', 'a@example.com'),
        _user('user-b', 'b@example.com'),
    ])

    pa = portfolio_module.Portfolio(user_id='user-a')
    pa.add_position({'Symbol': 'NVDA', 'Price': 100.0, 'Stop': 90.0, 'Target': 130.0}, shares=10)

    ap._save(ap._empty(), user_id='user-b', book='control')
    monkeypatch.setattr(ap, '_compute_atr_pct', lambda *a, **k: None)
    ap.add_position_direct('NVDA', entry_price=101.0, stop=91.0, target=131.0,
                           user_id='user-b', book='control')

    exit_positions, symbol_to_users = bs._gather_exit_positions_from_portfolios()

    assert [p['symbol'] for p in exit_positions].count('NVDA') == 1, (
        'the same symbol from two different users/books must appear once, not twice')
    emails = {u['email'] for u in symbol_to_users['NVDA']}
    assert emails == {'a@example.com', 'b@example.com'}, (
        'both the manual holder and the auto-book holder must be notified')


def test_db_failure_falls_back_to_the_unscoped_default_files_only(sandbox, monkeypatch):
    """If the users table can't be reached, this must degrade to exactly the
    old single-user behavior (Portfolio() + auto_portfolio.load(), both
    unscoped) — not raise, and not silently return nothing."""
    def _broken_get_db():
        raise RuntimeError('db unreachable')
        yield  # pragma: no cover — makes this a generator, never reached

    import api.database
    monkeypatch.setattr(api.database, 'get_db', _broken_get_db)

    default_p = portfolio_module.Portfolio()  # user_id=None: the unscoped file
    default_p.add_position({'Symbol': 'TSLA', 'Price': 250.0, 'Stop': 230.0, 'Target': 300.0}, shares=1)

    exit_positions, symbol_to_users = bs._gather_exit_positions_from_portfolios()

    assert [p['symbol'] for p in exit_positions] == ['TSLA']
    assert symbol_to_users == {}, 'the fallback path has no per-user routing information'


def test_a_partial_failure_does_not_leave_a_mix_of_both_paths(sandbox, monkeypatch):
    """If the multi-user loop fails PART WAY THROUGH (after loading some
    users' manual positions but before finishing), the exception handler
    must discard that partial state — the result must be the clean fallback,
    never a mix of one real user's data and the default file's data."""
    good_user = _user('user-a', 'a@example.com')
    pa = portfolio_module.Portfolio(user_id='user-a')
    pa.add_position({'Symbol': 'AAPL', 'Price': 100.0, 'Stop': 90.0, 'Target': 130.0}, shares=10)

    default_p = portfolio_module.Portfolio()
    default_p.add_position({'Symbol': 'TSLA', 'Price': 250.0, 'Stop': 230.0, 'Target': 300.0}, shares=1)

    class _ExplodingQuery:
        def all(self):
            # Returns one real user (so the manual-portfolio loop runs and
            # picks up AAPL) then the auto_portfolio loop blows up.
            return [good_user]

    class _ExplodingDB:
        def query(self, model):
            return _ExplodingQuery()

    def _fake_get_db():
        yield _ExplodingDB()

    import api.database
    monkeypatch.setattr(api.database, 'get_db', _fake_get_db)

    # Break only the per-user auto_portfolio call (loop 2) — the fallback
    # path's own auto_portfolio.load() (no args) must keep working, or this
    # test can't tell "discarded partial state" apart from "fallback broke too".
    real_ap_load = ap.load

    def _load_raises_for_named_users(user_id=None, book=ap.DEFAULT_BOOK):
        if user_id is not None:
            raise RuntimeError('boom')
        return real_ap_load(user_id=user_id, book=book)

    monkeypatch.setattr(ap, 'load', _load_raises_for_named_users)

    exit_positions, symbol_to_users = bs._gather_exit_positions_from_portfolios()

    symbols = [p['symbol'] for p in exit_positions]
    assert 'AAPL' not in symbols, (
        'partial state from before the failure must be discarded, not merged into the fallback')
    assert symbols == ['TSLA'], 'must be exactly the clean unscoped-default fallback'
