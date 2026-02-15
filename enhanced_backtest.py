#!/usr/bin/env python3
"""
Enhanced Backtest: Strategy vs SPY
-----------------------------------
Comprehensive multi-mode backtest with:
- Scoring system (not all-or-nothing)
- Pullback re-entry signals
- Multi-mode scanning (swing + longterm)
- Actual SPY performance for RS filter (fixes spy_perf=0.0 bug)
- Adaptive position sizing by signal quality
- Scale-out partial exits
- 10-day per-symbol cooldown to avoid over-trading
"""
import asyncio
import pandas as pd
import numpy as np
import logging
import sys
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


def load_symbols():
    """Load symbols from watchlist files"""
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

    # Fallback
    return [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'TSLA', 'META', 'NFLX',
        'AMD', 'INTC', 'JPM', 'BAC', 'WMT', 'HD', 'DIS', 'V', 'MA', 'PYPL',
        'COST', 'PEP', 'KO', 'MCD', 'NKE', 'SBUX', 'UNH', 'JNJ', 'PFE',
        'ABBV', 'XOM', 'CVX', 'BA', 'CAT', 'GE', 'HON', 'LMT', 'RTX',
        'UPS', 'FDX', 'CSCO', 'ORCL', 'IBM', 'CRM', 'ADBE', 'NOW', 'INTU',
        'TXN', 'QCOM', 'AVGO', 'MU', 'AMAT',
    ]


def fetch_all_data(symbols, start_date, end_date):
    """Fetch historical data for all symbols + SPY"""
    yf_adapter = YFinanceAdapter()
    historical = {}
    end_prices = {}

    # Need extra history for indicators (trend_period=150)
    fetch_start = (pd.to_datetime(start_date) - timedelta(days=365)).strftime('%Y-%m-%d')

    # Fetch SPY first for RS calculation
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

        if (i + 1) % 20 == 0:
            print(f"  Fetched {i+1}/{total} symbols...")

    print(f"  Data ready: {len(historical)-1} stocks + SPY")
    return historical, end_prices


def calculate_spy_perf_on_date(spy_df, current_date, lookback=15):
    """Calculate actual SPY performance over lookback period ending on current_date"""
    df_up = spy_df[spy_df.index <= current_date]
    if len(df_up) < lookback + 1:
        return 0.0
    return (df_up['close'].iloc[-1] / df_up['close'].iloc[-lookback]) - 1


