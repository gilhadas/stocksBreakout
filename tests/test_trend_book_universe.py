"""
A universe book may buy ONLY what its watchlist names — and must fail closed.

WHY THIS EXISTS
---------------
`trend` is the first book that differs from control in WHICH SYMBOLS it may buy
rather than in what it does with them. That introduces a failure mode the
policy-only A/B never had: if the universe filter silently stops applying, the
trend book quietly becomes a second copy of control. Nothing errors, both books
keep trading, and the comparison reads as "no difference between the universes"
when what actually happened is that there was only ever one universe.

That is the same shape as every bug in CLAUDE.md sections 22/23 — state that
looks live but isn't — so the filter is pinned from both sides: it must drop
off-universe rows, and it must NOT drop anything for a book without a universe.

The watchlist file is gitignored, lives on a docker volume seeded once, and is
copied in by hand, so "missing in production" is a realistic state rather than a
hypothetical. Missing therefore means "admit nothing" (obvious on day one), never
"admit everything" (invisible forever).
"""
from __future__ import annotations

import pandas as pd
import pytest

import auto_portfolio as ap
import book_compare
import utils


TODAY = None  # set per-test from ap._NY_TZ so the age bound never rejects fixtures


def _today() -> str:
    from datetime import datetime
    return datetime.now(ap._NY_TZ).strftime('%Y%m%d')


def _fname(mode: str = 'swing') -> str:
    return f"signals_{mode}_{_today()}_103303.csv"


def _frame(symbols) -> pd.DataFrame:
    """A signal frame every row of which clears the V9-H quality gate."""
    return pd.DataFrame([
        {'Symbol': s, 'Quality': 'PREMIUM', 'Type': 'BOUNCE', 'Price': 10.0,
         'Stop': 9.0, 'Target': 12.0, 'R:R': 2.0, 'Vol': 1.5, 'Dist': 5.0,
         'MinerviniScore': 8}
        for s in symbols
    ])


@pytest.fixture
def archive(monkeypatch, tmp_path):
    """One in-window signal file containing both on- and off-universe symbols.

    Returns the dict of books written, so a test can assert on what was saved.
    """
    monkeypatch.setattr(utils, '_is_cloud', lambda: False)
    # Directory-aware: the file lives in the SHARED stream only. Without this a
    # book that reads two directories would list the same name twice and every
    # count in these tests would double.
    monkeypatch.setattr(utils, 'list_files',
                        lambda d, pat='*', **k: [_fname()] if d == ap._SIGNALS_DIR else [])
    monkeypatch.setattr(utils, 'load_data',
                        lambda p: _frame(['NVDA', 'ZZZZ_OFF', 'PLTR']))

    # Prices/entries would otherwise hit the network. Fixed values keep the
    # admission arithmetic deterministic.
    monkeypatch.setattr(ap, '_fetch_entry_and_current', lambda *a, **k: (10.0, 10.0))

    books: dict[str, dict] = {}

    def _load(**kw):
        name = kw.get('book') or ap.DEFAULT_BOOK
        return books.setdefault(name, ap._empty())

    def _save(d, **kw):
        books[kw.get('book') or ap.DEFAULT_BOOK] = d

    monkeypatch.setattr(ap, 'load', _load)
    monkeypatch.setattr(ap, '_save', _save)
    return books


@pytest.fixture(autouse=True)
def _clear_universe_cache():
    ap._UNIVERSE_CACHE.clear()
    yield
    ap._UNIVERSE_CACHE.clear()


def _use_universe(monkeypatch, symbols):
    """Point the trend book at an in-memory watchlist."""
    monkeypatch.setattr(ap, 'BOOKS', {
        **ap.BOOKS,
        'trend': {**ap.BOOKS['trend'], 'universe': '/nonexistent/trend.txt'},
    })
    monkeypatch.setattr(utils, 'get_watchlist_from_file',
                        lambda p: list(symbols))


# ── The filter itself ────────────────────────────────────────────────────────

def test_off_universe_signal_is_not_admitted(archive, monkeypatch):
    _use_universe(monkeypatch, ['NVDA', 'PLTR'])

    res = ap.scan_and_add(book='trend')

    assert res['files_scanned'] == 1, \
        "the filter was never reached — this test would pass vacuously"
    assert 'ZZZZ_OFF' not in res['added_symbols'], \
        "a symbol outside the book's universe was bought"
    assert set(res['added_symbols']) == {'NVDA', 'PLTR'}
    assert res['skipped_universe'] == 1


def test_the_same_file_is_admitted_in_full_by_control(archive, monkeypatch):
    """The control book must be unaffected — this is the other half of the A/B."""
    _use_universe(monkeypatch, ['NVDA', 'PLTR'])

    res = ap.scan_and_add(book='control')

    assert res['files_scanned'] == 1
    assert 'ZZZZ_OFF' in res['added_symbols'], \
        "the universe filter leaked into a book that has no universe"
    assert res['skipped_universe'] == 0


