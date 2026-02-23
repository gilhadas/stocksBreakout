#!/usr/bin/env python3
"""
V8 Breakout Validation: Multi-Period Market Regime Testing
------------------------------------------------------------
Tests all strategy versions (V1 through V8X) across different market regimes:

  - BEARISH: 2022-01-01 to 2022-12-31  (SPY ~-19%)
  - BULLISH: 2023-01-01 to 2024-06-30  (SPY ~+40%)
  - MIXED:   2024-01-01 to 2025-12-31  (primary test period)

Uses single-pass scanning (V1/V2/V5X variants) consistent with enhanced_backtest.py.
Includes V8 scoring: Minervini Stage 2 proportional scoring, V7 momentum surge,
23 pattern detectors with volume confirmation, overextension filter.
"""
import argparse
import asyncio
import pandas as pd
import numpy as np
import logging
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from mock_trader import SimulationMode
from scanner import BreakoutDetector
from yfinance_adapter import YFinanceAdapter

logging.basicConfig(level=logging.WARNING, format='%(message)s')
logger = logging.getLogger(__name__)

# ── CLI args ─────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description='V8 Breakout Validation: Multi-Period Regime Testing')
    p.add_argument('--watchlist', default=None, help='Watchlist file path')
    p.add_argument('--capital', type=int, default=100_000, help='Initial capital')
    p.add_argument('--modes', default='swing,longterm', help='Scan modes (default: swing,longterm)')
    p.add_argument('--limit', type=int, default=0, help='Cap symbol count (0 = no cap)')
    p.add_argument('--minervini-threshold', type=int, default=7,
                   help='Min MinerviniScore for V8/V8X pool (default: 7)')
    p.add_argument('--versions', default='all',
                   help='Comma-separated versions to compare, e.g. v1,v6x,v8,v8x (default: all)')
    return p.parse_args()


# ── Symbol loading ────────────────────────────────────────────────────────────

def load_symbols(watchlist_path=None, limit=0):
    search_paths = [watchlist_path] if watchlist_path else [
        'input/watchlist3.txt', 'input/watchlist2.txt', 'watchlist.txt'
    ]
    for path in search_paths:
        if path is None:
            continue
        try:
            with open(path, 'r') as f:
                content = f.read()
            raw = [s.strip() for s in content.replace('\n', ',').split(',') if s.strip()]
            symbols = []
            for s in raw:
                if s.startswith('#'):
                    continue
                if ':' in s:
                    s = s.split(':')[1]
                if s:
                    symbols.append(s)
            symbols = sorted(list(set(symbols)))
            if limit > 0:
                symbols = symbols[:limit]
            if len(symbols) > 10:
                print(f"  Loaded {len(symbols)} symbols from {path}")
                return symbols
        except FileNotFoundError:
            continue

    return [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'TSLA', 'META', 'NFLX',
        'AMD', 'INTC', 'JPM', 'BAC', 'WMT', 'HD', 'DIS', 'V', 'MA', 'PYPL',
        'COST', 'PEP', 'KO', 'MCD', 'NKE', 'SBUX', 'UNH', 'JNJ', 'PFE',
        'ABBV', 'XOM', 'CVX', 'BA', 'CAT', 'GE', 'HON', 'LMT', 'RTX',
        'UPS', 'FDX', 'CSCO', 'ORCL', 'IBM', 'CRM', 'ADBE', 'NOW', 'INTU',
        'TXN', 'QCOM', 'AVGO', 'MU', 'AMAT',
    ]


# ── Data fetching ───────────────────────────────────────────────────────────

def fetch_all_data(symbols, start_date, end_date):
    yf_adapter = YFinanceAdapter()
    historical = {}
    end_prices = {}

    # Need extra history for indicators (trend_period=150)
    fetch_start = (pd.to_datetime(start_date) - timedelta(days=365)).strftime('%Y-%m-%d')

    spy_df = yf_adapter.get_historical_data('SPY', '1 day',
                                             start_date=fetch_start,
                                             end_date=end_date)
    if spy_df is not None and len(spy_df) > 0:
        historical['SPY'] = spy_df

    total = len(symbols)
    for i, symbol in enumerate(symbols):
        if symbol == 'SPY':
            continue
        try:
            df = yf_adapter.get_historical_data(symbol, '1 day',
                                                 start_date=fetch_start,
                                                 end_date=end_date)
            if df is not None and len(df) > 20:
                historical[symbol] = df
                end_prices[symbol] = float(df.iloc[-1]['close'])
        except Exception:
            pass

        if (i + 1) % 25 == 0:
            print(f"  Fetched {i+1}/{total} symbols...")

    print(f"  Data ready: {len(historical)-1} stocks + SPY")
    return historical, end_prices


