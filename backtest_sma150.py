#!/usr/bin/env python3
"""
SMA150 Crossover Backtest
=========================
Entry:  Close crosses above SMA150 (was below previous day)
Exit:   Close crosses below SMA150 (was above previous day)
Re-entry allowed on each new cross-up.
No TP, no stop-loss — pure SMA150 trend following.

Position sizing: 10% of capital per position (MAX_POS_PCT).
Max hold: unlimited — only exit on SMA150 cross-down or year-end force-close.

Usage:
  python backtest_sma150.py
  python backtest_sma150.py --watchlist input/1_26_Setups.txt
  python backtest_sma150.py --years 2022,2023,2024,2025,2026 --capital 100000
"""

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    import asyncio
    asyncio.get_event_loop()
except RuntimeError:
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())

from yfinance_adapter import YFinanceAdapter

SMA_PERIOD  = 150
MAX_POS_PCT = 0.10   # max 10% of capital per position

SPY_ANNUAL = {
    '2022': -18.65,
    '2023': +26.71,
    '2024': +26.05,
    '2025': +18.89,
    '2026': +5.77,
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_symbols(path: str) -> list[str]:
    lines = Path(path).read_text().splitlines()
    return [l.strip().split()[0] for l in lines if l.strip() and not l.startswith('#')]


def fetch_year_data(symbols: list[str], year: int, adapter: YFinanceAdapter) -> dict:
    """Fetch SMA_PERIOD + full-year daily bars for each symbol + SPY."""
    # Lookback: need SMA_PERIOD trading days before Jan 1 ≈ 8 calendar months
    start = f"{year - 1}-04-01"
    end   = f"{year}-12-31"
    data  = {}
    all_syms = symbols + (['SPY'] if 'SPY' not in symbols else [])
    total = len(all_syms)
    for i, sym in enumerate(all_syms, 1):
        if i % 20 == 0 or i == total:
            print(f"    Fetched {i}/{total} ({i/total*100:.1f}%)...")
        df = adapter.get_historical_data(sym, '1 day', start_date=start, end_date=end)
        if df is None or df.empty:
            continue
        df = df.sort_index()
        df['sma150'] = df['close'].rolling(SMA_PERIOD).mean()
        # Slope flag: SMA150 is flat-or-rising vs 10 bars ago
        df['sma150_rising'] = df['sma150'] >= df['sma150'].shift(10)
        data[sym] = df
    return data


def spy_stats(data: dict, year: int) -> tuple[float, float, float]:
    """Return (actual_return%, sharpe, max_drawdown%) for SPY in the given year."""
    df = data.get('SPY')
    if df is None:
        return SPY_ANNUAL.get(str(year), 0.0), 0.0, 0.0
    yr = df[df.index.year == year]['close']
    if len(yr) < 2:
        return SPY_ANNUAL.get(str(year), 0.0), 0.0, 0.0
    ret   = yr.iloc[-1] / yr.iloc[0] - 1.0
    daily = yr.pct_change().dropna()
    sh    = (daily.mean() / daily.std() * (252 ** 0.5)) if daily.std() > 0 else 0.0
    cum   = (1 + daily).cumprod()
    dd    = ((cum / cum.cummax()) - 1).min()
    return ret * 100, sh, dd * 100


def sharpe(equity_curve: list[float]) -> float:
    if len(equity_curve) < 2:
        return 0.0
    s = pd.Series(equity_curve)
    daily = s.pct_change().dropna()
    if daily.std() == 0:
        return 0.0
    return daily.mean() / daily.std() * (252 ** 0.5)


def max_drawdown(equity_curve: list[float]) -> float:
    s = pd.Series(equity_curve)
    return ((s / s.cummax()) - 1).min() * 100


# ─── Per-year simulation ───────────────────────────────────────────────────────

def simulate_year(year: int, symbols: list[str], data: dict,
                  capital: float, spy_filter: bool = False,
                  trend_filter: bool = True) -> dict:
    """
    SMA150 crossover simulation for one calendar year.
    - Enter on close that crosses above SMA150 (prev close was below)
      + trend_filter=True: only enter when stock's SMA150 is flat-or-rising
        (SMA150[today] >= SMA150[10 bars ago])
      + spy_filter=True: only enter when SPY close > SPY SMA150
    - Exit on close that crosses below SMA150 (prev close was above)
    - Re-entry allowed: each new cross-up opens a fresh position
    - Year-end: force-close all open positions at last close
    """
    # Build trading days for this year from SPY
    spy_df = data.get('SPY')
    if spy_df is not None:
        trading_days = spy_df[spy_df.index.year == year].index.normalize().unique().tolist()
    else:
        trading_days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq='B').tolist()
    trading_days = sorted(trading_days)

    cap       = capital
    open_pos  = {}        # sym → {qty, entry_price, entry_date}
    trades    = []
    equity_curve = [cap]
    _peak_equity = cap  # reserved for future DD circuit breaker

    for today in trading_days:
        today_norm = pd.Timestamp(today).normalize()

        # Mark-to-market equity for equity curve
        open_val = sum(
            data[s].loc[data[s].index.normalize() <= today_norm, 'close'].iloc[-1] * p['qty']
            if s in data and not data[s][data[s].index.normalize() <= today_norm].empty
            else p['entry_price'] * p['qty']
            for s, p in open_pos.items()
        ) if open_pos else 0.0
        equity_curve.append(cap + open_val)

        # --- Check exits first (SMA150 cross-down) ---
        for sym in list(open_pos.keys()):
            df = data.get(sym)
            if df is None:
                continue
            today_bar  = df[df.index.normalize() == today_norm]
            if today_bar.empty:
                continue
            # Need yesterday's bar
            prev_bars = df[df.index.normalize() < today_norm]
            if prev_bars.empty:
                continue
            prev_close = float(prev_bars.iloc[-1]['close'])
            prev_sma   = float(prev_bars.iloc[-1]['sma150']) if not pd.isna(prev_bars.iloc[-1]['sma150']) else None
            curr_close = float(today_bar.iloc[-1]['close'])
            curr_sma   = float(today_bar.iloc[-1]['sma150']) if not pd.isna(today_bar.iloc[-1]['sma150']) else None

            if prev_sma is None or curr_sma is None:
                continue

            # Cross-down: was above SMA150, now below
            if prev_close >= prev_sma and curr_close < curr_sma:
                pos = open_pos.pop(sym)
                proceeds = curr_close * pos['qty']
                cap += proceeds
                pnl = proceeds - pos['entry_price'] * pos['qty']
                trades.append({
                    'symbol':      sym,
                    'entry_date':  pos['entry_date'],
                    'exit_date':   today_norm.strftime('%Y-%m-%d'),
                    'entry_price': pos['entry_price'],
                    'exit_price':  curr_close,
                    'qty':         pos['qty'],
                    'pnl':         pnl,
                    'win':         pnl > 0,
                    'exit_reason': 'sma150_cross_down',
                })

        # --- Regime gate: SPY must be above its own SMA150 to allow new entries ---
        spy_allows_entry = True
        if spy_filter:
            spy_df2 = data.get('SPY')
            if spy_df2 is not None:
                spy_today = spy_df2[spy_df2.index.normalize() == today_norm]
                spy_prev  = spy_df2[spy_df2.index.normalize() < today_norm]
                if not spy_today.empty and not spy_prev.empty:
                    spy_close = float(spy_today.iloc[-1]['close'])
                    spy_sma   = spy_today.iloc[-1]['sma150']
                    spy_allows_entry = (not pd.isna(spy_sma)) and (spy_close > float(spy_sma))

        # --- Check entries (SMA150 cross-up) ---
        for sym in symbols:
            if not spy_allows_entry:
                break  # SPY below SMA150 — skip all new entries today
            if sym in open_pos:
                continue  # already holding
            df = data.get(sym)
            if df is None:
                continue
            today_bar = df[df.index.normalize() == today_norm]
            if today_bar.empty:
                continue
            prev_bars = df[df.index.normalize() < today_norm]
            if prev_bars.empty:
                continue

            prev_close = float(prev_bars.iloc[-1]['close'])
            prev_sma   = float(prev_bars.iloc[-1]['sma150']) if not pd.isna(prev_bars.iloc[-1]['sma150']) else None
            curr_close = float(today_bar.iloc[-1]['close'])
            curr_sma   = float(today_bar.iloc[-1]['sma150']) if not pd.isna(today_bar.iloc[-1]['sma150']) else None

            if prev_sma is None or curr_sma is None:
                continue

            # Trend filter: SMA150 must be flat-or-rising (not declining)
            if trend_filter:
                rising_raw = today_bar.iloc[-1].get('sma150_rising', None)
                if not (rising_raw is not None and not pd.isna(rising_raw) and bool(rising_raw)):
                    continue

            # Cross-up: was below SMA150, now above
            if prev_close <= prev_sma and curr_close > curr_sma:
                alloc = cap * MAX_POS_PCT
                if alloc < curr_close:
                    continue
                qty = int(alloc / curr_close)
                if qty < 1:
                    continue
                cost = qty * curr_close
                if cost > cap:
                    continue
                cap -= cost
                open_pos[sym] = {
                    'qty':         qty,
                    'entry_price': curr_close,
                    'entry_date':  today_norm.strftime('%Y-%m-%d'),
                }

    # --- Year-end: force-close all open positions ---
    if trading_days:
        last_day = pd.Timestamp(trading_days[-1]).normalize()
        for sym, pos in list(open_pos.items()):
            df = data.get(sym)
            last_bar = df[df.index.normalize() == last_day] if df is not None else None
            if last_bar is not None and not last_bar.empty:
                price = float(last_bar.iloc[-1]['close'])
            else:
                price = pos['entry_price']
            proceeds = price * pos['qty']
            cap += proceeds
            pnl = proceeds - pos['entry_price'] * pos['qty']
            trades.append({
                'symbol':      sym,
                'entry_date':  pos['entry_date'],
                'exit_date':   last_day.strftime('%Y-%m-%d'),
                'entry_price': pos['entry_price'],
                'exit_price':  price,
                'qty':         pos['qty'],
                'pnl':         pnl,
                'win':         pnl > 0,
                'exit_reason': 'year_end',
            })

    final_equity = cap
    ret_pct  = (final_equity / capital - 1) * 100
    sh       = sharpe(equity_curve)
    max_dd   = max_drawdown(equity_curve)
    n        = len(trades)
    wins     = sum(1 for t in trades if t['win'])
    wr       = wins / n * 100 if n else 0.0
    avg_hold = (
        sum((datetime.strptime(t['exit_date'], '%Y-%m-%d') -
             datetime.strptime(t['entry_date'], '%Y-%m-%d')).days
            for t in trades) / n
    ) if n else 0.0

    return {
        'return_pct':    ret_pct,
        'sharpe':        sh,
        'max_dd':        max_dd,
        'n_trades':      n,
        'win_rate':      wr,
        'avg_hold_days': avg_hold,
        'final_equity':  final_equity,
        'trades':        trades,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description='SMA150 crossover backtest')
    p.add_argument('--watchlist',   default='input/1_26_Setups.txt')
    p.add_argument('--years',       default='2022,2023,2024,2025,2026',
                   help='Comma-separated years')
    p.add_argument('--capital',     type=float, default=100_000)
    p.add_argument('--no-spy-filter',   action='store_true',
                   help='Disable SPY > SMA150 regime gate (allow entries in downtrends)')
    p.add_argument('--no-trend-filter', action='store_true',
                   help='Disable SMA150 slope filter (allow entries on declining SMA150)')
    return p.parse_args()


