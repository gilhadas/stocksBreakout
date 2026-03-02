#!/usr/bin/env python3
"""
V12 Weight Optimizer — Optuna walk-forward
==========================================
Finds optimal SCORING_WEIGHTS and SCORE_THRESHOLDS using Bayesian
optimization (Optuna TPE) with walk-forward validation.

Strategy
--------
1. Pre-scan once across all fold periods (threshold=0, all candidates pass)
   → collects candidates with raw 'checks' feature dicts
2. Each Optuna trial re-scores candidates arithmetically (fast, no re-scanning)
   → run_simulation on re-scored signals → measure out-of-sample Sharpe
3. Walk-forward: optimize on folds 1–3, validate on held-out fold 4

Usage
-----
  python weight_optimizer.py --trials 300
  python weight_optimizer.py --trials 100 --symbols input/MAGS.txt --quality PREMIUM
  python weight_optimizer.py --trials 300 --apply   # also print config.py patch
"""
import argparse
import asyncio
import json
import pickle
import sys
from datetime import datetime
from pathlib import Path

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
except ImportError:
    print("ERROR: optuna not installed.  Run:  pip install optuna")
    sys.exit(1)

import numpy as np

import config
from enhanced_backtest import fetch_all_data, run_prescan, run_simulation, load_symbols


# ── Walk-forward fold definitions ─────────────────────────────────────────────
FOLDS = [
    {'id': 1, 'scan_start': '2023-01-01', 'scan_end': '2023-06-30', 'role': 'optimize'},
    {'id': 2, 'scan_start': '2023-07-01', 'scan_end': '2023-12-31', 'role': 'optimize'},
    {'id': 3, 'scan_start': '2024-01-01', 'scan_end': '2024-06-30', 'role': 'optimize'},
    {'id': 4, 'scan_start': '2024-07-01', 'scan_end': '2024-12-31', 'role': 'validate'},
]
DATA_FETCH_START = '2022-01-01'   # extra history so early folds have 150+ bar warmup
DATA_FETCH_END   = '2024-12-31'

WEIGHT_KEYS    = list(config.SCORING_WEIGHTS.keys())
QUALITY_ORDER  = ['GOLD', 'PREMIUM', 'HIGH', 'STANDARD', 'LOW']


# ── Scoring helpers ──────────────────────────────────────────────────────────

def _score_to_quality(score: int, thresholds: dict) -> str:
    if score >= thresholds['GOLD']:     return 'GOLD'
    if score >= thresholds['PREMIUM']:  return 'PREMIUM'
    if score >= thresholds['HIGH']:     return 'HIGH'
    if score >= thresholds['STANDARD']: return 'STANDARD'
    return 'LOW'


def _passes_filter(quality: str, min_quality: str) -> bool:
    return QUALITY_ORDER.index(quality) <= QUALITY_ORDER.index(min_quality)


def rescore(candidates: list, weights: dict, thresholds: dict,
            min_quality: str = 'HIGH') -> list:
    """Re-score signal candidates from stored checks without re-scanning."""
    out = []
    for c in candidates:
        checks = c.get('checks', {})
        if not checks:
            continue
        score = sum(weights.get(k, 0) for k, v in checks.items() if v)
        quality = _score_to_quality(score, thresholds)
        if _passes_filter(quality, min_quality):
            sig = dict(c)
            sig['quality'] = quality
            out.append(sig)
    return out


# ── Pre-scan: collect candidates across all folds ───────────────────────────

def collect_candidates(historical: dict, end_prices: dict) -> dict:
    """Run run_prescan on each fold. Returns {fold_id: [candidates]}."""
    fold_candidates = {}
    for fold in FOLDS:
        print(f"  Fold {fold['id']}  {fold['scan_start']} → {fold['scan_end']} "
              f"({fold['role']})...")
        candidates = run_prescan(
            historical,
            fold['scan_start'],
            fold['scan_end'],
            modes=['swing', 'longterm'],
        )
        fold_candidates[fold['id']] = candidates
        print(f"    → {len(candidates)} candidates collected")
    return fold_candidates


# ── Optuna objective ─────────────────────────────────────────────────────────

