#!/usr/bin/env python3
"""
Fast Indicator Optimizer - Uses smart sampling and caching
Finds optimal indicator settings efficiently
"""
import asyncio
import pandas as pd
import logging
from pathlib import Path
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

async def quick_test(watchlist_path, start_date, end_date, vol_thresh, atr_mult, lookback):
    """Quick test of a specific configuration"""
    detector = BreakoutDetector()
    yf_adapter = YFinanceAdapter()
    
    # Load symbols (sample for speed)
    with open(watchlist_path, 'r') as f:
        content = f.read()
    
    raw_symbols = [s.strip() for s in content.replace('\n', ',').split(',') if s.strip()]
    symbols = []
    for s in raw_symbols:
        if s.startswith('#'): continue
        if ':' in s:
            s = s.split(':')[1]
        symbols.append(s)
    symbols = sorted(list(set(symbols)))[:100]  # Sample first 100 for speed
    
    all_signals = []
    historical_data = {}
    end_prices = {}
    
    for symbol in symbols:
        try:
            df = yf_adapter.get_historical_data(symbol, '1d', start_date="2024-01-01", end_date=end_date)
            if df is None or len(df) == 0: continue
            
            historical_data[symbol] = df
            end_prices[symbol] = float(df.iloc[-1]['close'])
            
            sim_dates = pd.date_range(start=start_date, end=end_date, freq='B')
            
            for sim_date in sim_dates:
                df_slice = df[df.index <= sim_date]
                if len(df_slice) < 50: continue
                
                if df_slice.index[-1].date() != sim_date.date():
                    continue
                
                sig = detector.detect(
                    df_slice, symbol, 'swing', '1d', spy_perf=0.0,
                    vol_thresh=vol_thresh,
                    atr_mult=atr_mult,
                    lookback=lookback
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
        except:
            pass
    
    if len(all_signals) == 0:
        return None
    
    sim = SimulationMode(
        start_date=start_date,
        end_date=end_date,
        initial_capital=100000,
        max_position_pct=0.10,
        max_risk_pct=0.01,
        use_trailing_stop=True,
        trailing_stop_atr_mult=4.0,
        trailing_stop_activation_pct=0.10
    )
    
    report = sim.run_simulation(all_signals, end_prices=end_prices, historical_data=historical_data)
    
    return {
        'vol_thresh': vol_thresh,
        'atr_mult': atr_mult,
        'lookback': lookback,
        'signals': len(all_signals),
        'return': report['total_return'],
        'win_rate': report['win_rate'],
        'sharpe': report['sharpe_ratio'],
        'drawdown': report['max_drawdown']
    }

async def run_fast_optimization():
    """Run fast optimization using strategic sampling"""
    print("🚀 Fast Indicator Optimization")
    print("=" * 80)
    print("Testing strategic configurations to find optimal settings...")
    print()
    
    # Test key configurations (not exhaustive, but strategic)
    configs = [
        # Current settings
        (1.3, 0.5, 20),
        # More selective (higher thresholds)
        (1.5, 0.7, 20),
        (1.7, 0.7, 20),
        # Less selective (lower thresholds)
        (1.1, 0.3, 20),
        (1.1, 0.5, 20),
        # Longer lookback
        (1.3, 0.5, 25),
        (1.3, 0.5, 30),
        # Shorter lookback
        (1.3, 0.5, 15),
        # Combinations
        (1.5, 0.5, 25),
        (1.1, 0.7, 20),
        (1.3, 0.3, 20),
        (1.3, 0.7, 20),
    ]
    
    results = []
    
    for i, (vol, atr, look) in enumerate(configs):
        print(f"Testing config {i+1}/{len(configs)}: Vol={vol}, ATR={atr}, Look={look}...", end=' ')
        
        # Test on watchlist3
        result = await quick_test("input/watchlist3.txt", "2025-01-01", "2025-12-31", vol, atr, look)
        
        if result:
            results.append(result)
            print(f"✓ Return: {result['return']:+.2f}%, Sharpe: {result['sharpe']:.2f}")
        else:
            print("✗ No signals")
    
    if not results:
        print("\n❌ No valid results")
        return
    
    # Sort by Sharpe Ratio
    results.sort(key=lambda x: x['sharpe'], reverse=True)
    
    print("\n" + "=" * 80)
    print("📊 RESULTS (sorted by Sharpe Ratio)")
    print("=" * 80)
    print(f"{'Vol':<6} {'ATR':<6} {'Look':<6} {'Signals':<8} {'Return':<10} {'Win%':<8} {'Sharpe':<8} {'DD':<8}")
    print("-" * 80)
    
    for r in results:
        print(f"{r['vol_thresh']:<6.1f} {r['atr_mult']:<6.1f} {r['lookback']:<6} "
              f"{r['signals']:<8} {r['return']:+.2f}%{'':<4} {r['win_rate']:.1f}%{'':<3} "
              f"{r['sharpe']:.2f}{'':<5} {r['drawdown']:.2f}%")
    
    best = results[0]
    print("\n" + "=" * 80)
    print("🎯 OPTIMAL CONFIGURATION FOUND:")
    print("=" * 80)
    print(f"  Volume Threshold:  {best['vol_thresh']} (current: 1.3)")
    print(f"  ATR Multiplier:    {best['atr_mult']} (current: 0.5)")
    print(f"  Lookback Period:   {best['lookback']} (current: 20)")
    print()
    print(f"  Expected Return:   {best['return']:+.2f}%")
    print(f"  Win Rate:          {best['win_rate']:.1f}%")
    print(f"  Sharpe Ratio:      {best['sharpe']:.2f}")
    print(f"  Max Drawdown:      {best['drawdown']:.2f}%")
    print(f"  Signals:           {best['signals']}")
    print()
    
    # Save results
    output_dir = Path('scanner_output/optimization')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    with open(output_dir / 'indicator_optimization_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"💾 Results saved to: scanner_output/optimization/indicator_optimization_results.json")
    
    return best

if __name__ == "__main__":
    asyncio.run(run_fast_optimization())
