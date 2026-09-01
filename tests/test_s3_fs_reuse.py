"""
The S3 filesystem must be built once per credential set, not once per call.

WHY THIS EXISTS
---------------
`_s3_fs()` used to construct a fresh `S3FileSystem` on every call. Constructing
one is cheap, but the first *operation* on each new instance builds a fresh
aiobotocore/botocore client, which JSON-parses the S3 service model and never
releases it. Measured against real S3: **+12.8 MB per call, perfectly linear**
(20 reads = +256 MB) versus **+0.0 MB** after the first when one instance is
reused; `tracemalloc` put `json/decoder.py:361` at +410 MB / 5.5M blocks with
botocore's model loaders right behind it.

That per-call term is what made the §18 archive replay fatal. 858 files ≈ 11 GB
of demand, so the process filled whatever `mem_limit` it was given — killed at
~1.37 GiB under a 1400m cap and ~2.45 GiB under a 2500m cap. Every "peak"
recorded in §15-§17 was the ceiling, never the demand.

Note this is NOT fully fixed by §18's age bound: the 7-day window still holds
~31 files, so a book with an empty `processed_files` set still made ~33 S3 calls
(~422 MB), and five such books exceed the current 2500m cap on their own.

`skip_instance_cache=True` must survive — it is the `4007cd0` workaround for the
AioSession kwarg bug in s3fs ≥2025 + aiobotocore ≥3.x. The memoization wraps
*around* fsspec's instance cache rather than re-enabling it.

These tests never touch the network: they stub `s3fs.S3FileSystem`.
"""
from __future__ import annotations

import pathlib
import sys
import threading
import types

import pytest

import utils


@pytest.fixture
def fake_s3fs(monkeypatch):
    """Replace s3fs.S3FileSystem with a counting stub and clear the cache."""
    built = []

    class FakeFS:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.ops = []
            built.append(self)

        def invalidate_cache(self, path=None):
            self.ops.append(('invalidate', path))

        def ls(self, prefix, detail=False):
            self.ops.append(('ls', prefix))
            return [f'{prefix}/signals_swing_20260728_1033.csv']

    module = types.ModuleType('s3fs')
    module.S3FileSystem = FakeFS
    monkeypatch.setitem(sys.modules, 's3fs', module)
    monkeypatch.setattr(utils, '_in_streamlit', lambda: False)
    monkeypatch.setattr(utils, '_load_secrets_toml', lambda: {})
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'key-1')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'secret-1')
    monkeypatch.setenv('AWS_DEFAULT_REGION', 'eu-central-1')

    utils._drop_s3_fs()
    yield built
    utils._drop_s3_fs()


# ─────────────────────────────────────────────────────────────────────────────
# The core claim
# ─────────────────────────────────────────────────────────────────────────────


def test_repeated_calls_reuse_one_filesystem(fake_s3fs):
    """The whole point: N calls must build 1 client, not N."""
    instances = {id(utils._s3_fs()) for _ in range(50)}
    assert len(instances) == 1, 'a new filesystem was built per call'
    assert len(fake_s3fs) == 1, f'built {len(fake_s3fs)} clients for 50 calls'


def test_the_aiobotocore_workaround_is_preserved(fake_s3fs):
    """Dropping skip_instance_cache would re-open the 4007cd0 bug."""
    fs = utils._s3_fs()
    assert fs.kwargs['skip_instance_cache'] is True
    assert fs.kwargs['key'] == 'key-1'
    assert fs.kwargs['client_kwargs'] == {'region_name': 'eu-central-1'}


def test_rotated_credentials_produce_a_new_filesystem(fake_s3fs, monkeypatch):
    """The cache key is the credential set, so a rotation is not served stale."""
    first = utils._s3_fs()
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'key-2')
    second = utils._s3_fs()
    assert first is not second
    assert second.kwargs['key'] == 'key-2'


def test_streamlit_sessions_still_use_the_connection(fake_s3fs, monkeypatch):
    """Streamlit's st.connection already caches; that branch must be untouched."""
    sentinel = object()
    monkeypatch.setattr(utils, '_in_streamlit', lambda: True)
    monkeypatch.setattr(utils, '_s3_conn',
                        lambda: types.SimpleNamespace(fs=sentinel))
    assert utils._s3_fs() is sentinel
    assert fake_s3fs == [], 'Streamlit path built a direct S3FileSystem'


