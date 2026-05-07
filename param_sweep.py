#!/usr/bin/env python3
"""
Parameter sweep for the backtest_regime_compare.py champion config.

Scans signals ONCE per year (the expensive step), then re-runs simulate()
with every parameter combination (cheap — milliseconds each).

Objective: 5yr_compound_return  -  trade_penalty * total_trades
           with optional Sharpe floor filter.

Usage:
  python param_sweep.py --no-tc --bounce-bear-gate 15
  python param_sweep.py --no-tc --bounce-bear-gate 15 --optuna-trials 200
  python param_sweep.py --no-tc --bounce-bear-gate 15 --output-json sweep.json
"""

import argparse
import itertools
import json
import sys
import time
from pathlib import Path

try:
    import asyncio
    asyncio.get_event_loop()
except RuntimeError:
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())

import numpy as np
import pandas as pd

from backtest_regime_compare import (
    fetch_all_data, run_scan, spy_benchmark,
    simulate, _pooled_cap,
    SPY_ANNUAL, YEAR_TYPE,
)


# ─── Grid definition ─────────────────────────────────────────────────────────
# Each axis defines discrete values to sweep.
# Total combinations = product of all axis lengths.
PARAM_GRID = {
    'pooled_cap':        [2, 3, 5, 10, 15],
    'quality':           ['premium', 'high'],   # 'premium'=PREMIUM+, 'high'=HIGH+
    'bounce_bear_gate':  [0, 10, 15, 20],
    'max_hold':          [20, 30, 45, 60],
    'sma150_slope_gate': [False, True],
    'atr_trail_mult':    [1.0, 1.5, 2.0],
    'atr_trail_always':  [False, True],
}

# Focused ATR sub-grid: champion values fixed, only ATR axes swept.
# 6 combinations — use with --atr-only flag.
PARAM_GRID_ATR = {
    'pooled_cap':        [10],
    'quality':           ['premium'],
    'bounce_bear_gate':  [15],
    'max_hold':          [30],
    'sma150_slope_gate': [False],
    'atr_trail_mult':    [1.0, 1.5, 2.0],
    'atr_trail_always':  [False, True],
}

# Penalty per trade in objective = return_pct - TRADE_PENALTY * total_trades
TRADE_PENALTY = 0.01
SHARPE_FLOOR  = 1.0   # combos with avg_sharpe < floor are excluded from top-N


# ─── Helpers ─────────────────────────────────────────────────────────────────

def compound_return(year_returns: list[float]) -> float:
    c = 1.0
    for r in year_returns:
        c *= (1 + r / 100)
    return (c - 1) * 100


def objective(total_return_5yr: float, total_trades: int) -> float:
    return total_return_5yr - TRADE_PENALTY * total_trades


# ─── Signal cache ─────────────────────────────────────────────────────────────