# ── SPY perf helper ─────────────────────────────────────────────────────────

def calculate_spy_perf_on_date(spy_df, current_date, lookback=15):
    df_up = spy_df[spy_df.index <= current_date]
    if len(df_up) < lookback + 1:
        return 0.0
    return (df_up['close'].iloc[-1] / df_up['close'].iloc[-lookback]) - 1


# ── Signal scanning (single-pass, matches enhanced_backtest.py) ────────────

def run_all_scans(historical, start_date, end_date, modes=None):
    """
    Single-pass scan producing 3 signal variants simultaneously (V1/V2/V5X).
    Matches enhanced_backtest.py approach — iterate (date x symbol) once.
    """
    if modes is None:
        modes = ['swing', 'longterm']

    detector = BreakoutDetector()
    spy_df = historical.get('SPY')

    variants = ['v1', 'v2', 'v5x']
    variant_flags = {
        'v1':  {'use_legacy_momentum': True,  'use_v4_overextension': False},
        'v2':  {'use_legacy_momentum': False, 'use_v4_overextension': False},
        'v5x': {'use_legacy_momentum': False, 'use_v4_overextension': True},
    }
    results   = {v: [] for v in variants}
    cooldowns = {v: {} for v in variants}

    sim_dates = pd.date_range(start=start_date, end=end_date, freq='B')
    symbols   = [s for s in historical if s != 'SPY']

    print(f"\n  [Single-pass] Scanning {len(symbols)} symbols x {len(modes)} modes "
          f"x {len(variants)} variants over {len(sim_dates)} days...")

    def _make_signal(sym, mode_name, sim_date, sig, sig_type):
        base = {
            'date': sim_date, 'symbol': sym, 'action': 'BUY',
            'price': sig['Price'], 'entry_price': sig['Price'],
            'stop_loss': sig['Stop'], 'take_profit': sig['Target'],
            'quality': sig['Quality'], 'mode': mode_name, 'type': sig_type,
            'minervini_score': sig.get('MinerviniScore', 0),   # V8: 0-8
            'is_momentum': sig.get('Type') == 'Momentum',      # V7
        }
        if sig_type == 'BREAKOUT':
            base.update({
                'win_probability': sig.get('WinProb', 0.50),
                'rr_grade': sig.get('RR_Grade', ''),
                'patterns': sig.get('Patterns', ''),
            })
        return base

    for day_idx, sim_date in enumerate(sim_dates):
        spy_perf = 0.0
        if spy_df is not None:
            spy_perf = calculate_spy_perf_on_date(spy_df, sim_date, lookback=15)

        for symbol in symbols:
            df = historical[symbol]
            df_slice = df[df.index <= sim_date]

            if len(df_slice) < 150:
                continue
            if df_slice.index[-1].date() != sim_date.date():
                continue

            for variant in variants:
                flags = variant_flags[variant]

                last_sig = cooldowns[variant].get(symbol)
                if last_sig and (sim_date - last_sig).days < 10:
                    continue

                for mode_name in modes:
                    # Breakout
                    try:
                        sig = detector.detect(
                            df_slice, symbol, mode_name, '1 day', spy_perf,
                            use_scoring=True, **flags
                        )
                    except Exception:
                        sig = None

                    if sig:
                        results[variant].append(_make_signal(symbol, mode_name, sim_date, sig, 'BREAKOUT'))
                        cooldowns[variant][symbol] = sim_date
                        break

                    # Pullback (only v2/v5x)
                    if not flags['use_legacy_momentum']:
                        try:
                            pb_sig = detector.detect_pullback(df_slice, symbol, mode_name, '1 day', spy_perf)
                        except Exception:
                            pb_sig = None

                        if pb_sig:
                            results[variant].append(_make_signal(symbol, mode_name, sim_date, pb_sig, 'PULLBACK'))
                            cooldowns[variant][symbol] = sim_date
                            break

        if (day_idx + 1) % 60 == 0:
            counts = ' | '.join(f"{v.upper()}={len(results[v])}" for v in variants)
            print(f"    Day {day_idx+1}/{len(sim_dates)}: {counts}")

    for variant in variants:
        sigs = results[variant]
        print(f"\n  [{variant.upper()}] Total signals: {len(sigs)}"
              f"  GOLD={sum(1 for s in sigs if s.get('quality')=='GOLD')}"
              f"  PREMIUM={sum(1 for s in sigs if s.get('quality')=='PREMIUM')}"
              f"  HIGH={sum(1 for s in sigs if s.get('quality')=='HIGH')}"
              f"  STANDARD={sum(1 for s in sigs if s.get('quality')=='STANDARD')}")

    return results


