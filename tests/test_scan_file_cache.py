"""
The per-run signal-file cache must be shared, isolated, and short-lived.

WHY THIS EXISTS
---------------
`scan_and_add()` runs once per registered user, and each call independently
re-listed S3 and re-downloaded the same in-window files — N books meant N x the
same GETs for byte-identical content. `_SCAN_FILE_CACHE` makes the archive read
once per run.

Three properties, and the middle one is a data-corruption risk rather than a
performance detail:

1. **Shared** — a file is read once no matter how many books consume it.
2. **Isolated** — every consumer gets its own copy. `scan_and_add` mutates what
   it receives (`df_raw.columns = [c.strip() ...]` rewrites the columns in
   place), so handing two books the same object lets the first book's edits
   reach the second. Silent cross-user contamination.
3. **Short-lived** — the cache is scoped to one `scan_and_add_all_users()` call
   and cleared even on failure. Signal files are appended to during the trading
   day; a frame surviving into a later scan would make a book miss that day's
   signals.
"""
from __future__ import annotations

import pandas as pd
import pytest

import auto_portfolio as ap
import utils


@pytest.fixture(autouse=True)
def _cache_off():
    """Never leak cache state between tests."""
    ap._SCAN_FILE_CACHE = None
    yield
    ap._SCAN_FILE_CACHE = None


def _frame():
    return pd.DataFrame([{' Symbol ': 'AAPL', 'Quality': 'PREMIUM', 'Price': 100.0}])


# ─────────────────────────────────────────────────────────────────────────────
# 1. Shared
# ─────────────────────────────────────────────────────────────────────────────


def test_file_is_read_once_when_the_cache_is_active(monkeypatch):
    reads: list[str] = []
    monkeypatch.setattr(utils, 'load_data',
                        lambda p: reads.append(p) or _frame())

    ap._SCAN_FILE_CACHE = {}
    for _ in range(5):
        ap._load_signal_frame('signals_swing_20260728_103303.csv')

    assert len(reads) == 1, f"cache inactive — {len(reads)} reads for one file"


def test_reads_are_not_cached_when_no_run_is_active(monkeypatch):
    """A lone scan_and_add, a backtest, or a test must not get stale frames."""
    reads: list[str] = []
    monkeypatch.setattr(utils, 'load_data',
                        lambda p: reads.append(p) or _frame())

    assert ap._SCAN_FILE_CACHE is None
    for _ in range(3):
        ap._load_signal_frame('signals_swing_20260728_103303.csv')

    assert len(reads) == 3


def test_a_missing_file_is_cached_as_none_without_re_reading(monkeypatch):
    """load_data returns None for a vanished file; don't retry it per book."""
    reads: list[str] = []
    monkeypatch.setattr(utils, 'load_data', lambda p: reads.append(p) or None)

    ap._SCAN_FILE_CACHE = {}
    assert ap._load_signal_frame('gone.csv') is None
    assert ap._load_signal_frame('gone.csv') is None
    assert len(reads) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. Isolated — the corruption guard
# ─────────────────────────────────────────────────────────────────────────────


def test_each_consumer_gets_an_independent_frame(monkeypatch):
    """The property that makes sharing safe. Remove the .copy() and this fails."""
    monkeypatch.setattr(utils, 'load_data', lambda p: _frame())
    ap._SCAN_FILE_CACHE = {}

    first = ap._load_signal_frame('signals_swing_20260728_103303.csv')
    second = ap._load_signal_frame('signals_swing_20260728_103303.csv')

    assert first is not second, "same object handed to two books"

    # Exactly what scan_and_add does at the top of its per-file block.
    first.columns = [c.strip() for c in first.columns]
    first['_source_file'] = 'book-one'

    assert list(second.columns) == [' Symbol ', 'Quality', 'Price'], (
        "one book's in-place column edit reached another book's frame")
    assert '_source_file' not in second.columns


def test_mutating_a_handout_does_not_poison_the_cache(monkeypatch):
    monkeypatch.setattr(utils, 'load_data', lambda p: _frame())
    ap._SCAN_FILE_CACHE = {}

    got = ap._load_signal_frame('f.csv')
    got.loc[0, 'Price'] = 999.0

    assert ap._load_signal_frame('f.csv').loc[0, 'Price'] == 100.0


# ─────────────────────────────────────────────────────────────────────────────
# 3. Short-lived
# ─────────────────────────────────────────────────────────────────────────────


def _run_all_users(monkeypatch, users, scan_impl):
    """Drive scan_and_add_all_users with a stubbed user table."""
    import types

    monkeypatch.setattr(ap, 'load', lambda **k: ap._empty())
    monkeypatch.setattr(ap, 'scan_and_add', scan_impl)
    fake_db = types.SimpleNamespace(
        query=lambda _m: types.SimpleNamespace(all=lambda: users))
    monkeypatch.setitem(
        __import__('sys').modules, 'api.database',
        types.SimpleNamespace(get_db=lambda: iter([fake_db])))
    monkeypatch.setitem(
        __import__('sys').modules, 'api.models',
        types.SimpleNamespace(User=object))
    return ap.scan_and_add_all_users()


def test_cache_is_active_during_the_run_and_cleared_after(monkeypatch):
    import types

    users = [types.SimpleNamespace(id='u1', email='a@x'),
             types.SimpleNamespace(id='u2', email='b@x')]
    seen = []

    def _scan(**kwargs):
        seen.append(ap._SCAN_FILE_CACHE)
        return {'added': 0}

    _run_all_users(monkeypatch, users, _scan)

    assert all(c is not None for c in seen), "cache was not active during the run"
    assert seen[0] is seen[1], "each book got a fresh cache — no sharing happened"
    assert ap._SCAN_FILE_CACHE is None, "cache leaked past the run"


def test_cache_is_cleared_even_when_a_book_raises(monkeypatch):
    """A leaked cache would serve stale frames to the next scan of the day."""
    import types

    users = [types.SimpleNamespace(id='u1', email='a@x')]

    def _boom(**kwargs):
        raise RuntimeError('book exploded')

    _run_all_users(monkeypatch, users, _boom)   # per-user errors are isolated
    assert ap._SCAN_FILE_CACHE is None
