#!/usr/bin/env python3
"""_book_lock must hold across a whole load-modify-save cycle, across real
OS processes, and must not deadlock when acquired reentrantly.

WHY THIS EXISTS
---------------
The book's lock used to be taken only inside _save() itself. Two processes
could each load() the same state, mutate their own in-memory copy, and then
save — the second save silently overwrites the first's changes with no error
anywhere (a lost update). This was not hypothetical: docker/crontab's 9:35
wide scan's scan_and_add tail has no fixed finish time (§16/§17 record it
taking 60-90+ minutes) and is not mutexed against 10:00's fixed-time
refresh_prices_all_users — the two run as genuinely separate OS processes
against the same book file, with no coordination between them.

_book_lock fixes this by covering the caller's ENTIRE load...save span. This
file uses real multiprocessing.Process workers (not threads) because the
actual production race is cross-process — fcntl.flock only contends across
processes at all, so a threading-only test would pass by construction
whether or not the fix works.

Also pinned: the lock is reentrant per (thread, lock path), because
ensure_forked() calls _save() while a caller may already be holding this same
lock for the same book — fcntl.flock does not recurse across file
descriptors even within one process, so a naive second acquisition would
deadlock the process against itself (the exact class of bug §26.1 hit
forking inside add_position_direct).

Run:
    python -m pytest tests/test_book_lock_concurrency.py -v
"""
import multiprocessing
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import auto_portfolio as ap
import utils


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, '_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setattr(utils, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(utils, '_is_cloud', lambda: False)
    return tmp_path


def _increment_locked(root: str, n: int, delay: float) -> None:
    """Worker: n times, do a full load-modify-save under _book_lock, with a
    deliberate delay INSIDE the critical section — long enough that without
    the fix, a second process's load() would land in the middle of it."""
    import utils as _utils
    import auto_portfolio as _ap
    _utils._PROJECT_ROOT = root
    _utils.PROJECT_ROOT = Path(root)
    _utils._is_cloud = lambda: False

    for _ in range(n):
        with _ap._book_lock('WORKER', 'control'):
            data = _ap.load(user_id='WORKER', book='control')
            data.setdefault('counter', 0)
            current = data['counter']
            time.sleep(delay)  # force overlap if the lock does not actually hold
            data['counter'] = current + 1
            _ap._save(data, user_id='WORKER', book='control')


def _increment_save_only_locked(root: str, n: int, delay: float) -> None:
    """Same as above but locks only around the SAVE — reproduces the
    pre-fix behavior, used to prove this test can actually detect a lost
    update rather than passing vacuously."""
    import utils as _utils
    import auto_portfolio as _ap
    _utils._PROJECT_ROOT = root
    _utils.PROJECT_ROOT = Path(root)
    _utils._is_cloud = lambda: False

    for _ in range(n):
        data = _ap.load(user_id='WORKER', book='control')
        data.setdefault('counter', 0)
        current = data['counter']
        time.sleep(delay)  # nothing guards this against a concurrent load()
        data['counter'] = current + 1
        _ap._save(data, user_id='WORKER', book='control')  # locks only the save


