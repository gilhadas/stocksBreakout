"""
Two books must never bleed into each other.

WHY THIS EXISTS
---------------
The A/B is only valid if the two arms are independent. `execute_swap` is the
sharpest edge here: it performs FIVE separate book operations — load, then
`close_position`, then `add_position_direct`, then a second load, then a save.
Every one takes `book`. Drop it from any single call and the swap closes a
position in one book while opening its replacement in the other, which
simultaneously corrupts both arms AND fabricates capital (one book loses a
position without gaining one, the other gains one it never paid for from its own
skipped list).

`add_position_direct` is the most fragile of the five because it deliberately
bypasses `_save` — it re-derives the path itself so load/dedup/save happen inside
ONE lock acquisition. That means it has its own copy of the path logic, and its
own opportunity to get the book wrong.

`archive_user_portfolio` is included because CLAUDE.md §23.1 documents what
happens when a book is left behind on user deletion: two orphans with ~$99k of
open positions each, indistinguishable from live books.
"""
from __future__ import annotations

import json

import pytest

import auto_portfolio as ap
import utils


@pytest.fixture
def books(tmp_path, monkeypatch):
    """Redirect all book IO to tmp_path. Returns a helper for reading them back.

    Pins the sandbox rather than trusting it: MEMORY.md records a harness that
    wrote a probe portfolio into the PRODUCTION S3 bucket because save_json
    mirrors to S3 whenever AWS creds are present.
    """
    monkeypatch.setattr(utils, '_is_cloud', lambda: False)
    monkeypatch.setattr(utils, '_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setattr(utils, 'PROJECT_ROOT', tmp_path)   # Path, not str — see _to_local_abs

    def read(user_id, book):
        p = tmp_path / ap._portfolio_path_for(user_id, book)
        return json.loads(p.read_text()) if p.exists() else None

    return read


def _seed(user_id, book, **over):
    data = ap._empty()
    data.update(over)
    ap._save(data, user_id=user_id, book=book)
    return data


# ── Basic independence ───────────────────────────────────────────────────────

def test_writing_one_book_leaves_the_other_untouched(books):
    _seed('U1', 'control', capital=111.0)
    _seed('U1', 'autoswap', capital=222.0)

    assert ap.load(user_id='U1', book='control')['capital'] == 111.0
    assert ap.load(user_id='U1', book='autoswap')['capital'] == 222.0


def test_the_two_books_are_separate_files_on_disk(books):
    _seed('U1', 'control', capital=1.0)
    _seed('U1', 'autoswap', capital=2.0)
    assert books('U1', 'control')['capital'] == 1.0
    assert books('U1', 'autoswap')['capital'] == 2.0


def test_a_missing_variant_does_not_fall_back_to_control(books):
    """Silent fallback would make the variant look like a perfect clone forever."""
    _seed('U1', 'control', capital=999.0, positions=[{'symbol': 'AAA'}])
    fresh = ap.load(user_id='U1', book='autoswap')
    assert fresh['positions'] == []
    assert fresh['capital'] == ap.INITIAL_CAPITAL


def test_same_book_name_is_isolated_across_users(books):
    _seed('U1', 'autoswap', capital=10.0)
    _seed('U2', 'autoswap', capital=20.0)
    assert ap.load(user_id='U1', book='autoswap')['capital'] == 10.0
    assert ap.load(user_id='U2', book='autoswap')['capital'] == 20.0


# ── add_position_direct — bypasses _save, so it needs its own guard ──────────

def test_add_position_direct_writes_to_the_requested_book(books):
    _seed('U1', 'control')
    _seed('U1', 'autoswap')

    res = ap.add_position_direct(symbol='AAA', entry_price=10.0, stop=9.0, target=12.0,
                                 user_id='U1', book='autoswap')
    assert res['added'], res

    assert [p['symbol'] for p in ap.load(user_id='U1', book='autoswap')['positions']] == ['AAA']
    assert ap.load(user_id='U1', book='control')['positions'] == [], \
        "add_position_direct bypasses _save and must still honour `book`"


def test_close_position_closes_in_the_requested_book(books):
    pos = {'symbol': 'AAA', 'entry_price': 10.0, 'stop': 9.0, 'target': 12.0,
           'shares': 10, 'cost': 100.0, 'date_added': '2026-08-01', 'mode': 'swing',
           'quality': 'GOLD', 'minervini_score': 0}
    _seed('U1', 'control', positions=[dict(pos)])
    _seed('U1', 'autoswap', positions=[dict(pos)])

    ap.close_position('AAA', 11.0, reason='manual', user_id='U1', book='autoswap')

    assert ap.load(user_id='U1', book='autoswap')['positions'] == []
    assert len(ap.load(user_id='U1', book='control')['positions']) == 1, \
        "closing in one book must not close in the other"


# ── execute_swap — the five-call function ────────────────────────────────────

def test_execute_swap_closes_and_opens_in_the_same_book(books, monkeypatch):
    """The mutation that matters: drop `book` from ANY internal call and the
    swap straddles two books."""
    pos = {'symbol': 'OLD', 'entry_price': 10.0, 'stop': 9.0, 'target': 12.0,
           'shares': 10, 'cost': 100.0, 'date_added': '2026-08-01', 'mode': 'swing',
           'quality': 'GOLD', 'minervini_score': 0}
    skip = {'symbol': 'NEW', 'date_added': '2026-08-05', 'mode': 'swing',
            'quality': 'PREMIUM', 'entry_price': 20.0, 'stop': 18.0, 'target': 26.0,
            'vol': 2.0, 'rr': 3.0, 'priority_score': 65.0}

    for b in ('control', 'autoswap'):
        _seed('U1', b, positions=[dict(pos)], skipped_cash=[dict(skip)])

    monkeypatch.setattr(ap, '_fetch_live_price', lambda s: {'OLD': 11.0, 'NEW': 20.0}[s])
    monkeypatch.setattr(ap, '_fetch_close_basis_price', lambda s: {'OLD': 11.0, 'NEW': 20.0}[s])

    res = ap.execute_swap('OLD', 'NEW', user_id='U1', book='autoswap')
    assert res['ok'], res

    var = ap.load(user_id='U1', book='autoswap')
    ctrl = ap.load(user_id='U1', book='control')

    assert [p['symbol'] for p in var['positions']] == ['NEW'], \
        "the replacement must open in the SAME book the close happened in"
    assert [t['symbol'] for t in var['closed']] == ['OLD']
    assert 'NEW' not in [s['symbol'] for s in var['skipped_cash']], \
        "the consumed skipped signal must be removed from the same book"

    assert [p['symbol'] for p in ctrl['positions']] == ['OLD'], \
        "the control book must be completely untouched by a swap in the variant"
    assert ctrl['closed'] == []
    assert [s['symbol'] for s in ctrl['skipped_cash']] == ['NEW']


def test_undo_swap_rewinds_only_its_own_book(books, monkeypatch):
    pos = {'symbol': 'OLD', 'entry_price': 10.0, 'stop': 9.0, 'target': 12.0,
           'shares': 10, 'cost': 100.0, 'date_added': '2026-08-01', 'mode': 'swing',
           'quality': 'GOLD', 'minervini_score': 0}
    skip = {'symbol': 'NEW', 'date_added': '2026-08-05', 'mode': 'swing',
            'quality': 'PREMIUM', 'entry_price': 20.0, 'stop': 18.0, 'target': 26.0,
            'vol': 2.0, 'rr': 3.0, 'priority_score': 65.0}
    for b in ('control', 'autoswap'):
        _seed('U1', b, positions=[dict(pos)], skipped_cash=[dict(skip)])

    monkeypatch.setattr(ap, '_fetch_live_price', lambda s: {'OLD': 11.0, 'NEW': 20.0}[s])
    ap.execute_swap('OLD', 'NEW', user_id='U1', book='autoswap')
    assert ap.undo_last_swap(user_id='U1', book='autoswap')['ok']

    assert [p['symbol'] for p in ap.load(user_id='U1', book='autoswap')['positions']] == ['OLD']
    assert [p['symbol'] for p in ap.load(user_id='U1', book='control')['positions']] == ['OLD']


# ── User deletion ────────────────────────────────────────────────────────────

def test_archiving_a_user_takes_every_book(books):
    """CLAUDE.md §23.1: a book left behind is indistinguishable from a live one."""
    _seed('U1', 'control', capital=1.0)
    _seed('U1', 'autoswap', capital=2.0)

    ap.archive_user_portfolio('U1')

    assert ap.load(user_id='U1', book='control')['capital'] == ap.INITIAL_CAPITAL
    assert ap.load(user_id='U1', book='autoswap')['capital'] == ap.INITIAL_CAPITAL
    assert books('U1', 'control') is None
    assert books('U1', 'autoswap') is None