def main():
    args       = parse_args()
    years      = [int(y) for y in args.years.split(',')]
    symbols    = load_symbols(args.watchlist)
    spy_filter   = not args.no_spy_filter
    trend_filter = not args.no_trend_filter
    adapter    = YFinanceAdapter(use_disk_cache=True)

    print('=' * 72)
    print('SMA150 CROSSOVER BACKTEST')
    print('=' * 72)
    print(f"Watchlist  : {args.watchlist}  ({len(symbols)} symbols)")
    print(f"Years      : {years}")
    print(f"Capital    : ${args.capital:,.0f}  |  SMA period: {SMA_PERIOD}  |  "
          f"Pos size: {MAX_POS_PCT*100:.0f}% per trade")
    filters = []
    if trend_filter: filters.append("SMA150 slope ≥ 0")
    if spy_filter:   filters.append("SPY > SMA150")
    filter_str = "  +  " + "  +  ".join(filters) if filters else "  (no filters)"
    print(f"Entry      : close crosses above SMA150{filter_str}")
    print(f"Exit       : close crosses below SMA150  (re-entry on next cross-up)")
    print()

    header = f"  {'Year':<6} {'Market':>7}  {'SPY':>8}  {'Return':>8}  "
    header += f"{'Sharpe':>7}  {'MaxDD':>7}  {'Trades':>7}  {'WR%':>6}  {'AvgHold':>8}"
    print(header)
    print('  ' + '-' * (len(header) - 2))

    compound = 1.0
    spy_compound = 1.0
    all_sharpes = []

    for year in years:
        market = {2022: 'BEAR', 2023: 'BULL', 2024: 'BULL',
                  2025: 'MIXED', 2026: 'MIXED'}.get(year, '?')
        print(f"\n  Fetching data for {year}...")
        data = fetch_year_data(symbols, year, adapter)
        spy_ret, _, _ = spy_stats(data, year)

        result = simulate_year(year, symbols, data, args.capital,
                               spy_filter=spy_filter, trend_filter=trend_filter)

        compound     *= (1 + result['return_pct'] / 100)
        spy_compound *= (1 + spy_ret / 100)
        all_sharpes.append(result['sharpe'])

        vs_spy = result['return_pct'] - spy_ret
        sign   = '+' if vs_spy >= 0 else ''
        print(f"  {year:<6} {market:>7}  {spy_ret:>+7.2f}%  "
              f"{result['return_pct']:>+7.2f}%  "
              f"{result['sharpe']:>+7.2f}  "
              f"{result['max_dd']:>+7.2f}%  "
              f"{result['n_trades']:>7}  "
              f"{result['win_rate']:>5.1f}%  "
              f"{result['avg_hold_days']:>7.1f}d  "
              f"(vs SPY {sign}{vs_spy:.1f}%)")

    print()
    print('  ' + '=' * 68)
    print(f"  5yr compound  SPY: {spy_compound - 1:>+.1%}   System: {compound - 1:>+.1%}   "
          f"vs SPY: {(compound - spy_compound):>+.1%}")
    print(f"  Avg Sharpe   : {sum(all_sharpes)/len(all_sharpes):>+.2f}")
    print()


if __name__ == '__main__':
    main()
