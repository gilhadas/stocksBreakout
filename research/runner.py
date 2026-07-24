#!/usr/bin/env python3
"""
Research runner — one tick per invocation. launchd schedules it; it does NOT loop.

Single-shot by design: a KeepAlive daemon with its own loop can wedge in a state
nothing observes, and a wedged research daemon looks identical to an idle one.
A tick either does its work and exits 0, or fails loudly and gets retried.

Each tick:
  1. Detect signal CSVs not yet in the panel.
  2. If any -> run update_panel.py (append + advance open rows).
  3. If the panel changed -> invoke the next queued worker agent via `claude -p`.
  4. Record the tick in budget.json; refuse to spend past the cap.

Roles are plain markdown in research/prompts/. The lead runs from its own launchd
schedule (once daily), not from here.

Usage:
    python research/runner.py                 # one tick
    python research/runner.py --dry-run       # report only
    python research/runner.py --role stops    # force a specific worker
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RESEARCH = ROOT / 'research'
LEDGER = RESEARCH / 'ledger'
PROMPTS = RESEARCH / 'prompts'
PANEL = RESEARCH / 'panel' / 'panel.parquet'
STATE = LEDGER / 'runner_state.json'
BUDGET = LEDGER / 'budget.json'

ROLE_PROMPTS = {'stops': 'worker_stops.md', 'picking': 'worker_picking.md',
                'lead': 'lead.md'}
CLAUDE_BIN = Path.home() / '.local' / 'bin' / 'claude'


def _load_json(p: Path, default: dict) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return dict(default)


def _save_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def new_signal_files() -> list[str]:
    """Signal CSVs on disk/S3 that the panel has never seen."""
    import pandas as pd
    from utils import list_files

    available = set(list_files('scanner_output/signals', 'signals_*.csv'))
    if not PANEL.exists():
        return sorted(available)
    known = set(pd.read_parquet(PANEL, columns=['source_file'])['source_file'].unique())
    return sorted(available - known)


def check_budget(budget: dict) -> tuple[bool, str]:
    spent = budget.get('agent_invocations_used', 0)
    cap = budget.get('agent_invocations_cap', 0)
    if cap and spent >= cap:
        return False, f"invocation cap reached ({spent}/{cap})"
    end = budget.get('end_date')
    if end and datetime.now(timezone.utc).strftime('%Y-%m-%d') > end:
        return False, f"past end_date {end}"
    return True, ''


def invoke_agent(role: str, extra: str = '') -> int:
    """Run one headless Claude invocation with the role prompt appended."""
    prompt_file = PROMPTS / ROLE_PROMPTS[role]
    shared = (PROMPTS / '_shared_guardrails.md').read_text()
    system = shared + '\n\n---\n\n' + prompt_file.read_text()

    task = (extra or
            "New panel data has landed. Do the next most valuable piece of work for your "
            "role per research/ledger/hypotheses.md. Append results to "
            "research/ledger/results.jsonl and write conclusions to "
            "research/ledger/decisions.md. If nothing is worth doing, say so and stop.")

    cmd = [str(CLAUDE_BIN), '-p', task, '--append-system-prompt', system]
    print(f"  invoking agent role={role} ({len(system)} chars of role prompt)")
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--role', choices=sorted(ROLE_PROMPTS), default=None,
                    help='force a role (default: alternate workers, stops first)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='invoke an agent even if no new signal files landed')
    args = ap.parse_args()

    LEDGER.mkdir(parents=True, exist_ok=True)
    state = _load_json(STATE, {'ticks': 0, 'last_role': None, 'last_tick': None})
    budget = _load_json(BUDGET, {'agent_invocations_cap': 0,
                                 'agent_invocations_used': 0, 'end_date': None})

    ok, why = check_budget(budget)
    if not ok:
        print(f"BUDGET STOP: {why}")
        return 0

    fresh = new_signal_files()
    print(f"tick {state['ticks'] + 1}: {len(fresh)} new signal file(s)")

    if not fresh and not args.force:
        print("no new data — nothing to do.")
        state['ticks'] += 1
        state['last_tick'] = datetime.now(timezone.utc).isoformat()
        if not args.dry_run:
            _save_json(STATE, state)
        return 0

    if args.dry_run:
        print(f"--dry-run: would update panel and invoke "
              f"{args.role or 'next worker'}")
        return 0

    if fresh:
        print("  updating panel...")
        u = subprocess.run([sys.executable, str(RESEARCH / 'panel' / 'update_panel.py')],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=7200)
        print('   ' + '\n   '.join(u.stdout.strip().splitlines()[-8:]))
        if u.returncode != 0:
            print(f"  panel update FAILED ({u.returncode}) — not invoking an agent on "
                  f"possibly-bad data:\n{u.stderr[-1500:]}")
            return 1

    # Alternate workers, stops first (it is the prioritised question).
    role = args.role or ('picking' if state.get('last_role') == 'stops' else 'stops')
    rc = invoke_agent(role)

    state['ticks'] += 1
    state['last_role'] = role
    state['last_tick'] = datetime.now(timezone.utc).isoformat()
    _save_json(STATE, state)
    budget['agent_invocations_used'] = budget.get('agent_invocations_used', 0) + 1
    _save_json(BUDGET, budget)

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
