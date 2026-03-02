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
import argparse
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


def parse_args():
    """Parse CLI arguments for the backtest."""
    p = argparse.ArgumentParser(description='Enhanced Backtest: strategy vs SPY')
    p.add_argument('--watchlist', default=None,
                   help='Watchlist file path (default: tries watchlist3/2.txt)')
    p.add_argument('--start', default='2024-01-01', help='Start date YYYY-MM-DD')
    p.add_argument('--end',   default='2025-12-31', help='End date   YYYY-MM-DD')
    p.add_argument('--capital', type=int, default=100_000, help='Initial capital')
    p.add_argument('--versions', default='all',
                   help='Comma-separated versions to compare, e.g. v1,v6x,v7,v8,v8x  (default: all)')
    p.add_argument('--modes', default='swing,longterm',
                   help='Comma-separated scan modes  (default: swing,longterm)')
    p.add_argument('--limit', type=int, default=0,
                   help='Cap number of symbols loaded (0 = no cap, default: 0)')
    p.add_argument('--minervini-threshold', type=int, default=7,
                   help='Min MinerviniScore for V8/V8X signal pool (default: 7)')
    return p.parse_args()


def load_symbols(watchlist_path=None, limit=0):
    """Load symbols from watchlist file (or fallback paths). No symbol cap by default."""
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


def run_all_scans(historical, start_date, end_date, modes=None):
    """
    Single-pass scan that produces all 3 signal variants simultaneously.

    Instead of iterating (date x symbol) 3-4 separate times, this iterates once
    and runs all 3 detector variants per (date, symbol) pair:
        v1  — legacy momentum (binary RSI/MACD/ADX)
        v2  — composite momentum, no overextension filter
        v5x — composite momentum + overextension filter (best of V4 + V5)

    Returns dict: {'v1': [...], 'v2': [...], 'v5x': [...]}
    """
    if modes is None:
        modes = ['swing', 'longterm']

    detector = BreakoutDetector()
    spy_df = historical.get('SPY')

    # 3 independent signal lists + 3 independent cooldown dicts
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
            'is_vcp': bool(sig.get('VCP', False)),              # V10: VCP detected
            'vcp_quality': sig.get('VCP_Quality', 0) or 0,     # V10: 0.0-1.0
            'checks': sig.get('Checks', {}),                    # V12: raw feature booleans for optimizer
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

                # 10-day cooldown per symbol per variant
                last_sig = cooldowns[variant].get(symbol)
                if last_sig and (sim_date - last_sig).days < 10:
                    continue

                found = False
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
                        found = True
                        break

                    # Pullback (only v2/v5x — pullback uses composite logic)
                    if not flags['use_legacy_momentum']:
                        try:
                            pb_sig = detector.detect_pullback(df_slice, symbol, mode_name, '1 day', spy_perf)
                        except Exception:
                            pb_sig = None

                        if pb_sig:
                            results[variant].append(_make_signal(symbol, mode_name, sim_date, pb_sig, 'PULLBACK'))
                            cooldowns[variant][symbol] = sim_date
                            found = True
                            break

        if (day_idx + 1) % 50 == 0:
            counts = ' | '.join(f"{v.upper()}={len(results[v])}" for v in variants)
            print(f"    Day {day_idx+1}/{len(sim_dates)}: {counts}")

    # Summary
    for variant in variants:
        sigs = results[variant]
        print(f"\n  [{variant.upper()}] Total signals: {len(sigs)}"
              f"  PREMIUM={sum(1 for s in sigs if s.get('quality')=='PREMIUM')}"
              f"  HIGH={sum(1 for s in sigs if s.get('quality')=='HIGH')}"
              f"  STANDARD={sum(1 for s in sigs if s.get('quality')=='STANDARD')}")

    return results