def test_a_book_without_a_universe_reports_zero_skipped(archive, monkeypatch):
    _use_universe(monkeypatch, ['NVDA'])
    assert ap.scan_and_add(book='autoswap')['skipped_universe'] == 0


# ── Fail closed ──────────────────────────────────────────────────────────────

def test_missing_watchlist_admits_nothing(archive, monkeypatch, caplog):
    """Missing file ⇒ buy NOTHING. Never fall through to "buy everything"."""
    _use_universe(monkeypatch, [])          # parses to zero symbols

    with caplog.at_level('ERROR'):
        res = ap.scan_and_add(book='trend')

    assert res['files_scanned'] == 1, "the filter was never reached"
    assert res['added_symbols'] == [], \
        "a missing universe fell through to no filter — the book became a copy of control"
    assert res['skipped_universe'] == 3
    assert any('admit NO signals' in r.message for r in caplog.records), \
        "silent failure: nothing in the log says why the book bought nothing"


def test_an_empty_universe_is_not_cached(monkeypatch):
    """A restored file must take effect on the next scan, without a restart.

    scanner-cron runs for days, so caching the failure would keep the book dead
    long after the operator fixed it.
    """
    _use_universe(monkeypatch, [])
    assert ap._book_universe('trend') == frozenset()
    assert ap._UNIVERSE_CACHE == {}

    monkeypatch.setattr(utils, 'get_watchlist_from_file', lambda p: ['NVDA'])
    assert ap._book_universe('trend') == frozenset({'NVDA'})


def test_universe_is_none_not_empty_for_unconstrained_books():
    """None and empty mean opposite things and must not be conflated."""
    assert ap._book_universe('control') is None
    assert ap._book_universe('autoswap') is None


# ── Seeding: a universe book starts empty, not as a clone of control ─────────

def test_trend_book_is_not_seeded_from_control(archive, monkeypatch):
    control = ap._empty()
    control['positions'] = [{'symbol': 'OFF_UNIVERSE', 'entry_price': 10.0,
                             'shares': 5, 'cost': 50.0, 'stop': 9.0,
                             'target': 12.0}]
    control['capital'] = 91234.0
    archive['control'] = control

    forked = ap.ensure_forked(user_id=None, book='trend')

    assert forked['positions'] == [], \
        "the trend book opened holding a name its own watchlist may not contain"
    assert forked['capital'] == ap.INITIAL_CAPITAL
    assert (forked.get('fork') or {}).get('source') is None


def test_autoswap_is_still_seeded_from_control(archive):
    """The policy A/B's arms must still start identical — unchanged behaviour."""
    control = ap._empty()
    control['positions'] = [{'symbol': 'AAA', 'entry_price': 10.0, 'shares': 5,
                             'cost': 50.0, 'stop': 9.0, 'target': 12.0}]
    archive['control'] = control

    forked = ap.ensure_forked(user_id=None, book='autoswap')

    assert [p['symbol'] for p in forked['positions']] == ['AAA']
    assert (forked.get('fork') or {}).get('source') == 'control'


# ── The landmine: a later fork must not move control's clock ────────────────

def test_forking_a_new_book_does_not_restamp_control(archive):
    """Control's fork date is the `since` every one of its metrics is measured
    from. If adding a third book moved it, the running control-vs-autoswap
    comparison would silently restart from today and discard its history."""
    control = ap._empty()
    control['fork'] = {'date': '2026-08-11', 'at': '2026-08-11T10:00:00',
                       'source': 'control', 'peer': 'autoswap', 'book': 'control'}
    archive['control'] = control

    ap.ensure_forked(user_id=None, book='trend')

    assert archive['control']['fork']['date'] == '2026-08-11', \
        "adding a book reset control's measurement window"
    assert archive['control']['fork']['peer'] == 'autoswap'


def test_first_ever_fork_still_stamps_control(archive):
    """The guard must not stop control being stamped the first time."""
    archive['control'] = ap._empty()

    ap.ensure_forked(user_id=None, book='autoswap')

    assert (archive['control'].get('fork') or {}).get('date'), \
        "control was never given a start date, so book_compare has no window"


def test_forced_refork_of_the_paired_book_does_restamp(archive):
    """`fork_books --force` on the book control is paired with is the deliberate
    "restart this experiment" case and must still reset both clocks."""
    control = ap._empty()
    control['fork'] = {'date': '2026-01-01', 'at': '2026-01-01T10:00:00',
                       'source': 'control', 'peer': 'autoswap', 'book': 'control'}
    archive['control'] = control

    ap.ensure_forked(user_id=None, book='autoswap', force=True)

    assert archive['control']['fork']['date'] != '2026-01-01', \
        "a forced re-fork of the paired book must restart the clock"


