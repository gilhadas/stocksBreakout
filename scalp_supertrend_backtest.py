#!/usr/bin/env python3
"""
scalp_supertrend_backtest.py
============================
Mini *1-minute* backtest answering: does the V15 Supertrend filter help
SCALPING mode? (the canonical VWAP + Supertrend + StochRSI scalping triad —
VWAP and StochRSI already exist in the scanner; this adds Supertrend.)

Why a bespoke harness
---------------------
Scalping is 1-min and intraday-only; the swing/daytrade backtests feed daily
bars and produce 0 scalping signals. Supertrend is wired into detect() as the
`supertrend_bull` scoring check (config SUPERTREND_CONFIG, weight 15), so this
toggles SUPERTREND_CONFIG['enabled'] to compare ON vs OFF on real 1-min data.

Honest constraints
------------------
* yfinance caps 1-min history at ~8 days -> a very short, single-regime window.
  DIRECTIONAL read only.
* Exits are a simple tight stop / target with a short max-hold, not a full
  fills/spread/queue simulation. Per-trade WR + avg return are the comparison.

Method
------
1. Fetch 1-min bars (8d) per symbol (scalping needs no market/sector context —
   tension + RS are disabled for scalping).
2. Pre-filter to candidate breakout bars (close > prior-`lookback` high) — this
   is detect()'s own mandatory gate, so nothing is missed.
3. Slice ~2 sessions of trailing bars (so the current day's VWAP starts at the
   session open) and call detect() in scalping mode with a per-slice
   reference_date (defeats the >7d stale-data guard).
4. Simulate each signal's exit (stop / target / max-hold), no overlaps.
5. Run twice -- Supertrend ON vs OFF -- and compare. Plus an UNBIASED
   direction split computed on the OFF arm (full, unfiltered population):
   do Supertrend-bullish scalps beat Supertrend-bearish ones?

Usage
-----
  venv/bin/python scalp_supertrend_backtest.py
  venv/bin/python scalp_supertrend_backtest.py --watchlist input/optimizer_watch.txt --limit 30
  venv/bin/python scalp_supertrend_backtest.py --max-hold 15
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

SLICE_BARS = 800          # ~2 regular sessions of 1-min bars (keeps current-day VWAP from the open)
DEFAULT_MAX_HOLD = 20     # scalping = seconds-to-minutes; cap the hold at ~20 one-min bars


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    df.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df.columns]
    return df


def fetch_1m(sym: str) -> pd.DataFrame:
    return _flatten(yf.download(sym, period='8d', interval='1m',
                                progress=False, auto_adjust=True))


def simulate_exit(df: pd.DataFrame, entry_pos: int, entry: float,
                  stop: float, target: float, max_hold: int):
    """Walk forward bar-by-bar; stop checked before target within a bar
    (conservative). Returns (exit_price, bars_held, outcome) or None."""
    n = len(df)
    last = min(entry_pos + max_hold, n - 1)
    if last <= entry_pos:
        return None
    for j in range(entry_pos + 1, last + 1):
        if float(df['low'].iloc[j]) <= stop:
            return stop, j - entry_pos, 'STOP'
        if float(df['high'].iloc[j]) >= target:
            return target, j - entry_pos, 'TARGET'
    return float(df['close'].iloc[last]), last - entry_pos, 'TIME'


def run_arm(enabled: bool, symbols, data1m, max_hold: int):
    """One arm. `st_dir` is recorded on every trade (computed independently of the
    filter) so the OFF arm gives an unbiased Supertrend direction split."""
    config.SUPERTREND_CONFIG['enabled'] = enabled       # scanner shares this dict object
    det = BreakoutDetector()
    lookback = MODES['scalping']['lookback']
    st_p, st_m = SUPERTREND_CONFIG['period'], SUPERTREND_CONFIG['multiplier']
    trades = []

    for sym in symbols:
        df = data1m.get(sym)
        if df is None or len(df) < SLICE_BARS:
            continue
        prev_high = df['high'].rolling(lookback).max().shift(1)
        candidates = np.where((df['close'] > prev_high).values)[0]

        open_until = -1
        for pos in candidates:
            if pos < SLICE_BARS or pos <= open_until:
                continue
            ts = df.index[pos]
            sl = df.iloc[pos - SLICE_BARS + 1:pos + 1]
            try:
                sig = det.detect(
                    sl, sym, 'scalping', '1 min', 0.0,
                    use_scoring=True, use_legacy_momentum=False, use_v4_overextension=False,
                    reference_date=ts.date(),
                )
            except Exception:
                continue
            if sig is None:
                continue
            entry = float(sig['Price'])
            stop = float(sig['Stop'])
            target = float(sig['Target'])
            if not (stop < entry < target):
                continue
            res = simulate_exit(df, int(pos), entry, stop, target, max_hold)
            if res is None:
                continue
            exit_price, bars, outcome = res
            # Unbiased entry-time Supertrend direction (independent of the filter arm)
            try:
                _, _dir = calculate_supertrend(sl, st_p, st_m)
                st_dir = int(_dir.iloc[-1])
            except Exception:
                st_dir = 0
            trades.append({
                'symbol': sym, 'time': ts, 'quality': sig.get('Quality', ''),
                'st_dir': st_dir, 'ret': (exit_price - entry) / entry,
                'outcome': outcome, 'bars': bars,
            })
            open_until = int(pos) + bars

    return pd.DataFrame(trades)


def _summ(df: pd.DataFrame) -> str:
    if df is None or len(df) == 0:
        return f"{'n=0':>6}   WR=  --    avg=  --      sum=  --"
    wr = (df['ret'] > 0).mean() * 100
    return (f"n={len(df):>4}   WR={wr:5.1f}%   avg={df['ret'].mean()*100:+6.3f}%   "
            f"sum={df['ret'].sum()*100:+7.1f}%")


def report(on: pd.DataFrame, off: pd.DataFrame):
    print("\n" + "=" * 78)
    print("SCALPING SUPERTREND BACKTEST — filter ON vs OFF  (1-min bars, ~8d, single regime)")
    print("=" * 78)
    for label, q in [('ALL signals', None), ('HIGH+', ('GOLD', 'PREMIUM', 'HIGH')),
                     ('PREMIUM+', ('GOLD', 'PREMIUM'))]:
        on_f = on if q is None else on[on['quality'].isin(q)]
        off_f = off if q is None else off[off['quality'].isin(q)]
        print(f"\n  [{label}]")
        print(f"    Supertrend OFF : {_summ(off_f)}")
        print(f"    Supertrend ON  : {_summ(on_f)}")

    print("\n" + "-" * 78)
    print("  Supertrend direction split — OFF arm, unfiltered  (the core 'does it separate' test)")
    print("-" * 78)
    if off is None or len(off) == 0:
        print("    (no trades)")
    else:
        print(f"    {'entry ST dir':>14}   {'n':>4}   {'WR':>6}   {'avg ret':>9}")
        for lab, d in [('bullish (+1)', 1), ('bearish (-1)', -1)]:
            sub = off[off['st_dir'] == d]
            if len(sub) == 0:
                print(f"    {lab:>14}   {0:>4}      --          --")
            else:
                print(f"    {lab:>14}   {len(sub):>4}   {(sub['ret']>0).mean()*100:5.1f}%   "
                      f"{sub['ret'].mean()*100:+8.3f}%")
        bull = off[off['st_dir'] == 1]['ret']
        bear = off[off['st_dir'] == -1]['ret']
        if len(bull) and len(bear):
            print(f"\n    bullish minus bearish avg return = "
                  f"{(bull.mean()-bear.mean())*100:+.3f}%  (>0 means Supertrend helps)")
    print("=" * 78)


def main():
    ap = argparse.ArgumentParser(description="Scalping Supertrend filter backtest (1-min, 8d)")
    ap.add_argument('--watchlist', default='input/optimizer_watch.txt')
    ap.add_argument('--limit', type=int, default=0, help='Max symbols (0=all)')
    ap.add_argument('--max-hold', type=int, default=DEFAULT_MAX_HOLD,
                    help='Max 1-min bars before forced exit (default 20)')
    args = ap.parse_args()

    with open(args.watchlist) as f:
        symbols = [ln.strip().upper() for ln in f if ln.strip() and not ln.startswith('#')]
    if args.limit:
        symbols = symbols[:args.limit]

    print("Scalping Supertrend Backtest")
    print(f"  Watchlist : {args.watchlist}  ({len(symbols)} symbols)")
    print(f"  Max hold  : {args.max_hold} one-min bars")
    print(f"  Supertrend: period={SUPERTREND_CONFIG['period']} mult={SUPERTREND_CONFIG['multiplier']}")
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
        print("  No usable symbols (need >= %d 1-min bars). Aborting." % SLICE_BARS)
        return
    rng = data1m[usable[0]].index
    print(f"  Loaded {len(usable)} usable symbols | 1m window {rng[0].date()} -> {rng[-1].date()}")

    print("\n  Running arm: Supertrend OFF ...")
    off = run_arm(False, usable, data1m, args.max_hold)
    print(f"    {len(off)} trades")
    print("  Running arm: Supertrend ON  ...")
    on = run_arm(True, usable, data1m, args.max_hold)
    print(f"    {len(on)} trades")

    report(on, off)


if __name__ == '__main__':
    main()