# ── SPY benchmark ───────────────────────────────────────────────────────────

def calculate_spy_benchmark(historical, start_date, end_date, initial_capital=100000):
    spy_df = historical.get('SPY')
    if spy_df is None or len(spy_df) == 0:
        return None

    sd = pd.to_datetime(start_date)
    ed = pd.to_datetime(end_date)
    spy_period = spy_df[(spy_df.index >= sd) & (spy_df.index <= ed)]
    if len(spy_period) < 2:
        return None

    start_price = float(spy_period.iloc[0]['close'])
    end_price = float(spy_period.iloc[-1]['close'])
    shares = initial_capital / start_price
    end_value = shares * end_price
    total_return = ((end_value - initial_capital) / initial_capital) * 100

    spy_period = spy_period.copy()
    spy_period['returns'] = spy_period['close'].pct_change()
    mean_ret = spy_period['returns'].mean()
    std_ret = spy_period['returns'].std()
    sharpe = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 0 else 0

    spy_period['cummax'] = spy_period['close'].cummax()
    spy_period['dd'] = (spy_period['close'] / spy_period['cummax'] - 1) * 100
    max_dd = spy_period['dd'].min()

    return {
        'total_return': total_return,
        'sharpe_ratio': sharpe,
        'max_drawdown': max_dd,
        'start_price': start_price,
        'end_price': end_price,
        'end_value': end_value,
    }


# ── Run one config ──────────────────────────────────────────────────────────

def run_sim(signals, start_date, end_date, end_prices, historical,
            initial_capital, pos_pct, risk_pct, spy_hedge=False, spy_alloc=0.0):
    if not signals:
        return None

    sim = SimulationMode(
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        max_position_pct=pos_pct,
        max_risk_pct=risk_pct,
        use_trailing_stop=False,
        spy_hedge_enabled=spy_hedge,
        spy_allocation_pct=spy_alloc,
    )
    report = sim.run_simulation(signals, end_prices=end_prices,
                                historical_data=historical)
    return report


# ── Print table ─────────────────────────────────────────────────────────────

def print_results_table(period_name, results_list, spy_report, initial_capital):
    spy_ret = spy_report['total_return'] if spy_report else 0
    spy_sharpe = spy_report['sharpe_ratio'] if spy_report else 0
    spy_dd = spy_report['max_drawdown'] if spy_report else 0

    print(f"\n{'=' * 130}")
    print(f"  {period_name}")
    print(f"{'=' * 130}")

    header = (f"{'Config':<44} {'Sigs':>5} {'Trades':>6} {'Return':>10} "
              f"{'WinRate':>8} {'Sharpe':>7} {'MaxDD':>8} {'W/L':>6} {'vs SPY':>10}")
    print(header)
    print("-" * 130)

    print(f"{'SPY Buy & Hold':<44} {'1':>5} {'1':>6} {spy_ret:>+9.2f}% "
          f"{'N/A':>8} {spy_sharpe:>7.2f} {spy_dd:>+7.2f}% {'N/A':>6} {'--':>10}")
    print("-" * 130)

    best_return = -999
    best_name = ""
    beats_spy_count = 0

    for r in results_list:
        name = r['config_name']
        sigs = r.get('signal_count', 0)
        trades = r['total_trades']
        ret = r['total_return']
        wr = r['win_rate']
        sharpe = r['sharpe_ratio']
        dd = r['max_drawdown']
        avg_w = r['avg_win']
        avg_l = r['avg_loss']
        wl = abs(avg_w / avg_l) if avg_l != 0 else 0
        diff = ret - spy_ret
        marker = " ***" if ret > spy_ret else ""

        print(f"{name:<44} {sigs:>5} {trades:>6} {ret:>+9.2f}% "
              f"{wr:>7.1f}% {sharpe:>7.2f} {dd:>+7.2f}% {wl:>5.2f} {diff:>+9.2f}%{marker}")

        if ret > best_return:
            best_return = ret
            best_name = name
        if ret > spy_ret:
            beats_spy_count += 1

    print("-" * 130)
    beats_spy = best_return > spy_ret
    print(f"  Best config: {best_name} ({best_return:+.2f}%)")
    print(f"  SPY return:  {spy_ret:+.2f}%")
    if beats_spy:
        print(f"  STRATEGY BEATS SPY by {best_return - spy_ret:+.2f}%!")
    else:
        print(f"  Gap to SPY: {spy_ret - best_return:.2f}%")
    print(f"  {beats_spy_count}/{len(results_list)} configs beat SPY")

    return results_list


