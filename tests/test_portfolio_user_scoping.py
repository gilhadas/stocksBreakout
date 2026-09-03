#!/usr/bin/env python3
"""Portfolio() must be scoped by user_id, matching where the API actually writes.

WHY THIS EXISTS
---------------
Portfolio() had no user_id parameter at all — every caller, regardless of who
they were, read and wrote the ONE unscoped scanner_output/portfolio/portfolio.json.
Meanwhile api/server.py's /manual-portfolio/buy|sell always writes to
scanner_output/portfolio/<user_id>/portfolio.json, because current_user.id is
never empty for an authenticated request. These are two different files with
no code path that ever reconciled them: a manually-bought position could
never be seen by the cron exit-evaluator, the monitor, the Streamlit
dashboard's manual-portfolio tab, or the surge monitor — no stop-loss check,
no exit alert, ever, with nothing anywhere reporting the gap. Found 2026-09
while investigating an unrelated timezone bug in the same endpoints
(CLAUDE.md §14). Verified zero current blast radius: no user has a per-user
portfolio.json yet, but the first manual buy would have silently misfired.

Run:
    python -m pytest tests/test_portfolio_user_scoping.py -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import portfolio as portfolio_module
import utils
from portfolio import Portfolio


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_module, 'PORTFOLIO_DIR', str(tmp_path / 'portfolio'))
    monkeypatch.setattr(portfolio_module, '_is_cloud', lambda: False)
    # utils.save_json/load_json/list_files check utils._is_cloud() directly (a
    # separate binding from portfolio_module's imported one) — tests/.env.test
    # sets fake AWS creds, which makes it True and sends every read/write at a
    # real (failing) S3 endpoint. Each attempt fails slowly rather than fast,
    # so this alone was the difference between sub-second and >20s tests here.
    monkeypatch.setattr(utils, '_is_cloud', lambda: False)
    return tmp_path


def test_default_user_id_keeps_the_original_unscoped_path(sandbox):
    """user_id=None must resolve to EXACTLY the pre-fix path — nothing that
    already calls Portfolio() with no argument changes behavior."""
    p = Portfolio()
    assert p._portfolio_file == str(sandbox / 'portfolio' / 'portfolio.json')
    assert p._snapshots_dir == str(sandbox / 'portfolio' / 'snapshots')


def test_two_users_get_two_different_files(sandbox):
    a = Portfolio(user_id='user-a')
    b = Portfolio(user_id='user-b')
    assert a._portfolio_file != b._portfolio_file
    assert 'user-a' in a._portfolio_file
    assert 'user-b' in b._portfolio_file


def test_a_users_position_is_invisible_to_another_user(sandbox):
    a = Portfolio(user_id='user-a')
    a.add_position({'Symbol': 'AAPL', 'Price': 100.0, 'Stop': 90.0, 'Target': 130.0}, shares=10)

    b = Portfolio(user_id='user-b')
    assert b.get_positions() == []

    # Re-loading user-a's own book must still see it.
    a_again = Portfolio(user_id='user-a')
    assert len(a_again.get_positions()) == 1
    assert a_again.get_positions()[0]['symbol'] == 'AAPL'


def test_this_is_the_path_manual_buy_actually_writes_to(sandbox):
    """Structural guard against the exact regression this fixes: confirms
    Portfolio(user_id=X)'s resolved path matches api/server.py's
    _portfolio_key(X) local path convention (scanner_output/portfolio/<id>/
    portfolio.json), so a position written by /manual-portfolio/buy is
    actually the same file a scoped Portfolio(user_id=X) will read."""
    uid = 'cf699841-1d06-522a-9dfb-3f9619a33854'
    p = Portfolio(user_id=uid)
    assert p._portfolio_file.replace('\\', '/').endswith(f'portfolio/{uid}/portfolio.json')


def test_snapshots_do_not_collide_between_users(sandbox):
    a = Portfolio(user_id='user-a')
    a.add_position({'Symbol': 'AAPL', 'Price': 100.0, 'Stop': 90.0, 'Target': 130.0}, shares=1)
    a.daily_snapshot()

    b = Portfolio(user_id='user-b')
    b.add_position({'Symbol': 'MSFT', 'Price': 400.0, 'Stop': 380.0, 'Target': 440.0}, shares=1)
    b.daily_snapshot()

    a_perf = a.get_performance()
    b_perf = b.get_performance()
    assert len(a_perf['equity_curve']) == 1
    assert len(b_perf['equity_curve']) == 1
    # If snapshots collided on one shared file, one user's curve would show 2
    # points (their own plus the other user's) instead of 1.


def test_the_no_arg_default_path_still_works_end_to_end(sandbox):
    """Backward-compat sanity check: existing default-user callers (e.g. a
    single-user deployment, or any script that hasn't been migrated to pass
    user_id) must keep working exactly as before."""
    p = Portfolio()
    p.add_position({'Symbol': 'TSLA', 'Price': 250.0, 'Stop': 230.0, 'Target': 300.0}, shares=5)

    p_reloaded = Portfolio()
    assert len(p_reloaded.get_positions()) == 1
    assert p_reloaded.get_positions()[0]['symbol'] == 'TSLA'
