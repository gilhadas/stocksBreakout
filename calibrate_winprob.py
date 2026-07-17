#!/usr/bin/env python3
"""
WinProb Calibration — replace the heuristic confluence-count win probability
with empirical win rates fitted from backtest trade logs.

Why: auto_portfolio and the backtest pooled cap rank admissions by
Quality → WinProb → R:R. The heuristic WinProb (base 30% + 6.4%/flag) is
uncalibrated, and BOUNCE/CONTINUATION/SMA20_CROSS/TREND_CONFIRM signals never
set WinProb at all (sort key = 0). This script fits WR by (signal_type,
quality) from champion-exit trade logs and writes a lookup table the scanner
consults at signal time.

Method:
  - Input: trades_YYYY_*_ATRalways-*.csv logs (champion exit policy) from
    scanner_output/backtests/. WR must be fitted under the same exit policy
    that live trading uses.
  - Fit on TRAIN years, report holdout WR on TEST years (leakage guard).
  - Empirical-Bayes shrinkage toward the global WR: a bucket with few trades
    stays near the prior; a bucket with hundreds dominates it:
        wr_hat = (wins + k·wr_global) / (n + k),   k = 10
  - Buckets below --min-n are dropped (scanner falls back to its heuristic).

Output: scanner_output/winprob_calibration.json
    {'buckets': {'BOUNCE|PREMIUM': {'win_prob': 0.61, 'n': 214, ...}, ...},
     'meta': {...}}

Usage:
    python calibrate_winprob.py                          # default glob + split
    python calibrate_winprob.py --train 2022,2023,2024 --test 2025,2026
    python calibrate_winprob.py --glob 'trades_*_pooled-10_ATRalways-2.csv'
"""
import argparse
import glob as globmod
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

BACKTEST_DIR = Path('scanner_output/backtests')
OUT_PATH = Path('scanner_output/winprob_calibration.json')
SHRINK_K = 10  # empirical-Bayes pseudo-count


def normalize_type(t) -> str:
    """Map trade-log signal_type to a calibration key. Plain breakouts log ''."""
    t = str(t or '').strip()
    return t if t and t.lower() != 'nan' else 'BREAKOUT'


def load_trades(pattern: str) -> pd.DataFrame:
    paths = sorted(globmod.glob(str(BACKTEST_DIR / pattern)))
    if not paths:
        raise SystemExit(f"No trade logs match {BACKTEST_DIR / pattern} — "
                         f"run backtest_regime_compare.py with --trades-log --atr-trail-always first.")
    frames = []
    for p in paths:
        df = pd.read_csv(p)
        m = re.search(r'trades_(\d{4})_', Path(p).name)
        df['year'] = int(m.group(1)) if m else 0
        df['source'] = Path(p).name
        frames.append(df)
    trades = pd.concat(frames, ignore_index=True)
    # SimEnd rows are censored (position still open at year boundary), not outcomes
    trades = trades[trades['reason'] != 'SimEnd'].copy()
    trades['type_key'] = trades['signal_type'].map(normalize_type)
    trades['quality'] = trades['quality'].astype(str).str.strip()
    print(f"Loaded {len(trades)} closed trades from {len(paths)} files "
          f"(years {sorted(trades['year'].unique())})")
    return trades


def fit(trades: pd.DataFrame, min_n: int) -> dict:
    global_wr = float(trades['win'].mean())
    buckets = {}
    for (t, q), grp in trades.groupby(['type_key', 'quality']):
        n = len(grp)
        wins = int(grp['win'].sum())
        wr_hat = (wins + SHRINK_K * global_wr) / (n + SHRINK_K)
        if n >= min_n:
            buckets[f"{t}|{q}"] = {
                'win_prob': round(wr_hat, 4),
                'raw_wr': round(wins / n, 4),
                'n': n,
                'avg_pnl_pct': round(float(grp['pnl_pct'].mean()), 3),
            }
    return {'global_wr': round(global_wr, 4), 'buckets': buckets}


def holdout_report(model: dict, test: pd.DataFrame):
    print(f"\nHoldout calibration check (test years):")
    print(f"  {'bucket':<28} {'predicted':>9} {'actual':>8} {'n_test':>7} {'err':>7}")
    print("  " + "-" * 64)
    rows = []
    for key, b in sorted(model['buckets'].items()):
        t, q = key.split('|')
        grp = test[(test['type_key'] == t) & (test['quality'] == q)]
        if len(grp) < 5:
            continue
        actual = float(grp['win'].mean())
        err = b['win_prob'] - actual
        rows.append(abs(err))
        print(f"  {key:<28} {b['win_prob']:>8.1%} {actual:>7.1%} {len(grp):>7} {err:>+7.1%}")
    if rows:
        print(f"  Mean |error|: {sum(rows) / len(rows):.1%} across {len(rows)} buckets")
    else:
        print("  (no buckets with >=5 test trades)")


def main():
    ap = argparse.ArgumentParser(description='Fit empirical WinProb from champion trade logs')
    ap.add_argument('--glob', default='trades_*_ATRalways-*.csv',
                    help='Trade-log glob under scanner_output/backtests/ (champion-exit logs only)')
    ap.add_argument('--train', default='2022,2023,2024', help='Fit years')
    ap.add_argument('--test', default='2025,2026', help='Holdout years (report only)')
    ap.add_argument('--min-n', type=int, default=20,
                    help='Min trades per bucket to publish (below → scanner heuristic fallback)')
    ap.add_argument('--all-years', action='store_true',
                    help='Refit on ALL years for the published table (after the holdout check '
                         'passes). Default publishes the train-only fit.')
    args = ap.parse_args()

    trades = load_trades(args.glob)
    train_years = {int(y) for y in args.train.split(',')}
    test_years = {int(y) for y in args.test.split(',')}
    train = trades[trades['year'].isin(train_years)]
    test = trades[trades['year'].isin(test_years)]
    print(f"Train: {len(train)} trades {sorted(train_years)} | "
          f"Test: {len(test)} trades {sorted(test_years)}")

    model = fit(train, args.min_n)
    print(f"\nGlobal train WR: {model['global_wr']:.1%}")
    print(f"Published buckets (n >= {args.min_n}):")
    for key, b in sorted(model['buckets'].items(), key=lambda kv: -kv[1]['n']):
        print(f"  {key:<28} wr={b['win_prob']:.1%} (raw {b['raw_wr']:.1%}, n={b['n']}, "
              f"avg_pnl={b['avg_pnl_pct']:+.2f}%)")

    if not test.empty:
        holdout_report(model, test)

    published = fit(trades, args.min_n) if args.all_years else model
    out = {
        'buckets': published['buckets'],
        'global_wr': published['global_wr'],
        'meta': {
            'fitted': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'glob': args.glob,
            'fit_years': 'all' if args.all_years else sorted(train_years),
            'holdout_years': sorted(test_years),
            'exit_policy': 'atr_trail_always_2.0',
            'shrinkage_k': SHRINK_K,
            'min_n': args.min_n,
            'key_format': 'SIGNAL_TYPE|QUALITY (BREAKOUT = plain breakout, logged as empty type)',
        },
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f"\nSaved calibration → {OUT_PATH}")


if __name__ == '__main__':
    main()
