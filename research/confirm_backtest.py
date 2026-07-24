#!/usr/bin/env python3
"""
Confirmatory gate: does a candidate stop rule survive a real bear market?

The panel (Apr-Jul 2026) contains NO sustained bear — SPY is +10% YTD over that
window. Stops matter most in a bear, so a rule tuned only on the panel is exactly
the rule most likely to fail when it counts. This wrapper runs the champion config
for 2022 (SPY -18.65%) at a candidate ATR trail multiplier and prints it against
the ATR x2.0 baseline.

This IS a simulation, unlike the panel. Label its numbers as such.

Ship criteria (CLAUDE.md sec11/sec13) — ALL must hold, judged on the
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
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / 'scanner_output' / 'backtests' / 'agent_confirm'

BASE_ARGS = ['--no-tc', '--bounce-bear-gate', '15', '--atr-trail-always',
             '--skip-old', '--realistic-sizing']


def run(mult: float, years: str, watchlist: str, tag: str) -> Path:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    log = OUTDIR / f"{tag}_mult{mult}_{years.replace(',', '-')}.log"
    cmd = [sys.executable, 'backtest_regime_compare.py', *BASE_ARGS,
           '--atr-trail-mult', str(mult), '--years', years,
           '--watchlist', watchlist]
    print(f"  running mult={mult} years={years} -> {log.name}", flush=True)
    with log.open('w') as fh:
        fh.write(f"# {' '.join(cmd)}\n# started {datetime.now(timezone.utc).isoformat()}\n")
        fh.flush()
        subprocess.run(cmd, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
                       timeout=6 * 3600)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--mult', type=float, required=True,
                    help='candidate ATR trail multiplier (live default is 2.0)')
    ap.add_argument('--baseline-mult', type=float, default=2.0)
    ap.add_argument('--years', default='2022',
                    help='default 2022 — the bear year the panel cannot supply')
    ap.add_argument('--watchlist', default='input/spx_plus.txt')
    ap.add_argument('--skip-baseline', action='store_true',
                    help='reuse a previous baseline log instead of re-running it')
    args = ap.parse_args()

    if abs(args.mult - args.baseline_mult) < 1e-9:
        print("candidate == baseline; nothing to compare.")
        return 1

    print(f"Confirmatory backtest — candidate ATRx{args.mult} vs baseline "
          f"ATRx{args.baseline_mult}, years={args.years}")
    print("NOTE: these are SIMULATED numbers, not panel measurements.\n")

    base_log = OUTDIR / f"baseline_mult{args.baseline_mult}_{args.years.replace(',', '-')}.log"
    if not (args.skip_baseline and base_log.exists()):
        base_log = run(args.baseline_mult, args.years, args.watchlist, 'baseline')
    cand_log = run(args.mult, args.years, args.watchlist, 'candidate')

    for label, lg in (('BASELINE', base_log), ('CANDIDATE', cand_log)):
        print(f"\n===== {label}  ({lg.name}) =====")
        rows = extract(lg)
        print('\n'.join(rows) if rows else '  (no result rows parsed — check the log)')

    print("\nDecide on the REALISTIC row only. Ship needs Sharpe >= baseline+0.10 AND")
    print(">15d WR not shrinking AND MaxDD not materially worse. Anything else is dormant.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
