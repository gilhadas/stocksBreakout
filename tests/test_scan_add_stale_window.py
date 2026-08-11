"""
scan_and_add must not walk the whole signal archive.

WHY THIS EXISTS
---------------
`scan_and_add`'s file loop was bounded ONLY by the book's `processed_files` set,
and `list_files()` reads S3 — so a book whose `processed_files` was empty or far
behind re-downloaded and parsed the ENTIRE archive on every scan.

On 2026-07-28 the archive held 858 signal CSVs going back to 2026-04-06, and one
live book (8 positions, 10 processed) was replaying 848 of them per run. Memory
grew with files consumed, so the process expanded to fill whatever `mem_limit` it
was given — it was OOM-killed at ~1.37 GiB under a 1400m cap and again at
~2.45 GiB under a 2500m cap. Three consecutive wide scans died this way, each
~40 minutes AFTER its detection phase had already finished and written its CSV.

The caller-side guard in `scan_and_add_all_users()` does not cover this: it fires
only when `processed_files` AND `positions` are both empty, so any book holding a
position falls straight through it.

There is a correctness half too, which outlives the memory issue. Entries are NOT
priced at today's price — _fetch_entry_and_current backdates entry_price to the
close on the signal's own date (deliberately distrusting the CSV's Price column
— HZ1), which is the right price for that day but the wrong day to be opening a
position on: date_added being backdated means days_held is large from the
instant the position exists, and it can already be past a MAX_HOLD_BARS
threshold on the very next exit check. See auto_portfolio.py's SIGNAL_MAX_AGE_DAYS
comment (corrected 2026-08-05, verified against a live yfinance call) for the
mechanism in full — this docstring previously repeated the same "priced at
today" misstatement.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import auto_portfolio as ap
import utils


def _fname(day: str, mode: str = 'swing') -> str:
    """signals_swing_20260406_103303.csv from '2026-04-06'."""
    return f"signals_{mode}_{day.replace('-', '')}_103303.csv"


def _days_ago(n: int) -> str:
    return (datetime.now(ap._NY_TZ).date() - timedelta(days=n)).isoformat()


def test_constant_is_sane():
    """A too-large window re-opens the door; a too-small one drops Monday's Friday signals."""
    assert 2 <= ap.SIGNAL_MAX_AGE_DAYS <= 14, ap.SIGNAL_MAX_AGE_DAYS


def test_stale_files_are_never_loaded(monkeypatch, tmp_path):
    """The whole point: old files must not reach load_data()."""
    old = [_fname(_days_ago(n)) for n in (400, 120, 60, 30, 8)]
    fresh = [_fname(_days_ago(n)) for n in (0, 1)]

    loaded: list[str] = []

    monkeypatch.setattr(utils, 'list_files', lambda *a, **k: list(old + fresh))

    def _fake_load(path):
        loaded.append(path.rsplit('/', 1)[-1])
        return None                      # empty -> loop marks processed and moves on
    monkeypatch.setattr(utils, 'load_data', _fake_load)

    book = ap._empty()
    monkeypatch.setattr(ap, 'load', lambda **k: book)
    monkeypatch.setattr(ap, '_save', lambda *a, **k: None)

    ap.scan_and_add()

    for f in old:
        assert f not in loaded, f"stale file was downloaded and parsed: {f}"
    for f in fresh:
        assert f in loaded, f"fresh file was skipped: {f}"


