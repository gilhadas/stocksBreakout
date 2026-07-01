#!/usr/bin/env python3
"""
optuna_learning_agent.py
========================
"Optuna in the loop" — a learning agent that periodically retunes the scanner's
SCORING_WEIGHTS with the Optuna walk-forward optimizer and feeds the result into
the SAME score_adjustments.json plumbing the scanner already applies on startup.

How it fits the existing learning loop
--------------------------------------
The project already has a statistical learner (validate_signals.py learn ->
score_adjustments.json -> scanner applies ±3-capped weight nudges). This agent
replaces those small statistical nudges with Optuna-optimized targets, but reuses
the exact same application path and its ±3 safety cap.

Each cycle:
  1. Run weight_optimizer.py (Optuna TPE, walk-forward, held-out fold 4).
  2. Read its best_weights_*.json report (now incl. baseline_per_fold = the live
     config evaluated on the same folds).
  3. GATE on the held-out fold: only proceed if the optimized config's
     out-of-sample Sharpe beats the current live config by >= --min-improvement.
  4. If it passes, write the optimized weights as `weight_recommendations` to
     score_adjustments.json. The scanner then moves each live weight toward the
     Optuna target by at most ±3 from the config default (its existing cap).
  5. Surface (but do NOT force) any feature where Optuna wants a move larger than
     ±3 — that needs a human config.py update; the agent just flags it.

Safety
------
* Never writes anything when the held-out gate fails (default: needs +0.10 Sharpe).
* The scanner's ±3 cap bounds how far any single weight can move from its hand-set
  config default — the agent cannot blow past that rail.
* SCORE_THRESHOLDS / rr_grade_scores are reported for human review only (the
  scanner's learning loader applies weights only).
* --dry-run runs the optimizer + gate and prints the decision without writing.

Usage
-----
  python optuna_learning_agent.py --trials 200                 # one cycle
  python optuna_learning_agent.py --trials 200 --dry-run       # gate only, no write
  python optuna_learning_agent.py --trials 200 --loop --interval 86400   # daily loop
  python optuna_learning_agent.py --symbols input/optimizer_watch.txt --min-improvement 0.15
"""
import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import config
from config import OUTPUT_DIR

