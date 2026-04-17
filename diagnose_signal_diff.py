#!/usr/bin/env python3
"""
Diagnostic: OLD V9-C vs NEW Regime-Adaptive signal differences.

Finds signals unique to NEW that OLD didn't generate, simulates each,
and profiles what makes them win or lose — to find filtering thresholds.

Usage:
  python diagnose_signal_diff.py [--years 2022,2024,2026]
"""

import argparse
import asyncio
import sys
from datetime import timedelta
from pathlib import Path
from collections import defaultdict

import pandas as pd
import numpy as np

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from scanner import BreakoutDetector
from yfinance_adapter import YFinanceAdapter
from backtest_regime_compare import (
    load_symbols, classify_day_regime, is_spy_below_sma200,
    collect_signals_old, collect_signals_new,
)
import logging
logging.basicConfig(level=logging.WARNING, format='%(message)s')


def fetch_all_data(symbols, start_date, end_date):
    adapter = YFinanceAdapter(use_disk_cache=True)
    all_syms = ['SPY'] + [s for s in symbols if s != 'SPY']
    historical = {}
    fetch_start = (pd.Timestamp(start_date) - timedelta(days=400)).strftime('%Y-%m-%d')
    for i, sym in enumerate(all_syms):
        df = adapter.get_historical_data(sym, '1 day', start_date=fetch_start, end_date=end_date)
        if df is not None and len(df) >= 50:
            historical[sym] = df
        if (i + 1) % 50 == 0 or (i + 1) == len(all_syms):
            print(f"  Fetched {i+1}/{len(all_syms)}...", flush=True)
    return historical


def measure_outcome(historical, symbol, entry_date, entry_price, stop, tp, max_hold=30):
    """Track what happens to a signal over max_hold days. Returns dict with outcome stats."""
    df = historical.get(symbol)
    if df is None:
        return None
    future = df[df.index > entry_date].head(max_hold)
    if len(future) == 0:
        return None

    max_price = entry_price
    min_price = entry_price
    exit_price = float(future['close'].iloc[-1])
    exit_reason = 'MAX_HOLD'
    days_held = len(future)

    for i, (idx, bar) in enumerate(future.iterrows()):
        max_price = max(max_price, float(bar['high']))
        min_price = min(min_price, float(bar['low']))
        if float(bar['low']) <= stop:
            exit_price = stop
            exit_reason = 'STOP'
            days_held = i + 1
            break
        if float(bar['high']) >= tp:
            exit_price = tp
            exit_reason = 'TP'
            days_held = i + 1
            break

    pnl_pct = (exit_price - entry_price) / entry_price * 100
    mae = (min_price - entry_price) / entry_price * 100  # max adverse excursion
    mfe = (max_price - entry_price) / entry_price * 100  # max favorable excursion

    return {
        'pnl_pct': round(pnl_pct, 2),
        'win': pnl_pct > 0,
        'exit_reason': exit_reason,
        'days_held': days_held,
        'mae': round(mae, 2),
        'mfe': round(mfe, 2),
    }