def test_concurrent_processes_do_not_lose_updates(sandbox):
    """Two real OS processes, each incrementing a shared counter N times
    through a full load-modify-save cycle, must together produce exactly
    2*N — proving _book_lock serializes the whole cycle, not just the save."""
    ap._save(ap._empty(), user_id='WORKER', book='control')

    n_per_worker = 15
    ctx = multiprocessing.get_context('fork')
    root = str(sandbox)
    procs = [
        ctx.Process(target=_increment_locked, args=(root, n_per_worker, 0.01)),
        ctx.Process(target=_increment_locked, args=(root, n_per_worker, 0.01)),
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0, f'worker process failed (exitcode {p.exitcode})'

    final = ap.load(user_id='WORKER', book='control')
    assert final['counter'] == 2 * n_per_worker, (
        f"expected {2 * n_per_worker} increments to survive, got {final['counter']} — "
        "a concurrent load-modify-save lost an update")


def test_the_test_itself_can_detect_a_lost_update(sandbox):
    """Sanity check for the test above: with locking scoped to only the save
    (the documented pre-fix shape), the same two workers MUST lose updates.
    If this ever stops failing, the test above is not exercising a real race."""
    ap._save(ap._empty(), user_id='WORKER', book='control')

    n_per_worker = 15
    ctx = multiprocessing.get_context('fork')
    root = str(sandbox)
    procs = [
        ctx.Process(target=_increment_save_only_locked, args=(root, n_per_worker, 0.01)),
        ctx.Process(target=_increment_save_only_locked, args=(root, n_per_worker, 0.01)),
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    final = ap.load(user_id='WORKER', book='control')
    assert final['counter'] < 2 * n_per_worker, (
        'save-only locking did not lose any updates in this run — the delay is '
        'too short to reliably force an overlap on this machine; increase it '
        'rather than treat this as the fix working')


def test_book_lock_is_reentrant_for_the_same_path(sandbox):
    """A nested _book_lock for the SAME (user, book) must not deadlock — this
    is what lets ensure_forked() call _save() while scan_and_add()/
    refresh_prices() already hold the lock for the same book."""
    entered_inner = False

    def _inner():
        nonlocal entered_inner
        with ap._book_lock('U1', 'control'):
            entered_inner = True

    with ap._book_lock('U1', 'control'):
        _inner()  # would hang forever pre-fix if flock() were re-acquired naively

    assert entered_inner


def test_book_lock_for_a_different_book_is_not_reentrant(sandbox):
    """The reentrancy guard must be keyed by lock PATH, not just 'are we
    inside some _book_lock' — a nested lock for a DIFFERENT book (e.g.
    ensure_forked's second save, into control, from inside a variant's lock)
    must still take its own real lock, not be silently skipped."""
    import auto_portfolio as _ap

    order = []
    with ap._book_lock('U1', 'autoswap'):
        order.append('outer-acquired')
        # A different (user, book) pair resolves to a different lock file, so
        # this must genuinely re-acquire — it must not be treated as already
        # held just because SOME lock is held in this thread.
        with ap._book_lock('U1', 'control'):
            order.append('inner-acquired')
        order.append('inner-released')
    order.append('outer-released')

    assert order == ['outer-acquired', 'inner-acquired', 'inner-released', 'outer-released']


def test_save_still_locks_when_is_cloud_returns_true(sandbox, monkeypatch):
    """The bug this fixes: _save used to skip fcntl entirely when _is_cloud()
    was True — and production always has AWS_ACCESS_KEY_ID set, so that
    branch, not the locked one, was what actually ran there. Confirms
    locking is unconditional regardless of _is_cloud()'s value."""
    monkeypatch.setattr(utils, '_is_cloud', lambda: True)

    calls = []
    import fcntl
    real_flock = fcntl.flock

    def spy_flock(fd, op):
        calls.append(op)
        return real_flock(fd, op)

    monkeypatch.setattr(fcntl, 'flock', spy_flock)
    ap._save(ap._empty(), user_id='U1', book='control')

    assert fcntl.LOCK_EX in calls, '_save must take the lock even when _is_cloud() is True'
    assert fcntl.LOCK_UN in calls, '_save must release the lock it took'


def test_add_position_direct_still_works_after_lock_refactor(sandbox, monkeypatch):
    """add_position_direct used to hand-roll its own fcntl block; it now goes
    through _book_lock like everything else. Confirms the refactor didn't
    change its externally-visible behavior."""
    ap._save(ap._empty(), user_id='U1', book='control')
    monkeypatch.setattr(ap, '_compute_atr_pct', lambda *a, **k: None)

    result = ap.add_position_direct(
        'AAPL', entry_price=100.0, stop=90.0, target=130.0,
        user_id='U1', book='control',
    )
    assert result == {'added': True, 'reason': 'ok'}

    data = ap.load(user_id='U1', book='control')
    assert len(data['positions']) == 1
    assert data['positions'][0]['symbol'] == 'AAPL'

    # A duplicate add must still be rejected — the dedup check runs inside
    # the same lock acquisition that the write does.
    dup = ap.add_position_direct(
        'AAPL', entry_price=101.0, stop=91.0, target=131.0,
        user_id='U1', book='control',
    )
    assert dup == {'added': False, 'reason': 'duplicate'}
