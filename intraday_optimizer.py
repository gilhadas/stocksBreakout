#!/usr/bin/env python3
"""
Intraday Breakout Optimizer
Tests different parameter combinations to find optimal settings for intraday trading.
Focuses on 15-minute timeframe and tests multiple approaches for identifying true breakouts.
"""
import asyncio
import pandas as pd
import itertools
import logging
from pathlib import Path
from datetime import datetime, timedelta
import json

# Python 3.14 compatibility
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from config import PORTFOLIO, MODES
from mock_trader import SimulationMode
from scanner import BreakoutDetector
from yfinance_adapter import YFinanceAdapter

logging.basicConfig(level=logging.WARNING, format='%(message)s')
logger = logging.getLogger(__name__)


async def test_intraday_config(symbols, start_date, end_date, config_params):
    """
    Test a specific configuration for intraday trading

    Args:
        symbols: List of symbols to test
        start_date: Start date for simulation (YYYY-MM-DD)
        end_date: End date for simulation (YYYY-MM-DD)
        config_params: Dict of parameters to test

    Returns:
        Dict with performance metrics
    """
    detector = BreakoutDetector()
    yf_adapter = YFinanceAdapter()

    all_signals = []
    historical_data = {}
    end_prices = {}

    # Use 15-minute bars for intraday
    timeframe = '15 mins'

    for symbol in symbols:
        try:
            # Fetch intraday data (15-min bars)
            df = yf_adapter.get_historical_data(
                symbol, timeframe,
                start_date=start_date,
                end_date=end_date
            )

            if df is None or len(df) == 0:
                continue

            historical_data[symbol] = df
            end_prices[symbol] = float(df.iloc[-1]['close'])

            # Scan each day's data
            dates = df.index.date
            unique_dates = sorted(set(dates))

            for trade_date in unique_dates:
                # Get data up to this date for scanning
                df_up_to = df[df.index.date <= trade_date]

                if len(df_up_to) < 50:
                    continue

                # Scan at multiple times during the day to find breakouts
                day_bars = df_up_to[df_up_to.index.date == trade_date]

                if len(day_bars) == 0:
                    continue

                # Check for breakout signals during the day
                # We'll scan at 10:00, 11:00, 12:00, 13:00, 14:00, 15:00
                for bar_idx in range(len(day_bars)):
                    current_time = day_bars.index[bar_idx]
                    hour = current_time.hour

                    # Only scan during market hours (10:00 AM - 3:00 PM)
                    if hour < 10 or hour > 15:
                        continue

                    # Get data slice up to this bar
                    df_slice = df[df.index <= current_time]

                    if len(df_slice) < 50:
                        continue

                    # Detect breakout with custom config
                    sig = detector.detect(
                        df_slice, symbol, 'daytrade', timeframe, spy_perf=0.0,
                        vol_thresh=config_params.get('vol_thresh'),
                        atr_mult=config_params.get('atr_mult'),
                        lookback=config_params.get('lookback')
                    )

                    if sig:
                        # Record signal
                        all_signals.append({
                            'date': current_time,
                            'symbol': symbol,
                            'action': 'BUY',
                            'price': sig['Price'],
                            'entry_price': sig['Price'],
                            'stop_loss': sig['Stop'],
                            'take_profit': sig['Target']
                        })

                        # Only take one signal per day per symbol
                        break

        except Exception as e:
            logger.debug(f"Error processing {symbol}: {e}")
            pass

    if len(all_signals) == 0:
        return None

    # Run simulation
    sim = SimulationMode(
        start_date=start_date,
        end_date=end_date,
        initial_capital=100000,
        max_position_pct=0.10,  # 10% per position for intraday
        max_risk_pct=0.01,      # 1% risk per trade
        use_trailing_stop=True,
        trailing_stop_atr_mult=2.5,  # Tighter trailing stop for intraday
        trailing_stop_activation_pct=0.05  # Activate after 5% profit
    )

    report = sim.run_simulation(all_signals, end_prices=end_prices, historical_data=historical_data)

    return {
        'signals': len(all_signals),
        'return': report['total_return'],
        'win_rate': report['win_rate'],
        'sharpe': report['sharpe_ratio'],
        'drawdown': report['max_drawdown'],
        'trades': report['total_trades'],
        'config': config_params
    }


