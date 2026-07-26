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
AC-RR-07  Panel staleness is independent of new-file arrival, so open rows keep
          accruing forward bars on a quiet day
AC-RR-08  "New file" comes from an explicit ingested record, not inferred from panel
          rows — an unusable CSV must not read as new forever
AC-RR-09  A candidate ranking model orders WITHIN the quality tier, never over it,
          and unscored signals degrade gracefully
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


# ── Cost controls: pinned model + per-invocation budget cap ────────────────────────
def test_invoke_agent_pins_model_and_budget(tmp_path, monkeypatch):
    """2026-07-26 finding: with no --model, `claude -p` silently inherited whatever the
    human's global CLI default was (Opus), burning ~46.6M cache-read tokens across 8
    real invocations in two days. --max-budget-usd is the per-invocation circuit
    breaker the invocation-count cap in budget.json can't provide (a single tick ran
    94 turns / ~10.6M cache-read tokens and still didn't count against that cap,
    because it exited nonzero)."""
    monkeypatch.setattr(runner, 'LEDGER', tmp_path)
    monkeypatch.setattr(runner, 'AGENT_SETTINGS', tmp_path / 'missing_settings.json')

    captured = {}

    def fake_run(cmd, **kwargs):
        captured['cmd'] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout='ok', stderr='')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    runner.invoke_agent('stops')

    cmd = captured['cmd']
    assert '--model' in cmd, "unattended agents must not inherit the interactive default"
    assert cmd[cmd.index('--model') + 1] == runner.AGENT_MODEL
    assert '--max-budget-usd' in cmd, "a runaway session must have its own cost cap"
    assert cmd[cmd.index('--max-budget-usd') + 1] == str(runner.AGENT_MAX_BUDGET_USD)


# ── AC-RR-07 ──────────────────────────────────────────────────────────────────────
def test_panel_staleness_is_independent_of_new_files():
    """update_panel has two jobs: append new files, and ADVANCE open rows. The tick
    used to return early whenever no new file had landed, so job 2 never ran on a
    quiet day and forward metrics silently went stale."""
    assert runner.panel_is_stale({}) is True, "no record yet => stale"
    assert runner.panel_is_stale({'last_panel_update': 'not-a-timestamp'}) is True

    fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert runner.panel_is_stale({'last_panel_update': fresh}) is False

    old = (datetime.now(timezone.utc)
           - timedelta(hours=runner.PANEL_REFRESH_HOURS + 1)).isoformat()
    assert runner.panel_is_stale({'last_panel_update': old}) is True


# ── AC-RR-08 ──────────────────────────────────────────────────────────────────────
def test_new_files_come_from_the_ingested_record_not_panel_rows(tmp_path, monkeypatch):
    """The regression: load_signal_rows() silently drops an empty / Symbol-less CSV,
    so it never reaches panel.source_file and the old inference reported it as new on
    every tick forever — 12 wasted agent invocations on 2026-07-24."""
    record = tmp_path / 'ingested_files.json'
    on_disk = ['signals_swing_20260401.csv',
               'signals_swing_20260402.csv',      # <- unusable: yields no panel rows
               'signals_longterm_20260401.csv']

    # new_signal_files() does `from utils import list_files` at call time, so patching
    # the source module is what takes effect.
    monkeypatch.setattr(runner, 'INGESTED', record)
    monkeypatch.setattr('utils.list_files', lambda *a, **k: on_disk)

    # update_panel examined all three (one produced no rows) and recorded that fact.
    record.write_text(json.dumps({'modes': ['swing', 'longterm'], 'files': on_disk}))
    assert runner.new_signal_files() == [], \
        "a file that yields no rows must still count as seen"

    # A genuinely new file is still detected.
    on_disk.append('signals_swing_20260403.csv')
    assert runner.new_signal_files() == ['signals_swing_20260403.csv']


def test_corrupt_ingested_record_falls_back_instead_of_crashing(tmp_path, monkeypatch):
    record = tmp_path / 'ingested_files.json'
    record.write_text('{not json')
    monkeypatch.setattr(runner, 'INGESTED', record)
    monkeypatch.setattr(runner, 'PANEL', tmp_path / 'missing.parquet')
    monkeypatch.setattr('utils.list_files', lambda *a, **k: ['signals_swing_20260401.csv'])

    assert runner.new_signal_files() == ['signals_swing_20260401.csv']


