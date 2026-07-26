#!/usr/bin/env python3
"""
Research runner — one tick per invocation. launchd schedules it; it does NOT loop.

Single-shot by design: a KeepAlive daemon with its own loop can wedge in a state
nothing observes, and a wedged research daemon looks identical to an idle one.
A tick either does its work and exits 0, or fails loudly and gets retried.

Each tick:
  0. Take an exclusive lock — the runner and lead are separate launchd labels running
     this same script, and two concurrent update_panel.py runs would silently lose a
     day's ingest (read -> mutate -> to_parquet overwrite, last writer wins).
  1. Detect signal CSVs update_panel.py has never SEEN (from its explicit
     ingested_files.json record — NOT by differencing against panel.source_file,
     which reported an unusable CSV as new forever).
  2. Refresh the panel if there are new files OR the panel is stale
     (PANEL_REFRESH_HOURS). These are INDEPENDENT of step 3: update_panel's second
     job is advancing forward metrics on rows still accruing bars, which must happen
     on quiet days too.
  3. If there is genuinely new signal data -> invoke the next queued worker agent via
     `claude -p`, under research/agent_settings.json (which is what actually enforces
     the propose-only rule — deny beats allow, and `-p` cannot prompt).
  4. Record the tick in budget.json; refuse to spend past the cap.

A failed invocation costs no budget and triggers geometric backoff (30m -> 8h); after
MAX_CONSECUTIVE_FAILURES the runner parks itself rather than grinding.

Roles are plain markdown in research/prompts/. The lead runs from its own launchd
schedule (once daily), not from here.

Usage:
    python research/runner.py                 # one tick
    python research/runner.py --dry-run       # report only
    python research/runner.py --role stops    # force a specific worker
    python research/runner.py --status        # print state/budget, invoke nothing
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RESEARCH = ROOT / 'research'
LEDGER = RESEARCH / 'ledger'
PROMPTS = RESEARCH / 'prompts'
PANEL = RESEARCH / 'panel' / 'panel.parquet'
INGESTED = RESEARCH / 'panel' / 'ingested_files.json'
STATE = LEDGER / 'runner_state.json'
BUDGET = LEDGER / 'budget.json'
LOCK = LEDGER / '.runner.lock'

# Permission set for unattended agents. `claude -p` cannot prompt, so anything not
# pre-approved is silently DENIED; and its deny rules are what actually enforce
# CLAUDE.md §14's "live config is propose-only" (previously carried by prompt text
# alone). Kept separate from .claude/settings.local.json so the human's interactive
# sessions are unaffected. Verified empirically: a Write to config.py is blocked,
# a Write under research/ succeeds, headless, with no prompt.
AGENT_SETTINGS = RESEARCH / 'agent_settings.json'

# Without an explicit --model, `claude -p` inherits whatever the human's global CLI
# default happens to be at invocation time — found 2026-07-26 to be Opus, which ran
# every tick on 07-24/07-25 (~46.6M cache-read tokens in two days across 8 real
# invocations). This work — panel stats, sweeps, structured writeups against a
# prescriptive guardrails doc — doesn't need Opus-level reasoning, and Opus is
# ~5x Sonnet's cost for the same tokens. Pinned so a change to the interactive
# default never silently changes what the unattended jobs cost.
AGENT_MODEL = 'claude-sonnet-5'

# budget.json's invocation cap counts invocations, not cost: 2026-07-24's worst tick
# ran 94 turns / ~10.6M cache-read tokens and still exited nonzero (didn't even count
# against the cap). --max-budget-usd is `claude -p`'s own circuit breaker, per
# invocation.
#
# Raised from $5 -> $12 on 2026-07-26: the first real 'lead' invocation (a heavier
# role — it audits both workers' full output against a 12.6k-char prompt, not just
# one focused task) needed headroom past $5. The 8 real worker ticks measured
# 07-24/07-25, translated to Sonnet pricing, ranged $2-$9.57 (avg $8.36) -- several
# would already have exceeded $5. $12 covers that observed range with margin instead
# of routinely truncating a session before it finishes its work.
AGENT_MAX_BUDGET_USD = 12.0

ROLE_PROMPTS = {'stops': 'worker_stops.md', 'picking': 'worker_picking.md',
                'lead': 'lead.md'}
WORKER_ROLES = ('stops', 'picking')
CLAUDE_BIN = Path.home() / '.local' / 'bin' / 'claude'

# Backoff after a failed invocation. The overnight run of 2026-07-24 burned 12
# consecutive ticks on session-limit rejections, each one first paying for a full
# yfinance panel refresh. Failures are almost always capacity, which does not clear
# in 30 minutes — so back off geometrically instead of retrying every tick.
BACKOFF_MINUTES = (30, 60, 120, 240, 480)
MAX_CONSECUTIVE_FAILURES = 8

# How long open rows may go without a forward-metric advance before the panel is
# refreshed even though no new signal file landed. Roughly daily: the measurement
# only changes when a new daily bar exists, so more often is wasted yfinance traffic.
PANEL_REFRESH_HOURS = 20


def _load_json(p: Path, default: dict) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return dict(default)


def _save_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


# Modes the panel actually ingests (must match update_panel.py's default). daytrade
# signals are NOT tracked (daytrade is retired), so they must be excluded here too —
# otherwise ~283 daytrade CSVs look perpetually "new", the panel update ignores them,
# and the runner invokes an agent every single tick on no real new data.
PANEL_MODES = ('swing', 'longterm')


def new_signal_files() -> list[str]:
    """Signal CSVs on disk/S3, in tracked modes, that update_panel has never SEEN.

    "Seen" comes from research/panel/ingested_files.json, written by update_panel.py
    after every successful run — NOT from differencing against panel.source_file.
    A CSV that load_signal_rows() drops (empty, or no Symbol column) produces no
    panel rows, so the old inference reported it as new on every tick, forever. That
    is what the ~283 daytrade CSVs did on 2026-07-24: 12 agent invocations burned on
    no real new data. Falls back to the old inference only when the record does not
    exist yet (first run after this change).
    """
    import pandas as pd
    from utils import list_files

    available = {f for f in list_files('scanner_output/signals', 'signals_*.csv')
                 if f.split('_')[1:2] and f.split('_')[1] in PANEL_MODES}

    if INGESTED.exists():
        try:
            seen = set(json.loads(INGESTED.read_text()).get('files', []))
            return sorted(available - seen)
        except Exception as e:
            print(f"  WARNING: {INGESTED.name} unreadable ({e}); falling back to panel rows")

    if not PANEL.exists():
        return sorted(available)
    known = set(pd.read_parquet(PANEL, columns=['source_file'])['source_file'].unique())
    return sorted(available - known)


def panel_is_stale(state: dict) -> bool:
    """True if open rows have not been advanced within PANEL_REFRESH_HOURS.

    update_panel.py has two jobs: append new files, and ADVANCE forward metrics on
    rows still accruing bars. The tick used to return early whenever no new file had
    landed, so job 2 never ran on a quiet day and open rows silently went stale. Panel
    freshness is decoupled from agent invocation: the panel refreshes on its own
    cadence, agents are still only invoked on genuinely new data.
    """
    last = state.get('last_panel_update')
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - when > timedelta(hours=PANEL_REFRESH_HOURS)


def lead_run_is_redundant(state: dict, fresh: list) -> bool:
    """True if a forced 'lead' invocation would have nothing new to audit.

    Unlike the worker roles (which only ever run when new signal data justifies it),
    research-lead's launchd job fires on a fixed daily clock with --force,
    unconditionally. Production cron doesn't scan on weekends, so every Saturday and
    Sunday this would otherwise re-run a full-cost ($2-12) audit of literally
    unchanged state -- found 2026-07-26 when the first successful lead run (a
    Sunday morning manual trigger) was about to be followed, hours later, by the
    same day's regular 18:47 fire with zero new signals and zero new ledger writes
    in between.
    """
    if fresh:
        return False
    last = state.get('last_lead_run')
    if not last:
        return False  # never run before -> let it run
    try:
        since = datetime.fromisoformat(last)
    except ValueError:
        return False  # can't tell -> don't block a real audit
    for name in ('decisions.md', 'results.jsonl'):
        p = LEDGER / name
        if p.exists() and datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc) > since:
            return False
    return True


def acquire_lock():
    """Exclusive, non-blocking. Returns the held fd, or None if another tick owns it.

    launchd will not run two copies of the SAME label, but `research-runner` and
    `research-lead` are different labels running this same script — an 18:47 lead tick
    can land on top of a runner tick. Both would then run update_panel.py, which does
    read -> mutate -> to_parquet(overwrite): last writer wins and a day's ingest
    disappears with no error anywhere.
    """
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        os.close(fd)
        if e.errno in (errno.EAGAIN, errno.EACCES):
            return None
        raise
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n".encode())
    return fd


def backoff_until(state: dict) -> datetime | None:
    """When the next invocation is allowed, or None if it is allowed now."""
    ra = state.get('retry_after')
    if not ra:
        return None
    try:
        when = datetime.fromisoformat(ra)
    except ValueError:
        return None
    return when if when > datetime.now(timezone.utc) else None


def next_backoff(consecutive_failures: int) -> datetime:
    idx = min(max(consecutive_failures, 1), len(BACKOFF_MINUTES)) - 1
    return datetime.now(timezone.utc) + timedelta(minutes=BACKOFF_MINUTES[idx])


def next_worker_role(state: dict) -> str:
    """Alternate workers, stops first (it is the prioritised question).

    Keyed on `last_worker_role`, NOT `last_role`: the latter also records lead runs,
    and since the lead runs daily from its own launchd job, keying off it made every
    post-lead tick pick 'stops' again — systematically starving 'picking'.
    """
    return 'picking' if state.get('last_worker_role') == 'stops' else 'stops'


def check_budget(budget: dict) -> tuple[bool, str]:
    spent = budget.get('agent_invocations_used', 0)
    cap = budget.get('agent_invocations_cap', 0)
    if cap and spent >= cap:
        return False, f"invocation cap reached ({spent}/{cap})"
    end = budget.get('end_date')
    if end and datetime.now(timezone.utc).strftime('%Y-%m-%d') > end:
        return False, f"past end_date {end}"
    return True, ''


def _recent_ledger_context(role: str, max_chars: int = 6000) -> str:
    """Tail of decisions.md + this role's last results, so a tick knows what is DONE.

    Without this the task string is a constant, and a worker re-reads the same
    hypotheses.md "Next tasks" list every tick. Since the lead is the only agent
    allowed to update that list, a worker whose lead has not run yet has every
    incentive to redo work it already completed.
    """
    parts = []
    dec = LEDGER / 'decisions.md'
    if dec.exists():
        tail = dec.read_text()[-max_chars:]
        parts.append("## Tail of research/ledger/decisions.md (most recent work)\n\n" + tail)

    res = LEDGER / 'results.jsonl'
    if res.exists():
        mine = [ln for ln in res.read_text().splitlines()
                if ln.strip() and f'"worker-{role}"' in ln]
        if mine:
            parts.append("## Your most recent results.jsonl entries (do NOT redo these)\n\n"
                         + '\n'.join(ln[:1200] for ln in mine[-4:]))
    return '\n\n---\n\n'.join(parts)


def invoke_agent(role: str, extra: str = '') -> int:
    """Run one headless Claude invocation with the role prompt appended."""
    prompt_file = PROMPTS / ROLE_PROMPTS[role]
    shared = (PROMPTS / '_shared_guardrails.md').read_text()
    system = shared + '\n\n---\n\n' + prompt_file.read_text()

    context = _recent_ledger_context(role)
    task = (extra or
            "New panel data has landed. Do the next most valuable piece of work for your "
            "role per research/ledger/hypotheses.md. Append results to "
            "research/ledger/results.jsonl and write conclusions to "
            "research/ledger/decisions.md. If nothing is worth doing, say so and stop.\n\n"
            "**Read the context below first.** If the next task in hypotheses.md is already "
            "answered there, do NOT redo it — either advance to the genuinely next open "
            "question, or state that you are blocked on the lead updating hypotheses.md and "
            "stop. Repeating completed work is a failure, not a safe default.\n\n"
            + context)

    cmd = [str(CLAUDE_BIN), '-p', task, '--append-system-prompt', system,
           '--model', AGENT_MODEL, '--max-budget-usd', str(AGENT_MAX_BUDGET_USD)]
    if AGENT_SETTINGS.exists():
        cmd += ['--settings', str(AGENT_SETTINGS)]
    else:
        print(f"  WARNING: {AGENT_SETTINGS} missing — agent runs with the human's "
              f"interactive permissions and NO live-config deny rules.")
    print(f"  invoking agent role={role} model={AGENT_MODEL} "
          f"budget=${AGENT_MAX_BUDGET_USD:.2f} ({len(system)} chars of role prompt, "
          f"{len(context)} chars of ledger context)")
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), timeout=3600,
                           capture_output=True, text=True)
        if r.stdout:
            print(r.stdout[-4000:])
        if r.returncode != 0:
            print(f"  agent exited {r.returncode}: {r.stderr[-1500:]}")
        return r.returncode
    except subprocess.TimeoutExpired:
        print("  agent timed out after 3600s")
        return 124
    except FileNotFoundError:
        print(f"  claude CLI not found at {CLAUDE_BIN}")
        return 127


def print_status() -> int:
    """Human-readable health. The overnight run failed 12 times with nothing surfacing
    it but a log tail nobody was reading — this is the one-command answer to
    'is the research system actually working?'."""
    state = _load_json(STATE, {})
    budget = _load_json(BUDGET, {})
    used = budget.get('agent_invocations_used', 0)
    cap = budget.get('agent_invocations_cap', 0)
    fails = state.get('consecutive_failures', 0)

    print(f"ticks run            : {state.get('ticks', 0)}")
    print(f"last tick            : {state.get('last_tick')}")
    print(f"last role / worker   : {state.get('last_role')} / {state.get('last_worker_role')}")
    print(f"budget               : {used}/{cap} invocations, end_date "
          f"{budget.get('end_date')}")
    ok, why = check_budget(budget)
    if not ok:
        print(f"  !! BUDGET STOP: {why}")

    print(f"consecutive failures : {fails}")
    if fails >= MAX_CONSECUTIVE_FAILURES:
        print(f"  !! PARKED (>= {MAX_CONSECUTIVE_FAILURES}) — will not invoke until "
              f"'consecutive_failures' is cleared in {STATE.name}")
    until = backoff_until(state)
    if until:
        mins = (until - datetime.now(timezone.utc)).total_seconds() / 60
        print(f"  backoff active until {until.isoformat(timespec='seconds')} "
              f"({mins:.0f} min)")

    print(f"agent settings       : {AGENT_SETTINGS} "
          f"{'OK' if AGENT_SETTINGS.exists() else '!! MISSING (no deny rules!)'}")
    try:
        print(f"new signal files     : {len(new_signal_files())}")
    except Exception as e:  # panel/S3 may be unavailable; status must never crash
        print(f"new signal files     : unavailable ({e.__class__.__name__}: {e})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--role', choices=sorted(ROLE_PROMPTS), default=None,
                    help='force a role (default: alternate workers, stops first)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='invoke an agent even if no new signal files landed; '
                         'also overrides an active backoff window')
    ap.add_argument('--status', action='store_true',
                    help='print state/budget/backoff and exit without invoking anything')
    args = ap.parse_args()

    LEDGER.mkdir(parents=True, exist_ok=True)

    if args.status:
        return print_status()

    lock_fd = acquire_lock()
    if lock_fd is None:
        print("another tick holds the lock — exiting (this is normal, not an error).")
        return 0
    try:
        return _tick(args)
    finally:
        os.close(lock_fd)


def _tick(args) -> int:
    state = _load_json(STATE, {'ticks': 0, 'last_role': None, 'last_tick': None})
    budget = _load_json(BUDGET, {'agent_invocations_cap': 0,
                                 'agent_invocations_used': 0, 'end_date': None})

    ok, why = check_budget(budget)
    if not ok:
        print(f"BUDGET STOP: {why}")
        return 0

    fails = state.get('consecutive_failures', 0)
    if fails >= MAX_CONSECUTIVE_FAILURES:
        print(f"PARKED: {fails} consecutive failed invocations "
              f"(>= {MAX_CONSECUTIVE_FAILURES}). Not invoking. Clear "
              f"'consecutive_failures' in {STATE.name} once the cause is understood.")
        return 0

    until = backoff_until(state)
    if until and not args.force:
        mins = (until - datetime.now(timezone.utc)).total_seconds() / 60
        print(f"BACKOFF: {fails} consecutive failure(s); next attempt in {mins:.0f} min "
              f"(at {until.isoformat(timespec='seconds')}).")
        return 0

    fresh = new_signal_files()
    stale = panel_is_stale(state)
    print(f"tick {state['ticks'] + 1}: {len(fresh)} new signal file(s)"
          f"{', panel stale' if stale else ''}")

    # Panel freshness and agent invocation are INDEPENDENT decisions. The panel is
    # refreshed on new data or on its own ~daily cadence (open rows must keep accruing
    # forward bars); an agent is invoked only on genuinely new data. Previously both
    # hung off `fresh`, so a quiet day advanced nothing at all.
    want_panel = bool(fresh) or stale
    want_agent = bool(fresh) or args.force

    if not want_panel and not want_agent:
        print("no new data, panel fresh — nothing to do.")
        state['ticks'] += 1
        state['last_tick'] = datetime.now(timezone.utc).isoformat()
        if not args.dry_run:
            _save_json(STATE, state)
        return 0

    role = args.role or next_worker_role(state)
    lead_skip = want_agent and role == 'lead' and lead_run_is_redundant(state, fresh)

    if args.dry_run:
        agent_desc = None
        if want_agent:
            agent_desc = f'skip {role} (redundant audit)' if lead_skip else f'invoke {role}'
        print(f"--dry-run: would {'update panel' if want_panel else 'skip panel'}"
              f"{' and ' + agent_desc if agent_desc else ''}")
        return 0

    if want_panel:
        print(f"  updating panel ({'new files' if fresh else 'stale, advancing open rows'})...")
        u = subprocess.run([sys.executable, str(RESEARCH / 'panel' / 'update_panel.py')],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=7200)
        print('   ' + '\n   '.join(u.stdout.strip().splitlines()[-8:]))
        if u.returncode != 0:
            print(f"  panel update FAILED ({u.returncode}) — not invoking an agent on "
                  f"possibly-bad data:\n{u.stderr[-1500:]}")
            # Deliberately do NOT stamp last_panel_update: the panel is still stale,
            # so the next tick retries the refresh rather than waiting out the cadence.
            state['ticks'] += 1
            state['last_tick'] = datetime.now(timezone.utc).isoformat()
            _save_json(STATE, state)
            return 1
        state['last_panel_update'] = datetime.now(timezone.utc).isoformat()
        _save_json(STATE, state)

    if not want_agent:
        print("  panel refreshed; no new signal data, so no agent invoked.")
        state['ticks'] += 1
        state['last_tick'] = datetime.now(timezone.utc).isoformat()
        _save_json(STATE, state)
        return 0

    if lead_skip:
        print(f"  lead: no new signal files and no ledger activity since the last lead "
              f"run ({state.get('last_lead_run')}) — skipping, nothing to audit.")
        state['ticks'] += 1
        state['last_tick'] = datetime.now(timezone.utc).isoformat()
        _save_json(STATE, state)
        return 0

    rc = invoke_agent(role)

    state['ticks'] += 1
    state['last_tick'] = datetime.now(timezone.utc).isoformat()

    # Only a SUCCESSFUL invocation costs budget and advances the role rotation. A
    # session-limit rejection (the CLI prints "You've hit your session limit" and
    # exits nonzero) produced no research — counting it would burn the cap on
    # nothing and would flip the rotation so the same role never retries. Leave
    # last_role unchanged so the next tick retries the SAME role once capacity
    # returns, and record consecutive failures so the runner can back off.
    if rc == 0:
        state['last_role'] = role
        if role in WORKER_ROLES:
            state['last_worker_role'] = role
        if role == 'lead':
            state['last_lead_run'] = datetime.now(timezone.utc).isoformat()
        state['consecutive_failures'] = 0
        state['retry_after'] = None
        _save_json(STATE, state)
        budget['agent_invocations_used'] = budget.get('agent_invocations_used', 0) + 1
        _save_json(BUDGET, budget)
    else:
        state['consecutive_failures'] = state.get('consecutive_failures', 0) + 1
        # Back off geometrically. A failure is nearly always capacity, which does not
        # clear in one 30-minute tick; retrying every tick just re-pays for a full
        # yfinance panel refresh to earn the same rejection (12 times, on 2026-07-24).
        when = next_backoff(state['consecutive_failures'])
        state['retry_after'] = when.isoformat()
        _save_json(STATE, state)
        print(f"  agent invocation FAILED (rc={rc}) — not charging budget "
              f"(used stays {budget.get('agent_invocations_used', 0)}/"
              f"{budget.get('agent_invocations_cap', 0)}); "
              f"{state['consecutive_failures']} consecutive failure(s). "
              f"Likely the Claude session limit; retrying role={role} no sooner than "
              f"{when.isoformat(timespec='seconds')}.")
        return rc

    # Persist the tick's ledger writes. Unattended, an agent that appended to
    # results.jsonl / decisions.md but didn't commit would lose that work on the
    # next crash or reset — and a half-written ledger is how the lead ends up
    # auditing a phantom. Commit the ledger (only) to research/auto-agents. Never
    # touches tracked live-config files; -- pathspec scopes it to the ledger dir.
    _commit_ledger(role, state['ticks'])
    return rc


def _commit_ledger(role: str, tick: int) -> None:
    branch = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                            cwd=str(ROOT), capture_output=True, text=True).stdout.strip()
    if branch != 'research/auto-agents':
        print(f"  not on research/auto-agents (on {branch!r}) — leaving ledger uncommitted")
        return
    rel = 'research/ledger'
    dirty = subprocess.run(['git', 'status', '--porcelain', '--', rel],
                           cwd=str(ROOT), capture_output=True, text=True).stdout.strip()
    if not dirty:
        return
    subprocess.run(['git', 'add', '--', rel], cwd=str(ROOT), check=False)
    msg = (f"research(ledger): tick {tick} — {role} [auto]\n\n"
           f"Automated ledger commit by research/runner.py.")
    r = subprocess.run(['git', 'commit', '-m', msg, '--', rel],
                       cwd=str(ROOT), capture_output=True, text=True)
    print(f"  ledger committed (tick {tick})" if r.returncode == 0
          else f"  ledger commit skipped: {r.stdout.strip()[-200:]}")


if __name__ == '__main__':
    sys.exit(main())