def test_stale_files_are_retired_without_being_loaded(monkeypatch):
    """
    Retiring without loading is the real invariant — the one that bounds memory.

    This test used to also assert every stale file was *persisted* into
    `processed_files`. That is no longer true, and deliberately so: the list is
    now pruned to the window in which it can still do work (see
    `test_processed_files_stays_bounded` below). What must never change is that a
    stale file is skipped *before* an S3 read, which is what makes the second
    assertion here the discriminating one — the loop also marks a file processed
    when load_data() returns empty, so "is in processed_files" alone would pass
    even with the age bound removed.
    """
    old = [_fname(_days_ago(n)) for n in (365, 90, 20)]
    fresh = [_fname(_days_ago(0))]

    monkeypatch.setattr(utils, 'list_files', lambda *a, **k: list(old + fresh))

    loaded: list[str] = []

    def _fake_load(path):
        loaded.append(path.rsplit('/', 1)[-1])
        return None
    monkeypatch.setattr(utils, 'load_data', _fake_load)

    book = ap._empty()
    monkeypatch.setattr(ap, 'load', lambda **k: book)
    saved = {}
    monkeypatch.setattr(ap, '_save', lambda d, **k: saved.update(d))

    ap.scan_and_add()

    for f in old:
        assert f not in loaded, f"stale file was retired only AFTER an S3 load: {f}"


def test_prune_window_exceeds_load_window():
    """The safety argument for pruning depends on this ordering.

    Pruning is safe only because anything dropped is already older than
    `stale_cutoff`, so it is re-retired for free on the next run. Raise
    SIGNAL_MAX_AGE_DAYS past the prune window and that inverts: previously
    pruned files become *loadable* again, and weeks-old signals get admitted as
    fresh positions at today's price.
    """
    assert ap.PROCESSED_PRUNE_MARGIN_DAYS > 0
    prune_window = ap.SIGNAL_MAX_AGE_DAYS + ap.PROCESSED_PRUNE_MARGIN_DAYS
    assert prune_window > ap.SIGNAL_MAX_AGE_DAYS


def test_processed_files_stays_bounded(monkeypatch):
    """`processed_files` must not grow without limit.

    It reached 860 filenames per book in production — the dominant payload of a
    29-64 KB JSON rewritten to S3 on every scan. Entries beyond the window are
    unreachable (the age bound retires them without a read regardless), so
    keeping them buys nothing.
    """
    ancient = [_fname(_days_ago(n)) for n in (400, 200, 120, 60)]
    recent = [_fname(_days_ago(n)) for n in (20, 1, 0)]

    monkeypatch.setattr(utils, 'list_files', lambda *a, **k: ancient + recent)
    monkeypatch.setattr(utils, 'load_data', lambda p: None)

    book = ap._empty()
    # Seed the book the way production looked: a long tail of consumed history.
    book['processed_files'] = list(ancient)
    monkeypatch.setattr(ap, 'load', lambda **k: book)
    saved = {}
    monkeypatch.setattr(ap, '_save', lambda d, **k: saved.update(d))

    ap.scan_and_add()

    persisted = set(saved['processed_files'])
    for f in ancient:
        assert f not in persisted, f"unreachable entry kept, state still grows: {f}"
    for f in recent:
        assert f in persisted, f"in-window entry dropped — file would be re-read: {f}"


def test_a_book_with_positions_but_empty_processed_is_still_bounded(monkeypatch):
    """
    The exact production shape that broke: positions present, processed_files far
    behind. scan_and_add_all_users()'s guard needs BOTH empty, so this book fell
    through it and replayed 848 files.
    """
    old = [_fname(_days_ago(n)) for n in range(30, 200)]
    monkeypatch.setattr(utils, 'list_files', lambda *a, **k: list(old))

    loaded = []
    monkeypatch.setattr(utils, 'load_data', lambda p: loaded.append(p) or None)

    book = ap._empty()
    book['positions'] = [{'symbol': 'AAPL', 'shares': 10, 'entry_price': 100.0,
                          'stop_loss': 90.0, 'cost': 1000.0}]
    monkeypatch.setattr(ap, 'load', lambda **k: book)
    monkeypatch.setattr(ap, '_save', lambda *a, **k: None)

    ap.scan_and_add()
    assert loaded == [], f"replayed {len(loaded)} archived file(s) despite the age bound"