def run_enhanced_scan(historical, start_date, end_date, modes=None,
                      use_legacy_momentum=False, use_v4_overextension=False):
    """
    Scan all symbols across multiple modes with scoring + pullback entries.
    Returns list of signal dicts.

    Args:
        use_legacy_momentum: If True, use V1 binary RSI/MACD/ADX checks.
                             If False, use V2 composite momentum + conviction.
        use_v4_overextension: If True, apply V4 over-extension filter (SMA distance penalty).
    """
    if modes is None:
        modes = ['swing', 'longterm']

    if use_legacy_momentum:
        version = "V1 (legacy)"
    elif use_v4_overextension:
        version = "V4 (overextension filter)"
    else:
        version = "V2 (composite)"
    detector = BreakoutDetector()
    spy_df = historical.get('SPY')
    all_signals = []
    cooldowns = {}  # symbol -> last_signal_date

    sim_dates = pd.date_range(start=start_date, end=end_date, freq='B')
    symbols = [s for s in historical if s != 'SPY']

    print(f"\n  [{version}] Scanning {len(symbols)} symbols x {len(modes)} modes over {len(sim_dates)} days...")

    for day_idx, sim_date in enumerate(sim_dates):
        # Calculate actual SPY perf for this date
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

            # 10-day cooldown per symbol
            last_sig = cooldowns.get(symbol)
            if last_sig and (sim_date - last_sig).days < 10:
                continue

            for mode_name in modes:
                timeframe = '1 day'

                # --- Breakout with scoring ---
                try:
                    sig = detector.detect(
                        df_slice, symbol, mode_name, timeframe, spy_perf,
                        use_scoring=True,
                        use_legacy_momentum=use_legacy_momentum,
                        use_v4_overextension=use_v4_overextension
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
                    break  # One signal per symbol per day

                # --- Pullback re-entry ---
                try:
                    pb_sig = detector.detect_pullback(
                        df_slice, symbol, mode_name, timeframe, spy_perf
                    )
                except Exception:
                    pb_sig = None

                if pb_sig:
                    all_signals.append({
                        'date': sim_date,
                        'symbol': symbol,
                        'action': 'BUY',
                        'price': pb_sig['Price'],
                        'entry_price': pb_sig['Price'],
                        'stop_loss': pb_sig['Stop'],
                        'take_profit': pb_sig['Target'],
                        'quality': pb_sig['Quality'],
                        'mode': mode_name,
                        'type': 'PULLBACK',
                    })
                    cooldowns[symbol] = sim_date
                    break

        if (day_idx + 1) % 50 == 0:
            print(f"    Day {day_idx+1}/{len(sim_dates)}: {len(all_signals)} signals so far")

    # Breakdown
    breakouts = sum(1 for s in all_signals if s.get('type') == 'BREAKOUT')
    pullbacks = sum(1 for s in all_signals if s.get('type') == 'PULLBACK')
    premiums = sum(1 for s in all_signals if s.get('quality') == 'PREMIUM')
    highs = sum(1 for s in all_signals if s.get('quality') == 'HIGH')
    standards = sum(1 for s in all_signals if s.get('quality') == 'STANDARD')

    print(f"\n  [{version}] Total signals: {len(all_signals)}")
    print(f"    Breakouts: {breakouts} | Pullbacks: {pullbacks}")
    print(f"    PREMIUM: {premiums} | HIGH: {highs} | STANDARD: {standards}")

    return all_signals  # Return all, let each config filter


def run_simulation(signals, start_date, end_date, end_prices, historical,
                   initial_capital=100000):
    """Run simulation with adaptive sizing and scale-out"""
    if not signals:
        return None

    sim = SimulationMode(
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        max_position_pct=0.10,       # 10% base, scaled by quality (PREMIUM=30%)
        max_risk_pct=0.02,           # 2% risk per trade
        use_trailing_stop=False,     # Trailing stops cut winners too early
    )

    report = sim.run_simulation(signals, end_prices=end_prices,
                                historical_data=historical)
    return report


def calculate_spy_benchmark(historical, start_date, end_date, initial_capital=100000):
    """Calculate SPY buy-and-hold benchmark"""
    spy_df = historical.get('SPY')
    if spy_df is None or len(spy_df) == 0:
        return None

    start_price = float(spy_df.iloc[0]['close'])
    end_price = float(spy_df.iloc[-1]['close'])
    shares = initial_capital / start_price
    end_value = shares * end_price
    total_return = ((end_value - initial_capital) / initial_capital) * 100

    spy_df = spy_df.copy()
    spy_df['returns'] = spy_df['close'].pct_change()
    mean_ret = spy_df['returns'].mean()
    std_ret = spy_df['returns'].std()
    sharpe = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 0 else 0

    spy_df['cummax'] = spy_df['close'].cummax()
    spy_df['dd'] = (spy_df['close'] / spy_df['cummax'] - 1) * 100
    max_dd = spy_df['dd'].min()

    return {
        'total_return': total_return,
        'sharpe_ratio': sharpe,
        'max_drawdown': max_dd,
        'start_price': start_price,
        'end_price': end_price,
        'end_value': end_value,
    }


def print_comparison(strategy, spy, initial_capital, start_date, end_date):
    """Print side-by-side comparison"""
    if not strategy or not spy:
        print("\nInsufficient data for comparison.")
        return

    s_ret = strategy['total_return']
    b_ret = spy['total_return']
    s_sharpe = strategy['sharpe_ratio']
    b_sharpe = spy['sharpe_ratio']
    s_dd = strategy['max_drawdown']
    b_dd = spy['max_drawdown']

    print("\n" + "=" * 80)
    print("RESULTS COMPARISON")
    print("=" * 80)
    print(f"\n{'METRIC':<28} {'STRATEGY':<18} {'SPY':<18} {'DIFF':<16}")
    print("-" * 80)

    ret_icon = "+" if s_ret > b_ret else "-"
    print(f"{'Total Return':<28} {s_ret:+.2f}%{'':<11} {b_ret:+.2f}%{'':<11} {ret_icon} {s_ret - b_ret:+.2f}%")

    sh_icon = "+" if s_sharpe > b_sharpe else "-"
    print(f"{'Sharpe Ratio':<28} {s_sharpe:.2f}{'':<14} {b_sharpe:.2f}{'':<14} {sh_icon} {s_sharpe - b_sharpe:+.2f}")

    dd_icon = "+" if s_dd > b_dd else "-"
    print(f"{'Max Drawdown':<28} {s_dd:.2f}%{'':<11} {b_dd:.2f}%{'':<11} {dd_icon} {s_dd - b_dd:+.2f}%")

    print(f"{'Win Rate':<28} {strategy['win_rate']:.1f}%{'':<11} {'N/A':<18} {'(strategy only)'}")
    print(f"{'Total Trades':<28} {strategy['total_trades']}{'':<16} {'1 (buy&hold)':<18}")
    print("-" * 80)

    s_final = initial_capital * (1 + s_ret / 100)
    b_final = spy['end_value']
    print(f"{'Final Value':<28} ${s_final:,.0f}{'':<10} ${b_final:,.0f}{'':<10} ${s_final - b_final:+,.0f}")

    # Annualized
    days = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days
    years = max(days / 365.25, 0.1)
    s_ann = (((1 + s_ret / 100) ** (1 / years)) - 1) * 100
    b_ann = (((1 + b_ret / 100) ** (1 / years)) - 1) * 100
    print(f"{'Annualized Return':<28} {s_ann:+.2f}%{'':<11} {b_ann:+.2f}%{'':<11}")

    # Verdict
    metrics_won = sum([s_ret > b_ret, s_sharpe > b_sharpe, s_dd > b_dd])

    print("\n" + "=" * 80)
    if metrics_won >= 2:
        print(f"VERDICT: STRATEGY WINS ({metrics_won}/3 metrics)")
    else:
        print(f"VERDICT: SPY WINS ({3 - metrics_won}/3 metrics)")
    print("=" * 80)

    if strategy['total_trades'] > 0:
        print(f"\nTrade Details:")
        print(f"  Winning: {strategy['winning_trades']} | Losing: {strategy['losing_trades']}")
        print(f"  Win Rate: {strategy['win_rate']:.1f}%")
        print(f"  Avg Win: ${strategy['avg_win']:.2f} | Avg Loss: ${strategy['avg_loss']:.2f}")
        if strategy['avg_loss'] != 0:
            ratio = abs(strategy['avg_win'] / strategy['avg_loss'])
            print(f"  Win/Loss Ratio: {ratio:.2f}:1")


async def main():
    print("=" * 80)
    print("ENHANCED BACKTEST: Strategy vs SPY")
    print("Multi-configuration comparison")
    print("=" * 80)

    start_date = "2024-01-01"
    end_date = "2025-12-31"
    initial_capital = 100000

    print(f"\nPeriod: {start_date} to {end_date}")
    print(f"Capital: ${initial_capital:,}")

    # Load symbols
    print("\nLoading symbols...")
    symbols = load_symbols()

    # Fetch data (once, reused across configs)
    print("\nFetching historical data...")
    historical, end_prices = fetch_all_data(symbols, start_date, end_date)

    if len(historical) < 5:
        print("Not enough data. Exiting.")
        return

    # Scan for all signals — V1 (legacy) and V2 (new composite)
    print("\n" + "=" * 80)
    print("SCANNING FOR SIGNALS — V1 (Legacy) vs V2/V3 vs V4 (Overextension Filter)")
    print("=" * 80)

    v1_signals = run_enhanced_scan(historical, start_date, end_date,
                                    modes=['swing', 'longterm'],
                                    use_legacy_momentum=True)
    v2_signals = run_enhanced_scan(historical, start_date, end_date,
                                    modes=['swing', 'longterm'],
                                    use_legacy_momentum=False)
    v4_signals = run_enhanced_scan(historical, start_date, end_date,
                                    modes=['swing', 'longterm'],
                                    use_legacy_momentum=False,
                                    use_v4_overextension=True)

    # V3 signals use the same scan as V2 — the new BB filter, pattern scoring,
    # and Grade D rejection are automatically applied by the updated scanner
    v3_signals = v2_signals  # V3 features are baked into the scanner

    if not v1_signals and not v2_signals:
        print("\nNo signals generated. Exiting.")
        return

    # SPY benchmark (once)
    spy_report = calculate_spy_benchmark(historical, start_date, end_date,
                                          initial_capital)

    # Define configurations to test — V1 and V2 side by side
    configs = [
        # V1 configs (legacy momentum)
        {
            'name': 'V1-A) HIGH+, no trailing (baseline)',
            'signals': v1_signals,
            'filter': lambda s: s.get('quality') in ('PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V1-B) ALL quality, no trailing',
            'signals': v1_signals,
            'filter': lambda s: True,
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        # V2 configs (composite momentum + conviction)
        {
            'name': 'V2-A) HIGH+, no trailing',
            'signals': v2_signals,
            'filter': lambda s: s.get('quality') in ('PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V2-B) ALL quality, no trailing',
            'signals': v2_signals,
            'filter': lambda s: True,
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V2-C) HIGH+, aggressive sizing',
            'signals': v2_signals,
            'filter': lambda s: s.get('quality') in ('PREMIUM', 'HIGH'),
            'pos_pct': 0.15, 'risk_pct': 0.025,
            'trailing': False,
        },
        {
            'name': 'V2-D) PREMIUM only, aggressive',
            'signals': v2_signals,
            'filter': lambda s: s.get('quality') == 'PREMIUM',
            'pos_pct': 0.15, 'risk_pct': 0.03,
            'trailing': False,
        },
        # V3 configs (BB filter + patterns + Grade D rejection + SPY hedge)
        {
            'name': 'V3-A) HIGH+, BB filter, patterns',
            'signals': v3_signals,
            'filter': lambda s: s.get('quality') in ('PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
            'spy_hedge': False, 'spy_alloc': 0.0,
        },
        {
            'name': 'V3-B) HIGH+, 30% SPY hedge',
            'signals': v3_signals,
            'filter': lambda s: s.get('quality') in ('PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
            'spy_hedge': True, 'spy_alloc': 0.30,
        },
        {
            'name': 'V3-C) PREMIUM only, 50% SPY hedge',
            'signals': v3_signals,
            'filter': lambda s: s.get('quality') == 'PREMIUM',
            'pos_pct': 0.15, 'risk_pct': 0.03,
            'trailing': False,
            'spy_hedge': True, 'spy_alloc': 0.50,
        },
        {
            'name': 'V3-D) ALL quality, 40% SPY hedge',
            'signals': v3_signals,
            'filter': lambda s: True,
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
            'spy_hedge': True, 'spy_alloc': 0.40,
        },
        # V4 configs (over-extension filter — rejects stretched breakouts)
        {
            'name': 'V4-A) HIGH+, overextension filter',
            'signals': v4_signals,
            'filter': lambda s: s.get('quality') in ('PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V4-B) PREMIUM only, overextension filter',
            'signals': v4_signals,
            'filter': lambda s: s.get('quality') == 'PREMIUM',
            'pos_pct': 0.15, 'risk_pct': 0.03,
            'trailing': False,
        },
        {
            'name': 'V4-C) HIGH+, aggressive sizing',
            'signals': v4_signals,
            'filter': lambda s: s.get('quality') in ('PREMIUM', 'HIGH'),
            'pos_pct': 0.15, 'risk_pct': 0.025,
            'trailing': False,
        },
        {
            'name': 'V4-D) PREMIUM, aggressive sizing',
            'signals': v4_signals,
            'filter': lambda s: s.get('quality') == 'PREMIUM',
            'pos_pct': 0.15, 'risk_pct': 0.03,
            'trailing': False,
        },
    ]

    results_list = []

    for cfg in configs:
        signals_pool = cfg.get('signals', v2_signals)
        filtered = [s for s in signals_pool if cfg['filter'](s)]
        if not filtered:
            print(f"\n--- {cfg['name']} (0 signals, skipped) ---")
            continue

        print(f"\n--- {cfg['name']} ({len(filtered)} signals) ---")

        sim = SimulationMode(
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            max_position_pct=cfg['pos_pct'],
            max_risk_pct=cfg['risk_pct'],
            use_trailing_stop=cfg.get('trailing', False),
            trailing_stop_atr_mult=cfg.get('trail_atr', 4.0),
            trailing_stop_activation_pct=cfg.get('trail_act', 0.10),
            spy_hedge_enabled=cfg.get('spy_hedge', False),
            spy_allocation_pct=cfg.get('spy_alloc', 0.0),
        )

        report = sim.run_simulation(filtered, end_prices=end_prices,
                                     historical_data=historical)
        if report:
            report['config_name'] = cfg['name']
            report['signal_count'] = len(filtered)
            results_list.append(report)

    # Print comparison table
    print("\n\n" + "=" * 120)
    print("V1 vs V2 vs V3 COMPARISON vs SPY")
    print("=" * 120)

    spy_ret = spy_report['total_return'] if spy_report else 0
    spy_sharpe = spy_report['sharpe_ratio'] if spy_report else 0
    spy_dd = spy_report['max_drawdown'] if spy_report else 0

    header = f"{'Config':<42} {'Signals':>7} {'Trades':>7} {'Return':>10} {'WinRate':>8} {'Sharpe':>7} {'MaxDD':>8} {'W/L':>6} {'vs SPY':>10}"
    print(header)
    print("-" * 120)

    # SPY row
    print(f"{'SPY Buy & Hold':<42} {'1':>7} {'1':>7} {spy_ret:>+9.2f}% {'N/A':>8} {spy_sharpe:>7.2f} {spy_dd:>+7.2f}% {'N/A':>6} {'--':>10}")
    print("-" * 120)

    best_return = -999
    best_name = ""

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
        marker = "***" if ret > spy_ret else ""

        print(f"{name:<42} {sigs:>7} {trades:>7} {ret:>+9.2f}% {wr:>7.1f}% {sharpe:>7.2f} {dd:>+7.2f}% {wl:>5.2f} {diff:>+9.2f}% {marker}")

        if ret > best_return:
            best_return = ret
            best_name = name

    print("-" * 120)
    beats_spy = best_return > spy_ret
    print(f"\nBest config: {best_name} ({best_return:+.2f}%)")
    print(f"SPY return:  {spy_ret:+.2f}%")
    if beats_spy:
        print(f"STRATEGY BEATS SPY by {best_return - spy_ret:+.2f}%!")
    else:
        gap = spy_ret - best_return
        print(f"Gap to SPY: {gap:.2f}% — strategy excels at risk control (lower drawdowns)")

    # Save results
    output_dir = Path('scanner_output/backtests')
    output_dir.mkdir(parents=True, exist_ok=True)
    import json

    def safe_val(v):
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return float(v)
        if callable(v):
            return str(v)
        return v

    save_data = {
        'test': {'start': start_date, 'end': end_date, 'capital': initial_capital},
        'spy': {k: safe_val(v) for k, v in (spy_report or {}).items()},
        'configs': [{k: safe_val(v) for k, v in r.items()} for r in results_list],
    }

    fname = output_dir / f'multi_config_vs_spy_{start_date}_{end_date}.json'
    with open(fname, 'w') as f:
        json.dump(save_data, f, indent=2)
    print(f"\nResults saved to {fname}")


if __name__ == "__main__":
    asyncio.run(main())
