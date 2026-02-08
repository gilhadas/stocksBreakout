#!/usr/bin/env python3
"""
V3 Hybrid Breakout Validation: Bullish vs Bearish Years
---------------------------------------------------------
Tests the V3 improvements (BB filter, patterns, Grade D rejection,
SPY hedge, win prob sizing, max hold) across different market regimes:

  - BEARISH: 2022-01-01 to 2022-12-31  (SPY ~-19%)
  - BULLISH: 2023-01-01 to 2024-06-30  (SPY ~+40%)
  - MIXED:   2024-01-01 to 2025-12-31  (original test period)

Compares V2 baseline vs V3 configs vs SPY buy-and-hold.
"""
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

# ── Symbol loading (reused from enhanced_backtest) ──────────────────────────

def load_symbols():
    for path in ['input/watchlist3.txt', 'input/watchlist2.txt', 'watchlist.txt']:
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
                symbols.append(s)
            symbols = sorted(list(set(symbols)))[:100]
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


# ── Signal scanning ─────────────────────────────────────────────────────────

def run_scan(historical, start_date, end_date, use_legacy_momentum=False,
             modes=None, label=""):
    if modes is None:
        modes = ['swing', 'longterm']

    version = label or ("V1" if use_legacy_momentum else "V2/V3")
    detector = BreakoutDetector()
    spy_df = historical.get('SPY')
    all_signals = []
    cooldowns = {}

    sim_dates = pd.date_range(start=start_date, end=end_date, freq='B')
    symbols = [s for s in historical if s != 'SPY']

    print(f"\n  [{version}] Scanning {len(symbols)} symbols x {len(modes)} modes "
          f"over {len(sim_dates)} days...")

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

            last_sig = cooldowns.get(symbol)
            if last_sig and (sim_date - last_sig).days < 10:
                continue

            for mode_name in modes:
                try:
                    sig = detector.detect(
                        df_slice, symbol, mode_name, '1 day', spy_perf,
                        use_scoring=True,
                        use_legacy_momentum=use_legacy_momentum
                    )
                except Exception:
                    sig = None

                if sig:
                    all_signals.append({
                        'date': sim_date,
                        'symbol': symbol,
                        'action': 'BUY',
                        'price': sig['Price'],
                        'entry_price': sig['Price'],
                        'stop_loss': sig['Stop'],
                        'take_profit': sig['Target'],
                        'quality': sig['Quality'],
                        'mode': mode_name,
                        'type': 'BREAKOUT',
                        'win_probability': sig.get('WinProb', 0.50),
                        'rr_grade': sig.get('RR_Grade', ''),
                        'patterns': sig.get('Patterns', ''),
                    })
                    cooldowns[symbol] = sim_date
                    break

        if (day_idx + 1) % 60 == 0:
            print(f"    Day {day_idx+1}/{len(sim_dates)}: {len(all_signals)} signals so far")

    premiums = sum(1 for s in all_signals if s.get('quality') == 'PREMIUM')
    highs = sum(1 for s in all_signals if s.get('quality') == 'HIGH')
    standards = sum(1 for s in all_signals if s.get('quality') == 'STANDARD')
    print(f"  [{version}] Total: {len(all_signals)} signals "
          f"(PREMIUM:{premiums} HIGH:{highs} STANDARD:{standards})")

    return all_signals


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

def run_period(period_name, start_date, end_date, symbols, initial_capital=100000):
    print(f"\n\n{'#' * 130}")
    print(f"#  {period_name}: {start_date} to {end_date}")
    print(f"{'#' * 130}")

    # Fetch data
    print("\n  Fetching historical data...")
    historical, end_prices = fetch_all_data(symbols, start_date, end_date)

    if len(historical) < 5:
        print("  Not enough data. Skipping period.")
        return None

    # SPY benchmark
    spy_report = calculate_spy_benchmark(historical, start_date, end_date,
                                          initial_capital)

    # Scan signals — V2 (which now includes V3 features: BB filter, patterns, grade D)
    v2_signals = run_scan(historical, start_date, end_date,
                          use_legacy_momentum=False, label="V2/V3")

    # Also scan V1 for comparison
    v1_signals = run_scan(historical, start_date, end_date,
                          use_legacy_momentum=True, label="V1 (legacy)")

    if not v2_signals and not v1_signals:
        print("\n  No signals generated. Skipping period.")
        return None

    # Define configs
    configs = [
        # V1 baseline
        {
            'name': 'V1) HIGH+, legacy momentum',
            'signals': v1_signals,
            'filter': lambda s: s.get('quality') in ('PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
        },
        # V2 baseline (before V3 code changes this was the best)
        {
            'name': 'V2) HIGH+, composite momentum',
            'signals': v2_signals,
            'filter': lambda s: s.get('quality') in ('PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
        },
        # V3 configs
        {
            'name': 'V3-A) HIGH+, BB+patterns+gradeD',
            'signals': v2_signals,
            'filter': lambda s: s.get('quality') in ('PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
        },
        {
            'name': 'V3-B) HIGH+, 30% SPY hedge',
            'signals': v2_signals,
            'filter': lambda s: s.get('quality') in ('PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'spy_hedge': True, 'spy_alloc': 0.30,
        },
        {
            'name': 'V3-C) PREMIUM only, 50% SPY hedge',
            'signals': v2_signals,
            'filter': lambda s: s.get('quality') == 'PREMIUM',
            'pos_pct': 0.15, 'risk_pct': 0.03,
            'spy_hedge': True, 'spy_alloc': 0.50,
        },
        {
            'name': 'V3-D) ALL quality, 40% SPY hedge',
            'signals': v2_signals,
            'filter': lambda s: True,
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'spy_hedge': True, 'spy_alloc': 0.40,
        },
    ]

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

def print_cross_period_summary(all_periods):
    print(f"\n\n{'=' * 130}")
    print("CROSS-PERIOD SUMMARY: V3 Hybrid Breakout Strategy")
    print(f"{'=' * 130}")

    # For each config name, gather returns across periods
    config_names_ordered = [
        'V1) HIGH+, legacy momentum',
        'V2) HIGH+, composite momentum',
        'V3-A) HIGH+, BB+patterns+gradeD',
        'V3-B) HIGH+, 30% SPY hedge',
        'V3-C) PREMIUM only, 50% SPY hedge',
        'V3-D) ALL quality, 40% SPY hedge',
    ]

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
    print("=" * 130)
    print("V3 HYBRID BREAKOUT VALIDATION: Bullish vs Bearish Market Regimes")
    print("=" * 130)

    initial_capital = 100000

    # Load symbols once
    print("\nLoading symbols...")
    symbols = load_symbols()

    # Define test periods
    periods = [
        ("BEARISH 2022: SPY -19%", "2022-01-01", "2022-12-31"),
        ("BULLISH 2023-24: SPY +40%", "2023-01-01", "2024-06-30"),
        ("MIXED 2024-25: original test", "2024-01-01", "2025-12-31"),
    ]

    all_period_results = []

    for period_name, start, end in periods:
        result = run_period(period_name, start, end, symbols, initial_capital)
        all_period_results.append(result)

    # Cross-period comparison
    print_cross_period_summary(all_period_results)

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

    fname = output_dir / 'v3_validation_multi_period.json'
    with open(fname, 'w') as f:
        json.dump(save_data, f, indent=2)
    print(f"\nResults saved to {fname}")


if __name__ == "__main__":
    asyncio.run(main())