def build_signal_cache(years, symbols, modes):
    """Scan signals once per year; return cache dict."""
    cache = {}
    for year in years:
        start = f"{year}-01-01"
        end   = f"{year}-12-31"
        print(f"\n  Fetching & scanning {year}...")
        historical, end_prices = fetch_all_data(symbols, start, end)
        if len(historical) < 5:
            print(f"  Not enough data for {year}, skipping.")
            continue
        spy_info = spy_benchmark(historical, start, end)
        new_signals = run_scan(historical, start, end, modes, config='new')
        new_premium = [s for s in new_signals if s.get('quality') in ('GOLD', 'PREMIUM')]
        new_high    = [s for s in new_signals if s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH')]
        cache[year] = {
            'historical':  historical,
            'end_prices':  end_prices,
            'new_premium': new_premium,
            'new_high':    new_high,
            'spy_info':    spy_info,
            'start':       start,
            'end':         end,
        }
        print(f"  {year}: {len(new_premium)} PREMIUM+ | {len(new_high)} HIGH+ signals cached")
    return cache


# ─── Single combination evaluation ───────────────────────────────────────────

def eval_combo(params: dict, cache: dict, base_capital: float,
               base_bounce_bear_gate: int, compound: bool = True) -> dict | None:
    """Evaluate one parameter combination across all cached years."""
    pooled_cap        = params['pooled_cap']
    quality           = params['quality']
    bbg               = params.get('bounce_bear_gate', base_bounce_bear_gate)
    max_hold          = params['max_hold']
    sma150_slope_gate = params.get('sma150_slope_gate', False)
    atr_trail_mult    = params.get('atr_trail_mult', 2.0)
    atr_trail_always  = params.get('atr_trail_always', False)

    year_returns = []
    year_trades  = []
    year_sharpes = []
    running_cap  = base_capital

    for year, d in sorted(cache.items()):
        raw_sigs = d['new_high'] if quality == 'high' else d['new_premium']
        pooled   = _pooled_cap(raw_sigs, max_per_day=pooled_cap)

        cap = running_cap if compound else base_capital

        rpt = simulate(
            pooled, d['start'], d['end'],
            d['end_prices'], d['historical'],
            capital=cap,
            tp_as_trail=not atr_trail_always,
            bounce_bear_gate=bbg,
            sma150_slope_gate=sma150_slope_gate,
            max_hold=max_hold,
            atr_trail_mult=atr_trail_mult,
            atr_trail_always=atr_trail_always,
        )
        if rpt is None:
            return None

        year_returns.append(rpt['total_return'])
        year_trades.append(rpt['total_trades'])
        year_sharpes.append(rpt['sharpe_ratio'])

        if compound:
            running_cap = rpt['final_capital']

    if not year_returns:
        return None

    total_ret    = compound_return(year_returns)
    total_trades = sum(year_trades)
    avg_sharpe   = float(np.mean(year_sharpes))
    obj          = objective(total_ret, total_trades)

    return {
        **params,
        'total_return_5yr': round(total_ret, 2),
        'total_trades':     total_trades,
        'avg_sharpe':       round(avg_sharpe, 3),
        'year_returns':     [round(r, 2) for r in year_returns],
        'year_trades':      year_trades,
        'objective':        round(obj, 2),
    }


# ─── Grid sweep ──────────────────────────────────────────────────────────────

def run_grid(cache, capital, base_bbg, compound=True, grid=None) -> list[dict]:
    g      = grid if grid is not None else PARAM_GRID
    keys   = list(g.keys())
    values = list(g.values())
    combos = list(itertools.product(*values))
    total  = len(combos)
    print(f"\n  Running {total} combinations ({total * len(cache)} simulate() calls)...")

    results = []
    t0 = time.time()
    for i, combo in enumerate(combos, 1):
        params = dict(zip(keys, combo))
        r = eval_combo(params, cache, capital, base_bbg, compound)
        if r is not None:
            results.append(r)
        if i % 50 == 0 or i == total:
            elapsed = time.time() - t0
            eta = elapsed / i * (total - i)
            print(f"    {i}/{total} done  ({elapsed:.0f}s elapsed, ETA {eta:.0f}s)")

    return results


# ─── Optuna sweep ────────────────────────────────────────────────────────────

def run_optuna(cache, capital, base_bbg, n_trials, compound=True):
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        print("  optuna not installed — skipping (pip install optuna)")
        return []

    def optuna_objective(trial):
        params = {
            'pooled_cap':        trial.suggest_int('pooled_cap', 2, 20),
            'quality':           trial.suggest_categorical('quality', ['premium', 'high']),
            'bounce_bear_gate':  trial.suggest_categorical('bounce_bear_gate', [0, 10, 15, 20]),
            'max_hold':          trial.suggest_int('max_hold', 15, 90),
            'sma150_slope_gate': trial.suggest_categorical('sma150_slope_gate', [False, True]),
            'atr_trail_mult':    trial.suggest_float('atr_trail_mult', 0.5, 3.0),
            'atr_trail_always':  trial.suggest_categorical('atr_trail_always', [False, True]),
        }
        r = eval_combo(params, cache, capital, base_bbg, compound)
        if r is None:
            return -999.0
        if r['avg_sharpe'] < SHARPE_FLOOR:
            return r['objective'] - 50  # penalize sub-floor Sharpe
        return r['objective']

    print(f"\n  Running Optuna TPE ({n_trials} trials)...")
    study = optuna.create_study(direction='maximize',
                                sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(optuna_objective, n_trials=n_trials, show_progress_bar=False)

    top = []
    for trial in sorted(study.trials, key=lambda t: -(t.value or -999))[:20]:
        if trial.value is None or trial.value < -900:
            continue
        params = trial.params
        r = eval_combo(params, cache, capital, base_bbg, compound)
        if r:
            top.append(r)
    return top


# ─── Print results ────────────────────────────────────────────────────────────

def print_results(results: list[dict], title: str, n: int = 20):
    if not results:
        print(f"  No results for {title}")
        return

    passed = [r for r in results if r['avg_sharpe'] >= SHARPE_FLOOR]
    ranked = sorted(passed, key=lambda r: -r['objective'])

    print(f"\n{'='*100}")
    print(f"  {title}  (Sharpe floor={SHARPE_FLOOR}, penalty={TRADE_PENALTY}/trade)")
    print(f"  Total evaluated: {len(results)} | Passing Sharpe floor: {len(passed)}")
    print(f"{'='*100}")
    hdr = (f"  {'Rank':>4}  {'PoolCap':>7}  {'Qual':>7}  {'BBG':>5}  "
           f"{'Hold':>5}  {'ATRmult':>7}  {'Always':>6}  "
           f"{'5yr%':>8}  {'Trades':>7}  {'Sharpe':>7}  {'Obj':>8}")
    print(hdr)
    print("  " + "-" * 105)

    for i, r in enumerate(ranked[:n], 1):
        atr_m  = f"{r.get('atr_trail_mult', 2.0):.1f}"
        always = 'Y' if r.get('atr_trail_always') else 'N'
        yr_str = " | ".join(f"{v:+.1f}" for v in r.get('year_returns', []))
        print(f"  {i:>4}  {r['pooled_cap']:>7}  {r['quality']:>7}  {r['bounce_bear_gate']:>5}  "
              f"{r['max_hold']:>5}  {atr_m:>7}  {always:>6}  "
              f"{r['total_return_5yr']:>+8.1f}%  {r['total_trades']:>7}  "
              f"{r['avg_sharpe']:>+7.2f}  {r['objective']:>+8.1f}  [{yr_str}]")

    # Champion (baseline: pooled_cap=10, premium, bbg=15, hold=30, no sma150)
    baseline = next((r for r in results if
                     r['pooled_cap'] == 10 and r['quality'] == 'premium' and
                     r['bounce_bear_gate'] == 15 and r['max_hold'] == 30 and
                     not r.get('atr_trail_always') and
                     abs(r.get('atr_trail_mult', 2.0) - 2.0) < 0.01), None)
    if baseline:
        print(f"\n  Champion baseline (pooled_cap=10, premium, bbg=15, hold=30, ATR×2 post-TP):")
        atr_m  = f"{baseline.get('atr_trail_mult', 2.0):.1f}"
        always = 'Y' if baseline.get('atr_trail_always') else 'N'
        yr_str = " | ".join(f"{v:+.1f}" for v in baseline.get('year_returns', []))
        print(f"  {'---':>4}  {baseline['pooled_cap']:>7}  {baseline['quality']:>7}  "
              f"{baseline['bounce_bear_gate']:>5}  {baseline['max_hold']:>5}  {atr_m:>7}  {always:>6}  "
              f"{baseline['total_return_5yr']:>+8.1f}%  {baseline['total_trades']:>7}  "
              f"{baseline['avg_sharpe']:>+7.2f}  {baseline['objective']:>+8.1f}  [{yr_str}]")
        if ranked:
            best = ranked[0]
            delta_ret    = best['total_return_5yr'] - baseline['total_return_5yr']
            delta_trades = best['total_trades']     - baseline['total_trades']
            print(f"\n  vs champion:  return {delta_ret:+.1f}%   trades {delta_trades:+d}   "
                  f"objective {best['objective'] - baseline['objective']:+.1f}")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description='Parameter sweep for backtest champion config')
    p.add_argument('--watchlist',         default=None)
    p.add_argument('--years',             default='2022,2023,2024,2025,2026')
    p.add_argument('--capital',           type=float, default=100_000)
    p.add_argument('--no-tc',             action='store_true')
    p.add_argument('--bounce-bear-gate',  type=int,   default=15,
                   help='Base BBG; also swept unless --fix-bbg is set')
    p.add_argument('--fix-bbg',           action='store_true',
                   help='Fix bounce_bear_gate to --bounce-bear-gate value (do not sweep it)')
    p.add_argument('--no-compound',       action='store_true',
                   help='Do not compound capital across years')
    p.add_argument('--optuna-trials',     type=int,   default=0,
                   help='Number of Optuna TPE trials (0=grid only)')
    p.add_argument('--output-json',       default=None,
                   help='Write full results to JSON file')
    p.add_argument('--top-n',            type=int,   default=20,
                   help='Top N results to print (default 20)')
    p.add_argument('--limit',            type=int,   default=0)
    p.add_argument('--atr-only',         action='store_true',
                   help='Use focused ATR sub-grid (champion values fixed, only ATR axes swept — 6 combos)')
    return p.parse_args()


def main():
    args = parse_args()

    if args.no_tc:
        import config as _cfg
        _cfg.TREND_CONFIRM['enabled'] = False
        print("⚠  --no-tc: TREND_CONFIRM disabled")

    years   = [int(y) for y in args.years.split(',')]
    modes   = ['swing', 'longterm']
    compound = not args.no_compound

    # Load symbols
    from backtest_regime_compare import load_symbols
    symbols = load_symbols(args.watchlist, args.limit)
    if not symbols:
        print("No symbols found.")
        return

    # Select grid
    active_grid = PARAM_GRID_ATR if args.atr_only else PARAM_GRID

    # Patch grid if BBG is fixed
    if args.fix_bbg and not args.atr_only:
        active_grid = dict(active_grid)
        active_grid['bounce_bear_gate'] = [args.bounce_bear_gate]
        print(f"  bounce_bear_gate fixed at {args.bounce_bear_gate}")

    n_combos = 1
    for v in active_grid.values():
        n_combos *= len(v)
    grid_label = "ATR sub-grid" if args.atr_only else "full grid"
    print(f"\nParam sweep: {n_combos} {grid_label} combinations × {len(years)} years")
    print(f"Compounding: {'ON' if compound else 'OFF'}")

    # Scan signals once per year
    print("\n=== SIGNAL SCAN PHASE ===")
    cache = build_signal_cache(years, symbols, modes)
    if not cache:
        print("No data cached.")
        return

    # Grid sweep
    print("\n=== GRID SWEEP ===")
    grid_results = run_grid(cache, args.capital, args.bounce_bear_gate, compound,
                            grid=active_grid)
    print_results(grid_results, "GRID SWEEP RESULTS", args.top_n)

    # Optuna sweep
    optuna_results = []
    if args.optuna_trials > 0:
        print("\n=== OPTUNA TPE SWEEP ===")
        optuna_results = run_optuna(cache, args.capital, args.bounce_bear_gate,
                                    args.optuna_trials, compound)
        print_results(optuna_results, "OPTUNA TPE RESULTS", args.top_n)

    # JSON output
    if args.output_json:
        all_results = {
            'grid': grid_results,
            'optuna': optuna_results,
            'config': {
                'years': years, 'capital': args.capital,
                'compound': compound, 'base_bbg': args.bounce_bear_gate,
                'trade_penalty': TRADE_PENALTY, 'sharpe_floor': SHARPE_FLOOR,
            },
        }
        Path(args.output_json).write_text(json.dumps(all_results, indent=2))
        print(f"\n  Results written to {args.output_json}")


if __name__ == '__main__':
    main()
