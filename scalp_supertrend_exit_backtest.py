#!/usr/bin/env python3
"""
scalp_supertrend_exit_backtest.py
=================================
Does Supertrend help SCALPING as a TRAILING-STOP EXIT (not an entry filter)?

The entry-filter form showed no edge (see scalp_supertrend_backtest.py). But
Supertrend's other canonical use is a dynamic trailing stop — the line rides up
under price and you exit when price falls to it. This directly targets scalping's
real failure mode: tight fixed cent-stops getting whipsawed out before a move.

Design (isolates the EXIT — entries are held constant)
-----------------------------------------------------
1. Collect scalping entries ONCE with the Supertrend entry-filter OFF (so the
   entry set is the unbiased baseline). Record entry / fixed stop / fixed target.
2. Precompute Supertrend on each symbol's full 1-min series (causal).
3. Simulate THREE exits on the SAME entries and forward bars:
     FIXED    — fixed cent stop + fixed target (baseline scalping exit)
     TRAIL    — Supertrend line as a trailing stop (prior-bar line, lookahead-safe),
                NO target cap -> let winners run
     TRAIL+TP — Supertrend trailing stop + keep the fixed target as an upside cap
4. Compare WR / avg / sum across the three on identical entries.

Honest constraints
------------------
* yfinance caps 1-min history at ~8 days -> short, single-regime window.
* Entries are NOT overlap-deduped (each evaluated independently) so the entry set
  is identical across exit methods; absolute `sum` overstates a tradeable book,
  but the RELATIVE exit comparison is apples-to-apples.

Usage
-----
  venv/bin/python scalp_supertrend_exit_backtest.py
  venv/bin/python scalp_supertrend_exit_backtest.py --watchlist input/optimizer_watch.txt --max-hold 40
"""
import argparse
import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())  # Python 3.14 event-loop setup (CLAUDE.md)

import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import yfinance as yf

import utils  # noqa: F401 — registers the SCAN log level used by scanner.py
import config
from config import MODES, SUPERTREND_CONFIG
from scanner import BreakoutDetector
from quantkit import calculate_supertrend

SLICE_BARS = 800
DEFAULT_MAX_HOLD = 30     # a touch longer than the entry-filter test so a trail has room to run


def _flatten(df):
    if df is None or df.empty:
        return df
    df = df.copy()
    df.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df.columns]
    return df


def fetch_1m(sym):
    return _flatten(yf.download(sym, period='8d', interval='1m', progress=False, auto_adjust=True))


# ─── Exit simulators (all share entry set; stop checked before target/trail) ───
def exit_fixed(df, pos, entry, stop, target, max_hold):
    n = len(df); last = min(pos + max_hold, n - 1)
    if last <= pos:
        return None
    for j in range(pos + 1, last + 1):
        if float(df['low'].iloc[j]) <= stop:
            return (stop - entry) / entry, j - pos, 'STOP'
        if float(df['high'].iloc[j]) >= target:
            return (target - entry) / entry, j - pos, 'TARGET'
    return (float(df['close'].iloc[last]) - entry) / entry, last - pos, 'TIME'


def exit_trail(df, st_line, pos, entry, target, max_hold, use_tp):
    """Supertrend trailing stop. Stop level during bar j is the PRIOR bar's line
    (st_line[j-1]) — known at the open of bar j, so lookahead-safe."""
    n = len(df); last = min(pos + max_hold, n - 1)
    if last <= pos:
        return None
    low = df['low'].values; high = df['high'].values; close = df['close'].values
    for j in range(pos + 1, last + 1):
        stop_level = st_line[j - 1]
        if np.isnan(stop_level):
            stop_level = -np.inf
        if low[j] <= stop_level:
            return (stop_level - entry) / entry, j - pos, 'TRAIL'
        if use_tp and high[j] >= target:
            return (target - entry) / entry, j - pos, 'TARGET'
    return (close[last] - entry) / entry, last - pos, 'TIME'


