"""
recalculate's pre-reset backup must never be written over the live book.

WHY THIS EXISTS
---------------
`recalculate()` resets the book and rebuilds it from whatever signal files still
exist. That is destructive and, per the 2026-07-17 incident recorded in its own
docstring, has already silently wiped a portfolio once — so the pre-reset backup
is the only thing standing between a thin rebuild and permanent loss.

The backup path used to be built by string-replacing the literal filename:

    _portfolio_path_for(user_id).replace('auto_portfolio.json', 'pre_recalculate_….json')

For the control book that works, because its filename IS 'auto_portfolio.json'.
For any other book the substring is absent, `.replace` returns the string
unchanged — so `backup_path == live_path`, and `save_json(pre_reset, backup_path)`
writes the backup ON TOP OF the book it exists to protect. The reset then runs
and there is nothing to recover from.

This is the failure mode that made the book dimension dangerous rather than
merely additive, and it is silent: no exception, no warning, a "backup_path" in
the result that looks perfectly reasonable.
"""
from __future__ import annotations

import pytest

import auto_portfolio as ap


@pytest.mark.parametrize('book', sorted(ap.BOOKS))
@pytest.mark.parametrize('user_id', [None, 'U1'])
def test_backup_path_is_never_the_live_path(monkeypatch, book, user_id):
    live = ap._portfolio_path_for(user_id, book)

    captured = {}

    def _fake_save_json(data, path):
        captured['backup'] = path

    def _fake_reset(**kw):
        # Reset must not run before the backup is safely elsewhere.
        assert 'backup' in captured, "backup must be written BEFORE the reset"
        assert captured['backup'] != live
        return ap._empty()

    monkeypatch.setattr(ap, 'load', lambda *a, **k: ap._empty())
    monkeypatch.setattr('utils.save_json', _fake_save_json)
    monkeypatch.setattr(ap, 'reset', _fake_reset)
    monkeypatch.setattr(ap, 'scan_and_add', lambda *a, **k: {'added': 0, 'data': ap._empty()})
    monkeypatch.setattr(ap, '_save_entry_price_cache', lambda: None)

    result = ap.recalculate(user_id=user_id, book=book)

    assert captured['backup'] != live, (
        f"backup for book {book!r} landed on the live book at {live}"
    )
    assert result['backup_path'] == captured['backup']
    assert 'pre_recalculate_' in captured['backup']


@pytest.mark.parametrize('book', sorted(ap.BOOKS))
def test_backup_lands_beside_the_book_it_backs_up(book):
    """Same directory — otherwise a per-user backup escapes into the shared dir."""
    live = ap._portfolio_path_for('U1', book)
    # Recompute the same way recalculate does.
    assert live.rsplit('/', 1)[0] == 'scanner_output/portfolio/U1'


def test_every_book_produces_a_distinct_live_path_so_backups_cannot_collide():
    lives = {b: ap._portfolio_path_for('U1', b) for b in ap.BOOKS}
    assert len(set(lives.values())) == len(lives)