# ── Swap advice ──────────────────────────────────────────────────────────────

def test_trend_book_runs_no_swap_stage(monkeypatch):
    """Telegram has no per-book routing; a third advising book is a third message."""
    called = []
    monkeypatch.setattr(ap, 'suggest_swaps',
                        lambda **kw: called.append(kw) or [])

    data = {'positions': [{'symbol': 'A'}], 'skipped_cash': [{'symbol': 'B'}]}
    assert ap._run_swap_stage(data, user_id=None, book='trend') == []
    assert called == [], "the trend book reached the swap advisor"


def test_control_still_advises(monkeypatch):
    called = []
    monkeypatch.setattr(ap, 'suggest_swaps',
                        lambda **kw: called.append(kw) or [])

    data = {'positions': [{'symbol': 'A'}], 'skipped_cash': [{'symbol': 'B'}]}
    ap._run_swap_stage(data, user_id=None, book='control')
    assert called, "control stopped advising — that is a behaviour change"


# ── book_compare: difference over the variant's own window ──────────────────

def _curve(points):
    return [{'date': d, 'total_value': v} for d, v in points]


def test_delta_uses_the_variants_window_not_controls(monkeypatch):
    """Control ran up 50% before the trend book existed. Differencing control's
    since-inception return from trend's three-week return would report a huge
    spurious deficit for trend."""
    control = ap._empty()
    control['fork'] = {'date': '2026-01-01'}
    control['equity_history'] = _curve([('2026-01-01', 100_000),
                                        ('2026-08-01', 150_000),
                                        ('2026-09-01', 151_500)])
    trend = ap._empty()
    trend['fork'] = {'date': '2026-08-01'}
    trend['equity_history'] = _curve([('2026-08-01', 100_000),
                                      ('2026-09-01', 102_000)])

    books = {'control': control, 'autoswap': ap._empty(), 'trend': trend}
    monkeypatch.setattr(ap, 'load',
                        lambda **kw: books[kw.get('book') or 'control'])

    rep = book_compare.compare_books()

    # Over the shared window both made ~1-2%, so the gap is small. Measured
    # against control's since-January +51.5% it would read as roughly -49.
    assert rep['vs_control']['trend']['return_pct'] == pytest.approx(1.0, abs=0.1)
    assert rep['vs_control']['trend']['since'] == '2026-08-01'


def test_same_window_books_are_differenced_directly(monkeypatch):
    """Books forked the same day share a window — behaviour must be unchanged."""
    control = ap._empty()
    control['fork'] = {'date': '2026-08-01'}
    control['equity_history'] = _curve([('2026-08-01', 100_000),
                                        ('2026-09-01', 110_000)])
    auto = ap._empty()
    auto['fork'] = {'date': '2026-08-01'}
    auto['equity_history'] = _curve([('2026-08-01', 100_000),
                                     ('2026-09-01', 115_000)])

    books = {'control': control, 'autoswap': auto, 'trend': ap._empty()}
    monkeypatch.setattr(ap, 'load',
                        lambda **kw: books[kw.get('book') or 'control'])

    rep = book_compare.compare_books()
    assert rep['vs_control']['autoswap']['return_pct'] == pytest.approx(5.0, abs=0.1)


# ── The shipped watchlist ────────────────────────────────────────────────────

def test_shipped_trend_watchlist_is_present_and_parses():
    """The file is load-bearing for admission and is gitignored by a blanket
    rule, so it is force-added. If it ever stops being tracked, the trend book
    goes dead in production and this is the only thing that would say so."""
    import os
    path = ap.BOOKS['trend']['universe']
    assert os.path.exists(path), f"{path} is missing — the trend book cannot buy anything"
    symbols = utils.get_watchlist_from_file(path)
    assert len(symbols) > 50, f"only {len(symbols)} symbols parsed from {path}"


# ── Private signal streams: isolation is structural, not a filter ────────────
#
# A scan of a narrow watchlist writes into a directory only the trend book
# lists. Control and autoswap never enumerate it, so an ad-hoc trend scan cannot
# reach them even if the universe filter were removed entirely. That is the
# difference between isolation and a filter: a filter has to be right every
# time, a listing that never happens cannot be wrong.

def test_only_the_trend_book_lists_the_private_directory():
    assert ap._book_signals_dirs('control') == [ap._SIGNALS_DIR]
    assert ap._book_signals_dirs('autoswap') == [ap._SIGNALS_DIR]
    dirs = ap._book_signals_dirs('trend')
    assert dirs[0] == ap._SIGNALS_DIR, 'trend must still read the shared stream'
    assert 'scanner_output/signals_trend' in dirs