def collect_and_eval(symbols, data1m, max_hold):
    config.SUPERTREND_CONFIG['enabled'] = False        # unbiased entry set
    det = BreakoutDetector()
    lookback = MODES['scalping']['lookback']
    st_p, st_m = SUPERTREND_CONFIG['period'], SUPERTREND_CONFIG['multiplier']
    rows = []

    for sym in symbols:
        df = data1m.get(sym)
        if df is None or len(df) < SLICE_BARS:
            continue
        st_line, _ = calculate_supertrend(df, st_p, st_m)   # full causal series
        st_line = st_line.values
        prev_high = df['high'].rolling(lookback).max().shift(1)
        candidates = np.where((df['close'] > prev_high).values)[0]

        for pos in candidates:
            if pos < SLICE_BARS:
                continue
            ts = df.index[pos]
            sl = df.iloc[pos - SLICE_BARS + 1:pos + 1]
            try:
                sig = det.detect(sl, sym, 'scalping', '1 min', 0.0,
                                 use_scoring=True, use_legacy_momentum=False,
                                 use_v4_overextension=False, reference_date=ts.date())
            except Exception:
                continue
            if sig is None:
                continue
            entry, stop, target = float(sig['Price']), float(sig['Stop']), float(sig['Target'])
            if not (stop < entry < target):
                continue
            f = exit_fixed(df, int(pos), entry, stop, target, max_hold)
            t = exit_trail(df, st_line, int(pos), entry, target, max_hold, use_tp=False)
            ttp = exit_trail(df, st_line, int(pos), entry, target, max_hold, use_tp=True)
            if f is None or t is None or ttp is None:
                continue
            rows.append({'symbol': sym, 'quality': sig.get('Quality', ''),
                         'fixed': f[0], 'trail': t[0], 'trailtp': ttp[0],
                         'fixed_out': f[2], 'trail_out': t[2], 'trailtp_out': ttp[2],
                         'trail_bars': t[1]})
    return pd.DataFrame(rows)


def _summ(series):
    if series is None or len(series) == 0:
        return f"{'n=0':>6}   WR=  --    avg=  --      sum=  --"
    return (f"n={len(series):>4}   WR={(series>0).mean()*100:5.1f}%   "
            f"avg={series.mean()*100:+6.3f}%   sum={series.sum()*100:+7.1f}%")


def report(t):
    print("\n" + "=" * 80)
    print("SCALPING SUPERTREND *EXIT* BACKTEST — same entries, 3 exit methods (1-min, ~8d)")
    print("=" * 80)
    for label, q in [('ALL entries', None), ('HIGH+', ('GOLD', 'PREMIUM', 'HIGH'))]:
        sub = t if q is None else t[t['quality'].isin(q)]
        print(f"\n  [{label}]")
        print(f"    FIXED (stop+TP)        : {_summ(sub['fixed'])}")
        print(f"    TRAIL (Supertrend)     : {_summ(sub['trail'])}")
        print(f"    TRAIL+TP (hybrid)      : {_summ(sub['trailtp'])}")
    # head-to-head on identical entries
    print("\n" + "-" * 80)
    print("  Per-entry deltas vs FIXED (paired — same entries)")
    print("-" * 80)
    for name in ['trail', 'trailtp']:
        d = (t[name] - t['fixed'])
        better = (d > 0).mean() * 100
        print(f"    {name:>9} - fixed : mean Δ={d.mean()*100:+.3f}%   "
              f"{name} beats fixed on {better:4.1f}% of entries")
    print("\n  Exit-reason mix (TRAIL):")
    print("   ", t['trail_out'].value_counts().to_dict(), f"| median trail hold={t['trail_bars'].median():.0f} bars")
    print("=" * 80)


def main():
    ap = argparse.ArgumentParser(description="Scalping Supertrend trailing-stop EXIT backtest")
    ap.add_argument('--watchlist', default='input/optimizer_watch.txt')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--max-hold', type=int, default=DEFAULT_MAX_HOLD)
    args = ap.parse_args()

    with open(args.watchlist) as f:
        symbols = [ln.strip().upper() for ln in f if ln.strip() and not ln.startswith('#')]
    if args.limit:
        symbols = symbols[:args.limit]

    print("Scalping Supertrend EXIT Backtest")
    print(f"  Watchlist : {args.watchlist}  ({len(symbols)} symbols)")
    print(f"  Max hold  : {args.max_hold} one-min bars | ST period={SUPERTREND_CONFIG['period']} mult={SUPERTREND_CONFIG['multiplier']}")
    print(f"  Fetching 1-min (8d) bars ...")

    data1m = {}
    for i, sym in enumerate(symbols, 1):
        df = fetch_1m(sym)
        if df is not None and not df.empty:
            data1m[sym] = df
        if i % 10 == 0:
            print(f"    fetched {i}/{len(symbols)} ...")

    usable = [s for s in symbols if s in data1m and len(data1m[s]) >= SLICE_BARS]
    if not usable:
        print("  No usable symbols. Aborting.")
        return
    rng = data1m[usable[0]].index
    print(f"  Loaded {len(usable)} usable symbols | 1m window {rng[0].date()} -> {rng[-1].date()}")

    print("\n  Collecting entries (filter OFF) + evaluating 3 exits ...")
    t = collect_and_eval(usable, data1m, args.max_hold)
    print(f"    {len(t)} entries evaluated")
    if len(t):
        report(t)


if __name__ == '__main__':
    main()
