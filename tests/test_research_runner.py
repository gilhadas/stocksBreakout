"""
Tests for research/runner.py — the unattended research agent scheduler, and for the
permission set that constrains the agents it launches.

These pin the fixes made after the 2026-07-24 overnight run, in which 27 ticks produced
only 2 pieces of research: 12 consecutive session-limit failures were retried every 30
minutes, each one first paying for a full yfinance panel refresh.

AC-RR-01  Backoff grows geometrically with consecutive failures and saturates
AC-RR-02  backoff_until() gates only while the window is in the future, and never
          crashes the tick on a malformed/absent timestamp
AC-RR-03  Worker rotation alternates and is NOT reset by a lead run (the lead has its
          own launchd job, so keying rotation off `last_role` starved 'picking')
AC-RR-04  The tick lock is exclusive — the runner and lead labels run the same script
          and concurrent update_panel.py runs silently lose a day's ingest
AC-RR-05  Ledger context is role-scoped, so a worker is told what it already did
AC-RR-06  agent_settings.json denies writes to every live-config file, and deny rules
          are present at all (they are what makes 'propose-only' structural)
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research import runner  # noqa: E402


# ── AC-RR-01 ──────────────────────────────────────────────────────────────────────
def test_backoff_is_geometric_and_saturates():
    before = datetime.now(timezone.utc)
    mins = []
    for fails in range(1, len(runner.BACKOFF_MINUTES) + 1):
        delta = runner.next_backoff(fails) - before
        mins.append(round(delta.total_seconds() / 60))

    assert mins == list(runner.BACKOFF_MINUTES), mins
    assert mins == sorted(mins), "backoff must be non-decreasing"

    # Past the end of the ladder it saturates rather than growing without bound.
    saturated = round((runner.next_backoff(99) - before).total_seconds() / 60)
    assert saturated == runner.BACKOFF_MINUTES[-1]

    # A first failure must not retry on the very next 30-minute tick's heels.
    assert runner.BACKOFF_MINUTES[0] >= 30


# ── AC-RR-02 ──────────────────────────────────────────────────────────────────────
def test_backoff_until_gates_only_on_a_future_window():
    future = (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat()
    assert runner.backoff_until({'retry_after': future}) is not None

    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert runner.backoff_until({'retry_after': past}) is None


@pytest.mark.parametrize('state', [{}, {'retry_after': None}, {'retry_after': 'garbage'}])
def test_backoff_until_never_crashes_the_tick(state):
    """A corrupt state file must degrade to 'run now', never raise — an exception here
    would take down the scheduler for every subsequent tick."""
    assert runner.backoff_until(state) is None


# ── AC-RR-03 ──────────────────────────────────────────────────────────────────────
def test_worker_rotation_alternates():
    assert runner.next_worker_role({}) == 'stops'
    assert runner.next_worker_role({'last_worker_role': 'stops'}) == 'picking'
    assert runner.next_worker_role({'last_worker_role': 'picking'}) == 'stops'


def test_lead_run_does_not_reset_worker_rotation():
    """The regression: `last_role` records lead runs too. Keying rotation off it made
    every tick after the daily lead pick 'stops', starving 'picking'."""
    state = {'last_worker_role': 'stops', 'last_role': 'lead'}
    assert runner.next_worker_role(state) == 'picking'


def test_lead_is_not_in_the_worker_rotation():
    assert 'lead' not in runner.WORKER_ROLES
    assert set(runner.WORKER_ROLES) < set(runner.ROLE_PROMPTS)


# ── AC-RR-04 ──────────────────────────────────────────────────────────────────────
def test_lock_is_exclusive(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'LOCK', tmp_path / '.runner.lock')

    first = runner.acquire_lock()
    assert first is not None, "first acquirer must get the lock"
    try:
        # A second tick in this process would still be a distinct flock attempt on a
        # separate fd; use a subprocess so the check matches the real two-label case.
        code = (
            "import sys; sys.path.insert(0, %r);\n"
            "from research import runner;\n"
            "from pathlib import Path;\n"
            "runner.LOCK = Path(%r);\n"
            "print('GOT' if runner.acquire_lock() is not None else 'BLOCKED')"
            % (str(ROOT), str(tmp_path / '.runner.lock'))
        )
        out = subprocess.run([sys.executable, '-c', code], capture_output=True,
                             text=True, timeout=60)
        assert 'BLOCKED' in out.stdout, f"stdout={out.stdout!r} stderr={out.stderr[-500:]!r}"
    finally:
        os.close(first)

    # Released — the next tick can take it.
    again = runner.acquire_lock()
    assert again is not None
    os.close(again)


# ── AC-RR-05 ──────────────────────────────────────────────────────────────────────
def test_ledger_context_is_role_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'LEDGER', tmp_path)
    (tmp_path / 'decisions.md').write_text('# Decisions\n\nH1 closed as null.\n')
    (tmp_path / 'results.jsonl').write_text(
        json.dumps({'agent': 'worker-stops', 'task': 'STOPS_MARKER'}) + '\n'
        + json.dumps({'agent': 'worker-picking', 'task': 'PICKING_MARKER'}) + '\n'
    )

    stops = runner._recent_ledger_context('stops')
    assert 'H1 closed as null.' in stops, "must carry the decisions tail"
    assert 'STOPS_MARKER' in stops
    assert 'PICKING_MARKER' not in stops, "must not feed a worker the other role's log"

    assert 'PICKING_MARKER' in runner._recent_ledger_context('picking')


def test_ledger_context_survives_an_empty_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'LEDGER', tmp_path)
    assert runner._recent_ledger_context('stops') == ''


# ── AC-RR-06 ──────────────────────────────────────────────────────────────────────
PROTECTED = ['config.py', 'auto_portfolio.py', 'orchestrator.py', 'scanner.py',
             'breakout_scanner.py', 'cron_jobs.txt', 'docker/crontab', '.env']


def test_agent_settings_is_valid_json_and_denies_live_config():
    """`claude -p` SILENTLY IGNORES a settings file that fails validation, so a schema
    slip here would leave the deny rules looking present but inert."""
    cfg = json.loads(runner.AGENT_SETTINGS.read_text())
    deny = cfg['permissions']['deny']
    assert deny, "an empty deny list makes 'propose-only' unenforced"

    for path in PROTECTED:
        assert f'Edit({path})' in deny, f'{path} is editable by an unattended agent'
        assert f'Write({path})' in deny, f'{path} is writable by an unattended agent'


def test_agent_settings_blocks_the_reach_outside_the_repo():
    """Research is local and propose-only: no EC2, no deploys, no pushing."""
    deny = json.loads(runner.AGENT_SETTINGS.read_text())['permissions']['deny']
    for rule in ('Bash(ssh:*)', 'Bash(aws:*)', 'Bash(docker:*)', 'Bash(git push:*)'):
        assert rule in deny, f'missing deny rule: {rule}'


def test_agent_settings_allows_the_ledger_it_must_write():
    allow = json.loads(runner.AGENT_SETTINGS.read_text())['permissions']['allow']
    assert 'Write(research/**)' in allow
    assert 'Edit(research/**)' in allow
