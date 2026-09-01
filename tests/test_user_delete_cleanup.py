"""
Deleting a user must not orphan their portfolio.

WHY THIS EXISTS
---------------
The users DB is a single `users` table — identity and nothing else. A user's
portfolio is stored *only* as JSON under `scanner_output/portfolio/<user_id>/`,
so `db.delete(user)` cascades to nothing and the book simply stays behind,
indistinguishable from a live one.

That is not hypothetical. Two orphaned books (16 and 17 open positions, ~$99k
deployed each) outlived their users long enough to be read back as production
state and reported as "the live books" during an unrelated investigation. The
data was fine; what it *meant* was wrong.

Three properties, and the last one is the one that makes the whole thing safe:

1. **Archived, not deleted** — a book is the only record a user ever traded.
2. **Copy verified before the original is removed** — `save_json` swallows S3
   errors, so a successful-looking write is not proof a copy exists.
3. **Failure aborts the user deletion** — the row is the only thing tying the
   files to a person. Delete it first and cleanup becomes archaeology, which is
   precisely how the orphans above became unattributable.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import auto_portfolio as ap
import utils


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Anchor every path helper at a tmp root and keep S3 out of it."""
    monkeypatch.setattr(utils, '_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setattr(utils, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(utils, '_is_cloud', lambda: False)
    return tmp_path


def _book(root: Path, user_id: str, name: str = 'auto_portfolio.json',
          positions: int = 1) -> Path:
    d = root / 'scanner_output' / 'portfolio' / user_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(json.dumps({
        'capital': 100000,
        'positions': [{'symbol': f'SYM{i}', 'cost': 5000} for i in range(positions)],
    }))
    return p


# ─────────────────────────────────────────────────────────────────────────────
# 1. Archived, not deleted
# ─────────────────────────────────────────────────────────────────────────────


def test_book_moves_to_the_deleted_namespace(sandbox):
    src = _book(sandbox, 'u1', positions=3)

    result = ap.archive_user_portfolio('u1')

    dst = sandbox / 'scanner_output' / 'portfolio' / '_deleted' / 'u1' / 'auto_portfolio.json'
    assert dst.exists(), "book was not archived"
    assert not src.exists(), "original left behind — the orphan survives"
    assert result['archived'] == ['auto_portfolio.json']
    # The point of archiving rather than deleting: the positions survive.
    assert len(json.loads(dst.read_text())['positions']) == 3


def test_every_file_in_the_directory_is_moved(sandbox):
    """A user dir holds more than one book — manual, scalp and recalc backups."""
    for name in ('auto_portfolio.json', 'portfolio.json',
                 'scalp_portfolio.json', 'pre_recalculate_20260802T160558.json'):
        _book(sandbox, 'u2', name=name)

    result = ap.archive_user_portfolio('u2')

    assert len(result['archived']) == 4, f"only moved {result['archived']}"
    live = sandbox / 'scanner_output' / 'portfolio' / 'u2'
    assert not any(live.glob('*.json')), "files left in the live namespace"


def test_missing_directory_is_a_no_op(sandbox):
    """Idempotence: re-running after a successful archive must not raise."""
    assert ap.archive_user_portfolio('never-existed')['archived'] == []


def test_rerun_after_partial_failure_finishes_the_job(sandbox):
    _book(sandbox, 'u3', name='a.json')
    _book(sandbox, 'u3', name='b.json')

    real_delete = utils.delete_file
    calls: list[str] = []

    def _fail_on_b(path, s3_path=None):
        calls.append(path)
        if path.endswith('b.json'):
            raise OSError('transient')
        return real_delete(path, s3_path)

    utils.delete_file = _fail_on_b
    try:
        with pytest.raises(RuntimeError):
            ap.archive_user_portfolio('u3')
    finally:
        utils.delete_file = real_delete

    # a.json is gone, b.json survived the failure — now the retry completes it.
    result = ap.archive_user_portfolio('u3')
    assert result['archived'] == ['b.json']
    assert not any((sandbox / 'scanner_output' / 'portfolio' / 'u3').glob('*.json'))


# ─────────────────────────────────────────────────────────────────────────────
# 2. Copy verified before the original is removed
# ─────────────────────────────────────────────────────────────────────────────


def test_original_survives_when_the_copy_cannot_be_read_back(sandbox, monkeypatch):
    """save_json only logs on S3 failure, so a write is not proof of a copy."""
    src = _book(sandbox, 'u4')
    real_load = utils.load_json
    monkeypatch.setattr(
        utils, 'load_json',
        lambda p, s3_path=None: None if '_deleted' in p else real_load(p),
    )

    with pytest.raises(RuntimeError, match='copy not verified'):
        ap.archive_user_portfolio('u4')

    assert src.exists(), "original deleted despite an unverified copy — data loss"


def test_unreadable_source_is_reported_not_skipped_silently(sandbox):
    """Corrupt JSON raises out of load_json; missing JSON returns None. Both
    must name the file and leave it alone rather than pass silently."""
    d = sandbox / 'scanner_output' / 'portfolio' / 'u5'
    d.mkdir(parents=True)
    (d / 'auto_portfolio.json').write_text('{ not json')

    with pytest.raises(RuntimeError, match='auto_portfolio.json'):
        ap.archive_user_portfolio('u5')

    assert (d / 'auto_portfolio.json').exists()


def test_unloadable_source_is_reported(sandbox, monkeypatch):
    """The None branch — load_json returns None rather than raising."""
    _book(sandbox, 'u6')
    monkeypatch.setattr(utils, 'load_json', lambda p, s3_path=None: None)

    with pytest.raises(RuntimeError, match='unreadable'):
        ap.archive_user_portfolio('u6')

    assert (sandbox / 'scanner_output' / 'portfolio' / 'u6' / 'auto_portfolio.json').exists()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Failure aborts the user deletion
# ─────────────────────────────────────────────────────────────────────────────


class _FakeUser:
    def __init__(self):
        self.id, self.email = 'u9', 'x@example.com'


class _FakeDB:
    def __init__(self):
        self.deleted, self.committed = [], False

    def query(self, _model):
        return self

    def filter(self, *_a, **_k):
        return self

    def first(self):
        return _FakeUser()

    def delete(self, obj):
        self.deleted.append(obj)

    def commit(self):
        self.committed = True


@pytest.fixture
def hooks(monkeypatch):
    from trading_api_kit import admin_routes
    monkeypatch.setattr(admin_routes, '_delete_hooks', [])
    return admin_routes


def test_hook_runs_before_the_row_is_deleted(hooks):
    from fastapi import HTTPException  # noqa: F401  (import parity with route)
    db, order = _FakeDB(), []

    hooks.register_user_delete_hook(lambda uid, email: order.append(('hook', uid)))
    original_delete = db.delete
    db.delete = lambda obj: (order.append(('db', obj.id)), original_delete(obj))[1]

    hooks.delete_user('u9', db=db)

    assert [step for step, _ in order] == ['hook', 'db'], (
        f"cleanup must precede the delete, got {order}")
    assert db.committed


def test_failing_hook_leaves_the_user_intact(hooks):
    from fastapi import HTTPException
    db = _FakeDB()

    def _boom(uid, email):
        raise RuntimeError('S3 unreachable')

    hooks.register_user_delete_hook(_boom)

    with pytest.raises(HTTPException) as exc:
        hooks.delete_user('u9', db=db)

    assert exc.value.status_code == 500
    assert 'not deleted' in exc.value.detail
    assert db.deleted == [], "user deleted despite failed cleanup — orphan created"
    assert not db.committed


def test_delete_still_works_with_no_hooks_registered(hooks):
    db = _FakeDB()
    assert hooks.delete_user('u9', db=db) == {'ok': True}
    assert db.committed


def test_host_app_registers_the_archive_hook(monkeypatch):
    """The kit is app-agnostic; the wiring lives in the host and must exist.

    Asserted behaviourally, not by grepping the source: an earlier version of
    this test looked for the string 'register_user_delete_hook' in api/server.py
    and passed even with the call deleted, because the *import* line matches too.
    """
    from trading_api_kit import admin_routes
    import api.server  # noqa: F401 — registration happens at import time

    assert admin_routes._delete_hooks, (
        "api/server.py registered no delete hook — deletions will silently "
        "orphan portfolios again")

    seen: list[str] = []
    monkeypatch.setattr(ap, 'archive_user_portfolio', lambda uid: seen.append(uid))
    for hook in admin_routes._delete_hooks:
        hook('uX', 'e@example.com')

    assert 'uX' in seen, "registered hook does not reach archive_user_portfolio"