def test_signal_ref_round_trips_and_keeps_shared_refs_bare():
    """A qualified ref must not change how EXISTING processed_files entries look.

    Those sets are the bookmark that stops a book replaying the archive (§18).
    Re-encoding shared files would invalidate every persisted bookmark at once.
    """
    bare = ap._signal_ref(ap._SIGNALS_DIR, 'signals_swing_20260904_1.csv')
    assert bare == 'signals_swing_20260904_1.csv', 'shared refs must stay bare'
    assert ap._split_signal_ref(bare) == (ap._SIGNALS_DIR, 'signals_swing_20260904_1.csv')

    q = ap._signal_ref('scanner_output/signals_trend', 'signals_swing_20260904_1.csv')
    assert q == 'signals_trend/signals_swing_20260904_1.csv'
    assert ap._split_signal_ref(q) == ('scanner_output/signals_trend',
                                       'signals_swing_20260904_1.csv')
    # The age bound reads the date out of a ref, not just a bare filename; if it
    # fell through to "today" a stale private file would be admitted forever.
    assert ap._date_from_filename(q) == '2026-09-04'


def _dir_aware_archive(monkeypatch, per_dir):
    """Stub list_files/load_data so each directory has its own files."""
    monkeypatch.setattr(utils, '_is_cloud', lambda: False)
    monkeypatch.setattr(utils, 'list_files',
                        lambda d, pat='*', **k: list(per_dir.get(d, {})))

    def _load(path):
        d, f = path.rsplit('/', 1)
        return per_dir.get(d, {}).get(f)
    monkeypatch.setattr(utils, 'load_data', _load)
    monkeypatch.setattr(ap, '_fetch_entry_and_current', lambda *a, **k: (10.0, 10.0))

    books = {}
    monkeypatch.setattr(ap, 'load',
                        lambda **kw: books.setdefault(kw.get('book') or ap.DEFAULT_BOOK,
                                                      ap._empty()))
    monkeypatch.setattr(ap, '_save',
                        lambda d, **kw: books.__setitem__(kw.get('book') or ap.DEFAULT_BOOK, d))
    return books


def test_private_stream_is_invisible_to_control(archive, monkeypatch):
    """The whole point: a trend-only scan must not reach control."""
    _use_universe(monkeypatch, ['NVDA', 'PLTR'])
    fn = _fname()
    _dir_aware_archive(monkeypatch, {
        'scanner_output/signals': {},
        'scanner_output/signals_trend': {fn: _frame(['NVDA', 'PLTR'])},
    })

    ctl = ap.scan_and_add(book='control')
    assert ctl['files_scanned'] == 0, \
        'control listed the private directory — isolation is broken'
    assert ctl['added_symbols'] == []

    tr = ap.scan_and_add(book='trend')
    assert tr['files_scanned'] == 1, 'trend did not read its own directory'
    assert set(tr['added_symbols']) == {'NVDA', 'PLTR'}


def test_trend_still_reads_the_shared_stream(archive, monkeypatch):
    """Isolation must be additive — the daily all.txt scan still feeds trend."""
    _use_universe(monkeypatch, ['NVDA'])
    fn = _fname()
    _dir_aware_archive(monkeypatch, {
        'scanner_output/signals': {fn: _frame(['NVDA', 'ZZZZ_OFF'])},
        'scanner_output/signals_trend': {},
    })

    tr = ap.scan_and_add(book='trend')
    assert tr['files_scanned'] == 1
    assert tr['added_symbols'] == ['NVDA'], 'trend stopped seeing the shared stream'
    assert tr['skipped_universe'] == 1


def test_same_named_file_in_both_dirs_is_not_masked(archive, monkeypatch):
    """Both directories name files signals_<mode>_<ts>.csv, so a collision is
    possible. Each must be loaded on its own, not served from the other's cache
    entry, or one day's private signals would silently replace the shared ones."""
    _use_universe(monkeypatch, ['NVDA', 'PLTR'])
    fn = _fname()
    _dir_aware_archive(monkeypatch, {
        'scanner_output/signals': {fn: _frame(['NVDA'])},
        'scanner_output/signals_trend': {fn: _frame(['PLTR'])},
    })
    ap._SCAN_FILE_CACHE = {}                     # the multi-user run's cache
    try:
        tr = ap.scan_and_add(book='trend')
    finally:
        ap._SCAN_FILE_CACHE = None

    assert tr['files_scanned'] == 2, 'a same-named file in the other dir was masked'
    assert set(tr['added_symbols']) == {'NVDA', 'PLTR'}


def test_save_results_rejects_a_path_as_subdir():
    """subdir_override lands in an S3 key and a local path."""
    import orchestrator
    orch = orchestrator.ScannerOrchestrator.__new__(orchestrator.ScannerOrchestrator)
    for bad in ('../evil', 'a/b', '..'):
        with pytest.raises(ValueError):
            orch.save_results([{'Symbol': 'X'}], 'swing', 'signals', subdir_override=bad)
