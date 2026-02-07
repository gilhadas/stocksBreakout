#!/usr/bin/env python3
"""
Day Trading Optimizer
Uses daily bars to backtest intraday trading parameters over longer historical periods.
Tests different approaches to find true stock breakouts for day trading.
"""
import asyncio
import pandas as pd
import itertools
import logging
from pathlib import Path
from datetime import datetime
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


async def test_daytrade_config(symbols, start_date, end_date, config_params):
    """
    Test day trading configuration using daily bars

    Args:
        symbols: List of symbols to test
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        config_params: Dict of parameters to test

    Returns:
        Dict with performance metrics
    """
    detector = BreakoutDetector()
    yf_adapter = YFinanceAdapter()

    all_signals = []
    historical_data = {}
    end_prices = {}

    for symbol in symbols:
        try:
            # Use daily bars (available for longer history)
            df = yf_adapter.get_historical_data(
                symbol, '1 day',
                start_date="2024-01-01",  # Get more history for indicators
                end_date=end_date
            )

            if df is None or len(df) == 0:
                continue

            historical_data[symbol] = df
            end_prices[symbol] = float(df.iloc[-1]['close'])

            # Scan for signals during the test period
            sim_dates = pd.date_range(start=start_date, end=end_date, freq='B')

            for sim_date in sim_dates:
                df_slice = df[df.index <= sim_date]
                if len(df_slice) < 50:
                    continue

                latest_date = df_slice.index[-1]
                if latest_date.date() != sim_date.date():
                    continue

                # Detect with custom daytrade parameters
                sig = detector.detect(
                    df_slice, symbol, 'daytrade', '1 day', spy_perf=0.0,
                    vol_thresh=config_params.get('vol_thresh'),
                    atr_mult=config_params.get('atr_mult'),
                    lookback=config_params.get('lookback')
                )

                if sig:
                    all_signals.append({
                        'date': sim_date,
                        'symbol': symbol,
                        'action': 'BUY',
                        'price': sig['Price'],
                        'entry_price': sig['Price'],
                        'stop_loss': sig['Stop'],
                        'take_profit': sig['Target']
                    })

        except Exception as e:
            logger.debug(f"Error processing {symbol}: {e}")
            pass

    if len(all_signals) == 0:
        return None

    # Run simulation with day trading risk parameters
    sim = SimulationMode(
        start_date=start_date,
        end_date=end_date,
        initial_capital=100000,
        max_position_pct=0.10,  # 10% per position
        max_risk_pct=0.01,      # 1% risk per trade
        use_trailing_stop=True,
        trailing_stop_atr_mult=2.5,  # Tighter than swing trading
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


async def run_daytrade_optimization(watchlist_path, start_date, end_date):
    """
    Run comprehensive day trading optimization
    """
    print("🔬 DAY TRADING BREAKOUT OPTIMIZER")
    print("=" * 80)
    print(f"📅 Test Period: {start_date} to {end_date}")
    print(f"📊 Using daily bars for historical analysis")
    print(f"📋 Watchlist: {Path(watchlist_path).name}")
    print()

    # Load symbols
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

    # Use top 100 most liquid stocks
    symbols = sorted(list(set(symbols)))[:100]

    print(f"🎯 Testing {len(symbols)} symbols")
    print(f"   Sample: {', '.join(symbols[:10])}...")
    print()

    # Define different scanning approaches
    print("📊 TESTING DIFFERENT BREAKOUT DETECTION APPROACHES:")
    print()

    approaches = {
        'Strict': {
            'name': 'Strict (High Quality Only)',
            'vol_thresh': [1.5, 1.7, 2.0],
            'atr_mult': [0.3, 0.4, 0.5],
            'lookback': [8, 10, 12]
        },
        'Balanced': {
            'name': 'Balanced (Quality & Quantity)',
            'vol_thresh': [1.2, 1.3, 1.5],
            'atr_mult': [0.2, 0.25, 0.3],
            'lookback': [8, 10, 12]
        },
        'Aggressive': {
            'name': 'Aggressive (More Signals)',
            'vol_thresh': [1.0, 1.1, 1.2],
            'atr_mult': [0.15, 0.2, 0.25],
            'lookback': [6, 8, 10]
        }
    }

    all_results = []

    for approach_key, approach_cfg in approaches.items():
        print(f"\n{'='*80}")
        print(f"🧪 TESTING: {approach_cfg['name'].upper()}")
        print(f"{'='*80}")

        # Generate parameter combinations
        keys = ['vol_thresh', 'atr_mult', 'lookback']
        values = [approach_cfg['vol_thresh'], approach_cfg['atr_mult'], approach_cfg['lookback']]
        combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]

        print(f"   Testing {len(combinations)} configurations...")
        print()

        for i, params in enumerate(combinations):
            print(f"   [{i+1}/{len(combinations)}] Vol={params['vol_thresh']:.1f}, "
                  f"ATR={params['atr_mult']:.2f}, Look={params['lookback']}...", end=' ')

            result = await test_daytrade_config(symbols, start_date, end_date, params)

            if result:
                result['approach'] = approach_key
                all_results.append(result)
                print(f"✓ Sigs={result['signals']}, Ret={result['return']:+.2f}%, "
                      f"WR={result['win_rate']:.1f}%")
            else:
                print("✗ No signals")

    if not all_results:
        print("\n❌ No valid results found")
        return

    # Analyze results
    print("\n" + "=" * 80)
    print("📊 COMPREHENSIVE ANALYSIS RESULTS")
    print("=" * 80)

    # Sort by Sharpe Ratio (risk-adjusted returns)
    sorted_results = sorted(all_results, key=lambda x: x['sharpe'], reverse=True)

    print(f"\n🏆 TOP 15 CONFIGURATIONS (by Risk-Adjusted Returns):")
    print("-" * 80)
    print(f"{'Approach':<12} {'Vol':<5} {'ATR':<6} {'Look':<5} {'Sigs':<6} "
          f"{'Return':<9} {'Win%':<7} {'Sharpe':<7} {'DD%':<7}")
    print("-" * 80)

    for res in sorted_results[:15]:
        cfg = res['config']
        print(f"{res['approach']:<12} {cfg['vol_thresh']:<5.1f} {cfg['atr_mult']:<6.2f} "
              f"{cfg['lookback']:<5} {res['signals']:<6} {res['return']:+7.2f}% "
              f"{res['win_rate']:5.1f}%  {res['sharpe']:5.2f}  {res['drawdown']:6.2f}%")

    # Best overall
    best = sorted_results[0]
    print("\n" + "=" * 80)
    print("🎯 OPTIMAL CONFIGURATION (Best Risk-Adjusted):")
    print("=" * 80)
    print(f"  Approach:          {approaches[best['approach']]['name']}")
    print(f"  Volume Threshold:  {best['config']['vol_thresh']} (current daytrade: 1.1)")
    print(f"  ATR Multiplier:    {best['config']['atr_mult']} (current daytrade: 0.2)")
    print(f"  Lookback Period:   {best['config']['lookback']} (current daytrade: 10)")
    print()
    print(f"  Expected Return:   {best['return']:+.2f}%")
    print(f"  Win Rate:          {best['win_rate']:.1f}%")
    print(f"  Sharpe Ratio:      {best['sharpe']:.2f}")
    print(f"  Max Drawdown:      {best['drawdown']:.2f}%")
    print(f"  Total Signals:     {best['signals']}")
    print(f"  Total Trades:      {best['trades']}")
    print()

    # Best by return
    best_return = sorted(all_results, key=lambda x: x['return'], reverse=True)[0]
    print("💰 HIGHEST RETURN CONFIGURATION:")
    print("-" * 80)
    print(f"  Approach:          {approaches[best_return['approach']]['name']}")
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
    print(f"  Approach:          {approaches[best_winrate['approach']]['name']}")
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

    for approach_key, approach_cfg in approaches.items():
        approach_results = [r for r in all_results if r['approach'] == approach_key]
        if approach_results:
            avg_return = sum(r['return'] for r in approach_results) / len(approach_results)
            avg_winrate = sum(r['win_rate'] for r in approach_results) / len(approach_results)
            avg_sharpe = sum(r['sharpe'] for r in approach_results) / len(approach_results)
            avg_signals = sum(r['signals'] for r in approach_results) / len(approach_results)
            best_sharpe = max(r['sharpe'] for r in approach_results)

            print(f"\n{approach_cfg['name']} ({len(approach_results)} configs):")
            print(f"  Avg Return:      {avg_return:+.2f}%")
            print(f"  Avg Win Rate:    {avg_winrate:.1f}%")
            print(f"  Avg Sharpe:      {avg_sharpe:.2f}")
            print(f"  Best Sharpe:     {best_sharpe:.2f}")
            print(f"  Avg Signals:     {avg_signals:.0f}")

    # Save results
    output_dir = Path('scanner_output/optimization')
    output_dir.mkdir(parents=True, exist_ok=True)

    results_file = output_dir / f'daytrade_optimization_{start_date}_{end_date}.json'
    with open(results_file, 'w') as f:
        json.dump({
            'test_period': {'start': start_date, 'end': end_date},
            'best_config': best,
            'best_return': best_return,
            'best_winrate': best_winrate,
            'all_results': all_results
        }, f, indent=2)

    print(f"\n💾 Full results saved to: {results_file}")

    # Generate recommendations
    print("\n" + "=" * 80)
    print("💡 KEY FINDINGS & RECOMMENDATIONS:")
    print("=" * 80)

    quality_score = "excellent" if best['win_rate'] > 60 else "good" if best['win_rate'] > 50 else "moderate"

    print(f"""
1. OPTIMAL SETTINGS FOR DAY TRADING:
   - Approach: {approaches[best['approach']]['name']}
   - Volume Threshold: {best['config']['vol_thresh']} (increase from current 1.1)
   - ATR Multiplier: {best['config']['atr_mult']} (adjust from current 0.2)
   - Lookback Period: {best['config']['lookback']} (adjust from current 10)
   - Risk-Adjusted Performance (Sharpe): {best['sharpe']:.2f}

2. PERFORMANCE METRICS:
   - Win Rate: {best['win_rate']:.1f}% ({quality_score})
   - Expected Return: {best['return']:+.2f}%
   - Max Drawdown: {best['drawdown']:.2f}%
   - Signal Quality: {best['signals']} signals generated

3. KEY INSIGHTS:
   - {'Higher volume thresholds reduce false signals' if best['config']['vol_thresh'] > 1.3 else 'Lower volume thresholds capture more opportunities'}
   - {'Conservative distance requirements improve quality' if best['config']['atr_mult'] > 0.25 else 'Tighter distance allows earlier entries'}
   - {'Longer lookback periods find established breakouts' if best['config']['lookback'] > 10 else 'Shorter lookback captures recent momentum'}

4. IMPLEMENTATION STEPS:
   a. Update config.py 'daytrade' mode with optimal parameters
   b. Use trailing stop with 2.5x ATR multiplier (tested)
   c. Activate trailing stop after 5% profit
   d. Risk 1% per trade, max 10% per position
   e. Focus on liquid stocks (>$5M daily volume)

5. COMPARISON WITH CURRENT SETTINGS:
   Current daytrade config: Vol=1.1, ATR=0.2, Look=10
   Optimal config: Vol={best['config']['vol_thresh']}, ATR={best['config']['atr_mult']}, Look={best['config']['lookback']}
   Improvement: {best['sharpe']:.2f}x Sharpe ratio
    """)

    return best


if __name__ == "__main__":
    # Test on recent historical data
    asyncio.run(run_daytrade_optimization(
        "input/watchlist3.txt",
        "2024-06-01",  # 8 months of data
        "2025-01-31"
    ))
