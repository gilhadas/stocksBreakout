#!/usr/bin/env python3
"""
validate_trades.py
==================
Replays the last N days of signals with:
  • Updated structural stop-loss (swing low − buffer)
  • Updated take-profit (Fibonacci 1.618 extension from consolidation range)

Two trade flows are simulated for every signal:
  Flow A — Fixed SL + TP  (new targets)
  Flow B — 2×ATR trailing stop, no TP  (let winners run)

Each flow produces one BUY row and one SELL row in the output CSV.

Usage:
  python validate_trades.py [--days 14]
Output:
  scanner_output/backtests/validated_trades_YYYYMMDD_HHMMSS.csv
"""

import sys
import ast
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

# ─── Scoring weights (new optimizer output) ──────────────────────────────────
SCORING_WEIGHTS = {
    'dist_confirm':          24,
    'at_key_support':        24,
    'candle_ok':             19,
    'near_52w_high':         17,
    'vol_confirm':           16,
    'rs_ok':                 16,
    'rsi_divergence':        14,
    'has_bullish_pattern':   13,
    'consolidation':         12,
    'pattern_vol_confirmed': 12,
    'sr_breakout':           11,
    'trendline_break':        9,
    'trend_ok':               2,
    'momentum_surge':         2,
    'minervini_template':     0,
}
TOTAL_PTS   = sum(SCORING_WEIGHTS.values())
THRESHOLDS  = {'GOLD': 99, 'PREMIUM': 69, 'HIGH': 65, 'STANDARD': 50}


def rescore(checks_raw) -> tuple:
    """Apply new weights to a Checks dict from the signal CSV. Returns (score, pct, quality)."""
    if not checks_raw or (isinstance(checks_raw, float) and np.isnan(checks_raw)):
        return 0, 0.0, 'N/A'
    try:
        d = ast.literal_eval(checks_raw) if isinstance(checks_raw, str) else checks_raw
    except Exception:
        return 0, 0.0, 'N/A'
    score = sum(SCORING_WEIGHTS.get(k, 5) * int(bool(v)) for k, v in d.items())
    pct   = score / TOTAL_PTS * 100
    for g in ('GOLD', 'PREMIUM', 'HIGH', 'STANDARD'):
        if pct >= THRESHOLDS[g]:
            return score, round(pct, 1), g
    return score, round(pct, 1), 'REJECT'


# ─── Data helpers ─────────────────────────────────────────────────────────────

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    pc = df['Close'].shift(1)
    tr = pd.concat(
        [(df['High'] - df['Low']),
         (df['High'] - pc).abs(),
         (df['Low']  - pc).abs()],
        axis=1
    ).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def fetch(symbol: str, entry_date: datetime, mode: str) -> pd.DataFrame:
    """Fetch price history: 45 days before entry → today."""
    interval = '15m' if mode == 'daytrade' else '1d'
    begin = (entry_date - timedelta(days=45)).strftime('%Y-%m-%d')
    end   = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    try:
        df = yf.Ticker(symbol).history(start=begin, end=end, interval=interval)
        if df.empty:
            return df
        # Normalise timezone
        if hasattr(df.index, 'tz') and df.index.tz is not None:
            df.index = df.index.tz_convert('America/New_York').tz_localize(None)
        else:
            df.index = pd.to_datetime(df.index)
        return df
    except Exception as exc:
        print(f"    [warn] {symbol}: yfinance error — {exc}")
        return pd.DataFrame()


def compute_sl_tp(df_pre: pd.DataFrame, entry: float, atr: float, mode: str,
                  old_stop: float = 0.0, old_target: float = 0.0) -> tuple:
    """
    Structural stop-loss + Fibonacci 1.618 take-profit.
    Falls back to old CSV values when pre-entry data is thin.
    """
    sl_mult = 2.5 if mode == 'swing' else 1.5
    tp_mult = 5.0 if mode == 'swing' else 3.0

    # ── Stop ──
    if len(df_pre) >= 10:
        n_bars  = min(20, len(df_pre))
        swing_l = df_pre['Low'].iloc[-n_bars:].min()
        atr_sl  = entry - sl_mult * atr
        stop    = max(swing_l * 0.995, atr_sl)
        stop    = min(stop, entry * 0.94)   # cap at 6 % below entry
    else:
        stop = old_stop if old_stop > 0 else entry * 0.95

    # ── Target ──
    atr_tp = entry + tp_mult * atr
    if len(df_pre) >= 15:
        n = min(20, len(df_pre))
        ch = df_pre['High'].iloc[-n:].max()
        cl = df_pre['Low'].iloc[-n:].min()
        fib = ch + 1.618 * (ch - cl) if ch > cl else atr_tp
    else:
        fib = atr_tp

    # Use old target as floor if the new one is suspiciously small
    tp = max(fib, atr_tp, entry * 1.05)
    if old_target > entry * 1.02:
        tp = max(tp, old_target)

    return round(stop, 2), round(tp, 2)


