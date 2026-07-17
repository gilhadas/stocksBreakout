#!/usr/bin/env python3
"""
Daytrade Admission A/B — does admitting daytrade signal files into the
auto-portfolio book help or hurt?

Replays the live signal-CSV backlog (scanner_output/signals, local + S3)
through a faithful copy of auto_portfolio.scan_and_add's admission logic
(V9-H mask → date-pooled ranking → MAX_ADDS_PER_SCAN cap → dedup-while-open
→ cash check), then simulates the live exit policy (ATR×2.0 always-on trail,
close-based, mirroring auto_portfolio.refresh_prices/_raise_atr_trail).

Arms:
  A — CONTROL:      all signal files (swing + longterm + daytrade)
  B — NO-DAYTRADE:  signals_daytrade_* files excluded
  C — DAYTRADE-ONLY: only daytrade files (diagnostic, not a candidate config)

The point of the A/B: with a shared daily cap and shared cash, daytrade
signals don't just add positions — they displace swing/longterm candidates
in the pooled ranking. Arm B measures what that displacement costs/saves.

Usage:
    python daytrade_admission_ab.py [--capital 10000] [--cap 10]
                                    [--start 2026-04-01] [--end 2026-06-30]

Simplifications vs production (identical across arms, so the comparison
holds): no ATR/event/balance sizing multipliers (flat 10% positions), no
split adjustment (window is Apr–Jun 2026), no FinBERT/earnings mults.
"""
import argparse
import asyncio
import re
import sys
from collections import defaultdict

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import numpy as np
import pandas as pd

from yfinance_adapter import YFinanceAdapter

MIN_MINERVINI = 7
TIEBREAK_DIST_CAP = 25.0
ATR_TRAIL_MULT = 2.0
ATR_BARS = 14
MAX_HOLD = {'swing': 30, 'daytrade': 30, 'longterm': 60}  # calendar days
QUALITY_MULT = {'GOLD': 2.0, 'PREMIUM': 2.0, 'HIGH': 1.5, 'STANDARD': 1.0}
POS_PCT = 0.05           # POSITION_SIZE_PCT
MAX_SINGLE_PCT = 0.10    # ATR_SIZING hard cap


def _date_from_filename(fname: str) -> str:
    m = re.search(r'(\d{8})', fname)
    return f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else ''


def _mode_from_filename(fname: str) -> str:
    m = re.match(r'signals_([a-z]+)_', fname)
    return m.group(1) if m else 'swing'


def load_signal_files(start: str, end: str) -> dict:
    """Return {date_str: [(fname, mode, DataFrame), ...]} for the window."""
    from utils import list_files, load_data
    fnames = sorted(list_files('scanner_output/signals', 'signals_*.csv'))
    by_date = defaultdict(list)
    for fname in fnames:
        d = _date_from_filename(fname)
        if not d or not (start <= d <= end):
            continue
        df = load_data(f"scanner_output/signals/{fname}")
        if df is None or df.empty:
            continue
        df.columns = [c.strip() for c in df.columns]
        if 'Symbol' not in df.columns and 'symbol' in df.columns:
            df = df.rename(columns={'symbol': 'Symbol'})
        if 'Quality' not in df.columns or 'Symbol' not in df.columns:
            continue
        by_date[d].append((fname, _mode_from_filename(fname), df))
    return by_date


def v9h_filter(df: pd.DataFrame) -> pd.DataFrame:
    """Mirror scan_and_add's V9-H mask: GOLD/PREMIUM; Minervini>=7 for breakout types."""
    mask = df['Quality'].isin(['GOLD', 'PREMIUM'])
    if 'MinerviniScore' in df.columns and 'Type' in df.columns:
        is_breakout = ~df['Type'].isin(['BOUNCE', 'CONTINUATION', 'SMA20_CROSS', 'TREND_CONFIRM'])
        minervini_ok = pd.to_numeric(df['MinerviniScore'], errors='coerce').fillna(0) >= MIN_MINERVINI
        mask = mask & (minervini_ok | ~is_breakout)
    elif 'MinerviniScore' in df.columns:
        mask = mask & (pd.to_numeric(df['MinerviniScore'], errors='coerce').fillna(0) >= MIN_MINERVINI)
    return df[mask].copy()