def test_concurrent_callers_share_one_filesystem(fake_s3fs):
    """Streamlit and uvicorn call this from threads; the cache must be locked."""
    seen: list = []

    def worker():
        seen.append(utils._s3_fs())

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({id(fs) for fs in seen}) == 1
    assert len(fake_s3fs) == 1, f'race built {len(fake_s3fs)} clients'


# ─────────────────────────────────────────────────────────────────────────────
# Self-healing — the one risk reuse introduces
# ─────────────────────────────────────────────────────────────────────────────


def test_a_stale_filesystem_is_rebuilt_and_the_op_retried(fake_s3fs):
    """`sb-api` runs for days; a pooled connection can go stale while idle."""
    calls = {'n': 0}

    def flaky(fs):
        calls['n'] += 1
        if calls['n'] == 1:
            raise ConnectionResetError('stale pooled connection')
        return 'recovered'

    assert utils._s3_call(flaky) == 'recovered'
    assert calls['n'] == 2, 'the op was not retried'
    assert len(fake_s3fs) == 2, 'the dead filesystem was not discarded'


def test_a_persistent_failure_propagates_to_the_local_fallback(fake_s3fs):
    """Retry must not swallow a real outage — callers fall back to local disk."""
    def always_fails(fs):
        raise OSError('S3 is genuinely down')

    with pytest.raises(OSError):
        utils._s3_call(always_fails)
    assert len(fake_s3fs) == 2, 'should have tried exactly twice'


def test_the_signal_csv_upload_self_heals_on_a_stale_pool(fake_s3fs, tmp_path,
                                                          monkeypatch):
    """orchestrator's S3 mirror must retry through _s3_call, not a bare _s3_fs().

    This was the only _s3_fs() call site outside utils.py, so it was the only S3
    access that did NOT self-heal after §19 memoized the filesystem for the life
    of the process. It matters more than the count suggests: scanner-cron runs
    for days (a stale idle pool is exactly the failure reuse introduces), this
    sits on the write path for the signal CSVs themselves, and its `except` only
    logs a warning — so the failure mode is the day's signals silently never
    reaching S3, with a successful-looking scan.
    """
    import orchestrator

    puts = []
    fail_first = {'n': 0}

    def put(self, local, remote):
        fail_first['n'] += 1
        if fail_first['n'] == 1:
            raise OSError('connection pool is closed')   # the stale-pool shape
        puts.append((local, remote))

    monkeypatch.setattr(type(utils._s3_fs()), 'put', put, raising=False)
    utils._drop_s3_fs()
    fake_s3fs.clear()          # ignore the instance built just to reach the type

    src = tmp_path / 'signals_swing_20260730_0935.csv'
    src.write_text('Symbol,Quality\nAAPL,PREMIUM\n')

    utils._s3_call(lambda fs: fs.put(str(src), 'bucket/scanner_output/signals/x.csv'))

    assert len(puts) == 1, 'the upload did not survive one stale-pool failure'
    assert len(fake_s3fs) == 2, 'a failed put must discard and rebuild the filesystem'

    # The structural half: no S3 access anywhere may bypass the retry wrapper.
    src_text = pathlib.Path(orchestrator.__file__).read_text()
    assert '_s3_fs().put' not in src_text, 'orchestrator bypasses _s3_call again'


def test_a_successful_op_never_rebuilds(fake_s3fs):
    """The happy path must not pay for the retry machinery."""
    for _ in range(10):
        utils._s3_call(lambda fs: 'ok')
    assert len(fake_s3fs) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Listing staleness — the only fsspec cache shared by a reused instance
# ─────────────────────────────────────────────────────────────────────────────


def test_list_files_invalidates_before_every_listing(fake_s3fs, monkeypatch):
    """Object reads are not stale on a reused fs, but `ls` results are cached.

    `list_files` is the only ls call site and it already invalidated first —
    which is precisely what makes reusing the filesystem safe. If that call is
    ever removed, a long-running process would stop seeing new signal files.
    """
    monkeypatch.setattr(utils, '_is_cloud', lambda: True)

    utils.list_files('scanner_output/signals', 'signals_*.csv')
    utils.list_files('scanner_output/signals', 'signals_*.csv')

    fs = fake_s3fs[0]
    assert len(fake_s3fs) == 1, 'listing rebuilt the filesystem'
    invalidations = [op for op in fs.ops if op[0] == 'invalidate']
    listings = [op for op in fs.ops if op[0] == 'ls']
    assert len(listings) == 2
    assert len(invalidations) == 2, 'ls ran against a possibly stale dircache'
    # Order matters: invalidate must precede its listing.
    assert [op[0] for op in fs.ops] == ['invalidate', 'ls', 'invalidate', 'ls']