# ─── Trade simulators ─────────────────────────────────────────────────────────

def sim_flow_a(df_post: pd.DataFrame, entry: float, stop: float, tp: float) -> dict:
    """Fixed SL + TP. Returns exit info."""
    for i, (idx, row) in enumerate(df_post.iterrows()):
        lo = float(row['Low'])
        hi = float(row['High'])
        if lo <= stop:
            return dict(Exit_Date=str(idx)[:10], Exit_Price=stop,
                        Exit_Reason='Stop', Bars_Held=i + 1)
        if hi >= tp:
            return dict(Exit_Date=str(idx)[:10], Exit_Price=tp,
                        Exit_Reason='Target', Bars_Held=i + 1)
    lc = float(df_post['Close'].iloc[-1])
    return dict(Exit_Date=str(df_post.index[-1])[:10], Exit_Price=lc,
                Exit_Reason='Open/MTM', Bars_Held=len(df_post))


def sim_flow_b(df_post: pd.DataFrame, atr_s: pd.Series, entry: float,
               stop0: float, trail_mult: float = 2.0) -> dict:
    """2×ATR trailing stop, no TP. Returns exit info."""
    trail = stop0
    peak  = entry
    for i, (idx, row) in enumerate(df_post.iterrows()):
        close = float(row['Close'])
        lo    = float(row['Low'])
        # Look up ATR for this bar
        if idx in atr_s.index:
            atr_v = float(atr_s.loc[idx])
        else:
            atr_v = float('nan')
        if np.isnan(atr_v):
            atr_v = max(entry - stop0, entry * 0.01)
        peak  = max(peak, close)
        trail = max(trail, peak - trail_mult * atr_v)
        if lo <= trail:
            return dict(Exit_Date=str(idx)[:10], Exit_Price=round(trail, 2),
                        Exit_Reason='Trail', Bars_Held=i + 1)
    lc = float(df_post['Close'].iloc[-1])
    return dict(Exit_Date=str(df_post.index[-1])[:10], Exit_Price=lc,
                Exit_Reason='Open/MTM', Bars_Held=len(df_post))


# ─── Load signals ─────────────────────────────────────────────────────────────