def make_objective(fold_candidates: dict, end_prices: dict,
                   historical: dict, min_quality: str):
    """Build the Optuna objective closure (optimize folds 1–3 only)."""
    opt_folds = [f for f in FOLDS if f['role'] == 'optimize']

    def objective(trial: optuna.Trial) -> float:
        # ── Sample weights ─────────────────────────────────────────────────
        weights = {k: trial.suggest_int(k, 0, 25) for k in WEIGHT_KEYS}

        # ── Sample thresholds with monotonicity constraint ─────────────────
        thresh_gold     = trial.suggest_int('thresh_gold',     82, 100)
        thresh_premium  = trial.suggest_int('thresh_premium',  65, 90)
        thresh_high     = trial.suggest_int('thresh_high',     45, 78)
        thresh_standard = trial.suggest_int('thresh_standard', 35, 65)

        if not (thresh_gold > thresh_premium > thresh_high > thresh_standard):
            return -999.0   # prune: invalid ordering

        thresholds = {
            'GOLD':     thresh_gold,
            'PREMIUM':  thresh_premium,
            'HIGH':     thresh_high,
            'STANDARD': thresh_standard,
        }

        # ── Evaluate on each optimization fold ────────────────────────────
        sharpes = []
        for fold in opt_folds:
            signals = rescore(
                fold_candidates[fold['id']],
                weights, thresholds, min_quality,
            )
            if len(signals) < 3:
                continue
            result = run_simulation(
                signals,
                fold['scan_start'],
                fold['scan_end'],
                end_prices,
                historical,
                initial_capital=100_000,
            )
            if result and result.get('total_trades', 0) >= 3:
                sharpes.append(result.get('sharpe_ratio', -2.0))

        return float(np.mean(sharpes)) if sharpes else -2.0

    return objective


# ── Stability report ─────────────────────────────────────────────────────────

def stability_report(study: optuna.Study, fold_candidates: dict,
                     end_prices: dict, historical: dict,
                     min_quality: str) -> dict:
    """Evaluate best trial on ALL folds (including held-out) and return report."""
    best = study.best_trial
    weights = {k: best.params[k] for k in WEIGHT_KEYS}
    thresholds = {
        'GOLD':     best.params['thresh_gold'],
        'PREMIUM':  best.params['thresh_premium'],
        'HIGH':     best.params['thresh_high'],
        'STANDARD': best.params['thresh_standard'],
    }

    per_fold = {}
    for fold in FOLDS:
        signals = rescore(
            fold_candidates[fold['id']],
            weights, thresholds, min_quality,
        )
        result = run_simulation(
            signals,
            fold['scan_start'],
            fold['scan_end'],
            end_prices,
            historical,
            initial_capital=100_000,
        ) if signals else None

        per_fold[str(fold['id'])] = {
            'role':     fold['role'],
            'period':   f"{fold['scan_start']} → {fold['scan_end']}",
            'signals':  len(signals),
            'trades':   result.get('total_trades',  0)   if result else 0,
            'sharpe':   round(result.get('sharpe_ratio', 0), 3)  if result else None,
            'return':   round(result.get('total_return',  0), 2) if result else None,
            'win_rate': round(result.get('win_rate',      0), 1) if result else None,
            'max_dd':   round(result.get('max_drawdown',  0), 2) if result else None,
        }

    return {
        'weights':    weights,
        'thresholds': thresholds,
        'best_value': round(best.value, 4),
        'per_fold':   per_fold,
        'generated':  datetime.now().isoformat(),
    }


# ── Config.py patch printer ──────────────────────────────────────────────────

def print_config_patch(weights: dict, thresholds: dict) -> None:
    orig_w = config.SCORING_WEIGHTS
    orig_t = config.SCORE_THRESHOLDS

    print("\n" + "=" * 62)
    print("  CONFIG.PY PATCH — review then paste manually")
    print("=" * 62)
    print("\nSCORING_WEIGHTS = {")
    for k, v in weights.items():
        orig = orig_w.get(k, 0)
        note = f"  # was {orig}" if v != orig else ""
        print(f"    '{k}': {v},{note}")
    print("}\n")
    print("SCORE_THRESHOLDS = {")
    for k, v in thresholds.items():
        orig = orig_t.get(k, 0)
        note = f"  # was {orig}" if v != orig else ""
        print(f"    '{k}': {v},{note}")
    print("}")
    print("=" * 62 + "\n")


# ── Main ─────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description='V12 Weight Optimizer')
    p.add_argument('--trials',   type=int, default=300,
                   help='Optuna trials (default 300)')
    p.add_argument('--symbols',  default=None,
                   help='Watchlist file (default: auto-detect)')
    p.add_argument('--limit',    type=int, default=0,
                   help='Cap number of symbols (0=no limit)')
    p.add_argument('--quality',  default='HIGH',
                   choices=['STANDARD', 'HIGH', 'PREMIUM', 'GOLD'],
                   help='Min quality filter for re-scored signals (default HIGH)')
    p.add_argument('--apply',    action='store_true',
                   help='Print config.py patch after optimization')
    return p.parse_args()


