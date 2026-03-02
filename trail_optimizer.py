"""
Trailing Stop Optimizer
=======================
Uses Optuna to find the best trailing-stop parameters for the auto virtual
portfolio, comparing three strategies:

  PCT  — trail_stop = max(initial_stop, highest_close * (1 - trail_pct))
  ATR  — trail_stop = max(initial_stop, highest_close - atr_mult * ATR14)
  BOTH — Optuna picks between PCT and ATR plus the associated numeric param

Pre-fetches historical data once, then re-simulates cheaply per Optuna trial.

Usage:
    python trail_optimizer.py                  # 200 trials, both modes
    python trail_optimizer.py --trials 300 --mode pct
    python trail_optimizer.py --trials 300 --mode atr
    python trail_optimizer.py --trials 200 --mode both

Output:
    scanner_output/optimizer/trail_best_YYYYMMDD.json
    (and a summary table printed to stdout)
"""

import argparse
import json
import os
import sys
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

# ── Project root on path ──────────────────────────────────────────────────────
_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT))

import auto_portfolio as ap

_NY_TZ = ZoneInfo('America/New_York')
_OUTPUT_DIR = _ROOT / 'scanner_output' / 'optimizer'

ATR_PERIOD  = 14          # bars for ATR calculation
ATR_WARMUP  = 45          # calendar days before entry to fetch for ATR warm-up


# ─── ATR helper (Wilder's EWM) ───────────────────────────────────────────────