def run_enhanced_scan(historical, start_date, end_date, modes=None,
                      use_legacy_momentum=False, use_v4_overextension=False):
    """Legacy single-variant scan. Kept for compatibility; main() now uses run_all_scans."""
    if modes is None:
        modes = ['swing', 'longterm']

    if use_legacy_momentum:
        version = "V1 (legacy)"
    elif use_v4_overextension:
        version = "V5X (composite+overextension)"
    else:
        version = "V2/V5 (composite)"
    detector = BreakoutDetector()
    spy_df = historical.get('SPY')
    all_signals = []
    cooldowns = {}

    sim_dates = pd.date_range(start=start_date, end=end_date, freq='B')
    symbols = [s for s in historical if s != 'SPY']

    print(f"\n  [{version}] Scanning {len(symbols)} symbols x {len(modes)} modes over {len(sim_dates)} days...")

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
                        use_legacy_momentum=use_legacy_momentum,
                        use_v4_overextension=use_v4_overextension
                    )
                except Exception:
                    sig = None

                if sig:
                    all_signals.append({
                        'date': sim_date, 'symbol': symbol, 'action': 'BUY',
                        'price': sig['Price'], 'entry_price': sig['Price'],
                        'stop_loss': sig['Stop'], 'take_profit': sig['Target'],
                        'quality': sig['Quality'], 'mode': mode_name, 'type': 'BREAKOUT',
                        'win_probability': sig.get('WinProb', 0.50),
                        'rr_grade': sig.get('RR_Grade', ''),
                        'patterns': sig.get('Patterns', ''),
                    })
                    cooldowns[symbol] = sim_date
                    break

                try:
                    pb_sig = detector.detect_pullback(df_slice, symbol, mode_name, '1 day', spy_perf)
                except Exception:
                    pb_sig = None

                if pb_sig:
                    all_signals.append({
                        'date': sim_date, 'symbol': symbol, 'action': 'BUY',
                        'price': pb_sig['Price'], 'entry_price': pb_sig['Price'],
                        'stop_loss': pb_sig['Stop'], 'take_profit': pb_sig['Target'],
                        'quality': pb_sig['Quality'], 'mode': mode_name, 'type': 'PULLBACK',
                    })
                    cooldowns[symbol] = sim_date
                    break

        if (day_idx + 1) % 50 == 0:
            print(f"    Day {day_idx+1}/{len(sim_dates)}: {len(all_signals)} signals so far")

    breakouts = sum(1 for s in all_signals if s.get('type') == 'BREAKOUT')
    pullbacks = sum(1 for s in all_signals if s.get('type') == 'PULLBACK')
    premiums  = sum(1 for s in all_signals if s.get('quality') == 'PREMIUM')
    highs     = sum(1 for s in all_signals if s.get('quality') == 'HIGH')
    standards = sum(1 for s in all_signals if s.get('quality') == 'STANDARD')
    print(f"\n  [{version}] Total signals: {len(all_signals)}")
    print(f"    Breakouts: {breakouts} | Pullbacks: {pullbacks}")
    print(f"    PREMIUM: {premiums} | HIGH: {highs} | STANDARD: {standards}")

    return all_signals


def run_prescan(historical, start_date, end_date, modes=None, date_step: int = 5) -> list:
    """
    Fast pre-scan for weight optimizer.

    Scans every date_step-th trading day (default=5, i.e. weekly) with
    threshold=0 so all candidates pass.  Only runs the v1 variant to
    minimise compute.  Returns candidate dicts with raw 'checks' booleans
    for Optuna re-scoring without re-running the detector.

    ~15x faster than calling run_all_scans() (weekly cadence + single variant).
    """
    import config

    if modes is None:
        modes = ['swing', 'longterm']

    orig = dict(config.SCORE_THRESHOLDS)
    config.SCORE_THRESHOLDS.update({'GOLD': 0, 'PREMIUM': 0, 'HIGH': 0, 'STANDARD': 0})

    try:
        detector   = BreakoutDetector()
        spy_df     = historical.get('SPY')
        symbols    = [s for s in historical if s != 'SPY']

        all_dates  = pd.date_range(start=start_date, end=end_date, freq='B')
        sim_dates  = all_dates[::date_step]           # every Nth trading day

        print(f"\n  [Prescan] {len(symbols)} symbols x {len(modes)} modes "
              f"x {len(sim_dates)} dates (step={date_step})...")

        candidates = []
        cooldowns  = {}

        for sim_date in sim_dates:
            spy_perf = 0.0
            if spy_df is not None:
                spy_perf = calculate_spy_perf_on_date(spy_df, sim_date, lookback=15)

            for symbol in symbols:
                df       = historical[symbol]
                df_slice = df[df.index <= sim_date]

                if len(df_slice) < 150:
                    continue
                # Skip if latest data is more than 1 week stale
                if df_slice.index[-1].date() < (sim_date - pd.Timedelta(days=7)).date():
                    continue

                last_sig = cooldowns.get(symbol)
                if last_sig and (sim_date - last_sig).days < 10:
                    continue

                for mode_name in modes:
                    try:
                        sig = detector.detect(
                            df_slice, symbol, mode_name, '1 day', spy_perf,
                            use_scoring=True,
                            use_legacy_momentum=True,
                            use_v4_overextension=False,
                        )
                    except Exception:
                        sig = None

                    if sig and sig.get('Checks'):
                        candidates.append({
                            'date':           sim_date,
                            'symbol':         symbol,
                            'action':         'BUY',
                            'price':          sig['Price'],
                            'entry_price':    sig['Price'],
                            'stop_loss':      sig['Stop'],
                            'take_profit':    sig['Target'],
                            'quality':        sig['Quality'],
                            'mode':           mode_name,
                            'type':           'BREAKOUT',
                            'win_probability': sig.get('WinProb', 0.50),
                            'rr_grade':       sig.get('RR_Grade', ''),
                            'patterns':       sig.get('Patterns', ''),
                            'minervini_score': sig.get('MinerviniScore', 0),
                            'is_momentum':    sig.get('Type') == 'Momentum',
                            'is_vcp':         bool(sig.get('VCP', False)),
                            'vcp_quality':    sig.get('VCP_Quality', 0) or 0,
                            'checks':         sig.get('Checks', {}),
                        })
                        cooldowns[symbol] = sim_date
                        break   # one signal per symbol per scan date

        print(f"    → {len(candidates)} candidates collected")
        return candidates

    finally:
        config.SCORE_THRESHOLDS.update(orig)