async def run_comprehensive_analysis(watchlist_path, start_date, end_date):
    """
    Run comprehensive analysis testing different approaches for intraday breakouts
    """
    print("🔬 INTRADAY BREAKOUT OPTIMIZER")
    print("=" * 80)
    print(f"📅 Period: {start_date} to {end_date}")
    print(f"📊 Timeframe: 15-minute bars")
    print(f"📋 Watchlist: {Path(watchlist_path).name}")
    print()

    # Load symbols (use a subset for faster testing)
    with open(watchlist_path, 'r') as f:
        content = f.read()

    raw_symbols = [s.strip() for s in content.replace('\n', ',').split(',') if s.strip()]
    symbols = []
    for s in raw_symbols:
        if s.startswith('#'):
            continue
        if ':' in s:
            s = s.split(':')[1]
        symbols.append(s)

    # Use unique symbols, limit to 50 for faster testing
    symbols = sorted(list(set(symbols)))[:50]

    print(f"🎯 Testing {len(symbols)} symbols")
    print(f"   Sample: {', '.join(symbols[:10])}...")
    print()

    # Define parameter grid for different approaches
    print("📊 TESTING MULTIPLE APPROACHES:")
    print()

    approaches = {
        'Conservative': {
            'vol_thresh': [1.5, 1.7, 2.0],
            'atr_mult': [0.3, 0.4, 0.5],
            'lookback': [10, 15, 20]
        },
        'Moderate': {
            'vol_thresh': [1.2, 1.3, 1.5],
            'atr_mult': [0.2, 0.3, 0.4],
            'lookback': [8, 10, 12]
        },
        'Aggressive': {
            'vol_thresh': [1.0, 1.1, 1.2],
            'atr_mult': [0.1, 0.2, 0.3],
            'lookback': [5, 8, 10]
        }
    }

    all_results = []

    for approach_name, param_grid in approaches.items():
        print(f"\n{'='*80}")
        print(f"🧪 TESTING: {approach_name.upper()} APPROACH")
        print(f"{'='*80}")

        # Generate combinations for this approach
        keys, values = zip(*param_grid.items())
        combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]

        print(f"   Testing {len(combinations)} configurations...")
        print()

        for i, params in enumerate(combinations):
            print(f"   [{i+1}/{len(combinations)}] Vol={params['vol_thresh']}, "
                  f"ATR={params['atr_mult']}, Look={params['lookback']}...", end=' ')

            result = await test_intraday_config(symbols, start_date, end_date, params)

            if result:
                result['approach'] = approach_name
                all_results.append(result)
                print(f"✓ Sigs={result['signals']}, Ret={result['return']:+.2f}%, "
                      f"WR={result['win_rate']:.1f}%, Sharpe={result['sharpe']:.2f}")
            else:
                print("✗ No signals")

    if not all_results:
        print("\n❌ No valid results found")
        return

    # Analyze results
    print("\n" + "=" * 80)
    print("📊 COMPREHENSIVE ANALYSIS RESULTS")
    print("=" * 80)

    # Sort by Sharpe Ratio
    sorted_results = sorted(all_results, key=lambda x: x['sharpe'], reverse=True)

    print(f"\n🏆 TOP 10 CONFIGURATIONS (by Sharpe Ratio):")
    print("-" * 80)
    print(f"{'Approach':<13} {'Vol':<5} {'ATR':<5} {'Look':<5} {'Sigs':<6} "
          f"{'Return':<9} {'Win%':<7} {'Sharpe':<7} {'DD%':<7}")
    print("-" * 80)

    for res in sorted_results[:10]:
        cfg = res['config']
        print(f"{res['approach']:<13} {cfg['vol_thresh']:<5.1f} {cfg['atr_mult']:<5.1f} "
              f"{cfg['lookback']:<5} {res['signals']:<6} {res['return']:+7.2f}% "
              f"{res['win_rate']:5.1f}%  {res['sharpe']:5.2f}  {res['drawdown']:6.2f}%")

    # Best configuration overall
    best = sorted_results[0]
    print("\n" + "=" * 80)
    print("🎯 OPTIMAL CONFIGURATION (Best Risk-Adjusted Returns):")
    print("=" * 80)
    print(f"  Approach:          {best['approach']}")
    print(f"  Volume Threshold:  {best['config']['vol_thresh']} (current: 1.1)")
    print(f"  ATR Multiplier:    {best['config']['atr_mult']} (current: 0.2)")
    print(f"  Lookback Period:   {best['config']['lookback']} (current: 10)")
    print()
    print(f"  Expected Return:   {best['return']:+.2f}%")
    print(f"  Win Rate:          {best['win_rate']:.1f}%")
    print(f"  Sharpe Ratio:      {best['sharpe']:.2f}")
    print(f"  Max Drawdown:      {best['drawdown']:.2f}%")
    print(f"  Total Signals:     {best['signals']}")
    print(f"  Total Trades:      {best['trades']}")
    print()

    # Best by highest return
    best_return = sorted(all_results, key=lambda x: x['return'], reverse=True)[0]
    print("💰 HIGHEST RETURN CONFIGURATION:")
    print("-" * 80)
    print(f"  Approach:          {best_return['approach']}")
    print(f"  Parameters:        Vol={best_return['config']['vol_thresh']}, "
          f"ATR={best_return['config']['atr_mult']}, Look={best_return['config']['lookback']}")
    print(f"  Return:            {best_return['return']:+.2f}%")
    print(f"  Win Rate:          {best_return['win_rate']:.1f}%")
    print(f"  Sharpe Ratio:      {best_return['sharpe']:.2f}")
    print()

    # Best win rate
    best_winrate = sorted(all_results, key=lambda x: x['win_rate'], reverse=True)[0]
    print("🎯 HIGHEST WIN RATE CONFIGURATION:")
    print("-" * 80)
    print(f"  Approach:          {best_winrate['approach']}")
    print(f"  Parameters:        Vol={best_winrate['config']['vol_thresh']}, "
          f"ATR={best_winrate['config']['atr_mult']}, Look={best_winrate['config']['lookback']}")
    print(f"  Win Rate:          {best_winrate['win_rate']:.1f}%")
    print(f"  Return:            {best_winrate['return']:+.2f}%")
    print(f"  Sharpe Ratio:      {best_winrate['sharpe']:.2f}")
    print()

    # Compare approaches
    print("=" * 80)
    print("📊 APPROACH COMPARISON:")
    print("=" * 80)

    for approach_name in approaches.keys():
        approach_results = [r for r in all_results if r['approach'] == approach_name]
        if approach_results:
            avg_return = sum(r['return'] for r in approach_results) / len(approach_results)
            avg_winrate = sum(r['win_rate'] for r in approach_results) / len(approach_results)
            avg_sharpe = sum(r['sharpe'] for r in approach_results) / len(approach_results)
            avg_signals = sum(r['signals'] for r in approach_results) / len(approach_results)

            print(f"\n{approach_name} Approach (avg of {len(approach_results)} configs):")
            print(f"  Avg Return:    {avg_return:+.2f}%")
            print(f"  Avg Win Rate:  {avg_winrate:.1f}%")
            print(f"  Avg Sharpe:    {avg_sharpe:.2f}")
            print(f"  Avg Signals:   {avg_signals:.1f}")

    # Save results
    output_dir = Path('scanner_output/optimization')
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save detailed results
    results_file = output_dir / f'intraday_optimization_{start_date}_{end_date}.json'
    with open(results_file, 'w') as f:
        json.dump({
            'test_period': {'start': start_date, 'end': end_date},
            'best_config': best,
            'all_results': all_results
        }, f, indent=2)

    print(f"\n💾 Full results saved to: {results_file}")

    # Generate recommendations
    print("\n" + "=" * 80)
    print("💡 RECOMMENDATIONS:")
    print("=" * 80)
    print(f"""
Based on the analysis:

1. OPTIMAL SETTINGS (Best Risk-Adjusted):
   - Use {best['approach']} approach
   - Volume Threshold: {best['config']['vol_thresh']}
   - ATR Multiplier: {best['config']['atr_mult']}
   - Lookback Period: {best['config']['lookback']}
   - Expected Sharpe: {best['sharpe']:.2f}

2. KEY INSIGHTS:
   - {best['approach']} approach provides best risk-adjusted returns
   - Win rate of {best['win_rate']:.1f}% is {'excellent' if best['win_rate'] > 60 else 'good' if best['win_rate'] > 50 else 'moderate'}
   - Generated {best['signals']} signals over the test period

3. IMPLEMENTATION:
   - Update config.py 'daytrade' mode with these parameters
   - Use 15-minute timeframe for scanning
   - Consider using trailing stop with 2.5x ATR multiplier
   - Activate trailing stop after 5% profit
    """)

    return best