def load_signals(days: int) -> pd.DataFrame:
    cutoff  = datetime.now() - timedelta(days=days)
    sig_dir = Path('scanner_output/signals')
    frames  = []
    for f in sorted(sig_dir.glob('signals_*.csv')):
        try:
            parts     = f.stem.split('_')
            file_date = datetime.strptime(parts[2], '%Y%m%d')
            if file_date < cutoff:
                continue
            df = pd.read_csv(f)
            df['_file_date'] = file_date
            df['_mode']      = parts[1] if len(parts) > 1 else 'swing'
            frames.append(df)
        except Exception:
            pass
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    # Parse --days argument
    days = 14
    for i, arg in enumerate(sys.argv[1:]):
        if arg == '--days' and i + 2 < len(sys.argv):
            days = int(sys.argv[i + 2])
        elif arg.lstrip('-').isdigit():
            days = int(arg)

    print(f"\n{'='*70}")
    print(f"  Trade Validation Backtest — last {days} days")
    print(f"  Flow A: Fixed SL + Updated Fibonacci TP")
    print(f"  Flow B: 2×ATR Trailing Stop, no TP")
    print(f"{'='*70}\n")

    signals = load_signals(days)
    if signals.empty:
        print("No signal files found. Exiting.")
        return

    print(f"Loaded {len(signals)} rows from {signals['_file_date'].nunique()} dates.\n")

    transactions = []
    seen = set()

    for _, row in signals.iterrows():
        sym  = str(row.get('Symbol', row.get('symbol', ''))).strip()
        mode = str(row.get('_mode', 'swing')).lower()
        fd   = row['_file_date']
        key  = (sym, fd.date(), mode)
        if not sym or key in seen:
            continue
        seen.add(key)

        entry      = float(row.get('Price', row.get('price', 0)))
        old_stop   = float(row.get('Stop',   entry * 0.95))
        old_target = float(row.get('Target', entry * 1.10))
        old_q      = str(row.get('Quality', '?'))
        sig_type   = str(row.get('Type', ''))
        rsi_val    = row.get('RSI', '')

        if entry <= 0:
            continue

        # Re-score if Checks column exists (V12+)
        new_score, new_pct, new_q = rescore(row.get('Checks', float('nan')))

        print(f"  {sym:8s} [{mode:9s}] {fd.date()}  {old_q}", end='')
        if new_q != 'N/A':
            print(f"→{new_q} ({new_pct:.0f}%)", end='')
        print(f"  {sig_type or 'breakout'}", end='')

        # ── Fetch price data ──
        df_hist = fetch(sym, fd, mode)
        if df_hist.empty or len(df_hist) < 5:
            print("  — no data, skipped")
            continue

        atr_s     = calc_atr(df_hist)
        entry_dt  = pd.Timestamp(fd.date())

        # Split pre / post entry
        df_pre  = df_hist[df_hist.index <  entry_dt]
        df_post = df_hist[df_hist.index >= entry_dt]

        # For daytrade: only keep same-day bars for post (daytrade = intraday)
        # But for backtest purposes we follow until stopped/targeted
        if df_post.empty:
            print("  — no post-entry bars, skipped")
            continue

        # ATR at entry
        if not df_pre.empty:
            valid_atr = atr_s[df_pre.index].dropna()
            atr_entry = float(valid_atr.iloc[-1]) if not valid_atr.empty else entry * 0.02
        else:
            atr_entry = entry * 0.02

        new_stop, new_target = compute_sl_tp(df_pre, entry, atr_entry, mode,
                                             old_stop, old_target)
        rr_a = round((new_target - entry) / max(entry - new_stop, entry * 0.01), 2)

        print(f"  SL {old_stop:.2f}→{new_stop:.2f}  TP {old_target:.2f}→{new_target:.2f}  R:R {rr_a}")

        # Shared metadata for both flows
        meta = dict(
            Symbol      = sym,
            Mode        = mode,
            Signal_Type = sig_type or 'Breakout',
            Signal_Date = str(fd.date()),
            Entry_Price = entry,
            Old_Stop    = old_stop,
            New_Stop    = new_stop,
            Old_Target  = old_target,
            New_Target  = new_target,
            Old_Quality = old_q,
            New_Quality = new_q if new_q != 'N/A' else old_q,
            Score_Pct   = new_pct if new_q != 'N/A' else '',
            RR          = rr_a,
            RSI         = rsi_val,
        )

        # ── Flow A: Fixed SL + TP ──
        res_a = sim_flow_a(df_post, entry, new_stop, new_target)
        pnl_a = round((res_a['Exit_Price'] - entry) / entry * 100, 2)
        transactions.append({**meta, 'Flow': 'A_Fixed_SL_TP',
                              'Action': 'BUY', 'Trans_Date': str(fd.date()),
                              'Trans_Price': entry,
                              'Exit_Reason': '', 'Bars_Held': '', 'PnL_Pct': '', 'Win': ''})
        transactions.append({**meta, 'Flow': 'A_Fixed_SL_TP',
                              'Action': 'SELL', 'Trans_Date': res_a['Exit_Date'],
                              'Trans_Price': res_a['Exit_Price'],
                              'Exit_Reason': res_a['Exit_Reason'],
                              'Bars_Held': res_a['Bars_Held'],
                              'PnL_Pct': pnl_a, 'Win': pnl_a > 0})

        # ── Flow B: Trailing stop ──
        res_b = sim_flow_b(df_post, atr_s, entry, new_stop)
        pnl_b = round((res_b['Exit_Price'] - entry) / entry * 100, 2)
        transactions.append({**meta, 'Flow': 'B_Trail_2ATR',
                              'Action': 'BUY', 'Trans_Date': str(fd.date()),
                              'Trans_Price': entry,
                              'Exit_Reason': '', 'Bars_Held': '', 'PnL_Pct': '', 'Win': ''})
        transactions.append({**meta, 'Flow': 'B_Trail_2ATR',
                              'Action': 'SELL', 'Trans_Date': res_b['Exit_Date'],
                              'Trans_Price': res_b['Exit_Price'],
                              'Exit_Reason': res_b['Exit_Reason'],
                              'Bars_Held': res_b['Bars_Held'],
                              'PnL_Pct': pnl_b, 'Win': pnl_b > 0})

    if not transactions:
        print("\nNo transactions generated.")
        return

    out = pd.DataFrame(transactions)

    # Column order — BUY/SELL rows share all columns
    col_order = [
        'Flow', 'Action', 'Symbol', 'Mode', 'Signal_Type',
        'Trans_Date', 'Trans_Price', 'Signal_Date', 'Entry_Price',
        'New_Stop', 'New_Target', 'RR', 'Old_Stop', 'Old_Target',
        'Old_Quality', 'New_Quality', 'Score_Pct', 'RSI',
        'Exit_Reason', 'Bars_Held', 'PnL_Pct', 'Win',
    ]
    out = out[[c for c in col_order if c in out.columns]]

    out_dir = Path('scanner_output/backtests')
    out_dir.mkdir(parents=True, exist_ok=True)
    ts    = datetime.now().strftime('%Y%m%d_%H%M%S')
    fname = out_dir / f"validated_trades_{ts}.csv"
    out.to_csv(fname, index=False)

    # ── Summary ──────────────────────────────────────────────────────────────
    sells = out[out['Action'] == 'SELL'].copy()
    sells['PnL_Pct'] = pd.to_numeric(sells['PnL_Pct'], errors='coerce')
    sells['Win']     = sells['Win'].map(lambda x: bool(x) if x != '' else False)

    print(f"\n{'='*70}")
    print(f"  RESULTS SUMMARY  ({len(seen)} unique signals)\n")

    for flow, label in [('A_Fixed_SL_TP', 'Fixed SL + Fib TP'),
                        ('B_Trail_2ATR',  'Trailing 2×ATR, no TP')]:
        s = sells[sells['Flow'] == flow].dropna(subset=['PnL_Pct'])
        if s.empty:
            continue
        n    = len(s)
        w    = int(s['Win'].sum())
        avg  = s['PnL_Pct'].mean()
        best = s['PnL_Pct'].max()
        worst= s['PnL_Pct'].min()
        exits= s['Exit_Reason'].value_counts().to_dict()

        print(f"  Flow {flow[0]} — {label}")
        print(f"    Trades : {n}   Wins: {w}/{n} ({w/n*100:.0f}%)")
        print(f"    Avg    : {avg:+.2f}%   Best: {best:+.2f}%   Worst: {worst:+.2f}%")
        print(f"    Exits  : {exits}")

        # Per-quality breakdown
        for q in ['GOLD', 'PREMIUM', 'HIGH', 'STANDARD']:
            sq = s[s['Old_Quality'] == q]
            if sq.empty:
                continue
            qw = int(sq['Win'].sum())
            print(f"    {q:8s}: {len(sq)} trades  WR {qw/len(sq)*100:.0f}%  avg {sq['PnL_Pct'].mean():+.2f}%")
        print()

    # Individual trade summary (SELL rows only, sorted by signal date)
    print(f"\n{'─'*70}")
    print(f"  TRADE LOG\n")
    sell_display = sells[sells['Flow'] == 'A_Fixed_SL_TP'].copy()
    sell_display['PnL_str'] = sell_display['PnL_Pct'].apply(
        lambda x: f"{x:+.2f}%" if pd.notna(x) else '?'
    )
    for _, r in sell_display.sort_values('Signal_Date').iterrows():
        win_icon = '✓' if bool(r.get('Win')) else '✗'
        print(f"  {win_icon} {r['Symbol']:8s} {r['Old_Quality']:8s} "
              f"entry {r['Entry_Price']:.2f}  exit {r['Trans_Price']:.2f} "
              f"({r['Exit_Reason']:9s}) {r['PnL_str']:>8s}"
              f"  [{r.get('Signal_Date','')}→{r['Trans_Date']}]")

    print(f"\n  Saved {len(out)} rows → {fname}\n")


if __name__ == '__main__':
    main()