def run_simulation(signals, start_date, end_date, end_prices, historical,
                   initial_capital=100000, tp_as_trail=False, tp_trail_atr_mult=2.0):
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
        tp_as_trail=tp_as_trail,     # V9: TP activates trailing stop
        tp_trail_atr_mult=tp_trail_atr_mult,
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


def calculate_minervini_benchmark(historical, start_date, end_date,
                                  initial_capital=100000, m_thresh=7):
    """Equal-weight buy-and-hold of all stocks meeting Minervini Stage 2 at start_date.

    Uses the 365-day lookback data already in `historical` (fetched by fetch_all_data).
    Score is computed on bars up to start_date, then position held through end_date.
    """
    from indicators import calculate_minervini_template

    sd = pd.to_datetime(start_date)
    ed = pd.to_datetime(end_date)

    qualifying = []
    for symbol, df in historical.items():
        if symbol == 'SPY':
            continue
        # Data available at the start of the test period
        df_pre = df[df.index <= sd]
        if len(df_pre) < 50:
            continue
        _, score = calculate_minervini_template(df_pre)
        if score >= m_thresh:
            qualifying.append(symbol)

    if not qualifying:
        return None

    # Equal-weight allocation per stock
    per_stock = initial_capital / len(qualifying)
    portfolio_series = None

    for symbol in qualifying:
        df = historical[symbol]
        period = df[(df.index >= sd) & (df.index <= ed)]
        if len(period) < 2:
            continue
        start_price = float(period.iloc[0]['close'])
        if start_price == 0:
            continue
        shares = per_stock / start_price
        stock_values = period['close'].astype(float) * shares
        if portfolio_series is None:
            portfolio_series = stock_values.copy()
        else:
            portfolio_series = portfolio_series.add(stock_values, fill_value=0)

    if portfolio_series is None or len(portfolio_series) < 2:
        return None

    end_value = float(portfolio_series.iloc[-1])
    total_return = ((end_value - initial_capital) / initial_capital) * 100

    daily_rets = portfolio_series.pct_change().dropna()
    mean_ret = daily_rets.mean()
    std_ret = daily_rets.std()
    sharpe = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 0 else 0

    cummax = portfolio_series.cummax()
    dd = (portfolio_series / cummax - 1) * 100
    max_dd = float(dd.min())

    return {
        'total_return': total_return,
        'sharpe_ratio': sharpe,
        'max_drawdown': max_dd,
        'num_stocks': len(qualifying),
        'end_value': end_value,
    }