# ── AC-RR-09 ──────────────────────────────────────────────────────────────────────
def _sig(symbol, quality='PREMIUM'):
    return {'date': '2026-04-01', 'symbol': symbol, 'quality': quality,
            'win_prob': 0, 'rr': 2.5, 'sma_dist_pct': 5, 'vol': 1}


def test_rank_scores_order_within_tier_never_over_it(tmp_path):
    from backtest_regime_compare import _pooled_cap, load_rank_scores

    sigs = [_sig('AAA'), _sig('BBB'), _sig('CCC'), _sig('GGG', 'GOLD')]

    f = tmp_path / 'scores.csv'
    f.write_text('date,symbol,score\n2026-04-01,CCC,9.0\n2026-04-01,AAA,1.0\n')
    out = [s['symbol'] for s in _pooled_cap(sigs, max_per_day=4,
                                            rank_scores=load_rank_scores(str(f)))]

    assert out[0] == 'GGG', "quality tier must stay primary — GOLD>PREMIUM is the one " \
                            "robust thing the live panel showed the ranking does"
    assert out.index('CCC') < out.index('AAA'), "higher score ranks earlier within tier"
    assert out.index('AAA') < out.index('BBB'), "unscored signals sort behind scored ones"


def test_rank_scores_absent_leaves_default_order_untouched():
    """The default path must be byte-identical — every documented baseline depends on it."""
    from backtest_regime_compare import _pooled_cap

    sigs = [_sig('AAA'), _sig('BBB'), _sig('CCC'), _sig('GGG', 'GOLD')]
    assert ([s['symbol'] for s in _pooled_cap(sigs, max_per_day=4)]
            == [s['symbol'] for s in _pooled_cap(sigs, max_per_day=4, rank_scores=None)])


def test_rank_scores_rejects_a_bad_schema(tmp_path):
    from backtest_regime_compare import load_rank_scores

    f = tmp_path / 'bad.csv'
    f.write_text('date,ticker\n2026-04-01,X\n')
    with pytest.raises(ValueError, match='missing column'):
        load_rank_scores(str(f))


# ── The promotion gate must target the population live actually trades ────────────
def _confirm_backtest():
    """Import the gate module by path (research/ has no package __init__)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        'confirm_backtest', ROOT / 'research' / 'confirm_backtest.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_confirm_backtest_population_is_a_choice_not_a_hardcode():
    """--no-tc was hardcoded until 2026-07-25. That was a DELIBERATE 'run the champion
    config' choice, not a bug — but it was never reconciled against H4, so the gate
    could not confirm a panel-derived rule on live's 87%-TREND_CONFIRM population.
    Asserts on the real runtime value, not on source text."""
    m = _confirm_backtest()
    assert '--no-tc' not in m.BASE_ARGS, \
        'population must be selected via --population, never hardcoded in BASE_ARGS'
    assert '--realistic-sizing' in m.BASE_ARGS, '§11 requires judging on the realistic arm'
    assert '--trades-log' in m.BASE_ARGS, 'needed to report the realized signal-type mix'


def test_confirm_backtest_champion_population_restores_no_tc():
    """The documented §7-§13 baselines are only reproducible with --no-tc, so that
    path must remain available — it is a different population, not a wrong one."""
    m = _confirm_backtest()
    import inspect

    src = inspect.getsource(m.run)
    assert "population == 'champion'" in src and "--no-tc" in src, \
        '--population champion must still pass --no-tc'


def test_confirm_backtest_log_paths_are_consistent():
    """--skip-baseline must look for exactly what run() writes (they diverged for the
    ranking arm: 'multNone' vs 'rank')."""
    m = _confirm_backtest()
    assert m.log_path('baseline', 'live', 2.0, '2022,2024').name \
        == 'baseline_live_mult2.0_2022-2024.log'
    assert m.log_path('baseline', 'live', None, '2022,2024').name \
        == 'baseline_live_rank_2022-2024.log'