def _stub_archive(monkeypatch, fnames):
    """Point scan_and_add at `fnames` and record which ones get loaded."""
    monkeypatch.setattr(utils, 'list_files', lambda *a, **k: list(fnames))
    loaded = []
    monkeypatch.setattr(utils, 'load_data', lambda p: loaded.append(p) or None)
    book = ap._empty()
    monkeypatch.setattr(ap, 'load', lambda **k: book)
    monkeypatch.setattr(ap, '_save', lambda *a, **k: None)
    return loaded


def test_explicit_min_date_still_overrides_the_window(monkeypatch):
    """Deliberate backfills pass min_date + allow_stale; the bound must not break them."""
    old = [_fname(_days_ago(n)) for n in (100, 90)]
    loaded = _stub_archive(monkeypatch, old)

    ap.scan_and_add(min_date=_days_ago(365), allow_stale=True)
    assert len(loaded) == len(old), (
        "min_date + allow_stale must still reach old files — backfill is a supported flow")


# ── min_date must not silently bypass the AGE bound as well as the bookmark ──
#
# One branch used to do both jobs, so any min_date turned off the age guard too.
# That was reachable from the UI without anyone opting in: the "Filter by date"
# picker defaults to the FIRST OF THE CURRENT MONTH, so ticking it and pressing
# "Scan Signals" late in the month backfilled weeks-old signals into a live book.

def test_min_date_is_clamped_to_the_age_bound_by_default(monkeypatch):
    """Without allow_stale, a too-old min_date must NOT reach stale files."""
    old = [_fname(_days_ago(n)) for n in (100, 90, 30)]
    loaded = _stub_archive(monkeypatch, old)

    result = ap.scan_and_add(min_date=_days_ago(365))

    assert loaded == [], (
        f"min_date bypassed the {ap.SIGNAL_MAX_AGE_DAYS}d age bound and loaded "
        f"{len(loaded)} stale file(s)")
    assert result['stale_clamped'] == {
        'requested': _days_ago(365),
        'applied':   _days_ago(ap.SIGNAL_MAX_AGE_DAYS),
    }, "the clamp must be reported back, not applied silently"


def test_clamped_min_date_still_admits_files_inside_the_window(monkeypatch):
    """Clamping narrows the window — it must not turn the scan into a no-op."""
    files = [_fname(_days_ago(n)) for n in (100, 2)]
    loaded = _stub_archive(monkeypatch, files)

    ap.scan_and_add(min_date=_days_ago(365))

    assert len(loaded) == 1 and _fname(_days_ago(2)) in loaded[0], (
        f"expected only the in-window file to load, got {loaded}")


def test_in_window_min_date_is_untouched(monkeypatch):
    """A min_date already inside the window must pass through unchanged."""
    files = [_fname(_days_ago(n)) for n in (5, 2)]
    loaded = _stub_archive(monkeypatch, files)

    result = ap.scan_and_add(min_date=_days_ago(6))

    assert len(loaded) == 2, f"in-window min_date must reach both files, got {loaded}"
    assert result['stale_clamped'] is None, "nothing was clamped; must not report a clamp"


def test_recalculate_opts_into_stale_signals(monkeypatch):
    """recalculate rebuilds a book from history — clamping it would thin the rebuild.

    Guards the split that makes the default safe: scan_and_add defaults to
    allow_stale=False, so recalculate MUST opt in explicitly or the 2026-07-17
    silent-wipe incident recurs in a new form.
    """
    seen = {}

    def _fake_scan(**kwargs):
        seen.update(kwargs)
        return ap._build_result([], [], [], 0, 0, ap._empty())

    monkeypatch.setattr(ap, '_load_for_write', lambda **k: ap._empty())
    monkeypatch.setattr(ap, 'reset', lambda **k: None)
    monkeypatch.setattr(ap, 'scan_and_add', _fake_scan)
    monkeypatch.setattr(ap, '_save_entry_price_cache', lambda: None)
    monkeypatch.setattr('utils.save_json', lambda *a, **k: None)

    ap.recalculate(min_date=_days_ago(365))
    assert seen.get('allow_stale') is True, (
        "recalculate must pass allow_stale=True — it exists to rebuild history")