def pooled_rank(frames: list) -> pd.DataFrame:
    """Mirror scan_and_add's pooled sort: Quality → WinProb → R:R → Dist(cap25) → Vol."""
    v9h = pd.concat(frames, ignore_index=True)
    sort_cols, sort_asc = [], []
    v9h['_q_rank'] = v9h['Quality'].map({'GOLD': 0, 'PREMIUM': 1, 'HIGH': 2}).fillna(3)
    sort_cols.append('_q_rank'); sort_asc.append(True)
    if 'WinProb' in v9h.columns:
        v9h['_wp'] = pd.to_numeric(v9h['WinProb'], errors='coerce').fillna(0)
        sort_cols.append('_wp'); sort_asc.append(False)
    if 'R:R' in v9h.columns:
        v9h['_rr'] = pd.to_numeric(v9h['R:R'], errors='coerce').fillna(0)
        sort_cols.append('_rr'); sort_asc.append(False)
    if 'Dist' in v9h.columns:
        v9h['_dist'] = (pd.to_numeric(v9h['Dist'], errors='coerce')
                        .fillna(-1e9).clip(upper=TIEBREAK_DIST_CAP))
        sort_cols.append('_dist'); sort_asc.append(False)
    if 'Vol' in v9h.columns:
        v9h['_vol'] = pd.to_numeric(v9h['Vol'], errors='coerce').fillna(0)
        sort_cols.append('_vol'); sort_asc.append(False)
    v9h = v9h.sort_values(sort_cols, ascending=sort_asc)
    return v9h.drop_duplicates(subset=['Symbol'], keep='first')


def fetch_history(symbols: set, start: str, end: str) -> dict:
    adapter = YFinanceAdapter(use_disk_cache=True)
    fetch_start = (pd.Timestamp(start) - pd.Timedelta(days=45)).strftime('%Y-%m-%d')
    hist = {}
    for i, sym in enumerate(sorted(symbols)):
        try:
            df = adapter.get_historical_data(sym, '1 day', start_date=fetch_start, end_date=end)
            if df is not None and len(df) >= 5:
                df = df.copy()
                df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
                hist[sym] = df
        except Exception:
            pass
        if (i + 1) % 25 == 0:
            print(f"    fetched {i + 1}/{len(symbols)}", flush=True)
    return hist