def main():
    args = parse_args()
    output_dir = Path('scanner_output/optimizer')
    output_dir.mkdir(parents=True, exist_ok=True)

    symbols = load_symbols(args.symbols, limit=args.limit)
    if not symbols:
        print("ERROR: No symbols loaded. Provide --symbols <file>")
        sys.exit(1)
    if 'SPY' not in symbols:
        symbols = symbols + ['SPY']   # needed for RS calculation

    opt_fold_count = len([f for f in FOLDS if f['role'] == 'optimize'])
    val_fold_count = len([f for f in FOLDS if f['role'] == 'validate'])

    print(f"\n{'='*55}")
    print(f"  V12 Weight Optimizer")
    print(f"{'='*55}")
    print(f"  Symbols : {len(symbols)-1}  (+SPY)")
    print(f"  Trials  : {args.trials}")
    print(f"  Quality : >= {args.quality}")
    print(f"  Folds   : {opt_fold_count} optimize  /  {val_fold_count} held-out")
    print(f"  Weights : {len(WEIGHT_KEYS)} features")
    print(f"  Data    : {DATA_FETCH_START} → {DATA_FETCH_END}")
    print(f"{'='*55}\n")

    # ── 1. Fetch historical data ─────────────────────────────────────────────
    print("Step 1/3  Fetching historical data...")
    historical, end_prices = fetch_all_data(symbols, DATA_FETCH_START, DATA_FETCH_END)
    print(f"  Fetched {len(historical)-1} symbols + SPY\n")

    # ── 2. Pre-scan all folds once ───────────────────────────────────────────
    print("Step 2/3  Pre-scanning (quality threshold=0, all candidates)...")
    fold_candidates = collect_candidates(historical, end_prices)

    total = sum(len(v) for v in fold_candidates.values())
    print(f"  Total candidates: {total}\n")
    if total < 10:
        print("ERROR: Too few candidates. Check watchlist or date range.")
        sys.exit(1)

    # ── 3. Optuna study ──────────────────────────────────────────────────────
    print(f"Step 3/3  Optuna optimization ({args.trials} trials)...")

    progress = {'n': 0}
    def _progress_cb(study, trial):
        progress['n'] += 1
        if progress['n'] % 25 == 0:
            best = study.best_value if study.best_value > -900 else float('nan')
            print(f"  Trial {progress['n']:4d}/{args.trials}  best Sharpe={best:.3f}")

    objective = make_objective(fold_candidates, end_prices, historical, args.quality)
    study = optuna.create_study(
        direction='maximize',
        sampler=optuna.samplers.TPESampler(seed=42),
    )
    study.optimize(objective, n_trials=args.trials, callbacks=[_progress_cb])

    print(f"\n  ✓ Optimization complete")
    print(f"    Best Sharpe (opt folds avg): {study.best_value:.3f}\n")

    # ── Stability report ─────────────────────────────────────────────────────
    report = stability_report(
        study, fold_candidates, end_prices, historical, args.quality
    )

    print("── Per-fold results (best trial) ─────────────────────────")
    for fid, fr in report['per_fold'].items():
        tag = '[HELD-OUT]' if fr['role'] == 'validate' else '[opt]    '
        sharpe_str = f"{fr['sharpe']:+.3f}" if fr['sharpe'] is not None else '   N/A '
        ret_str    = f"{fr['return']:+.1f}%" if fr['return']  is not None else '   N/A'
        print(f"  Fold {fid} {tag}  {fr['period']}  "
              f"signals={fr['signals']:3d}  trades={fr['trades']:3d}  "
              f"Sharpe={sharpe_str}  return={ret_str}")

    # ── Save outputs ─────────────────────────────────────────────────────────
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    weights_path = output_dir / f'best_weights_{ts}.json'
    weights_path.write_text(json.dumps(report, indent=2))
    print(f"\n  Saved → {weights_path}")

    try:
        study_path = output_dir / f'study_{ts}.pkl'
        study_path.write_bytes(pickle.dumps(study))
        print(f"  Saved → {study_path}")
    except Exception:
        pass

    if args.apply:
        print_config_patch(report['weights'], report['thresholds'])


if __name__ == '__main__':
    main()