# ── Run one period ──────────────────────────────────────────────────────────

def run_period(period_name, start_date, end_date, symbols, initial_capital=100000,
               scan_modes=None, m_thresh=7, requested_versions='all'):
    if scan_modes is None:
        scan_modes = ['swing', 'longterm']

    print(f"\n\n{'#' * 130}")
    print(f"#  {period_name}: {start_date} to {end_date}")
    print(f"{'#' * 130}")

    # Fetch data (disk-cached — fast on repeat runs)
    print("\n  Fetching historical data (disk-cached)...")
    historical, end_prices = fetch_all_data(symbols, start_date, end_date)

    if len(historical) < 5:
        print("  Not enough data. Skipping period.")
        return None

    # SPY benchmark
    spy_report = calculate_spy_benchmark(historical, start_date, end_date,
                                          initial_capital)

    # Single-pass scan — all 3 variants (V1/V2/V5X) in one loop
    print(f"\n  Scanning signals (single-pass: V1 / V2-V6 / V5X-V6X / V7 / V8 / V8X)...")
    all_scan_results = run_all_scans(historical, start_date, end_date, modes=scan_modes)

    v1_signals  = all_scan_results['v1']
    v2_signals  = all_scan_results['v2']
    v5x_signals = all_scan_results['v5x']

    v5_signals = v2_signals   # V5/V6 reuse v2 base

    # V7: momentum surge only
    v7_signals = [s for s in v2_signals if s.get('is_momentum')]
    # V8: Minervini ≥ threshold on V2 base
    v8_signals  = [s for s in v2_signals  if s.get('minervini_score', 0) >= m_thresh]
    # V8X: Minervini ≥ threshold + overextension filter
    v8x_signals = [s for s in v5x_signals if s.get('minervini_score', 0) >= m_thresh]

    print(f"  [V7]  Momentum surge: {len(v7_signals)}  "
          f"[V8]  Minervini≥{m_thresh}: {len(v8_signals)}  "
          f"[V8X] Minervini≥{m_thresh}+overext: {len(v8x_signals)}")

    if not v1_signals and not v2_signals:
        print("\n  No signals generated. Skipping period.")
        return None

    # Define configs — V1 through V8X
    configs = [
        # V1 baseline
        {
            'name': 'V1) HIGH+, legacy momentum',
            'signals': v1_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
        },
        # V2 composite momentum
        {
            'name': 'V2) HIGH+, composite momentum',
            'signals': v2_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
        },
        # V6 (V5 + 23 patterns with volume confirmation scoring)
        {
            'name': 'V6-A) HIGH+, 10% cap',
            'signals': v5_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
        },
        {
            'name': 'V6-B) PREMIUM+, 10% cap',
            'signals': v5_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
        },
        # V6X: V6 + overextension filter (previous recommended)
        {
            'name': 'V6X-A) HIGH+, overext+10% cap',
            'signals': v5x_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
        },
        {
            'name': 'V6X-B) PREMIUM+, overext+10% cap',
            'signals': v5x_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
        },
        # V6X SPY hedge variants
        {
            'name': 'V6X-C) HIGH+, 30% SPY hedge',
            'signals': v5x_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'spy_hedge': True, 'spy_alloc': 0.30,
        },
        # V7: momentum surge signals only
        {
            'name': 'V7) Momentum-surge, HIGH+',
            'signals': v7_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
        },
        # V8: Minervini Stage 2 filter on V2 base
        {
            'name': f'V8-A) Minervini≥{m_thresh}, HIGH+',
            'signals': v8_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
        },
        {
            'name': f'V8-B) Minervini≥{m_thresh}, PREMIUM+',
            'signals': v8_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
        },
        # V8X: Minervini Stage 2 + overextension filter (most selective)
        {
            'name': f'V8X-A) Minervini≥{m_thresh}+overext, HIGH+',
            'signals': v8x_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
        },
        {
            'name': f'V8X-B) Minervini≥{m_thresh}+overext, PREMIUM+',
            'signals': v8x_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
        },
    ]

    # Filter by --versions if specified
    if requested_versions.lower() != 'all':
        requested = sorted(
            [v.strip().upper() for v in requested_versions.split(',')],
            key=len, reverse=True,
        )
        def _version_match(name):
            nu = name.upper()
            for v in requested:
                if nu.startswith(v + ')') or nu.startswith(v + '-'):
                    return True
            return False
        configs = [c for c in configs if _version_match(c['name'])]

    results_list = []
    for cfg in configs:
        filtered = [s for s in cfg.get('signals', []) if cfg['filter'](s)]
        if not filtered:
            print(f"\n  --- {cfg['name']} (0 signals, skipped) ---")
            continue

        print(f"\n  --- {cfg['name']} ({len(filtered)} signals) ---")

        report = run_sim(
            filtered, start_date, end_date, end_prices, historical,
            initial_capital, cfg['pos_pct'], cfg['risk_pct'],
            spy_hedge=cfg.get('spy_hedge', False),
            spy_alloc=cfg.get('spy_alloc', 0.0)
        )
        if report:
            report['config_name'] = cfg['name']
            report['signal_count'] = len(filtered)
            results_list.append(report)

    print_results_table(period_name, results_list, spy_report, initial_capital)

    return {
        'period': period_name,
        'start': start_date,
        'end': end_date,
        'spy': spy_report,
        'results': results_list,
    }