async def quick_intraday_test():
    """
    Quick test with recent data for faster results
    """
    print("🚀 QUICK INTRADAY BREAKOUT TEST")
    print("=" * 80)
    print("Testing last 5 trading days with strategic configurations...")
    print()

    # Use recent dates
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

    # Test on a small subset of liquid stocks
    test_symbols = [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'TSLA', 'META', 'NFLX',
        'AMD', 'INTC', 'JPM', 'BAC', 'WMT', 'HD', 'DIS', 'BA'
    ]

    # Strategic configurations to test
    configs = [
        {'vol_thresh': 1.5, 'atr_mult': 0.3, 'lookback': 10, 'name': 'Conservative'},
        {'vol_thresh': 1.2, 'atr_mult': 0.2, 'lookback': 10, 'name': 'Moderate'},
        {'vol_thresh': 1.0, 'atr_mult': 0.2, 'lookback': 8, 'name': 'Aggressive'},
        {'vol_thresh': 1.3, 'atr_mult': 0.25, 'lookback': 12, 'name': 'Balanced'},
    ]

    results = []

    for cfg in configs:
        print(f"Testing {cfg['name']}: Vol={cfg['vol_thresh']}, ATR={cfg['atr_mult']}, "
              f"Look={cfg['lookback']}...", end=' ')

        result = await test_intraday_config(
            test_symbols, start_date, end_date,
            {'vol_thresh': cfg['vol_thresh'], 'atr_mult': cfg['atr_mult'], 'lookback': cfg['lookback']}
        )

        if result:
            result['name'] = cfg['name']
            results.append(result)
            print(f"✓ Sigs={result['signals']}, Ret={result['return']:+.2f}%, "
                  f"WR={result['win_rate']:.1f}%")
        else:
            print("✗ No signals")

    if not results:
        print("\n❌ No valid results")
        return

    # Show results
    print("\n" + "=" * 80)
    print("📊 QUICK TEST RESULTS")
    print("=" * 80)

    results.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n{'Config':<15} {'Signals':<8} {'Return':<10} {'Win%':<8} {'Sharpe':<8}")
    print("-" * 80)

    for r in results:
        print(f"{r['name']:<15} {r['signals']:<8} {r['return']:+8.2f}% {r['win_rate']:6.1f}%  {r['sharpe']:6.2f}")

    best = results[0]
    print(f"\n🎯 Best Configuration: {best['name']}")
    print(f"   Expected Return: {best['return']:+.2f}%")
    print(f"   Win Rate: {best['win_rate']:.1f}%")
    print(f"   Sharpe Ratio: {best['sharpe']:.2f}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == '--quick':
        # Quick test mode
        asyncio.run(quick_intraday_test())
    else:
        # Full optimization
        asyncio.run(run_comprehensive_analysis(
            "input/watchlist3.txt",
            "2025-01-01",
            "2025-01-31"
        ))
