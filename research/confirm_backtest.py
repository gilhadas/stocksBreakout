#!/usr/bin/env python3
"""
Confirmatory gate: does a candidate rule survive history — on the population LIVE
actually trades?

The panel (Apr-Jul 2026) contains NO sustained bear: SPY is +10% YTD over that window.
Stops matter most in a bear, so a rule tuned only on the panel is exactly the rule most
likely to fail when it counts. This wrapper runs a candidate against a baseline in a
paired comparison.

This IS a simulation, unlike the panel. Label its numbers as such.

Why `--population` exists (history — do not "simplify" this away)
-----------------------------------------------------------------
This script originally hardcoded `--no-tc`. That was **deliberate, not a slip**: its
first docstring said it "runs the champion config", and BASE_ARGS was a verbatim copy
of the canonical champion CLI in CLAUDE.md §7. Using the documented champion as the
reference point is a defensible choice — it makes gate numbers comparable to every
baseline in §7-§13.

What was never reconciled is that `--no-tc` DISABLES TREND_CONFIRM, while live runs
TC Path A: the live archive is 87% TREND_CONFIRM / 8% BOUNCE, and `--no-tc` yields a
~99.7% BOUNCE stream. H4 (the population divergence) had been logged that same morning
— five hours before this script was written — but nothing in the docstring, the
guardrails, or the ledger records the tradeoff being weighed. So the gate could not
confirm a panel-derived (TC) rule on the population live actually trades.

Hence the explicit choice instead of a silent default:
  * `--population live`      (default) TC Path A enabled, exactly as production runs.
  * `--population champion`  passes `--no-tc`, reproducing the documented baselines.
Both arms of a run always use the SAME population, so the ship bar (candidate vs its
own baseline, >= +0.10 Sharpe) stays internally valid either way. What changes is
which population the answer is *about* — never mix numbers across the two.

`--rank-scores` likewise widened the gate: it previously accepted only `--mult` (an ATR
trail multiplier), so a candidate RANKING model had no promotion path at all.

A structural limit you cannot fix by flags — READ THIS BEFORE TRUSTING A 2022 RESULT
------------------------------------------------------------------------------------
TREND_CONFIRM is blocked in RED_MARKET and BEARISH regimes (backtest_regime_compare.py,
collect_signals: `if _TC_CFG.get('enabled') and regime not in ('RED_MARKET','BEARISH')`).
2022 is a sustained bear, so it is mostly those regimes — meaning **the 2022 bear gate
cannot exercise live's dominant signal type, because live does not emit that type in a
bear.** That is a property of the strategy, not a bug in this script.

Consequence: 2022 is a genuine DOWNSIDE check (does the rule blow up in a bear?) but it
is NOT a test of a TREND_CONFIRM-derived rule. This script therefore reports the
realized signal-type mix of every arm, so a gate result containing zero TREND_CONFIRM
trades is visible rather than silently meaningless, and defaults to running a bull year
alongside the bear one.

Ship criteria (CLAUDE.md §11/§13) — ALL must hold, judged on the
REALISTIC-sizing arm, never the idealized one:
  * Sharpe >= baseline + 0.10
  * >15d hold win-rate does NOT shrink
  * MaxDD not materially worse

Measurement-noise caveat: two identical runs of this backtest have differed by
0.25 Sharpe (yfinance fetch failures vary the loaded universe). Both arms here run
in the SAME process against the same fetched data, so the comparison is paired and
that noise largely cancels — but do not compare a number from this script against
a Sharpe quoted from some other run.

Usage:
    python research/confirm_backtest.py --mult 2.5
    python research/confirm_backtest.py --mult 1.5 --years 2022,2025
    python research/confirm_backtest.py --rank-scores research/tmp/model_preds.csv
    python research/confirm_backtest.py --mult 2.5 --population champion  # reproduce §7
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / 'scanner_output' / 'backtests' / 'agent_confirm'

# --skip-old and --realistic-sizing are non-negotiable: §11's standing rule is that
# every config decision is judged on the realistic arm. --trades-log is required so the
# realized signal-type mix can be reported (see the module docstring).
BASE_ARGS = ['--bounce-bear-gate', '15', '--atr-trail-always',
             '--skip-old', '--realistic-sizing', '--trades-log']


def log_path(tag: str, population: str, mult: float | None, years: str) -> Path:
    """Single source of truth for the log name, so --skip-baseline looks for exactly
    what run() writes."""
    stamp = f"mult{mult}" if mult is not None else 'rank'
    return OUTDIR / f"{tag}_{population}_{stamp}_{years.replace(',', '-')}.log"


def run(years: str, watchlist: str, tag: str, mult: float | None = None,
        rank_scores: str | None = None, population: str = 'live') -> Path:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    log = log_path(tag, population, mult, years)

    cmd = [sys.executable, 'backtest_regime_compare.py', *BASE_ARGS,
           '--years', years, '--watchlist', watchlist]
    if population == 'champion':
        cmd.append('--no-tc')
    if mult is not None:
        cmd += ['--atr-trail-mult', str(mult)]
    if rank_scores:
        cmd += ['--rank-scores', rank_scores]

    print(f"  running {tag} [{population}] {stamp} years={years} -> {log.name}", flush=True)
    with log.open('w') as fh:
        fh.write(f"# {' '.join(cmd)}\n# started {datetime.now(timezone.utc).isoformat()}\n")
        fh.flush()
        r = subprocess.run(cmd, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
                           timeout=6 * 3600)
    if r.returncode != 0:
        print(f"  !! backtest exited {r.returncode} — see {log}")
    return log


def extract(log: Path) -> list[str]:
    """Pull the REALISTIC arm rows + hold-splits — the only ones that decide."""
    keep, lines = [], log.read_text(errors='ignore').splitlines()
    for i, ln in enumerate(lines):
        if 'REALISTIC sizing, no swap' in ln or 'SPY Buy & Hold' in ln \
                or 'pooled-cap=10 ★' in ln:
            keep.append(ln.rstrip())
            if i + 1 < len(lines) and 'Hold' in lines[i + 1]:
                keep.append(lines[i + 1].rstrip())
    return keep


def type_mix(years: str) -> dict:
    """Realized signal-type mix from the per-trade logs the run just wrote.

    This is the guard against the failure this script shipped with for a month: a gate
    that reports a clean pass while containing zero trades of the type the candidate
    rule was derived from.
    """
    mix: Counter = Counter()
    for year in years.split(','):
        for p in (ROOT / 'scanner_output' / 'backtests').glob(f'trades_{year.strip()}_*.csv'):
            try:
                with p.open(newline='') as fh:
                    for row in csv.DictReader(fh):
                        if row.get('signal_type'):
                            mix[row['signal_type']] += 1
            except Exception:
                continue
    return dict(mix)


def report_mix(years: str) -> None:
    mix = type_mix(years)
    if not mix:
        print("\n  (no per-trade logs found — cannot verify the signal-type mix)")
        return
    total = sum(mix.values())
    print(f"\n  Realized signal-type mix across {years} ({total} trades):")
    for t, n in sorted(mix.items(), key=lambda kv: -kv[1]):
        print(f"    {t:<16} {n:>6}  ({100 * n / total:.1f}%)")

    tc = mix.get('TREND_CONFIRM', 0)
    if total and tc / total < 0.10:
        print(f"\n  ⚠  TREND_CONFIRM is {100 * tc / total:.1f}% of these trades, but it is "
              f"~87% of LIVE signals.")
        print("     TREND_CONFIRM is blocked in RED_MARKET/BEARISH, so a bear year cannot "
              "exercise it.")
        print("     Treat this run as a DOWNSIDE check only — it does NOT confirm a "
              "TREND_CONFIRM-derived rule.")
        print("     Add a bull year (e.g. --years 2022,2024) to actually test live's "
              "dominant population.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--mult', type=float, default=None,
                    help='candidate ATR trail multiplier (live default is 2.0)')
    ap.add_argument('--baseline-mult', type=float, default=2.0)
    ap.add_argument('--rank-scores', default=None, metavar='FILE',
                    help='CSV (date,symbol,score) from a candidate RANKING model. '
                         'Baseline arm runs the same config WITHOUT it.')
    ap.add_argument('--years', default='2022,2024',
                    help='default 2022,2024 — 2022 is the bear downside check the panel '
                         'cannot supply, 2024 is a year where live\'s dominant '
                         'TREND_CONFIRM population actually fires (it is blocked in '
                         'bear regimes). Use --years 2022 only for a pure downside check.')
    ap.add_argument('--population', choices=('live', 'champion'), default='live',
                    help="'live' (default) leaves TREND_CONFIRM Path A enabled, matching "
                         "production (87%% TC). 'champion' passes --no-tc to reproduce the "
                         "documented CLAUDE.md baselines (~99.7%% BOUNCE) — a different "
                         "population, so do not mix its numbers with a live-arm result.")
    ap.add_argument('--watchlist', default='input/spx_plus.txt')
    ap.add_argument('--skip-baseline', action='store_true',
                    help='reuse a previous baseline log instead of re-running it')
    args = ap.parse_args()

    if args.mult is None and not args.rank_scores:
        print("Nothing to confirm: pass --mult (a stop candidate) or --rank-scores "
              "(a ranking candidate).")
        return 1
    if args.mult is not None and abs(args.mult - args.baseline_mult) < 1e-9:
        print("candidate == baseline; nothing to compare.")
        return 1

    what = []
    if args.mult is not None:
        what.append(f"ATR×{args.mult} vs ATR×{args.baseline_mult}")
    if args.rank_scores:
        what.append(f"ranking model {Path(args.rank_scores).name} vs current ranking")
    print(f"Confirmatory backtest — {'; '.join(what)}, years={args.years}, "
          f"population={args.population}")
    print("NOTE: these are SIMULATED numbers, not panel measurements.")
    if args.population == 'champion':
        print("⚠  --population champion: TREND_CONFIRM DISABLED. This reproduces the "
              "documented baselines but is NOT the population live trades.\n")
    else:
        print("Population = live (TREND_CONFIRM Path A enabled, as in production).\n")

    base_mult = args.baseline_mult if args.mult is not None else None
    base_log = log_path('baseline', args.population, base_mult, args.years)
    if not (args.skip_baseline and base_log.exists()):
        base_log = run(args.years, args.watchlist, 'baseline', mult=base_mult,
                       rank_scores=None, population=args.population)

    cand_log = run(args.years, args.watchlist, 'candidate', mult=args.mult,
                   rank_scores=args.rank_scores, population=args.population)

    for label, lg in (('BASELINE', base_log), ('CANDIDATE', cand_log)):
        print(f"\n===== {label}  ({lg.name}) =====")
        rows = extract(lg)
        print('\n'.join(rows) if rows else '  (no result rows parsed — check the log)')

    report_mix(args.years)

    print("\nDecide on the REALISTIC row only. Ship needs Sharpe >= baseline+0.10 AND")
    print(">15d WR not shrinking AND MaxDD not materially worse. Anything else is dormant.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