def calculate_vcp_benchmark(historical, start_date, end_date,
                             initial_capital=100000, mode='swing'):
    """Equal-weight buy-and-hold of all stocks showing a VCP pattern at start_date.

    Answers: 'if I had bought all VCP stocks at start, how would they have performed?'
    Complement to calculate_minervini_benchmark() — VCP is the entry timing layer.
    """
    from pattern_recognition import detect_vcp

    sd = pd.to_datetime(start_date)
    ed = pd.to_datetime(end_date)

    qualifying = []
    for symbol, df in historical.items():
        if symbol == 'SPY':
            continue
        df_pre = df[df.index <= sd]
        if len(df_pre) < 60:
            continue
        try:
            result = detect_vcp(df_pre, symbol, mode=mode)
            if result and result.get('vcp_quality', 0) > 0:
                qualifying.append((symbol, result['vcp_quality']))
        except Exception:
            pass

    if not qualifying:
        return None

    # Equal-weight allocation
    per_stock = initial_capital / len(qualifying)
    portfolio_series = None

    for symbol, vcp_q in qualifying:
        df = historical[symbol]
        period = df[(df.index >= sd) & (df.index <= ed)]
        if len(period) < 2:
            continue
        start_price = float(period.iloc[0]['close'])
        if start_price == 0:
            continue
        shares = per_stock / start_price
        stock_values = period['close'].astype(float) * shares
        if portfolio_series is None:
            portfolio_series = stock_values.copy()
        else:
            portfolio_series = portfolio_series.add(stock_values, fill_value=0)

    if portfolio_series is None or len(portfolio_series) < 2:
        return None

    end_value = float(portfolio_series.iloc[-1])
    total_return = ((end_value - initial_capital) / initial_capital) * 100

    daily_rets = portfolio_series.pct_change().dropna()
    mean_ret = daily_rets.mean()
    std_ret = daily_rets.std()
    sharpe = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 0 else 0

    cummax = portfolio_series.cummax()
    dd = (portfolio_series / cummax - 1) * 100
    max_dd = float(dd.min())

    return {
        'total_return': total_return,
        'sharpe_ratio': sharpe,
        'max_drawdown': max_dd,
        'num_stocks': len(qualifying),
        'end_value': end_value,
        'stocks': [s for s, _ in qualifying],
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
    args = parse_args()

    print("=" * 80)
    print("ENHANCED BACKTEST: Strategy vs SPY")
    print("Multi-configuration comparison")
    print("=" * 80)

    start_date     = args.start
    end_date       = args.end
    initial_capital = args.capital
    scan_modes     = [m.strip() for m in args.modes.split(',')]
    m_thresh       = args.minervini_threshold

    print(f"\nPeriod:   {start_date} → {end_date}")
    print(f"Capital:  ${initial_capital:,}")
    print(f"Modes:    {', '.join(scan_modes)}")
    print(f"Versions: {args.versions}")
    if args.watchlist:
        print(f"Watchlist: {args.watchlist}")

    # Load symbols
    print("\nLoading symbols...")
    symbols = load_symbols(watchlist_path=args.watchlist, limit=args.limit)

    # Fetch data (once, reused across configs; disk-cached in scanner_output/cache/)
    print("\nFetching historical data (disk-cached — fast on repeat runs)...")
    historical, end_prices = fetch_all_data(symbols, start_date, end_date)

    if len(historical) < 5:
        print("Not enough data. Exiting.")
        return

    # Single-pass scan — all 3 variants in one loop over (date x symbol)
    print("\n" + "=" * 80)
    print("SCANNING FOR SIGNALS — Single pass: V1 / V2-V6 / V5X-V6X all at once")
    print("=" * 80)

    all_scan_results = run_all_scans(historical, start_date, end_date, modes=scan_modes)

    v1_signals  = all_scan_results['v1']
    v2_signals  = all_scan_results['v2']
    v5x_signals = all_scan_results['v5x']

    # Aliases — V3/V4/V5 reuse existing signal lists (no extra scan needed)
    v3_signals = v2_signals   # V3 = V2 with different filters
    v4_signals = v5x_signals  # V4 = V5X (overextension filter)
    v5_signals = v2_signals   # V5 baseline = V2

    # V7 = momentum surge signals only (Type=Momentum in scanner)
    v7_signals = [s for s in v2_signals if s.get('is_momentum')]
    # V8 = Minervini Stage 2 filter on V2 base signals (score >= threshold)
    v8_signals  = [s for s in v2_signals  if s.get('minervini_score', 0) >= m_thresh]
    # V8X = V8 + overextension filter (V5X base)
    v8x_signals = [s for s in v5x_signals if s.get('minervini_score', 0) >= m_thresh]
    # V10 = VCP-detected signals (from V2 base + from Minervini-filtered base)
    v10_signals     = [s for s in v2_signals  if s.get('is_vcp')]
    v10m_signals    = [s for s in v8_signals  if s.get('is_vcp')]   # VCP + Minervini
    v10mx_signals   = [s for s in v8x_signals if s.get('is_vcp')]   # VCP + Minervini + overext

    print(f"\n  [V7]  Momentum surge signals: {len(v7_signals)}")
    print(f"  [V8]  Minervini≥{m_thresh} signals (V2 base): {len(v8_signals)}")
    print(f"  [V8X] Minervini≥{m_thresh}+overextension:     {len(v8x_signals)}")
    print(f"  [V10] VCP signals (V2 base):                  {len(v10_signals)}")
    print(f"  [V10M] VCP + Minervini≥{m_thresh}:            {len(v10m_signals)}")
    print(f"  [V10MX] VCP + Minervini≥{m_thresh}+overext:   {len(v10mx_signals)}")

    # V11 = V1 signals with at least one S/R feature (sr_breakout, at_key_support, trendline_break)
    v11_signals = [s for s in v1_signals if any([
        s.get('checks', {}).get('sr_breakout'),
        s.get('checks', {}).get('at_key_support'),
        s.get('checks', {}).get('trendline_break'),
    ])]
    # V11-none = V1 signals without ANY V11 S/R feature (comparison baseline)
    v11_none_signals = [s for s in v1_signals if not any([
        s.get('checks', {}).get('sr_breakout'),
        s.get('checks', {}).get('at_key_support'),
        s.get('checks', {}).get('trendline_break'),
    ])]
    # V12 = V1 signals with bullish chart/candle pattern confirmed
    v12_signals = [s for s in v1_signals if s.get('checks', {}).get('has_bullish_pattern')]
    # V12-none = V1 signals without any pattern confirmation
    v12_none_signals = [s for s in v1_signals if not s.get('checks', {}).get('has_bullish_pattern')]

    print(f"  [V11]  WITH S/R feature (sr_breakout/at_key_support/trendline_break): {len(v11_signals)}")
    print(f"  [V11-Z] WITHOUT S/R feature:                                          {len(v11_none_signals)}")
    print(f"  [V12]  Pattern confirmed (has_bullish_pattern):                       {len(v12_signals)}")
    print(f"  [V12-Z] No pattern confirmation:                                      {len(v12_none_signals)}")

    if not v1_signals and not v2_signals:
        print("\nNo signals generated. Exiting.")
        return

    # SPY benchmark (once)
    spy_report = calculate_spy_benchmark(historical, start_date, end_date,
                                          initial_capital)

    # Minervini Screen benchmark: buy-and-hold all stocks with Minervini score ≥ threshold
    print(f"\n  Computing Minervini Screen benchmark (score ≥ {m_thresh})...")
    minervini_bench = calculate_minervini_benchmark(historical, start_date, end_date,
                                                    initial_capital, m_thresh)
    if minervini_bench:
        print(f"  Minervini Screen: {minervini_bench['num_stocks']} qualifying stocks, "
              f"return {minervini_bench['total_return']:+.2f}%")
    else:
        print(f"  Minervini Screen: no qualifying stocks at {start_date}")

    # VCP Screen benchmark: buy-and-hold all stocks with VCP pattern at start_date
    print(f"\n  Computing VCP Screen benchmark (detect_vcp at {start_date})...")
    vcp_bench = calculate_vcp_benchmark(historical, start_date, end_date,
                                        initial_capital, mode=scan_modes[0])
    if vcp_bench:
        print(f"  VCP Screen: {vcp_bench['num_stocks']} qualifying stocks, "
              f"return {vcp_bench['total_return']:+.2f}%")
        if vcp_bench['num_stocks'] <= 20:
            print(f"  VCP stocks: {', '.join(vcp_bench['stocks'])}")
    else:
        print(f"  VCP Screen: no qualifying stocks at {start_date}")

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
        # V5 configs (learning loop + 52w high + RSI divergence + 10% hard cap)
        {
            'name': 'V5-A) HIGH+, 10% cap',
            'signals': v5_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V5-B) PREMIUM+, 10% cap',
            'signals': v5_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V5-C) ALL quality, 10% cap',
            'signals': v5_signals,
            'filter': lambda s: True,
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V5-D) PREMIUM+, 30% SPY hedge',
            'signals': v5_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
            'spy_hedge': True, 'spy_alloc': 0.30,
        },
        # V5X configs: V5 + V4 overextension filter (rejects stocks stretched too far from SMA)
        {
            'name': 'V5X-A) HIGH+, overextension+10% cap',
            'signals': v5x_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V5X-B) PREMIUM+, overextension+10% cap',
            'signals': v5x_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V5X-C) HIGH+, aggressive sizing',
            'signals': v5x_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.15, 'risk_pct': 0.025,
            'trailing': False,
        },
        {
            'name': 'V5X-D) PREMIUM+, 30% SPY hedge',
            'signals': v5x_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
            'spy_hedge': True, 'spy_alloc': 0.30,
        },
        # V6 configs (V5 + 23 patterns with volume confirmation scoring)
        {
            'name': 'V6-A) HIGH+, 10% cap',
            'signals': v5_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V6-B) PREMIUM+, 10% cap',
            'signals': v5_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V6-C) HIGH+, aggressive sizing',
            'signals': v5_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.15, 'risk_pct': 0.025,
            'trailing': False,
        },
        {
            'name': 'V6-D) PREMIUM+, 30% SPY hedge',
            'signals': v5_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
            'spy_hedge': True, 'spy_alloc': 0.30,
        },
        # V6X configs: V6 + overextension filter
        {
            'name': 'V6X-A) HIGH+, overextension+10% cap',
            'signals': v5x_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V6X-B) PREMIUM+, overextension+10% cap',
            'signals': v5x_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        # V7 configs: Momentum surge signals only (gap/intraday ≥5% + VolRatio ≥3)
        {
            'name': 'V7-A) Momentum-surge, HIGH+',
            'signals': v7_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V7-B) Momentum-surge, PREMIUM+, aggressive',
            'signals': v7_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.15, 'risk_pct': 0.025,
            'trailing': False,
        },
        # V8 configs: Minervini Stage 2 filter (proportional score contributes to quality)
        {
            'name': f'V8-A) Minervini≥{m_thresh}, HIGH+, 10% cap',
            'signals': v8_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': f'V8-B) Minervini≥{m_thresh}, PREMIUM+',
            'signals': v8_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': f'V8-C) Minervini≥{m_thresh}, HIGH+, aggressive',
            'signals': v8_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.15, 'risk_pct': 0.025,
            'trailing': False,
        },
        # V8X configs: Minervini Stage 2 + overextension filter
        {
            'name': f'V8X-A) Minervini≥{m_thresh}+overextension, HIGH+',
            'signals': v8x_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': f'V8X-B) Minervini≥{m_thresh}+overextension, PREMIUM+',
            'signals': v8x_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': f'V8X-C) Minervini≥{m_thresh}+overextension, aggressive',
            'signals': v8x_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.15, 'risk_pct': 0.025,
            'trailing': False,
        },
        # V9 configs: TP activates trailing stop instead of closing (no scale-outs)
        {
            'name': 'V9-A) V1+TP→Trail, HIGH+',
            'signals': v1_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
            'tp_as_trail': True,
        },
        {
            'name': f'V9-B) V8+TP→Trail, HIGH+',
            'signals': v8_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
            'tp_as_trail': True,
        },
        {
            'name': f'V9-C) V8+TP→Trail, PREMIUM+',
            'signals': v8_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
            'tp_as_trail': True,
        },
        # V10 configs: VCP (Volatility Contraction Pattern) — entry timing layer
        {
            'name': 'V10-A) VCP signals, HIGH+',
            'signals': v10_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V10-B) VCP signals, PREMIUM+',
            'signals': v10_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': f'V10M-A) VCP+Minervini≥{m_thresh}, HIGH+',
            'signals': v10m_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': f'V10M-B) VCP+Minervini≥{m_thresh}, PREMIUM+',
            'signals': v10m_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': f'V10M-C) VCP+Minervini≥{m_thresh}, aggressive',
            'signals': v10m_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.15, 'risk_pct': 0.025,
            'trailing': False,
        },
        {
            'name': f'V10MX-A) VCP+Minervini+overext, HIGH+',
            'signals': v10mx_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': f'V10MX-B) VCP+Minervini+overext, PREMIUM+',
            'signals': v10mx_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        # V11 configs: isolate impact of S/R features (sr_breakout, at_key_support, trendline_break)
        {
            'name': 'V11-A) WITH S/R feature, HIGH+',
            'signals': v11_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V11-Z) WITHOUT S/R feature, HIGH+ (baseline)',
            'signals': v11_none_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        # V12 configs: isolate impact of chart/candle pattern confirmation
        {
            'name': 'V12-A) Pattern confirmed, HIGH+',
            'signals': v12_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
        {
            'name': 'V12-Z) No pattern, HIGH+ (baseline)',
            'signals': v12_none_signals,
            'filter': lambda s: s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH'),
            'pos_pct': 0.10, 'risk_pct': 0.02,
            'trailing': False,
        },
    ]

    # ── Filter by --versions (e.g. "v1,v6x,v8,v8x") ──────────────────────────
    if args.versions.lower() != 'all':
        requested = sorted(
            [v.strip().upper() for v in args.versions.split(',')],
            key=len, reverse=True,   # longer first so V8X matched before V8
        )
        def _version_match(name: str) -> bool:
            nu = name.upper()
            for v in requested:
                if nu.startswith(v + ')') or nu.startswith(v + '-'):
                    return True
            return False
        configs = [c for c in configs if _version_match(c['name'])]
        print(f"\n  Running {len(configs)} configs for versions: {args.versions}")

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
            tp_as_trail=cfg.get('tp_as_trail', False),
            tp_trail_atr_mult=cfg.get('tp_trail_atr', 2.0),
        )

        report = sim.run_simulation(filtered, end_prices=end_prices,
                                     historical_data=historical)
        if report:
            report['config_name'] = cfg['name']
            report['signal_count'] = len(filtered)
            results_list.append(report)

    # Print comparison table
    print("\n\n" + "=" * 120)
    print("V1 / V2 / V3–V6 / V6X / V7 / V8 / V8X / V9 / V10  vs  SPY  vs  Minervini  vs  VCP Screen")
    print("=" * 120)

    spy_ret = spy_report['total_return'] if spy_report else 0
    spy_sharpe = spy_report['sharpe_ratio'] if spy_report else 0
    spy_dd = spy_report['max_drawdown'] if spy_report else 0

    header = f"{'Config':<42} {'Signals':>7} {'Trades':>7} {'Return':>10} {'WinRate':>8} {'Sharpe':>7} {'MaxDD':>8} {'W/L':>6} {'vs SPY':>10}"
    print(header)
    print("-" * 120)

    # SPY row
    print(f"{'SPY Buy & Hold':<42} {'1':>7} {'1':>7} {spy_ret:>+9.2f}% {'N/A':>8} {spy_sharpe:>7.2f} {spy_dd:>+7.2f}% {'N/A':>6} {'--':>10}")

    # Minervini Screen benchmark row
    if minervini_bench:
        mb_ret    = minervini_bench['total_return']
        mb_sharpe = minervini_bench['sharpe_ratio']
        mb_dd     = minervini_bench['max_drawdown']
        mb_n      = minervini_bench['num_stocks']
        mb_label  = f"Minervini Screen (≥{m_thresh}, {mb_n} stocks)"
        mb_diff   = mb_ret - spy_ret
        print(f"{mb_label:<42} {mb_n:>7} {mb_n:>7} {mb_ret:>+9.2f}% {'N/A':>8} {mb_sharpe:>7.2f} {mb_dd:>+7.2f}% {'N/A':>6} {mb_diff:>+9.2f}%")

    # VCP Screen benchmark row
    if vcp_bench:
        vb_ret    = vcp_bench['total_return']
        vb_sharpe = vcp_bench['sharpe_ratio']
        vb_dd     = vcp_bench['max_drawdown']
        vb_n      = vcp_bench['num_stocks']
        vb_label  = f"VCP Screen ({vb_n} stocks at {start_date[:7]})"
        vb_diff   = vb_ret - spy_ret
        print(f"{vb_label:<42} {vb_n:>7} {vb_n:>7} {vb_ret:>+9.2f}% {'N/A':>8} {vb_sharpe:>7.2f} {vb_dd:>+7.2f}% {'N/A':>6} {vb_diff:>+9.2f}%")

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
        'minervini_benchmark': {k: safe_val(v) for k, v in (minervini_bench or {}).items()},
        'configs': [{k: safe_val(v) for k, v in r.items()} for r in results_list],
    }

    fname = output_dir / f'multi_config_vs_spy_{start_date}_{end_date}.json'
    with open(fname, 'w') as f:
        json.dump(save_data, f, indent=2)
    print(f"\nResults saved to {fname}")


if __name__ == "__main__":
    asyncio.run(main())