logging.basicConfig(level=logging.INFO, format='%(asctime)s  %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger('optuna_learning_agent')

OPT_DIR = Path('scanner_output/optimizer')
ADJUSTMENTS_FILE = Path(OUTPUT_DIR, 'score_adjustments.json')
AUDIT_LOG = OPT_DIR / 'learning_agent_log.jsonl'
WEIGHT_CAP = 3          # mirror scanner._load_score_adjustments() ±3 cap (for flagging only)


# ── Pure helpers (unit-tested without running Optuna) ────────────────────────────
def held_out_lift(report: dict):
    """Return (optimized_sharpe, baseline_sharpe, lift) for the held-out fold.

    The held-out fold is the one with role == 'validate' in per_fold. lift is None
    if either side is missing.
    """
    per = report.get('per_fold', {})
    base = report.get('baseline_per_fold', {})
    val_id = next((fid for fid, fr in per.items() if fr.get('role') == 'validate'), None)
    if val_id is None:
        return None, None, None
    opt_s = per[val_id].get('sharpe')
    base_s = base.get(val_id, {}).get('sharpe')
    if opt_s is None or base_s is None:
        return opt_s, base_s, None
    return opt_s, base_s, round(opt_s - base_s, 3)


def build_recommendations(report: dict, current_weights: dict, when: str):
    """Translate report['weights'] into the scanner's weight_recommendations format.

    Only features present in current_weights and whose target differs are included.
    Returns (recommendations, over_cap) where over_cap lists features whose desired
    move exceeds the scanner's ±3 auto-apply cap (need a human config.py change).
    """
    recs, over_cap = [], []
    for feat, target in report.get('weights', {}).items():
        if feat not in current_weights:
            continue
        cur = current_weights[feat]
        if target == cur:
            continue
        recs.append({
            'feature': feat,
            'current_weight': cur,
            'recommended_weight': target,
            'reason': f'optuna walk-forward target ({when})',
        })
        if abs(target - cur) > WEIGHT_CAP:
            over_cap.append((feat, cur, target))
    return recs, over_cap


def gate_passes(lift, min_improvement: float) -> bool:
    return lift is not None and lift >= min_improvement


def merge_adjustments(existing: dict, recommendations: list, meta: dict) -> dict:
    """Produce the score_adjustments.json payload (overwrites weight_recommendations)."""
    out = dict(existing or {})
    out['weight_recommendations'] = recommendations
    out['source'] = 'optuna_learning_agent'
    out['meta'] = meta
    return out


# ── Orchestration ────────────────────────────────────────────────────────────────
def run_optimizer(trials: int, symbols: str, quality: str, limit: int = 0,
                  end_date: str = None) -> Path:
    """Run weight_optimizer.py and return the freshest best_weights_*.json it wrote."""
    OPT_DIR.mkdir(parents=True, exist_ok=True)
    before = set(OPT_DIR.glob('best_weights_*.json'))
    cmd = [sys.executable, 'weight_optimizer.py', '--trials', str(trials), '--quality', quality]
    if symbols:
        cmd += ['--symbols', symbols]
    if limit:
        cmd += ['--limit', str(limit)]
    if end_date:
        cmd += ['--end-date', end_date]   # roll folds to validate on the most recent window
    logger.info(f"running optimizer: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        logger.error(f"optimizer failed (rc={proc.returncode}):\n{proc.stderr[-2000:]}")
        raise RuntimeError("weight_optimizer.py failed")
    after = set(OPT_DIR.glob('best_weights_*.json'))
    new = after - before
    candidates = new or after
    if not candidates:
        raise FileNotFoundError("optimizer produced no best_weights_*.json")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _audit(record: dict):
    OPT_DIR.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open('a') as f:
        f.write(json.dumps(record, default=str) + '\n')


def run_once(args) -> dict:
    when = datetime.now().strftime('%Y-%m-%d %H:%M')
    report_path = run_optimizer(args.trials, args.symbols, args.quality, args.limit, args.end_date)
    report = json.loads(report_path.read_text())
    opt_s, base_s, lift = held_out_lift(report)
    recs, over_cap = build_recommendations(report, config.SCORING_WEIGHTS, when)

    logger.info(f"held-out Sharpe: optimized={opt_s}  baseline={base_s}  lift={lift}  "
                f"(need >= {args.min_improvement})")
    decision = {'time': when, 'report': str(report_path), 'opt_sharpe': opt_s,
                'base_sharpe': base_s, 'lift': lift, 'min_improvement': args.min_improvement,
                'n_recs': len(recs), 'applied': False, 'dry_run': args.dry_run}

    if not gate_passes(lift, args.min_improvement):
        logger.info("GATE FAILED — optimized config does not beat live config out-of-sample. "
                    "No changes written.")
        decision['result'] = 'gate_failed'
        _audit(decision)
        return decision

    if over_cap:
        flagged = ', '.join(f"{f}:{c}->{t}" for f, c, t in over_cap)
        logger.info(f"NOTE — Optuna wants moves larger than ±{WEIGHT_CAP} (needs manual "
                    f"config.py update): {flagged}")
    decision['over_cap'] = [{'feature': f, 'current': c, 'target': t} for f, c, t in over_cap]
    decision['recommendations'] = recs

    if args.dry_run:
        logger.info(f"GATE PASSED (lift={lift}) — DRY RUN, would write {len(recs)} "
                    f"weight recommendations to {ADJUSTMENTS_FILE}")
        decision['result'] = 'gate_passed_dry_run'
        _audit(decision)
        return decision

    existing = {}
    if ADJUSTMENTS_FILE.exists():
        try:
            existing = json.loads(ADJUSTMENTS_FILE.read_text())
        except Exception:
            existing = {}
    payload = merge_adjustments(existing, recs, {
        'lift': lift, 'opt_sharpe': opt_s, 'base_sharpe': base_s,
        'report': str(report_path), 'generated': when,
    })
    ADJUSTMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ADJUSTMENTS_FILE.write_text(json.dumps(payload, indent=2, default=str))
    logger.info(f"APPLIED — wrote {len(recs)} recommendations to {ADJUSTMENTS_FILE} "
                f"(scanner applies ±{WEIGHT_CAP}/cycle on next startup).")
    decision['applied'] = True
    decision['result'] = 'applied'
    _audit(decision)
    return decision


def main():
    p = argparse.ArgumentParser(description="Optuna-in-the-loop learning agent")
    p.add_argument('--trials', type=int, default=200, help='Optuna trials per cycle (default 200)')
    p.add_argument('--symbols', default=None, help='Watchlist for the optimizer (default: optimizer config)')
    p.add_argument('--limit', type=int, default=0, help='Cap symbols passed to the optimizer (0=no cap)')
    p.add_argument('--end-date', default=datetime.now().strftime('%Y-%m-%d'),
                   help='Roll optimizer folds to validate on the window ending here '
                        '(default: today, so the loop learns from recent data). '
                        'Pass "" to use the optimizer\'s fixed 2023-2024 folds.')
    p.add_argument('--quality', default='HIGH', help='Min signal quality for scoring (default HIGH)')
    p.add_argument('--min-improvement', type=float, default=0.10,
                   help='Required held-out Sharpe lift over live config to apply (default 0.10)')
    p.add_argument('--dry-run', action='store_true', help='Run optimizer + gate, print decision, write nothing')
    p.add_argument('--loop', action='store_true', help='Repeat on --interval until interrupted')
    p.add_argument('--interval', type=int, default=86400, help='Loop seconds between cycles (default 1 day)')
    args = p.parse_args()

    if not args.loop:
        run_once(args)
        return
    logger.info(f"loop mode: every {args.interval}s (Ctrl-C to stop)")
    while True:
        try:
            run_once(args)
        except Exception as e:
            logger.error(f"cycle error: {e}")
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info("stopped.")
            break


if __name__ == '__main__':
    main()
