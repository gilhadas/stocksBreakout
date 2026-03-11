#!/usr/bin/env python3
"""
backtest_new_signals.py
─────────────────────────────────────────────────────────────────────
A/B comparison: OLD scanner (breakout+bounce only) vs NEW scanner
(breakout+bounce+continuation+sma20_cross).

Runs both through identical SimulationMode to get apples-to-apples
return/Sharpe/MaxDD comparison.

Usage:
  python backtest_new_signals.py
  python backtest_new_signals.py --start 2024-06-01 --end 2025-12-31
  python backtest_new_signals.py --watchlist input/optimizer_watch.txt --limit 40
"""

import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import warnings
warnings.filterwarnings('ignore')

import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

from scanner import BreakoutDetector
from yfinance_adapter import YFinanceAdapter
from mock_trader import SimulationMode
from config import MODES

def parse_args():
    p = argparse.ArgumentParser(description='A/B backtest: old vs new signal types')
    p.add_argument('--watchlist', default='input/optimizer_watch.txt')
    p.add_argument('--start', default='2024-01-01')
    p.add_argument('--end', default='2025-12-31')
    p.add_argument('--capital', type=float, default=100_000)
    p.add_argument('--limit', type=int, default=0, help='Max symbols (0=all)')
    p.add_argument('--date-step', type=int, default=5,
                   help='Scan every N business days (5=weekly, 1=daily)')
    return p.parse_args()


def load_symbols(path: str, limit: int = 0) -> list[str]:
    symbols = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' in line:
            line = line.split(':')[1]
        symbols.append(line.upper())
    symbols = list(dict.fromkeys(symbols))  # dedupe preserving order
    if limit > 0:
        symbols = symbols[:limit]
    return symbols


def fetch_data(symbols: list[str], start: str, end: str) -> dict:
    adapter = YFinanceAdapter(use_disk_cache=True)
    historical = {}
    # Fetch extra history before start for indicator warmup
    warmup_start = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime('%Y-%m-%d')
    end_plus = (pd.Timestamp(end) + pd.Timedelta(days=5)).strftime('%Y-%m-%d')

    for i, sym in enumerate(symbols):
        print(f"\r  Fetching [{i+1}/{len(symbols)}] {sym:6}", end='')
        df = adapter.get_historical_data(sym, 'daily', start_date=warmup_start, end_date=end_plus)
        if df is not None and len(df) >= 50:
            historical[sym] = df
    print()
    return historical


def calculate_spy_perf(spy_df, sim_date, lookback=15):
    if spy_df is None:
        return 0.0
    mask = spy_df.index <= sim_date
    df = spy_df[mask]
    if len(df) < lookback:
        return 0.0
    return (df['close'].iloc[-1] / df['close'].iloc[-lookback]) - 1


def scan_signals(historical: dict, start: str, end: str,
                 use_new_detectors: bool = False,
                 date_step: int = 5) -> list[dict]:
    """
    Scan all symbols across the date range.

    If use_new_detectors=True, also runs detect_continuation() and
    detect_sma20_cross() as fallback when breakout+bounce don't fire.
    """
    detector = BreakoutDetector()
    spy_df = historical.get('SPY')
    symbols = [s for s in historical if s != 'SPY']
    sim_dates = pd.date_range(start=start, end=end, freq='B')[::date_step]
    modes = ['swing', 'longterm']

    signals = []
    cooldowns = {}

    label = "NEW (breakout+bounce+cont+sma20)" if use_new_detectors else "OLD (breakout+bounce)"
    print(f"\n  Scanning [{label}]: {len(symbols)} symbols x {len(sim_dates)} days...")

    for day_idx, sim_date in enumerate(sim_dates):
        spy_perf = calculate_spy_perf(spy_df, sim_date)

        for symbol in symbols:
            df = historical[symbol]
            df_slice = df[df.index <= sim_date]

            if len(df_slice) < 150:
                continue
            if df_slice.index[-1].date() != sim_date.date():
                continue

            # 10-day cooldown per symbol
            last_sig = cooldowns.get(symbol)
            if last_sig and (sim_date - last_sig).days < 10:
                continue

            sig = None
            sig_type = None

            for mode_name in modes:
                # 1. Breakout (V2 composite)
                try:
                    sig = detector.detect(
                        df_slice, symbol, mode_name, '1 day', spy_perf,
                        use_scoring=True,
                        use_legacy_momentum=False,
                        use_v4_overextension=False,
                        reference_date=sim_date.date(),
                    )
                except Exception:
                    sig = None

                if sig:
                    sig_type = 'BREAKOUT'
                    break

                # 2. Bounce
                try:
                    sig = detector.detect_bounce(df_slice, symbol, mode_name, '1 day')
                except Exception:
                    sig = None

                if sig:
                    sig_type = 'BOUNCE'
                    break

                # 3. NEW: Continuation (Day 2/3 surge)
                if use_new_detectors and sig is None:
                    try:
                        sig = detector.detect_continuation(
                            df_slice, symbol, mode_name, '1 day', spy_perf
                        )
                    except Exception:
                        sig = None

                    if sig:
                        sig_type = 'CONTINUATION'
                        break

                # 4. NEW: SMA 20 crossing
                if use_new_detectors and sig is None:
                    try:
                        sig = detector.detect_sma20_cross(
                            df_slice, symbol, mode_name, '1 day', spy_perf
                        )
                    except Exception:
                        sig = None

                    if sig:
                        sig_type = 'SMA20_CROSS'
                        break

            if sig and sig_type:
                signals.append({
                    'date': sim_date,
                    'symbol': symbol,
                    'action': 'BUY',
                    'price': sig['Price'],
                    'entry_price': sig['Price'],
                    'stop_loss': sig['Stop'],
                    'take_profit': sig['Target'],
                    'quality': sig['Quality'],
                    'mode': mode_name,
                    'type': sig_type,
                    'minervini_score': sig.get('MinerviniScore', 0),
                    'is_momentum': sig.get('Type') == 'Momentum',
                    'is_vcp': bool(sig.get('VCP', False)),
                    'vcp_quality': sig.get('VCP_Quality', 0) or 0,
                    'checks': sig.get('Checks', {}),
                    'win_probability': sig.get('WinProb', 0.50),
                    'rr_grade': sig.get('RR_Grade', ''),
                    'patterns': sig.get('Patterns', ''),
                })
                cooldowns[symbol] = sim_date

        if (day_idx + 1) % 50 == 0:
            print(f"    Day {day_idx+1}/{len(sim_dates)}: {len(signals)} signals so far")

    # Summary by type
    type_counts = {}
    quality_counts = {}
    for s in signals:
        t = s['type']
        q = s['quality']
        type_counts[t] = type_counts.get(t, 0) + 1
        quality_counts[q] = quality_counts.get(q, 0) + 1

    print(f"  Total: {len(signals)} signals")
    print(f"    By type: {type_counts}")
    print(f"    By quality: {quality_counts}")

    return signals