# ── Cross-period comparison ─────────────────────────────────────────────────

def print_cross_period_summary(all_periods, m_thresh=7):
    print(f"\n\n{'=' * 130}")
    print("CROSS-PERIOD SUMMARY: V8 Breakout Strategy — Bear / Bull / Mixed")
    print(f"{'=' * 130}")

    # Gather all unique config names from actual results (order preserved)
    seen = set()
    config_names_ordered = []
    for p in all_periods:
        if not p:
            continue
        for r in p.get('results', []):
            name = r['config_name']
            if name not in seen:
                seen.add(name)
                config_names_ordered.append(name)

    period_names = [p['period'] for p in all_periods if p]

    # Header
    col_w = 16
    header = f"{'Config':<44}"
    for pn in period_names:
        short = pn.split(':')[0].strip() if ':' in pn else pn[:col_w]
        header += f" {short:>{col_w}}"
    header += f" {'Avg':>{col_w}}"
    print(header)
    print("-" * 130)

    # SPY row
    spy_row = f"{'SPY Buy & Hold':<44}"
    spy_rets = []
    for p in all_periods:
        if p and p.get('spy'):
            r = p['spy']['total_return']
            spy_rets.append(r)
            spy_row += f" {r:>{col_w-1}.2f}%"
        else:
            spy_row += f" {'N/A':>{col_w}}"
    if spy_rets:
        spy_row += f" {np.mean(spy_rets):>{col_w-1}.2f}%"
    print(spy_row)
    print("-" * 130)

    # Config rows
    for cname in config_names_ordered:
        row = f"{cname:<44}"
        rets = []
        for p in all_periods:
            if not p:
                row += f" {'N/A':>{col_w}}"
                continue
            found = None
            for r in p.get('results', []):
                if r['config_name'] == cname:
                    found = r
                    break
            if found:
                ret = found['total_return']
                rets.append(ret)
                row += f" {ret:>{col_w-1}.2f}%"
            else:
                row += f" {'--':>{col_w}}"
        if rets:
            row += f" {np.mean(rets):>{col_w-1}.2f}%"
        else:
            row += f" {'--':>{col_w}}"
        print(row)

    print("-" * 130)

    # Drawdown comparison
    print(f"\n{'MAX DRAWDOWN COMPARISON':<44}")
    header2 = f"{'Config':<44}"
    for pn in period_names:
        short = pn.split(':')[0].strip() if ':' in pn else pn[:col_w]
        header2 += f" {short:>{col_w}}"
    print(header2)
    print("-" * 130)

    spy_dd_row = f"{'SPY Buy & Hold':<44}"
    for p in all_periods:
        if p and p.get('spy'):
            dd = p['spy']['max_drawdown']
            spy_dd_row += f" {dd:>{col_w-1}.2f}%"
        else:
            spy_dd_row += f" {'N/A':>{col_w}}"
    print(spy_dd_row)

    for cname in config_names_ordered:
        row = f"{cname:<44}"
        for p in all_periods:
            if not p:
                row += f" {'N/A':>{col_w}}"
                continue
            found = None
            for r in p.get('results', []):
                if r['config_name'] == cname:
                    found = r
                    break
            if found:
                dd = found['max_drawdown']
                row += f" {dd:>{col_w-1}.2f}%"
            else:
                row += f" {'--':>{col_w}}"
        print(row)

    print("-" * 130)

    # Final verdict
    print(f"\n{'KEY INSIGHTS':}")
    print("-" * 80)

    for p in all_periods:
        if not p:
            continue
        pname = p['period']
        spy_ret = p['spy']['total_return'] if p.get('spy') else 0
        spy_dd = p['spy']['max_drawdown'] if p.get('spy') else 0

        best_ret = -999
        best_name = ""
        best_dd_name = ""
        best_dd = -999

        for r in p.get('results', []):
            if r['total_return'] > best_ret:
                best_ret = r['total_return']
                best_name = r['config_name']
            if r['max_drawdown'] > best_dd:
                best_dd = r['max_drawdown']
                best_dd_name = r['config_name']

        beats_spy = best_ret > spy_ret
        icon = "BEATS SPY" if beats_spy else "TRAILS SPY"

        print(f"\n  {pname}:")
        print(f"    SPY: {spy_ret:+.2f}% return, {spy_dd:.2f}% max DD")
        print(f"    Best return: {best_name} ({best_ret:+.2f}%) [{icon} by {best_ret - spy_ret:+.2f}%]")
        print(f"    Best risk:   {best_dd_name} ({best_dd:.2f}% max DD vs SPY {spy_dd:.2f}%)")

    print(f"\n{'=' * 130}")


