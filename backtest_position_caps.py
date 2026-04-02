#!/usr/bin/env python3
"""
Backtest Position Sizing Caps: 5% vs 10% vs 15% vs 20%
------------------------------------------------------
Simple comparison of max_single_position_pct impact on returns.
Uses synthetic breakout signals to focus on position sizing logic.
"""
import asyncio
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import logging

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from mock_trader import MockTrader
from yfinance_adapter import YFinanceAdapter
import config

logging.basicConfig(level=logging.WARNING)


def load_symbols(filepath='input/backtest_200.txt', limit=100):
    """Load symbols from backtest file."""
    try:
        with open(filepath, 'r') as f:
            symbols = [s.strip() for s in f.readlines() if s.strip() and not s.startswith('#')]
        print(f"  Loaded {len(symbols)} symbols")
        return symbols[:limit]
    except FileNotFoundError:
        return ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'TSLA', 'META', 'NFLX',
                'AMD', 'INTC', 'JPM', 'BAC', 'WMT', 'HD', 'DIS', 'V', 'MA', 'PYPL',
                'COST', 'PEP', 'KO']


def fetch_all_data(symbols, start_date, end_date):
    """Fetch historical data."""
    yf_adapter = YFinanceAdapter()
    historical = {}

    fetch_start = (pd.to_datetime(start_date) - timedelta(days=365)).strftime('%Y-%m-%d')

    spy_df = yf_adapter.get_historical_data('SPY', '1 day',
                                             start_date=fetch_start,
                                             end_date=end_date)
    if spy_df is not None and len(spy_df) > 0:
        historical['SPY'] = spy_df

    for i, symbol in enumerate(symbols):
        try:
            df = yf_adapter.get_historical_data(symbol, '1 day',
                                                 start_date=fetch_start,
                                                 end_date=end_date)
            if df is not None and len(df) > 50:
                historical[symbol] = df
        except Exception:
            pass

        if (i + 1) % 25 == 0:
            print(f"  Fetched {i+1}/{len(symbols)} symbols...")

    print(f"  Ready: {len(historical)-1} stocks + SPY")
    return historical


def generate_signals(df, symbol, current_date):
    """Generate synthetic breakout signals based on simple price patterns."""
    # Trim to current date
    df_up = df[df.index <= current_date]

    if len(df_up) < 20:
        return None

    close = df_up['close']
    high = df_up['high']
    low = df_up['low']
    volume = df_up['volume']

    # Recent price action
    curr_price = close.iloc[-1]
    prev_close = close.iloc[-2]

    # Simple breakout logic: break above 20-day high with volume
    high_20 = high.iloc[-20:].max()
    low_20 = low.iloc[-20:].min()
    avg_vol = volume.iloc[-20:].mean()
    curr_vol = volume.iloc[-1]

    # Signal: price breaks above 20-day high AND volume > 1.5x average
    # AND gap or strong move (>0.5% from prev close)
    price_break = curr_price > high_20 * 1.0001  # Small tolerance for float precision
    vol_confirm = curr_vol > avg_vol * 1.5
    move_confirm = (curr_price - prev_close) / prev_close > 0.003  # 0.3% move

    if not (price_break and vol_confirm and move_confirm):
        return None

    # Calculate levels
    stop_loss = low_20 * 0.98  # 2% below 20-day low
    risk = curr_price - stop_loss
    target = curr_price + (risk * 2)  # 2:1 RR

    # Determine quality (higher vol = better signal)
    vol_ratio = curr_vol / avg_vol
    if vol_ratio > 3.0:
        quality = 'PREMIUM'
    elif vol_ratio > 2.0:
        quality = 'HIGH'
    else:
        quality = 'STANDARD'

    return {
        'signal': 'BUY',
        'quality': quality,
        'price': curr_price,
        'stop_loss': stop_loss,
        'take_profit': target,
        'type': 'SYNTHETIC_BREAKOUT',
    }