def run_year(year, symbols, historical):
    start_date = f'{year}-01-01'
    end_date = f'{year}-12-31'

    detector = BreakoutDetector()
    spy_df = historical.get('SPY')
    sim_dates = pd.date_range(start=start_date, end=end_date, freq='B')
    modes = ['swing', 'longterm']

    cooldowns_old = {}
    cooldowns_new = {}
    old_signals = {}  # (date, symbol) → signal
    new_signals = {}

    print(f"\n{'='*80}")
    print(f"YEAR {year} — Scanning {len(symbols)} symbols × {len(sim_dates)} days")
    print(f"{'='*80}")

    for day_idx, sim_date in enumerate(sim_dates):
        if (day_idx + 1) % 50 == 0:
            print(f"  Progress: {day_idx+1}/{len(sim_dates)} days...", flush=True)

        regime, spy_pct, vol_pct = ('NORMAL', 0.0, 0.5) if spy_df is None \
            else classify_day_regime(spy_df, sim_date)
        spy_perf_frac = spy_pct / 100.0

        for symbol in [s for s in symbols if s != 'SPY']:
            # OLD scan
            last_old = cooldowns_old.get(symbol)
            last_new = cooldowns_new.get(symbol)

            df = historical.get(symbol)
            if df is None:
                continue
            df_slice = df[df.index <= sim_date]
            if len(df_slice) < 150 or df_slice.index[-1].date() != sim_date.date():
                continue

            for mode in modes:
                # OLD
                if not (last_old and (sim_date - last_old).days < 10):
                    sig_old = collect_signals_old(detector, df_slice, symbol, mode, spy_perf_frac)
                    if sig_old:
                        quality = sig_old.get('Quality', 'STANDARD')
                        min_score = sig_old.get('MinerviniScore', 0) or 0
                        if quality in ('GOLD', 'PREMIUM') or \
                           sig_old.get('Type') in ('SMA20_CROSS', 'BOUNCE', 'CONTINUATION', 'Momentum'):
                            key = (sim_date, symbol)
                            old_signals[key] = {
                                **sig_old, 'regime': regime, 'mode': mode,
                                'spy_pct': spy_pct, 'vol_pct': vol_pct,
                            }
                            cooldowns_old[symbol] = sim_date

                # NEW
                if not (last_new and (sim_date - last_new).days < 10):
                    sig_new = collect_signals_new(detector, df_slice, symbol, mode, spy_perf_frac, regime)
                    if sig_new:
                        quality = sig_new.get('Quality', 'STANDARD')
                        key = (sim_date, symbol)
                        new_signals[key] = {
                            **sig_new, 'regime': regime, 'mode': mode,
                            'spy_pct': spy_pct, 'vol_pct': vol_pct,
                        }
                        cooldowns_new[symbol] = sim_date

    # Find signals unique to NEW (not in OLD)
    new_only_keys = set(new_signals.keys()) - set(old_signals.keys())
    shared_keys = set(new_signals.keys()) & set(old_signals.keys())
    old_only_keys = set(old_signals.keys()) - set(new_signals.keys())

    print(f"\n  Signal counts: OLD={len(old_signals)}, NEW={len(new_signals)}")
    print(f"  Shared: {len(shared_keys)} | NEW-only: {len(new_only_keys)} | OLD-only: {len(old_only_keys)}")

    # Measure outcomes for each group
    groups = {
        'SHARED (in both)': [(k, new_signals[k]) for k in shared_keys],
        'NEW-ONLY (extra)': [(k, new_signals[k]) for k in new_only_keys],
        'OLD-ONLY (lost)': [(k, old_signals[k]) for k in old_only_keys],
    }

    for group_name, items in groups.items():
        if not items:
            continue
        outcomes = []
        type_stats = defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl_sum': 0})
        regime_stats = defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl_sum': 0})
        quality_stats = defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl_sum': 0})
        rr_buckets = {'rr<1.5': [], 'rr1.5-2.5': [], 'rr>2.5': []}
        vol_buckets = {'vol<1.5': [], 'vol1.5-2.5': [], 'vol>2.5': []}
        rsi_buckets = {'rsi<40': [], 'rsi40-60': [], 'rsi60-80': [], 'rsi>80': []}
        dist_buckets = {'dist<5%': [], 'dist5-10%': [], 'dist>10%': []}

        for (date, symbol), sig in items:
            entry = float(sig.get('Price', 0))
            stop = float(sig.get('Stop', entry * 0.95))
            tp = float(sig.get('Target', entry * 1.10))
            if entry <= 0:
                continue
            o = measure_outcome(historical, symbol, date, entry, stop, tp)
            if o is None:
                continue

            sig_type = sig.get('Type', 'BREAKOUT')
            regime = sig.get('regime', 'NORMAL')
            quality = sig.get('Quality', 'STANDARD')
            rr = float(sig.get('R:R', 0))
            vol = float(sig.get('Vol', 0))
            rsi = float(sig.get('RSI', 50))
            dist = abs(float(sig.get('Dist', 0) or sig.get('SMA_Dist%', 0) or 0))

            o['type'] = sig_type
            o['regime'] = regime
            o['quality'] = quality
            o['rr'] = rr
            o['vol'] = vol
            o['rsi'] = rsi
            o['dist'] = dist
            o['symbol'] = symbol
            outcomes.append(o)

            # Accumulate stats
            for bucket, stats in [(sig_type, type_stats), (regime, regime_stats), (quality, quality_stats)]:
                stats[bucket]['pnl_sum'] += o['pnl_pct']
                if o['win']:
                    stats[bucket]['wins'] += 1
                else:
                    stats[bucket]['losses'] += 1

            # R:R buckets
            if rr < 1.5: rr_buckets['rr<1.5'].append(o['pnl_pct'])
            elif rr <= 2.5: rr_buckets['rr1.5-2.5'].append(o['pnl_pct'])
            else: rr_buckets['rr>2.5'].append(o['pnl_pct'])

            # Volume buckets
            if vol < 1.5: vol_buckets['vol<1.5'].append(o['pnl_pct'])
            elif vol <= 2.5: vol_buckets['vol1.5-2.5'].append(o['pnl_pct'])
            else: vol_buckets['vol>2.5'].append(o['pnl_pct'])

            # RSI buckets
            if rsi < 40: rsi_buckets['rsi<40'].append(o['pnl_pct'])
            elif rsi <= 60: rsi_buckets['rsi40-60'].append(o['pnl_pct'])
            elif rsi <= 80: rsi_buckets['rsi60-80'].append(o['pnl_pct'])
            else: rsi_buckets['rsi>80'].append(o['pnl_pct'])

            # Distance from SMA buckets
            if dist < 5: dist_buckets['dist<5%'].append(o['pnl_pct'])
            elif dist <= 10: dist_buckets['dist5-10%'].append(o['pnl_pct'])
            else: dist_buckets['dist>10%'].append(o['pnl_pct'])

        if not outcomes:
            continue

        wins = sum(1 for o in outcomes if o['win'])
        total = len(outcomes)
        avg_pnl = np.mean([o['pnl_pct'] for o in outcomes])
        med_pnl = np.median([o['pnl_pct'] for o in outcomes])
        avg_mae = np.mean([o['mae'] for o in outcomes])
        avg_mfe = np.mean([o['mfe'] for o in outcomes])

        print(f"\n  ── {group_name}: {total} signals ──")
        print(f"     WR: {wins/total*100:.1f}% | Avg P&L: {avg_pnl:+.2f}% | Median P&L: {med_pnl:+.2f}%")
        print(f"     Avg MAE: {avg_mae:.2f}% | Avg MFE: {avg_mfe:.2f}%")

        # By type
        print(f"\n     By Type:")
        for t, s in sorted(type_stats.items(), key=lambda x: -abs(x[1]['pnl_sum'])):
            n = s['wins'] + s['losses']
            wr = s['wins'] / n * 100 if n else 0
            avg = s['pnl_sum'] / n if n else 0
            print(f"       {t:<16} {n:>4} sigs | WR {wr:5.1f}% | Avg P&L {avg:+.2f}%")

        # By regime
        print(f"     By Regime:")
        for r, s in sorted(regime_stats.items(), key=lambda x: -abs(x[1]['pnl_sum'])):
            n = s['wins'] + s['losses']
            wr = s['wins'] / n * 100 if n else 0
            avg = s['pnl_sum'] / n if n else 0
            print(f"       {r:<16} {n:>4} sigs | WR {wr:5.1f}% | Avg P&L {avg:+.2f}%")

        # By quality
        print(f"     By Quality:")
        for q, s in sorted(quality_stats.items(), key=lambda x: -abs(x[1]['pnl_sum'])):
            n = s['wins'] + s['losses']
            wr = s['wins'] / n * 100 if n else 0
            avg = s['pnl_sum'] / n if n else 0
            print(f"       {q:<16} {n:>4} sigs | WR {wr:5.1f}% | Avg P&L {avg:+.2f}%")

        # Bucket analysis
        print(f"     By R:R ratio:")
        for label, pnls in rr_buckets.items():
            if pnls:
                wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
                print(f"       {label:<16} {len(pnls):>4} sigs | WR {wr:5.1f}% | Avg {np.mean(pnls):+.2f}%")

        print(f"     By Volume ratio:")
        for label, pnls in vol_buckets.items():
            if pnls:
                wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
                print(f"       {label:<16} {len(pnls):>4} sigs | WR {wr:5.1f}% | Avg {np.mean(pnls):+.2f}%")

        print(f"     By RSI:")
        for label, pnls in rsi_buckets.items():
            if pnls:
                wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
                print(f"       {label:<16} {len(pnls):>4} sigs | WR {wr:5.1f}% | Avg {np.mean(pnls):+.2f}%")

        print(f"     By SMA Distance:")
        for label, pnls in dist_buckets.items():
            if pnls:
                wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
                print(f"       {label:<16} {len(pnls):>4} sigs | WR {wr:5.1f}% | Avg {np.mean(pnls):+.2f}%")

        # Worst signals detail
        worst = sorted(outcomes, key=lambda x: x['pnl_pct'])[:10]
        print(f"\n     Worst 10 signals:")
        print(f"       {'Symbol':<8} {'Type':<16} {'Regime':<12} {'Quality':<10} {'P&L%':>7} {'MAE%':>7} {'R:R':>5} {'Vol':>5} {'RSI':>5}")
        for o in worst:
            print(f"       {o['symbol']:<8} {o['type']:<16} {o['regime']:<12} {o['quality']:<10} "
                  f"{o['pnl_pct']:>+6.2f}% {o['mae']:>+6.2f}% {o['rr']:>5.2f} {o['vol']:>5.2f} {o['rsi']:>5.1f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--years', default='2022,2024,2026', help='Years with worst dilution')
    p.add_argument('--watchlist', default='input/backtest_200.txt')
    args = p.parse_args()

    years = [int(y.strip()) for y in args.years.split(',')]
    symbols = load_symbols(args.watchlist)

    for year in years:
        start = f'{year}-01-01'
        end = f'{year}-12-31'
        print(f"\nFetching data for {year}...")
        historical = fetch_all_data(symbols, start, end)
        run_year(year, [s for s in symbols if s in historical], historical)


if __name__ == '__main__':
    main()
