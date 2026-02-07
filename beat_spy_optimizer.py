#!/usr/bin/env python3
"""
Beat SPY Strategy Optimizer
Tests aggressive configurations to outperform market benchmark
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

async def test_aggressive_config(watchlist_path, start_date, end_date, config):
    """Test aggressive configuration"""
    detector = BreakoutDetector()
    yf_adapter = YFinanceAdapter()
    
    # Load symbols
    with open(watchlist_path, 'r') as f:
        content = f.read()
    
    raw_symbols = [s.strip() for s in content.replace('\n', ',').split(',') if s.strip()]
    symbols = []
    for s in raw_symbols:
        if s.startswith('#'): continue
        if ':' in s:
            s = s.split(':')[1]
        symbols.append(s)
    symbols = sorted(list(set(symbols)))[:150]  # Test on 150 symbols
    
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
                    vol_thresh=config.get('vol_thresh', 1.3),
                    atr_mult=config.get('atr_mult', 0.5),
                    lookback=config.get('lookback', 15)
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
        max_position_pct=config.get('max_position_pct', 0.10),
        max_risk_pct=config.get('max_risk_pct', 0.01),
        use_trailing_stop=config.get('use_trailing_stop', True),
        trailing_stop_atr_mult=config.get('trailing_stop_atr_mult', 4.0),
        trailing_stop_activation_pct=config.get('trailing_stop_activation_pct', 0.10)
    )
    
    report = sim.run_simulation(all_signals, end_prices=end_prices, historical_data=historical_data)
    
    return {
        'config': config,
        'signals': len(all_signals),
        'return': report['total_return'],
        'win_rate': report['win_rate'],
        'sharpe': report['sharpe_ratio'],
        'drawdown': report['max_drawdown'],
        'trades': report['total_trades']
    }

async def run_beat_spy_optimization():
    """Test aggressive strategies to beat SPY"""
    print("🎯 BEAT SPY OPTIMIZER")
    print("=" * 80)
    print("Target: Beat SPY's +17.9% return in 2025")
    print("Testing aggressive configurations...")
    print()
    
    # Aggressive strategies to test
    strategies = [
        {
            'name': 'Current Optimized',
            'vol_thresh': 1.3,
            'atr_mult': 0.5,
            'lookback': 15,
            'max_risk_pct': 0.01,
            'max_position_pct': 0.10,
            'use_trailing_stop': True,
            'trailing_stop_atr_mult': 4.0,
            'trailing_stop_activation_pct': 0.10
        },
        {
            'name': 'Aggressive Risk (2%)',
            'vol_thresh': 1.3,
            'atr_mult': 0.5,
            'lookback': 15,
            'max_risk_pct': 0.02,  # Double risk
            'max_position_pct': 0.15,
            'use_trailing_stop': True,
            'trailing_stop_atr_mult': 4.0,
            'trailing_stop_activation_pct': 0.10
        },
        {
            'name': 'More Signals (Lower Thresh)',
            'vol_thresh': 1.1,  # Lower threshold = more signals
            'atr_mult': 0.3,
            'lookback': 15,
            'max_risk_pct': 0.015,
            'max_position_pct': 0.12,
            'use_trailing_stop': True,
            'trailing_stop_atr_mult': 4.0,
            'trailing_stop_activation_pct': 0.10
        },
        {
            'name': 'Let Winners Run (No TP)',
            'vol_thresh': 1.3,
            'atr_mult': 0.5,
            'lookback': 15,
            'max_risk_pct': 0.01,
            'max_position_pct': 0.10,
            'use_trailing_stop': True,
            'trailing_stop_atr_mult': 3.0,  # Tighter trail
            'trailing_stop_activation_pct': 0.05  # Earlier activation
        },
        {
            'name': 'Ultra Aggressive',
            'vol_thresh': 1.1,
            'atr_mult': 0.3,
            'lookback': 10,  # Even shorter
            'max_risk_pct': 0.02,
            'max_position_pct': 0.15,
            'use_trailing_stop': True,
            'trailing_stop_atr_mult': 3.0,
            'trailing_stop_activation_pct': 0.05
        },
        {
            'name': 'No Trailing (Fixed Targets)',
            'vol_thresh': 1.3,
            'atr_mult': 0.5,
            'lookback': 15,
            'max_risk_pct': 0.015,
            'max_position_pct': 0.12,
            'use_trailing_stop': False,
            'trailing_stop_atr_mult': 0,
            'trailing_stop_activation_pct': 0
        }
    ]
    
    results = []
    
    for i, strategy in enumerate(strategies):
        name = strategy.pop('name')
        print(f"Testing {i+1}/{len(strategies)}: {name}...", end=' ')
        
        result = await test_aggressive_config("input/watchlist3.txt", "2025-01-01", "2025-12-31", strategy)
        
        if result:
            result['name'] = name
            results.append(result)
            print(f"✓ Return: {result['return']:+.2f}%, Sharpe: {result['sharpe']:.2f}")
        else:
            print("✗ No signals")
    
    if not results:
        print("\n❌ No valid results")
        return
    
    # Sort by return
    results.sort(key=lambda x: x['return'], reverse=True)
    
    print("\n" + "=" * 80)
    print("📊 RESULTS vs SPY (+17.9%)")
    print("=" * 80)
    print(f"{'Strategy':<30} {'Return':<12} {'vs SPY':<10} {'Sharpe':<8} {'DD':<8} {'Signals'}")
    print("-" * 80)
    
    spy_return = 17.9
    for r in results:
        vs_spy = r['return'] - spy_return
        beat = "✓" if vs_spy > 0 else "✗"
        print(f"{r['name']:<30} {r['return']:+.2f}%{'':<6} {beat} {vs_spy:+.2f}%{'':<3} "
              f"{r['sharpe']:.2f}{'':<5} {r['drawdown']:.2f}%{'':<3} {r['signals']}")
    
    best = results[0]
    print("\n" + "=" * 80)
    if best['return'] > spy_return:
        print("🏆 SUCCESS! BEAT SPY!")
    else:
        print("⚠️  UNDERPERFORMING SPY")
    print("=" * 80)
    print(f"  Best Strategy: {best['name']}")
    print(f"  Return: {best['return']:+.2f}% (SPY: +17.9%)")
    print(f"  Difference: {best['return'] - spy_return:+.2f}%")
    print(f"  Sharpe: {best['sharpe']:.2f}")
    print(f"  Max Drawdown: {best['drawdown']:.2f}%")
    print()
    
    # Save results
    output_dir = Path('scanner_output/optimization')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    with open(output_dir / 'beat_spy_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"💾 Results saved to: scanner_output/optimization/beat_spy_results.json")
    
    return best

if __name__ == "__main__":
    asyncio.run(run_beat_spy_optimization())