def run_backtest_with_cap(symbols, historical, start_date, end_date, position_cap_pct, cap_name):
    """Run backtest with specific position cap."""
    print(f"\n{'='*80}")
    print(f"Testing: max_single_position_pct = {cap_name} ({position_cap_pct:.0%})")
    print(f"{'='*80}")

    # Override config
    original_cap = config.ATR_SIZING['max_single_position_pct']
    config.ATR_SIZING['max_single_position_pct'] = position_cap_pct

    try:
        trader = MockTrader(initial_capital=100_000, max_position_pct=0.10, max_risk_pct=0.01)
        trader.historical_data = historical

        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)

        current_date = start_dt
        signal_count = 0
        trade_count = 0
        cooldown_dict = {}

        while current_date <= end_dt:
            if current_date.weekday() >= 5:  # Skip weekends
                current_date += timedelta(days=1)
                continue

            # Clean cooldowns
            expired = [s for s, dt in cooldown_dict.items() if current_date > dt]
            for s in expired:
                del cooldown_dict[s]

            # Scan for signals
            for symbol in symbols:
                if symbol == 'SPY' or symbol in cooldown_dict:
                    continue
                if symbol not in historical:
                    continue

                df = historical[symbol]
                result = generate_signals(df, symbol, current_date)

                if result:
                    signal_count += 1
                    current_price = result['price']
                    stop_loss = result['stop_loss']
                    take_profit = result['take_profit']
                    quality = result['quality']

                    shares = trader.calculate_position_size(
                        price=current_price,
                        stop_loss=stop_loss,
                        quality=quality,
                        symbol=symbol,
                        as_of_date=current_date
                    )

                    if shares > 0:
                        trader.enter_trade(
                            symbol=symbol,
                            action='BUY',
                            quantity=shares,
                            price=current_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            signal_type=quality
                        )
                        cooldown_dict[symbol] = current_date + timedelta(days=10)
                        trade_count += 1

            # Process exits
            for trade_id, trade in list(trader.open_positions.items()):
                symbol = trade.symbol
                if symbol not in historical:
                    continue

                df = historical[symbol]
                df_up = df[df.index <= current_date]
                if len(df_up) == 0:
                    continue

                current_price = float(df_up['close'].iloc[-1])

                if current_price <= trade.stop_loss:
                    trader.exit_trade(symbol, trade.stop_loss, 'Stop Loss')
                elif current_price >= trade.take_profit:
                    trader.exit_trade(symbol, trade.take_profit, 'Take Profit')
                elif (current_date - trade.entry_time).days >= 21:
                    trader.exit_trade(symbol, current_price, 'Hold Expire')

            current_date += timedelta(days=1)

        # Close remaining
        for trade_id, trade in list(trader.open_positions.items()):
            symbol = trade.symbol
            if symbol in historical:
                final_price = float(historical[symbol]['close'].iloc[-1])
                trader.exit_trade(symbol, final_price, 'EOD')

        stats = trader.get_stats()

        print(f"\n  Signals:    {signal_count:>5} | Trades: {trade_count:>4} | Return: {stats['total_return']:>7.2f}%")
        print(f"  P&L:        ${stats['total_pnl']:>+10,.0f} | Win Rate: {stats['win_rate']:>5.1f}%")
        print(f"  Final:      ${stats.get('current_capital', trader.capital):>10,.0f}")

        return {
            'cap_pct': position_cap_pct,
            'cap_name': cap_name,
            'signals': signal_count,
            'trades': trade_count,
            'return': stats['total_return'],
            'pnl': stats['total_pnl'],
            'win_rate': stats['win_rate'],
            'final_capital': stats.get('current_capital', trader.capital),
        }

    finally:
        config.ATR_SIZING['max_single_position_pct'] = original_cap


def main():
    print("\n" + "="*80)
    print("POSITION SIZING CAP COMPARISON (Synthetic Signals)")
    print("="*80)

    symbols = load_symbols(limit=80)
    if not symbols:
        print("ERROR: No symbols")
        return

    print(f"\nFetching data (2024-2025)...")
    historical = fetch_all_data(symbols, '2024-01-01', '2025-12-31')

    if not historical or 'SPY' not in historical:
        print("ERROR: Data fetch failed")
        return

    caps_to_test = [
        (0.05, '5%'),
        (0.10, '10%'),
        (0.15, '15%'),
        (0.20, '20%'),
    ]

    results_list = []
    for cap_pct, cap_name in caps_to_test:
        result = run_backtest_with_cap(
            symbols, historical, '2024-01-01', '2025-12-31',
            cap_pct, cap_name
        )
        results_list.append(result)

    # Summary table
    print("\n" + "="*80)
    print("RESULTS SUMMARY (2024-2025, 80 symbols)")
    print("="*80 + "\n")

    df_results = pd.DataFrame(results_list)
    df_display = df_results[['cap_name', 'signals', 'trades', 'return', 'pnl', 'win_rate']]
    df_display.columns = ['Cap', 'Signals', 'Trades', 'Return %', 'Total P&L $', 'Win %']

    print(df_display.to_string(index=False))

    best_idx = df_results['return'].idxmax()
    best = df_results.iloc[best_idx]

    print(f"\n{'BEST PERFORMER:'} {best['cap_name']} → +{best['return']:.2f}%")
    print(f"  ({int(best['trades'])} trades, ${best['pnl']:+,.0f} P&L, {best['win_rate']:.1f}% WR)")

    # Comparison vs 5%
    baseline_return = df_results.iloc[0]['return']
    print(f"\nComparison vs 5% baseline:")
    for idx, row in df_results.iterrows():
        diff = row['return'] - baseline_return
        pct_gain = (diff / abs(baseline_return)) * 100 if baseline_return != 0 else 0
        print(f"  {row['cap_name']:>3}: {row['return']:>7.2f}% ({diff:>+6.2f}%, {pct_gain:>+6.1f}% relative)")

    # Save
    output_file = 'backtests/backtest_position_caps.txt'
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, 'w') as f:
        f.write("="*80 + "\n")
        f.write("POSITION SIZING CAP COMPARISON\n")
        f.write("Method: Synthetic breakout signals (price break above 20-day high + volume confirmation)\n")
        f.write("Period: 2024-2025 | Symbols: 80 | Initial Capital: $100,000\n")
        f.write("="*80 + "\n\n")
        f.write(df_display.to_string(index=False))
        f.write(f"\n\nBEST PERFORMER: {best['cap_name']} → +{best['return']:.2f}%\n")
        f.write(f"Analysis: Comparison vs 5% baseline:\n")
        for idx, row in df_results.iterrows():
            diff = row['return'] - baseline_return
            pct_gain = (diff / abs(baseline_return)) * 100 if baseline_return != 0 else 0
            f.write(f"  {row['cap_name']:>3}: {row['return']:>7.2f}% ({diff:>+6.2f}%)\n")

    print(f"\nResults saved to {output_file}")


if __name__ == '__main__':
    main()
