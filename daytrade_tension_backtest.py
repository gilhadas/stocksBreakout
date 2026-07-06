#!/usr/bin/env python3
"""
daytrade_tension_backtest.py
============================
Mini *intraday* backtest answering one question: does the V14 Tension Index
(quantkit/tension.py) help DAYTRADE-mode signals?

Why this exists
---------------
The 5-year swing backtest (backtest_regime_compare.py) cannot test daytrade:
that harness hardcodes modes=['swing','longterm'] and feeds DAILY bars, on
which daytrade's intraday-tuned gates produce 0 signals. Tension is wired only
into BreakoutDetector.detect() (the BREAKOUT path); the swing champion is ~100%
BOUNCE, so tension never touches it there either. Daytrade is the one mode whose
signals (a) flow through detect() and (b) exercise tension's fractal-alignment
component (15-min structure vs the daily trend).

Honest constraints
------------------
* yfinance caps 15-min history at ~60 days -> recent, SINGLE-REGIME window only.
  This is a DIRECTIONAL read, not a multi-regime conclusion.
* Daytrade breakouts are sparse (~1-3 per liquid symbol / 60d). Use the full
  watchlist to accumulate a usable sample.
* Exits are a simple intraday ATR stop / target with a max-hold cap, not a
  full portfolio simulation. Per-trade WR + avg return are the comparison.

Method
------
1. Fetch 15-min bars (60d) + daily bars (1y) per symbol; SPY daily + each
   sector ETF daily (lookahead-safe: daily context sliced to < signal date).
2. Pre-filter to candidate breakout bars (close > prior-`lookback` high) to
   avoid 75k pointless detect() calls; this is detect()'s own mandatory gate.
3. Call detect() in daytrade mode with full daily context so tension's
   confirmation + fractal sub-scores are REAL (not degraded).
4. Simulate each signal's intraday exit (stop / target / max-hold), no
   overlapping positions per symbol.
5. Run twice -- tension ON vs OFF -- and compare. Plus a tension-band table
   (from the ON arm) showing whether higher tension -> better daytrade outcomes.

Usage
-----
  venv/bin/python daytrade_tension_backtest.py
  venv/bin/python daytrade_tension_backtest.py --watchlist input/optimizer_watch.txt --limit 40
  venv/bin/python daytrade_tension_backtest.py --max-hold 13
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
from config import MODES, SENTIMENT
from scanner import BreakoutDetector

try:
    from sentiment import get_sector_for_ticker
except Exception:                                   # pragma: no cover - defensive
    def get_sector_for_ticker(_sym):
        return None

SLICE_BARS = 320          # trailing 15-min bars handed to detect() (keeps indicator calc fast)
DEFAULT_MAX_HOLD = 26     # ~1 trading day of 15-min bars


# ─── Data helpers ─────────────────────────────────────────────────────────────
def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    df.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df.columns]
    return df


def fetch_15m(sym: str) -> pd.DataFrame:
    return _flatten(yf.download(sym, period='60d', interval='15m',
                                progress=False, auto_adjust=True))


def fetch_daily(sym: str) -> pd.DataFrame:
    return _flatten(yf.download(sym, period='1y', interval='1d',
                                progress=False, auto_adjust=True))


def resolve_sector_etf(sym: str) -> str:
    try:
        sec = get_sector_for_ticker(sym)
    except Exception:
        sec = None
    return SENTIMENT.get('sector_etfs', {}).get(sec, {}).get('etf', 'SPY')


def _before(df: pd.DataFrame, the_date) -> pd.DataFrame:
    """Lookahead-safe slice: rows strictly before `the_date` (a datetime.date)."""
    if df is None or df.empty:
        return df
    return df[np.array([d.date() < the_date for d in df.index])]


# ─── Exit simulation ──────────────────────────────────────────────────────────
def simulate_exit(df15: pd.DataFrame, entry_pos: int, entry: float,
                  stop: float, target: float, max_hold: int):
    """Walk forward bar-by-bar. Stop checked before target within a bar
    (conservative). Returns (exit_price, bars_held, outcome) or None if no
    forward data exists."""
    n = len(df15)
    last = min(entry_pos + max_hold, n - 1)
    if last <= entry_pos:
        return None
    for j in range(entry_pos + 1, last + 1):
        low = float(df15['low'].iloc[j])
        high = float(df15['high'].iloc[j])
        if low <= stop:
            return stop, j - entry_pos, 'STOP'
        if high >= target:
            return target, j - entry_pos, 'TARGET'
    return float(df15['close'].iloc[last]), last - entry_pos, 'TIME'


# ─── One arm (tension ON or OFF) ──────────────────────────────────────────────
def run_arm(enabled: bool, symbols, data15, datad, spy_daily,
            sector_daily, sector_of, max_hold: int):
    config.TENSION_CONFIG['enabled'] = enabled          # scanner shares this dict object
    det = BreakoutDetector()
    lookback = MODES['daytrade']['lookback']
    trades = []

    for sym in symbols:
        df15 = data15.get(sym)
        dfd = datad.get(sym)
        if df15 is None or len(df15) < SLICE_BARS or dfd is None or dfd.empty:
            continue

        prev_high = df15['high'].rolling(lookback).max().shift(1)
        candidates = np.where((df15['close'] > prev_high).values)[0]
        sec_df_full = sector_daily.get(sector_of.get(sym, 'SPY'))

        open_until = -1
        for pos in candidates:
            if pos < SLICE_BARS or pos <= open_until:
                continue
            ts = df15.index[pos]
            sl15 = df15.iloc[pos - SLICE_BARS + 1:pos + 1]
            dd = _before(dfd, ts.date())
            sd = _before(spy_daily, ts.date())
            secd = _before(sec_df_full, ts.date()) if sec_df_full is not None else None
            try:
                sig = det.detect(
                    sl15, sym, 'daytrade', '15 mins', 0.0,
                    use_scoring=True, use_legacy_momentum=False, use_v4_overextension=False,
                    reference_date=ts.date(),
                    daily_df=dd, spy_df=sd, sector_df=secd,
                )
            except Exception:
                continue
            if sig is None:
                continue

            entry = float(sig['Price'])
            stop = float(sig['Stop'])
            target = float(sig['Target'])
            if not (stop < entry < target):
                continue  # malformed risk geometry — skip
            res = simulate_exit(df15, int(pos), entry, stop, target, max_hold)
            if res is None:
                continue
            exit_price, bars, outcome = res
            tval = sig.get('Tension')
            trades.append({
                'symbol': sym,
                'time': ts,
                'quality': sig.get('Quality', ''),
                'tension': float(tval) if tval not in ('', None) else np.nan,
                'ret': (exit_price - entry) / entry,
                'outcome': outcome,
                'bars': bars,
            })
            open_until = int(pos) + bars   # no overlapping positions per symbol

    return pd.DataFrame(trades)


# ─── Reporting ────────────────────────────────────────────────────────────────
def _summ(df: pd.DataFrame) -> str:
    if df is None or len(df) == 0:
        return f"{'n=0':>6}   WR=  --    avg=  --      sum=  --"
    wr = (df['ret'] > 0).mean() * 100
    avg = df['ret'].mean() * 100
    tot = df['ret'].sum() * 100        # equal-weight sum of per-trade returns (proxy)
    return f"n={len(df):>4}   WR={wr:5.1f}%   avg={avg:+6.2f}%   sum={tot:+7.1f}%"


def report(on: pd.DataFrame, off: pd.DataFrame):
    print("\n" + "=" * 78)
    print("DAYTRADE TENSION BACKTEST — tension ON vs OFF  (15-min bars, ~60d, single regime)")
    print("=" * 78)
    for label, q in [('ALL signals', None), ('HIGH+', ('GOLD', 'PREMIUM', 'HIGH')),
                     ('PREMIUM+', ('GOLD', 'PREMIUM'))]:
        on_f = on if q is None else on[on['quality'].isin(q)]
        off_f = off if q is None else off[off['quality'].isin(q)]
        print(f"\n  [{label}]")
        print(f"    tension OFF : {_summ(off_f)}")
        print(f"    tension ON  : {_summ(on_f)}")

    # Tension-band table from the ON arm: does higher tension -> better outcomes?
    print("\n" + "-" * 78)
    print("  Tension-band outcome split (ON arm — the core 'does tension separate' test)")
    print("-" * 78)
    band = on.dropna(subset=['tension'])
    if len(band) == 0:
        print("    (no tension-scored trades)")
    else:
        edges = [0.0, 0.45, 0.50, 0.55, 1.01]
        labels = ['<0.45', '0.45-0.50', '0.50-0.55', '>=0.55']
        band = band.assign(_b=pd.cut(band['tension'], bins=edges, labels=labels, right=False))
        print(f"    {'band':>10}   {'n':>4}   {'WR':>6}   {'avg ret':>8}")
        for lab in labels:
            sub = band[band['_b'] == lab]
            if len(sub) == 0:
                print(f"    {lab:>10}   {0:>4}      --         --")
            else:
                print(f"    {lab:>10}   {len(sub):>4}   {(sub['ret']>0).mean()*100:5.1f}%   "
                      f"{sub['ret'].mean()*100:+7.2f}%")
        corr = band['tension'].corr(band['ret'])
        print(f"\n    Pearson corr(tension, trade return) = {corr:+.3f}  "
              f"(n={len(band)})  >0 means tension is predictive")
    print("=" * 78)


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Daytrade Tension Index backtest (intraday, 60d)")
    ap.add_argument('--watchlist', default='input/optimizer_watch.txt')
    ap.add_argument('--limit', type=int, default=0, help='Max symbols (0=all)')
    ap.add_argument('--max-hold', type=int, default=DEFAULT_MAX_HOLD,
                    help='Max 15-min bars to hold before forced exit (default 26 = ~1 day)')
    args = ap.parse_args()

    with open(args.watchlist) as f:
        symbols = [ln.strip().upper() for ln in f if ln.strip() and not ln.startswith('#')]
    if args.limit:
        symbols = symbols[:args.limit]

    print(f"Daytrade Tension Backtest")
    print(f"  Watchlist : {args.watchlist}  ({len(symbols)} symbols)")
    print(f"  Max hold  : {args.max_hold} bars (~{args.max_hold/26:.1f} trading days)")
    print(f"  Fetching 15-min (60d) + daily (1y) + SPY + sector ETFs ...")

    data15, datad, sector_of, sector_etfs = {}, {}, {}, set()
    for i, sym in enumerate(symbols, 1):
        df15 = fetch_15m(sym)
        if df15 is None or df15.empty:
            continue
        data15[sym] = df15
        datad[sym] = fetch_daily(sym)
        etf = resolve_sector_etf(sym)
        sector_of[sym] = etf
        sector_etfs.add(etf)
        if i % 10 == 0:
            print(f"    fetched {i}/{len(symbols)} ...")

    spy_daily = fetch_daily('SPY')
    sector_daily = {etf: fetch_daily(etf) for etf in sector_etfs}
    usable = [s for s in symbols if s in data15 and len(data15[s]) >= SLICE_BARS]
    rng = next(iter(data15.values())).index
    print(f"  Loaded {len(usable)} usable symbols | 15m window {rng[0].date()} -> {rng[-1].date()}")
    print(f"  Sector ETFs: {sorted(sector_etfs)}")

    print("\n  Running arm: tension OFF ...")
    off = run_arm(False, usable, data15, datad, spy_daily, sector_daily, sector_of, args.max_hold)
    print(f"    {len(off)} trades")
    print("  Running arm: tension ON  ...")
    on = run_arm(True, usable, data15, datad, spy_daily, sector_daily, sector_of, args.max_hold)
    print(f"    {len(on)} trades")

    report(on, off)


if __name__ == '__main__':
    main()
