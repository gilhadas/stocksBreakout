#!/usr/bin/env python3
"""Recalculate jobs must be scoped to the user who started them.

WHY THIS EXISTS
---------------
GET /portfolio/recalculate/status/{job_id} was authed (get_current_user) but
not SCOPED: _RECALC_JOBS was a flat {job_id: {...}} dict with no owner check,
so any logged-in user who obtained another user's job_id (browser history,
server logs, a shared screenshot, a Referer header) could poll it and read
that user's recalculate summary — positions, P&L, symbols, dollar amounts.
job_id is a bare uuid4 hex fragment; nothing else gates this endpoint.

Fixed by stamping each job with the id of the user who created it and
checking it in the status endpoint. A mismatch returns the SAME 404 as an
unknown job_id — a distinct 403 would itself confirm to an attacker that
the id exists and belongs to someone else.

Also covers the four `timezone(timedelta(hours=-4))` instances in this file,
replaced with ZoneInfo('America/New_York') — a fixed UTC-4 offset is simply
wrong for roughly a third of the year (EST is UTC-5), which showed up as an
hour of skew in timestamps once DST ends in November.

Run:
    python -m pytest tests/test_recalc_job_scoping.py -v
"""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import api.server as srv


def _user(uid: str) -> SimpleNamespace:
    return SimpleNamespace(id=uid, email=f"{uid}@example.com")


@pytest.fixture(autouse=True)
def clean_jobs():
    """_RECALC_JOBS is module-level global state — must not leak between tests."""
    srv._RECALC_JOBS.clear()
    yield
    srv._RECALC_JOBS.clear()


def test_owner_can_read_their_own_job_status():
    srv._RECALC_JOBS["job1"] = {
        "status": "done", "result": {"positions": 3}, "user_id": "user-a",
    }
    result = srv.portfolio_recalculate_status("job1", current_user=_user("user-a"))
    assert result["status"] == "done"
    assert result["result"] == {"positions": 3}


def test_a_different_user_cannot_read_the_job_even_knowing_its_id():
    srv._RECALC_JOBS["job1"] = {
        "status": "done", "result": {"positions": 3, "pnl": 4210.55}, "user_id": "user-a",
    }
    with pytest.raises(Exception) as exc_info:
        srv.portfolio_recalculate_status("job1", current_user=_user("user-b"))
    assert getattr(exc_info.value, "status_code", None) == 404, (
        "a job belonging to another user must be unreadable, not just relabeled")


def test_unknown_job_and_someone_elses_job_return_the_identical_response():
    """A 403 (vs 404) on a real-but-not-mine job would itself leak that the
    id exists and belongs to someone — the two cases must be indistinguishable."""
    srv._RECALC_JOBS["real-job"] = {"status": "done", "result": {}, "user_id": "user-a"}

    with pytest.raises(Exception) as unknown_exc:
        srv.portfolio_recalculate_status("does-not-exist", current_user=_user("user-b"))
    with pytest.raises(Exception) as foreign_exc:
        srv.portfolio_recalculate_status("real-job", current_user=_user("user-b"))

    assert unknown_exc.value.status_code == foreign_exc.value.status_code == 404
    assert unknown_exc.value.detail == foreign_exc.value.detail


def test_recalculate_endpoint_stamps_the_job_with_its_creator(monkeypatch):
    """Structural check on the write side: the endpoint that CREATES a job
    must record who created it, or the read-side check above has nothing to
    compare against."""
    monkeypatch.setattr(srv, "_book_of", lambda req: "control")

    class _StubThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            pass  # never actually run recalculate() — this test is about job_id ownership

    import threading as _threading
    monkeypatch.setattr(_threading, "Thread", _StubThread)

    req = srv.RecalculateRequest(min_date=None, position_pct=None, book=None)
    resp = srv.portfolio_recalculate_endpoint(req, current_user=_user("user-a"))

    job_id = resp["job_id"]
    assert srv._RECALC_JOBS[job_id]["user_id"] == "user-a"
    # And the owner can immediately poll it; a different user still cannot.
    assert srv.portfolio_recalculate_status(job_id, current_user=_user("user-a"))["status"] == "running"
    with pytest.raises(Exception) as exc_info:
        srv.portfolio_recalculate_status(job_id, current_user=_user("user-b"))
    assert exc_info.value.status_code == 404


def test_timeout_path_still_preserves_ownership():
    """The TTL branch rewrites the job dict in place — it must not drop the
    user_id stamp while doing so."""
    srv._RECALC_JOBS["stale-job"] = {
        "status": "running", "started": time.time() - srv._RECALC_JOB_TTL - 1,
        "user_id": "user-a",
    }
    result = srv.portfolio_recalculate_status("stale-job", current_user=_user("user-a"))
    assert result["status"] == "error"
    assert result["error"] == "timeout"
    assert srv._RECALC_JOBS["stale-job"]["user_id"] == "user-a"


# ── timezone fix ──────────────────────────────────────────────────────────────

def test_no_fixed_utc_offset_timezones_remain():
    """Guards against a fixed timezone(timedelta(hours=-4)) creeping back in —
    it is wrong for roughly a third of the year once DST ends (EST is UTC-5,
    not UTC-4). Every NY timestamp in this file must come from ZoneInfo."""
    src = Path(srv.__file__).read_text()
    assert 'timedelta(hours=-4)' not in src, (
        'a fixed UTC-4 offset is back — this is wrong outside EDT (roughly '
        "March-November); use ZoneInfo('America/New_York') instead")
    assert src.count("ZoneInfo('America/New_York')") >= 4, (
        'expected the four manual-portfolio timestamp sites to use ZoneInfo')