def run_simulation(signals: list[dict], historical: dict,
                   start: str, end: str, capital: float,
                   config: dict) -> dict:
    """Run SimulationMode on filtered signals and return metrics."""
    name = config['name']
    filt = config.get('filter', lambda s: True)
    filtered = [s for s in signals if filt(s)]

    if not filtered:
        return {'name': name, 'total_return': 0, 'sharpe_ratio': 0,
                'max_drawdown': 0, 'total_trades': 0, 'win_rate': 0}

    sim = SimulationMode(
        start_date=start,
        end_date=end,
        initial_capital=capital,
        max_position_pct=config.get('pos_pct', 0.10),
        max_risk_pct=config.get('risk_pct', 0.02),
        use_trailing_stop=config.get('trailing', False),
        trailing_stop_atr_mult=config.get('trail_atr', 4.0),
        trailing_stop_activation_pct=config.get('trail_act', 0.10),
        tp_as_trail=config.get('tp_as_trail', False),
        tp_trail_atr_mult=config.get('tp_trail_atr', 2.0),
    )

    report = sim.run_simulation(filtered, historical_data=historical)
    report['name'] = name
    report['n_signals'] = len(filtered)
    return report


def main():
    args = parse_args()
    print("=" * 90)
    print("  A/B BACKTEST: OLD scanner vs NEW scanner (continuation + SMA20 cross)")
    print(f"  Period: {args.start} → {args.end}  |  Capital: ${args.capital:,.0f}")
    print("=" * 90)

    # Load symbols
    symbols = load_symbols(args.watchlist, args.limit)
    if 'SPY' not in symbols:
        symbols.append('SPY')
    print(f"\n  Loaded {len(symbols)} symbols from {args.watchlist}")

    # Fetch data
    historical = fetch_data(symbols, args.start, args.end)
    print(f"  Historical data for {len(historical)} symbols")

    # --- SCAN: OLD (breakout + bounce only) ---
    old_signals = scan_signals(historical, args.start, args.end,
                               use_new_detectors=False, date_step=args.date_step)

    # --- SCAN: NEW (breakout + bounce + continuation + sma20_cross) ---
    new_signals = scan_signals(historical, args.start, args.end,
                               use_new_detectors=True, date_step=args.date_step)

    # --- CONFIGS ---
    configs = [
        # OLD: V9-C equivalent (best known config)
        {
            'name': 'OLD-A) PREMIUM+ (breakout+bounce)',
            'signals_key': 'old',
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'tp_as_trail': True, 'tp_trail_atr': 2.0,
        },
        {
            'name': 'OLD-B) HIGH+ (breakout+bounce)',
            'signals_key': 'old',
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'tp_as_trail': True, 'tp_trail_atr': 2.0,
        },
        # NEW: same filters but with continuation + SMA20 cross
        {
            'name': 'NEW-A) PREMIUM+ (all signals)',
            'signals_key': 'new',
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'tp_as_trail': True, 'tp_trail_atr': 2.0,
        },
        {
            'name': 'NEW-B) HIGH+ (all signals)',
            'signals_key': 'new',
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'tp_as_trail': True, 'tp_trail_atr': 2.0,
        },
        # NEW: continuation-only isolation
        {
            'name': 'NEW-C) CONT only HIGH+',
            'signals_key': 'new',
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH')
                                and s.get('type') == 'CONTINUATION',
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'tp_as_trail': True, 'tp_trail_atr': 2.0,
        },
        # NEW: SMA20 cross isolation
        {
            'name': 'NEW-D) SMA20X only HIGH+',
            'signals_key': 'new',
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH')
                                and s.get('type') == 'SMA20_CROSS',
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'tp_as_trail': True, 'tp_trail_atr': 2.0,
        },
        # NEW: bounce-only isolation (with relaxed quality)
        {
            'name': 'NEW-E) BOUNCE only HIGH+',
            'signals_key': 'new',
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH')
                                and s.get('type') == 'BOUNCE',
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'tp_as_trail': True, 'tp_trail_atr': 2.0,
        },
    ]

    signals_map = {'old': old_signals, 'new': new_signals}

    # --- RUN SIMULATIONS ---
    print("\n" + "=" * 90)
    print("  RUNNING SIMULATIONS")
    print("=" * 90)

    results = []
    for cfg in configs:
        sigs = signals_map[cfg['signals_key']]
        report = run_simulation(sigs, historical, args.start, args.end, args.capital, cfg)
        results.append(report)
        ret = report.get('total_return', 0)
        sharpe = report.get('sharpe_ratio', 0)
        dd = report.get('max_drawdown', 0)
        trades = report.get('total_trades', 0)
        wr = report.get('win_rate', 0)
        n_sig = report.get('n_signals', 0)
        print(f"  {cfg['name']:40} | Ret: {ret:+7.1f}% | Sharpe: {sharpe:.2f} | "
              f"DD: {dd:.1f}% | Trades: {trades:3} | WR: {wr:.0f}% | Sigs: {n_sig}")

    # --- SPY BENCHMARK ---
    spy_df = historical.get('SPY')
    spy_ret = 0
    if spy_df is not None:
        spy_start_mask = spy_df.index >= args.start
        spy_end_mask = spy_df.index <= args.end
        spy_period = spy_df[spy_start_mask & spy_end_mask]
        if len(spy_period) >= 2:
            spy_ret = (spy_period['close'].iloc[-1] / spy_period['close'].iloc[0] - 1) * 100
    print(f"  {'SPY (buy & hold)':40} | Ret: {spy_ret:+7.1f}%")

    # --- COMPARISON TABLE ---
    print("\n" + "=" * 90)
    print("  COMPARISON: OLD vs NEW")
    print("=" * 90)
    print(f"\n  {'Config':40} {'Return':>8} {'Sharpe':>8} {'MaxDD':>8} {'Trades':>7} {'WinRate':>8} {'Signals':>8}")
    print(f"  {'-'*40} {'-'*8} {'-'*8} {'-'*8} {'-'*7} {'-'*8} {'-'*8}")

    for r in results:
        ret = r.get('total_return', 0)
        sharpe = r.get('sharpe_ratio', 0)
        dd = r.get('max_drawdown', 0)
        trades = r.get('total_trades', 0)
        wr = r.get('win_rate', 0)
        n_sig = r.get('n_signals', 0)
        print(f"  {r['name']:40} {ret:+7.1f}% {sharpe:7.2f} {dd:+7.1f}% {trades:7} {wr:7.0f}% {n_sig:8}")

    print(f"  {'SPY (buy & hold)':40} {spy_ret:+7.1f}%")

    # Highlight winner
    best = max(results, key=lambda r: r.get('sharpe_ratio', 0))
    print(f"\n  WINNER (best Sharpe): {best['name']}")
    print(f"    Return: {best.get('total_return', 0):+.1f}% | "
          f"Sharpe: {best.get('sharpe_ratio', 0):.2f} | "
          f"MaxDD: {best.get('max_drawdown', 0):.1f}%")

    # Delta: NEW-A vs OLD-A
    old_a = next((r for r in results if 'OLD-A' in r['name']), None)
    new_a = next((r for r in results if 'NEW-A' in r['name']), None)
    if old_a and new_a:
        d_ret = new_a.get('total_return', 0) - old_a.get('total_return', 0)
        d_sharpe = new_a.get('sharpe_ratio', 0) - old_a.get('sharpe_ratio', 0)
        d_trades = new_a.get('total_trades', 0) - old_a.get('total_trades', 0)
        print(f"\n  DELTA (NEW-A vs OLD-A):")
        print(f"    Return: {d_ret:+.1f}%  |  Sharpe: {d_sharpe:+.2f}  |  Trades: {d_trades:+d}")


if __name__ == '__main__':
    main()