# ── Main ────────────────────────────────────────────────────────────────────

async def main():
    args = parse_args()
    scan_modes = [m.strip() for m in args.modes.split(',')]
    m_thresh   = args.minervini_threshold

    print("=" * 130)
    print("V8 BREAKOUT VALIDATION: Multi-Period Market Regime Testing")
    print("=" * 130)
    print(f"  Modes: {', '.join(scan_modes)}  |  Minervini threshold: {m_thresh}"
          f"  |  Versions: {args.versions}")
    if args.watchlist:
        print(f"  Watchlist: {args.watchlist}")

    # Load symbols once (shared across all periods — data fetched per period separately)
    print("\nLoading symbols...")
    symbols = load_symbols(watchlist_path=args.watchlist, limit=args.limit)

    # Define test periods
    periods = [
        ("BEARISH 2022: SPY ~-19%",     "2022-01-01", "2022-12-31"),
        ("BULLISH 2023-H1-2024: SPY ~+40%", "2023-01-01", "2024-06-30"),
        ("MIXED 2024-2025: primary test", "2024-01-01", "2025-12-31"),
    ]

    all_period_results = []

    for period_name, start, end in periods:
        result = run_period(
            period_name, start, end, symbols,
            initial_capital=args.capital,
            scan_modes=scan_modes,
            m_thresh=m_thresh,
            requested_versions=args.versions,
        )
        all_period_results.append(result)

    # Cross-period comparison
    print_cross_period_summary(all_period_results, m_thresh=m_thresh)

    # Save results
    output_dir = Path('scanner_output/backtests')
    output_dir.mkdir(parents=True, exist_ok=True)

    def safe_val(v):
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return float(v)
        if callable(v):
            return str(v)
        return v

    save_data = []
    for p in all_period_results:
        if p:
            save_data.append({
                'period': p['period'],
                'start': p['start'],
                'end': p['end'],
                'spy': {k: safe_val(v) for k, v in (p['spy'] or {}).items()},
                'configs': [{k: safe_val(v) for k, v in r.items()} for r in p['results']],
            })

    fname = output_dir / 'v8_validation_multi_period.json'
    with open(fname, 'w') as f:
        json.dump(save_data, f, indent=2)
    print(f"\nResults saved to {fname}")


if __name__ == "__main__":
    asyncio.run(main())