def _trail_level(df: pd.DataFrame, upto) -> float | None:
    """ATR×2 trail candidate from the last 15 bars up to `upto` (mirrors _raise_atr_trail)."""
    window = df[df.index <= upto].tail(ATR_BARS + 1)
    if len(window) < ATR_BARS:
        return None
    tr = pd.concat([
        window['high'] - window['low'],
        (window['high'] - window['close'].shift(1)).abs(),
        (window['low'] - window['close'].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.mean())
    if atr <= 0:
        return None
    return float(window['close'].iloc[-1]) - ATR_TRAIL_MULT * atr


def run_arm(label: str, by_date: dict, hist: dict, trading_days: list,
            capital: float, cap_per_day: int, exclude_modes: set,
            end: str) -> dict:
    cash = capital
    open_pos = {}          # sym → position dict
    trades = []
    equity_curve = []

    dates_with_signals = {d: files for d, files in by_date.items()}

    for today in trading_days:
        d = today.strftime('%Y-%m-%d')

        # ── exits first (mirror refresh_prices: raise trail, then close-based check)
        for sym in list(open_pos):
            p = open_pos[sym]
            df = hist.get(sym)
            if df is None or today not in df.index:
                continue
            if today > p['entry_dt']:
                trail = _trail_level(df, today)
                if trail is not None and trail > p['stop']:
                    p['stop'] = trail
                cl = float(df.loc[today, 'close'])
                days_held = (today - p['entry_dt']).days
                exit_px, reason = None, None
                if cl <= p['stop']:
                    exit_px, reason = p['stop'], 'atr_trail_stop'
                elif days_held >= MAX_HOLD.get(p['mode'], 30):
                    exit_px, reason = cl, 'max_hold'
                if exit_px is not None:
                    pnl = (exit_px - p['entry']) * p['shares']
                    cash += exit_px * p['shares']
                    trades.append({**p, 'exit': exit_px, 'exit_date': d,
                                   'pnl': pnl, 'days': days_held, 'reason': reason})
                    del open_pos[sym]

        # ── admissions (date-pooled, ranked, capped)
        files = dates_with_signals.get(d, [])
        frames = []
        for fname, mode, df_raw in files:
            if mode in exclude_modes:
                continue
            df_f = v9h_filter(df_raw)
            if df_f.empty:
                continue
            df_f['_file_mode'] = mode
            frames.append(df_f)
        if frames:
            ranked = pooled_rank(frames)
            adds = 0
            for _, row in ranked.iterrows():
                if adds >= cap_per_day:
                    break
                sym = str(row.get('Symbol', '')).strip().upper()
                if not sym or sym == 'NAN' or sym in open_pos:
                    continue
                df = hist.get(sym)
                if df is None:
                    continue
                # entry at close of signal date (mirrors _fetch_entry_and_current)
                avail = df[df.index >= today]
                if avail.empty or (avail.index[0] - today).days > 3:
                    continue
                entry_dt = avail.index[0]
                entry = float(avail['close'].iloc[0])
                stop = float(pd.to_numeric(pd.Series([row.get('Stop')]), errors='coerce').iloc[0] or 0)
                # production guards: stop below entry, max 30% away
                if stop <= 0 or stop >= entry or (entry - stop) / entry > 0.30:
                    stop = round(entry * 0.95, 4)
                quality = str(row.get('Quality', 'PREMIUM'))
                pos_val = min(capital_now(cash, open_pos, hist, today) * POS_PCT
                              * QUALITY_MULT.get(quality, 1.0),
                              capital_now(cash, open_pos, hist, today) * MAX_SINGLE_PCT)
                shares = max(1, int(pos_val / entry))
                cost = shares * entry
                if cost > cash:
                    continue  # skipped_cash (no retry, mirrors production)
                cash -= cost
                open_pos[sym] = {
                    'symbol': sym, 'mode': str(row.get('Mode', row.get('_file_mode', 'swing'))).lower(),
                    'quality': quality, 'type': str(row.get('Type', '')),
                    'entry': entry, 'entry_dt': entry_dt, 'entry_date': d,
                    'stop': stop, 'shares': shares,
                }
                adds += 1

        # ── mark equity
        mv = 0.0
        for sym, p in open_pos.items():
            df = hist.get(sym)
            px = p['entry']
            if df is not None:
                upto = df[df.index <= today]
                if not upto.empty:
                    px = float(upto['close'].iloc[-1])
            mv += px * p['shares']
        equity_curve.append(cash + mv)

    # close remaining at last close
    last_day = trading_days[-1]
    for sym, p in list(open_pos.items()):
        df = hist.get(sym)
        px = p['entry']
        if df is not None:
            upto = df[df.index <= last_day]
            if not upto.empty:
                px = float(upto['close'].iloc[-1])
        pnl = (px - p['entry']) * p['shares']
        trades.append({**p, 'exit': px, 'exit_date': end, 'pnl': pnl,
                       'days': (last_day - p['entry_dt']).days, 'reason': 'open_marked'})

    eq = np.array(equity_curve)
    ret = (eq[-1] - capital) / capital * 100 if len(eq) else 0.0
    daily = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([0.0])
    sharpe = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0.0
    dd = float(((eq / np.maximum.accumulate(eq)) - 1).min() * 100) if len(eq) else 0.0
    wins = [t for t in trades if t['pnl'] > 0]
    short = [t for t in trades if t['days'] <= 15]
    longh = [t for t in trades if t['days'] > 15]

    def wr(ts):
        return 100 * sum(1 for t in ts if t['pnl'] > 0) / len(ts) if ts else 0.0

    by_mode = defaultdict(lambda: {'n': 0, 'pnl': 0.0})
    for t in trades:
        by_mode[t['mode']]['n'] += 1
        by_mode[t['mode']]['pnl'] += t['pnl']

    return {'label': label, 'return_pct': ret, 'sharpe': sharpe, 'max_dd': dd,
            'n_trades': len(trades), 'wr': wr(trades),
            'short_n': len(short), 'short_wr': wr(short),
            'long_n': len(longh), 'long_wr': wr(longh),
            'by_mode': dict(by_mode), 'trades': trades}


def capital_now(cash: float, open_pos: dict, hist: dict, today) -> float:
    mv = 0.0
    for sym, p in open_pos.items():
        df = hist.get(sym)
        px = p['entry']
        if df is not None:
            upto = df[df.index <= today]
            if not upto.empty:
                px = float(upto['close'].iloc[-1])
        mv += px * p['shares']
    return cash + mv


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--capital', type=float, default=10_000)
    ap.add_argument('--cap', type=int, default=10, help='MAX_ADDS_PER_SCAN (default 10)')
    ap.add_argument('--start', default='2026-04-01')
    ap.add_argument('--end', default='2026-06-30')
    args = ap.parse_args()

    print(f"Loading signal files {args.start} → {args.end} (local + S3)...")
    by_date = load_signal_files(args.start, args.end)
    n_files = sum(len(v) for v in by_date.values())
    mode_counts = defaultdict(int)
    for files in by_date.values():
        for _, mode, _df in files:
            mode_counts[mode] += 1
    print(f"  {n_files} files across {len(by_date)} dates — {dict(mode_counts)}")
    if not by_date:
        sys.exit("No signal files in window.")

    # candidate symbols = union of V9-H-passing symbols (all modes, incl. daytrade)
    symbols = set()
    for files in by_date.values():
        for _, _mode, df in files:
            symbols |= set(v9h_filter(df)['Symbol'].astype(str).str.strip().str.upper())
    symbols.discard('NAN')
    symbols.add('SPY')
    print(f"Fetching daily history for {len(symbols)} symbols...")
    hist = fetch_history(symbols, args.start, args.end)
    spy = hist.get('SPY')
    if spy is None:
        sys.exit("No SPY data — cannot build trading calendar.")
    trading_days = sorted(spy.index[(spy.index >= args.start) & (spy.index <= args.end)])
    spy_ret = (float(spy.loc[trading_days[-1], 'close']) /
               float(spy.loc[trading_days[0], 'close']) - 1) * 100

    arms = [
        ('A — CONTROL (all modes)',        set()),
        ('B — NO-DAYTRADE',                {'daytrade'}),
        ('C — DAYTRADE-ONLY (diagnostic)', {'swing', 'longterm'}),
    ]
    results = [run_arm(lbl, by_date, hist, trading_days, args.capital,
                       args.cap, excl, args.end) for lbl, excl in arms]

    print(f"\n{'=' * 100}")
    print(f"DAYTRADE ADMISSION A/B — {args.start} → {args.end}  "
          f"(cap={args.cap}, capital=${args.capital:,.0f}, SPY {spy_ret:+.2f}%)")
    print(f"{'=' * 100}")
    print(f"  {'Arm':<34} {'Return':>8} {'Sharpe':>7} {'MaxDD':>8} {'Trades':>7} "
          f"{'WR%':>6} {'≤15d n/WR':>12} {'>15d n/WR':>12}")
    print("  " + "-" * 98)
    for r in results:
        print(f"  {r['label']:<34} {r['return_pct']:>+7.2f}% {r['sharpe']:>7.2f} "
              f"{r['max_dd']:>+7.2f}% {r['n_trades']:>7} {r['wr']:>5.1f}% "
              f"{r['short_n']:>5}/{r['short_wr']:>4.1f}% {r['long_n']:>5}/{r['long_wr']:>4.1f}%")
    print("\n  Arm A per-mode P&L:")
    for mode, agg in sorted(results[0]['by_mode'].items()):
        print(f"    {mode:<10} {agg['n']:>4} trades  ${agg['pnl']:>+10.2f}")

    delta = results[1]['sharpe'] - results[0]['sharpe']
    print(f"\n  VERDICT: B−A Sharpe delta = {delta:+.2f} "
          f"({'drop daytrade admissions' if delta >= 0.10 else 'keep current champion' if delta > -0.10 else 'daytrade admissions HELP — keep'})"
          f"  [decision rule: ship if ≥ +0.10]")

    out = pd.DataFrame([{k: v for k, v in r.items() if k not in ('trades', 'by_mode')}
                        for r in results])
    out_path = 'scanner_output/backtests/daytrade_admission_ab.csv'
    out.to_csv(out_path, index=False)
    print(f"\n  Saved summary → {out_path}")


if __name__ == '__main__':
    main()