def _atr_series(hist: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """Wilder's ATR using EWM smoothing (correct definition).

    yfinance uses capitalized column names: High, Low, Close.
    """
    tr = pd.concat([
        hist['High'] - hist['Low'],
        (hist['High'] - hist['Close'].shift(1)).abs(),
        (hist['Low']  - hist['Close'].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


# ─── Pre-fetch historical data for all positions ─────────────────────────────

def prefetch(positions: list[dict]) -> dict[str, pd.DataFrame]:
    """Download history for each symbol from before date_added to today.

    Returns {symbol: DataFrame} with timezone-naive DatetimeIndex.
    ATR warm-up period is included so the ATR series is accurate at entry.
    """
    hist_cache: dict[str, pd.DataFrame] = {}
    seen: set[str] = set()

    for p in positions:
        sym = p['symbol']
        if sym in seen:
            continue
        seen.add(sym)

        date_added = p['date_added']
        # Fetch extra history before entry for ATR warm-up
        warmup_start = (
            pd.Timestamp(date_added) - pd.Timedelta(days=ATR_WARMUP + 10)
        ).strftime('%Y-%m-%d')

        try:
            hist = yf.Ticker(sym.replace(' ', '-')).history(
                start=warmup_start, auto_adjust=True
            )
            if hist is None or hist.empty:
                print(f"  [WARN] No history for {sym}")
                continue

            # Strip timezone
            idx = hist.index
            if hasattr(idx, 'tz') and idx.tz is not None:
                idx = idx.tz_localize(None)
            hist.index = idx

            # Pre-compute ATR on the full (warm-up included) series
            hist['_ATR'] = _atr_series(hist)
            hist_cache[sym] = hist
            print(f"  {sym}: {len(hist)} bars from {hist.index[0].date()} "
                  f"(ATR at entry: {hist.loc[hist.index >= pd.Timestamp(date_added), '_ATR'].iloc[0]:.2f})")

        except Exception as e:
            print(f"  [WARN] {sym}: fetch failed — {e}")

    return hist_cache


# ─── Single-position simulation ───────────────────────────────────────────────

def _simulate_one(p: dict, hist: pd.DataFrame,
                  trail_pct: float, atr_mult: float) -> dict:
    """Simulate one position and return result dict.

    Args:
        trail_pct: % trailing if atr_mult == 0
        atr_mult:  ATR multiplier if > 0 (ATR mode)

    Returns dict with keys: symbol, pnl_pct, hold_days, closed, exit_date
    """
    date_added   = p['date_added']
    initial_stop = p['stop']
    entry_price  = p['entry_price']
    use_atr      = atr_mult > 0

    entry_dt        = pd.Timestamp(date_added)
    hist_from_entry = hist[hist.index >= entry_dt]
    if hist_from_entry.empty:
        return None

    highest_close = entry_price
    first_bar     = True

    for date, row in hist_from_entry.iterrows():
        close = float(row['Close'])
        low   = float(row['Low'])

        if first_bar:
            if close > highest_close:
                highest_close = close
            first_bar = False
            continue

        # Trailing stop level for this bar
        if use_atr:
            atr_val    = float(row.get('_ATR', np.nan))
            if np.isnan(atr_val):
                atr_val = float(hist['_ATR'].dropna().iloc[0])
            trail_stop = max(initial_stop,
                             round(highest_close - atr_mult * atr_val, 4))
        else:
            trail_stop = max(initial_stop,
                             round(highest_close * (1.0 - trail_pct), 4))

        if low <= trail_stop:
            exit_px    = trail_stop
            hold_days  = max(1, (date - entry_dt).days)
            pnl_pct    = (exit_px - entry_price) / entry_price * 100
            return {
                'symbol':     p['symbol'],
                'pnl_pct':   round(pnl_pct, 4),
                'hold_days': hold_days,
                'exit_date': date.strftime('%Y-%m-%d'),
                'exit_px':   round(exit_px, 4),
                'closed':    True,
            }

        if close > highest_close:
            highest_close = close

    # Not stopped out — mark to market at today's last close
    last_close = float(hist_from_entry['Close'].dropna().iloc[-1])
    hold_days  = max(1, (hist_from_entry.index[-1] - entry_dt).days)
    pnl_pct    = (last_close - entry_price) / entry_price * 100
    return {
        'symbol':     p['symbol'],
        'pnl_pct':   round(pnl_pct, 4),
        'hold_days': hold_days,
        'exit_date': hist_from_entry.index[-1].strftime('%Y-%m-%d'),
        'exit_px':   round(last_close, 4),
        'closed':    False,  # still holding (mark to market)
    }


# ─── Portfolio simulation (all positions for one trial) ──────────────────────

def simulate_portfolio(positions: list[dict], hist_cache: dict,
                       trail_pct: float, atr_mult: float) -> dict:
    """Simulate all positions and return aggregate metrics."""
    results = []
    for p in positions:
        hist = hist_cache.get(p['symbol'])
        if hist is None:
            continue
        r = _simulate_one(p, hist, trail_pct=trail_pct, atr_mult=atr_mult)
        if r is not None:
            results.append(r)

    if not results:
        return {'sharpe': -99.0, 'total_pnl_pct': 0.0, 'win_rate': 0.0, 'n': 0}

    returns = np.array([r['pnl_pct'] for r in results])
    wins    = np.sum(returns > 0)

    mean_r  = np.mean(returns)
    std_r   = np.std(returns, ddof=1) if len(returns) > 1 else 1.0
    if std_r < 0.01:
        std_r = 0.01
    sharpe = mean_r / std_r   # position-level Sharpe (not annualized)

    return {
        'sharpe':        round(float(sharpe), 4),
        'total_pnl_pct': round(float(mean_r), 4),
        'win_rate':      round(float(wins / len(results) * 100), 1),
        'n':             len(results),
        'results':       results,
    }


# ─── Baseline: initial fixed stop only ───────────────────────────────────────

def baseline_fixed_stop(positions: list[dict], hist_cache: dict) -> dict:
    """Simulate with no trailing stop — hold until initial_stop hit or today."""
    results = []
    for p in positions:
        hist = hist_cache.get(p['symbol'])
        if hist is None:
            continue

        date_added   = p['date_added']
        initial_stop = p['stop']
        entry_price  = p['entry_price']
        entry_dt     = pd.Timestamp(date_added)
        hist_fe      = hist[hist.index >= entry_dt]
        if hist_fe.empty:
            continue

        closed = False
        for date, row in hist_fe.iterrows():
            if float(row['Low']) <= initial_stop:
                exit_px   = initial_stop
                hold_days = max(1, (date - entry_dt).days)
                pnl_pct   = (exit_px - entry_price) / entry_price * 100
                results.append({'symbol': p['symbol'], 'pnl_pct': pnl_pct,
                                'hold_days': hold_days, 'closed': True})
                closed = True
                break

        if not closed:
            last_close = float(hist_fe['Close'].dropna().iloc[-1])
            hold_days  = max(1, (hist_fe.index[-1] - entry_dt).days)
            pnl_pct    = (last_close - entry_price) / entry_price * 100
            results.append({'symbol': p['symbol'], 'pnl_pct': pnl_pct,
                            'hold_days': hold_days, 'closed': False})

    if not results:
        return {'sharpe': -99.0, 'total_pnl_pct': 0.0, 'win_rate': 0.0, 'n': 0}

    returns = np.array([r['pnl_pct'] for r in results])
    wins    = np.sum(returns > 0)
    std_r   = max(np.std(returns, ddof=1) if len(returns) > 1 else 1.0, 0.01)
    return {
        'sharpe':        round(float(np.mean(returns) / std_r), 4),
        'total_pnl_pct': round(float(np.mean(returns)), 4),
        'win_rate':      round(float(wins / len(returns) * 100), 1),
        'n':             len(results),
    }


# ─── Optuna objective ─────────────────────────────────────────────────────────

def make_objective(positions, hist_cache, mode):
    import optuna

    def objective(trial):
        if mode == 'pct':
            trail_pct = trial.suggest_float('trail_pct', 0.03, 0.35)
            atr_mult  = 0.0
        elif mode == 'atr':
            trail_pct = 0.0
            atr_mult  = trial.suggest_float('atr_mult', 0.5, 6.0)
        else:  # both
            use_atr  = trial.suggest_categorical('use_atr', [True, False])
            if use_atr:
                trail_pct = 0.0
                atr_mult  = trial.suggest_float('atr_mult', 0.5, 6.0)
            else:
                trail_pct = trial.suggest_float('trail_pct', 0.03, 0.35)
                atr_mult  = 0.0

        metrics = simulate_portfolio(positions, hist_cache,
                                     trail_pct=trail_pct, atr_mult=atr_mult)
        if metrics['n'] < 3:
            return -99.0
        return metrics['sharpe']

    return objective


# ─── Pretty-print comparison table ───────────────────────────────────────────

def _row(label, m):
    return (f"  {label:<32} Sharpe={m['sharpe']:>+6.2f}  "
            f"AvgRet={m['total_pnl_pct']:>+6.1f}%  "
            f"WR={m['win_rate']:>5.1f}%  n={m['n']}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Trail stop optimizer')
    parser.add_argument('--trials', type=int, default=200,
                        help='Number of Optuna trials (default 200)')
    parser.add_argument('--mode', choices=['pct', 'atr', 'both'],
                        default='both',
                        help='Parameter mode to optimize (default: both)')
    args = parser.parse_args()

    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    # ── Load portfolio ────────────────────────────────────────────────────────
    data      = ap.load()
    positions = data['positions']
    if not positions:
        print("No open positions in auto portfolio. Run 'Scan Signals' first.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Trail Stop Optimizer  ({len(positions)} open positions)")
    print(f"Mode: {args.mode.upper()}   Trials: {args.trials}")
    print(f"{'='*60}\n")

    # ── Pre-fetch history ─────────────────────────────────────────────────────
    print("Pre-fetching historical data ...")
    hist_cache = prefetch(positions)
    valid_pos  = [p for p in positions if p['symbol'] in hist_cache]
    print(f"\n{len(valid_pos)}/{len(positions)} positions have data\n")

    if len(valid_pos) < 3:
        print("Need at least 3 positions with data. Aborting.")
        sys.exit(1)

    # ── Baseline (fixed stop, no trailing) ────────────────────────────────────
    baseline = baseline_fixed_stop(valid_pos, hist_cache)
    print("Baseline (fixed stop, no trailing):")
    print(_row("Fixed Stop", baseline))
    print()

    # ── Run Optuna study ─────────────────────────────────────────────────────
    study = optuna.create_study(direction='maximize',
                                sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(make_objective(valid_pos, hist_cache, args.mode),
                   n_trials=args.trials,
                   show_progress_bar=True)

    best = study.best_trial
    best_params  = best.params
    best_sharpe  = best.value

    # ── Evaluate best params ──────────────────────────────────────────────────
    if args.mode == 'pct':
        trail_pct_best = best_params['trail_pct']
        atr_mult_best  = 0.0
    elif args.mode == 'atr':
        trail_pct_best = 0.0
        atr_mult_best  = best_params['atr_mult']
    else:
        use_atr_best   = best_params.get('use_atr', False)
        trail_pct_best = 0.0 if use_atr_best else best_params.get('trail_pct', ap.TRAIL_PCT)
        atr_mult_best  = best_params.get('atr_mult', 0.0) if use_atr_best else 0.0

    best_metrics = simulate_portfolio(valid_pos, hist_cache,
                                      trail_pct=trail_pct_best,
                                      atr_mult=atr_mult_best)

    # ── Also evaluate current default (trail_pct=8%) for reference ───────────
    default_metrics = simulate_portfolio(valid_pos, hist_cache,
                                         trail_pct=ap.TRAIL_PCT, atr_mult=0.0)

    # ── Print results ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    print(_row("Fixed Stop (no trailing)", baseline))
    print(_row(f"Default PCT {ap.TRAIL_PCT*100:.0f}%", default_metrics))

    if args.mode in ('pct', 'both'):
        # Find best PCT-only for comparison
        pct_trials = [t for t in study.trials
                      if t.params.get('use_atr') is False or 'trail_pct' in t.params]
        if pct_trials:
            best_pct_t = max(pct_trials, key=lambda t: t.value or -99)
            pct_m = simulate_portfolio(valid_pos, hist_cache,
                                       trail_pct=best_pct_t.params.get('trail_pct', 0.08),
                                       atr_mult=0.0)
            print(_row(f"Best PCT ({best_pct_t.params.get('trail_pct', 0.08)*100:.1f}%)",
                       pct_m))

    if args.mode in ('atr', 'both'):
        atr_trials = [t for t in study.trials
                      if t.params.get('use_atr') is True or (
                          args.mode == 'atr' and 'atr_mult' in t.params)]
        if atr_trials:
            best_atr_t = max(atr_trials, key=lambda t: t.value or -99)
            atr_m = simulate_portfolio(valid_pos, hist_cache,
                                       trail_pct=0.0,
                                       atr_mult=best_atr_t.params.get('atr_mult', 2.5))
            print(_row(f"Best ATR ({best_atr_t.params.get('atr_mult', 2.5):.2f}x ATR14)",
                       atr_m))

    print(_row(f"** OVERALL BEST **", best_metrics))
    print()

    # ── Best params detail ────────────────────────────────────────────────────
    print("Best parameters found:")
    for k, v in best_params.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")

    # ── Per-position breakdown for best params ────────────────────────────────
    print(f"\nPer-position results (best params):")
    print(f"  {'Symbol':<8} {'Entry':>8} {'Exit':>8} {'Ret%':>7} {'Days':>5} {'Status':<10}")
    print(f"  {'-'*8} {'-'*8} {'-'*8} {'-'*7} {'-'*5} {'-'*10}")
    for r in sorted(best_metrics.get('results', []),
                    key=lambda x: x['pnl_pct'], reverse=True):
        sym    = r['symbol']
        ep     = next((p['entry_price'] for p in valid_pos if p['symbol'] == sym), 0)
        status = 'stopped' if r['closed'] else 'open'
        print(f"  {sym:<8} {ep:>8.2f} {r['exit_px']:>8.2f} "
              f"{r['pnl_pct']:>+7.1f}% {r['hold_days']:>5d} {status:<10}")

    # ── Save output ───────────────────────────────────────────────────────────
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(_NY_TZ).strftime('%Y%m%d')
    out_path = _OUTPUT_DIR / f'trail_best_{today}.json'

    output = {
        'run_date':      today,
        'mode':          args.mode,
        'trials':        args.trials,
        'n_positions':   len(valid_pos),
        'baseline':      baseline,
        'default_pct':   {'trail_pct': ap.TRAIL_PCT, **default_metrics},
        'best_params':   best_params,
        'best_sharpe':   best_sharpe,
        'best_metrics':  best_metrics,
        'recommendation': {
            'use_atr':   atr_mult_best > 0,
            'trail_pct': trail_pct_best,
            'atr_mult':  atr_mult_best,
        }
    }
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nResults saved → {out_path}")

    # ── Recommendation ────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("RECOMMENDATION")
    print(f"{'='*60}")
    if atr_mult_best > 0:
        print(f"  Use ATR mode: atr_mult = {atr_mult_best:.2f}")
        print(f"  In portfolio_page.py: set atr_mult={atr_mult_best:.2f}")
        print(f"  auto_portfolio.simulate_trailing_stops(atr_mult={atr_mult_best:.2f})")
    else:
        print(f"  Use PCT mode: trail_pct = {trail_pct_best:.4f} "
              f"({trail_pct_best*100:.1f}%)")
        print(f"  In auto_portfolio.py: set TRAIL_PCT = {trail_pct_best:.3f}")
        print(f"  auto_portfolio.simulate_trailing_stops(trail_pct={trail_pct_best:.3f})")

    improvement = best_metrics['sharpe'] - baseline['sharpe']
    if improvement > 0.1:
        print(f"\n  Trailing stop IMPROVES on fixed stop by {improvement:+.2f} Sharpe units.")
    elif improvement < -0.1:
        print(f"\n  Trailing stop HURTS vs fixed stop ({improvement:+.2f} Sharpe). "
              f"Consider keeping initial stops only.")
    else:
        print(f"\n  Trailing stop is NEUTRAL vs fixed stop ({improvement:+.2f} Sharpe).")
    print()


if __name__ == '__main__':
    main()
