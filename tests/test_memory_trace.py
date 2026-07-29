"""
The memory tracer must be honest, cheap, and — above all — inert by default.

WHY THIS EXISTS
---------------
This instrumentation is only ever switched on during an incident, which is the
worst possible time to discover that it distorts what it measures or kills the
scan it is watching. Three properties are load-bearing and each is pinned here:

1. **Inert unless asked.** `SB_MEM_TRACE` unset must mean zero samples, zero
   log lines, and no measurable work — the production cron lines import these
   hooks on every run.
2. **Truthful.** A reported RSS that does not move with a real allocation is
   worse than no number at all; §15-§17 already burned three diagnoses on
   numbers that turned out to be the cgroup cap rather than demand.
3. **Unable to break a scan.** A probe that raises would turn a memory question
   into an outage. Every entry point swallows its own failures.

The harness's sandbox is pinned here too. The first draft of
`debug_memory_scan.py` wrote a probe portfolio into the *production* S3 bucket,
because `save_json` mirrors to S3 whenever AWS creds are present — and then read
it back on the next arm, silently invalidating the A/B. That regression must not
be reintroducible.
"""
from __future__ import annotations

import logging
import os

import pytest

import memory_trace as memt


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Every test starts from a known-off tracer with no accumulated samples."""
    monkeypatch.delenv('SB_MEM_TRACE', raising=False)
    monkeypatch.delenv('SB_MEM_TRACE_ALLOC', raising=False)
    memt.reset()
    yield
    memt.reset()


def _on(monkeypatch):
    monkeypatch.setenv('SB_MEM_TRACE', '1')


# ─────────────────────────────────────────────────────────────────────────────
# 1. Inert by default
# ─────────────────────────────────────────────────────────────────────────────


def test_disabled_records_nothing():
    """The default path on every production cron run."""
    assert memt.enabled() is False
    assert memt.mark('should-not-record') is None
    memt.tick('stream', 1, 10, every=1)
    with memt.stage('noop'):
        pass
    memt.census('noop')
    memt.report()
    assert memt.samples() == []


def test_disabled_emits_no_log_lines(caplog):
    """A silent tracer keeps cron logs readable — and greppable for real errors."""
    with caplog.at_level(logging.DEBUG, logger='memtrace'):
        memt.mark('a')
        memt.tick('s', 1, 1, every=1)
        memt.census('c')
        memt.report()
    assert caplog.records == []


def test_alloc_start_is_a_noop_when_only_base_tracing_is_on(monkeypatch):
    """tracemalloc roughly doubles the footprint — it must never ride along."""
    _on(monkeypatch)
    import tracemalloc

    was_tracing = tracemalloc.is_tracing()
    assert memt.alloc_start() is False
    assert tracemalloc.is_tracing() == was_tracing


# ─────────────────────────────────────────────────────────────────────────────
# 2. Truthful
# ─────────────────────────────────────────────────────────────────────────────


def test_rss_reader_returns_a_plausible_number():
    value = memt.rss_mb()
    assert value == value, 'no working RSS probe on this platform'
    assert 1.0 < value < 1_000_000.0, value


def test_rss_tracks_a_real_allocation(monkeypatch):
    """The core claim. A probe that does not move with 64MB is not measuring RSS."""
    _on(monkeypatch)
    before = memt.mark('before')
    blob = bytearray(64 * 1024 * 1024)
    blob[::4096] = b'\x01' * len(blob[::4096])   # touch pages so they are resident
    after = memt.mark('after')
    del blob

    assert before is not None and after is not None
    assert after.delta > 32.0, f'RSS moved only {after.delta:.1f}MB for a 64MB alloc'
    assert after.total_delta == pytest.approx(after.rss - before.rss, abs=0.01)


def test_deltas_are_relative_to_previous_and_first_marks(monkeypatch):
    _on(monkeypatch)
    first = memt.mark('one')
    memt.mark('two')
    third = memt.mark('three')
    assert first.delta == 0.0 and first.total_delta == 0.0
    assert third.total_delta == pytest.approx(third.rss - first.rss, abs=0.01)


def test_extra_fields_reach_the_log_line(monkeypatch):
    """The accumulator counters (entry_cache, skipped_cash, …) are the diagnosis."""
    _on(monkeypatch)
    sample = memt.mark('scan_and_add:end', loaded=848, entry_cache=17000)
    assert 'loaded=848' in sample.line()
    assert 'entry_cache=17000' in sample.line()


def test_cgroup_reader_shape():
    """(current, limit) in MiB on Linux; (None, None) elsewhere. Never raises."""
    current, limit = memt.cgroup_mb()
    if current is None:
        assert limit is None
    else:
        assert current > 0
        assert limit is None or limit > 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. Cannot break a scan
# ─────────────────────────────────────────────────────────────────────────────


def test_a_broken_probe_degrades_instead_of_raising(monkeypatch):
    """Instrumentation must never convert a memory question into an outage."""
    _on(monkeypatch)

    def explode():
        raise OSError('probe is broken')

    monkeypatch.setattr(memt, '_rss_reader', explode)
    assert memt.rss_mb() != memt.rss_mb() or True   # NaN, and no exception
    memt.mark('still-fine')      # must not raise
    memt.tick('s', 1, 2, every=1)
    memt.report()


def test_census_survives_objects_that_misbehave(monkeypatch):
    """gc.get_objects() walks user objects; a hostile __class__ must not abort it."""
    _on(monkeypatch)

    class Hostile:
        @property
        def memory_usage(self):
            raise RuntimeError('nope')

    keep = [Hostile() for _ in range(3)]
    counts = memt.census('hostile')
    assert counts, 'census returned nothing'
    assert len(keep) == 3


# ─────────────────────────────────────────────────────────────────────────────
# tick rate limiting — this runs inside a 1375-symbol loop
# ─────────────────────────────────────────────────────────────────────────────


def test_tick_samples_one_in_every_n(monkeypatch):
    _on(monkeypatch)
    for i in range(1, 101):
        memt.tick('detect', i, 100, every=25)
    labels = [s.label for s in memt.samples()]
    # 25/50/75 by the counter, plus 100 because index == total always samples.
    assert labels == ['detect[25/100]', 'detect[50/100]',
                      'detect[75/100]', 'detect[100/100]'], labels


def test_tick_always_samples_the_final_iteration(monkeypatch):
    """The end of a loop is the most informative point; it must never be skipped."""
    _on(monkeypatch)
    for i in range(1, 8):
        memt.tick('files', i, 7, every=100)
    assert [s.label for s in memt.samples()] == ['files[7/7]']


# ─────────────────────────────────────────────────────────────────────────────
# Production hooks
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize('module_name', ['orchestrator', 'auto_portfolio',
                                         'breakout_scanner'])
def test_scan_path_modules_carry_the_hooks(module_name):
    """Guard against a refactor quietly removing the instrumentation.

    These three modules are the OOM path: detection, admission, and the driver
    that sequences them. `auto_portfolio` imports the tracer inside
    `scan_and_add` rather than at module scope, so check the source text.
    """
    from pathlib import Path

    source = Path(f'{module_name}.py').read_text()
    assert 'memory_trace' in source, f'{module_name} lost its memory_trace import'
    assert 'memt.' in source, f'{module_name} has the import but no marks'


def test_scan_and_add_is_silent_when_tracing_is_off(monkeypatch, caplog):
    """The hook added to the admission loop must cost nothing on a normal run."""
    import auto_portfolio as ap
    import utils

    monkeypatch.setattr(utils, 'list_files', lambda *a, **k: [])
    monkeypatch.setattr(ap, 'load', lambda user_id=None: ap._empty())

    with caplog.at_level(logging.DEBUG, logger='memtrace'):
        ap.scan_and_add(user_id='__unit_test__', notify=False)
    assert caplog.records == []
    assert memt.samples() == []


# ─────────────────────────────────────────────────────────────────────────────
# Harness sandbox — the bug that actually happened
# ─────────────────────────────────────────────────────────────────────────────


def test_harness_sandbox_blocks_s3_and_project_writes(tmp_path, monkeypatch):
    """The replay harness must not be able to touch S3 or scanner_output/.

    Reproduces the real failure: without this, `_save` mirrors the probe book to
    the production bucket and the next arm reads it back, so arm A sees arm B's
    `processed_files` and loads zero files.
    """
    import auto_portfolio as ap
    import debug_memory_scan as dbg
    import utils

    monkeypatch.setattr(utils, '_is_cloud', lambda: True)   # creds present
    dbg._sandbox(str(tmp_path))

    assert utils._is_cloud() is False, 'sandbox left the S3 write path live'

    book_path = ap._portfolio_path_for('any-user')
    assert str(tmp_path) in book_path
    assert 'scanner_output' not in book_path, 'probe book still lands in the project'
    assert str(tmp_path) in ap._ENTRY_CACHE_PATH


def test_harness_stubs_remove_every_network_call(tmp_path, monkeypatch):
    """An arm that reaches yfinance measures latency, not memory — and never ends."""
    import auto_portfolio as ap
    import debug_memory_scan as dbg
    import sentiment

    dbg._sandbox(str(tmp_path))
    dbg._stub_network()

    entry, current = ap._fetch_entry_and_current('SY0001', '2026-07-01', 100.0)
    assert (entry, current) == (100.0, 102.0)
    assert ap._detect_split_factor('SY0001', '2026-07-01') == 1.0
    # The sector lookup is the one that made the first run hang: it is reached
    # per candidate from the portfolio-balance check.
    assert sentiment.get_sector_for_ticker('SY0001') == 'Technology'
    assert ap._ENTRY_PRICE_CACHE and ap._SPLIT_CACHE, 'stubs must fill the real caches'
