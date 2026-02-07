#!/usr/bin/env python3
"""
Advanced Indicator Optimizer
Tests various combinations of technical indicators to find optimal settings
"""
import asyncio
import pandas as pd
import itertools
import logging
from pathlib import Path

# Python 3.14 compatibility
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from config import PORTFOLIO, MODES
from mock_trader import SimulationMode
from scanner import BreakoutDetector
from yfinance_adapter import YFinanceAdapter

logging.basicConfig(level=logging.ERROR, format='%(message)s')
logger = logging.getLogger(__name__)

async def test_indicator_config(watchlist_path, start_date, end_date, config_params):
    """
    Test a specific indicator configuration
    
    Args:
        watchlist_path: Path to watchlist file
        start_date: Start date for simulation
        end_date: End date for simulation
        config_params: Dict of indicator parameters to test
    
    Returns:
        Dict with performance metrics
    """
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
    symbols = sorted(list(set(symbols)))
    
    all_signals = []
    historical_data = {}
    end_prices = {}
    
    # Scan symbols with custom config
    for symbol in symbols:
        try:
            df = yf_adapter.get_historical_data(symbol, '1d', start_date="2024-01-01", end_date=end_date)
            if df is None or len(df) == 0: continue
            
            historical_data[symbol] = df
            end_prices[symbol] = float(df.iloc[-1]['close'])
            
            # Scan with custom parameters
            sim_dates = pd.date_range(start=start_date, end=end_date, freq='B')
            
            for sim_date in sim_dates:
                df_slice = df[df.index <= sim_date]
                if len(df_slice) < 50: continue
                
                latest_date = df_slice.index[-1]
                if latest_date.date() != sim_date.date():
                    continue
                
                # Detect with custom config
                sig = detector.detect(
                    df_slice, symbol, 'swing', '1d', spy_perf=0.0,
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
            pass
    
    if len(all_signals) == 0:
        return None
    
    # Run simulation
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
        'signals': len(all_signals),
        'return': report['total_return'],
        'win_rate': report['win_rate'],
        'sharpe': report['sharpe_ratio'],
        'drawdown': report['max_drawdown'],
        'trades': report['total_trades']
    }

async def run_indicator_optimization(watchlists, start_date, end_date):
    """
    Run comprehensive indicator optimization
    """
    print("🔬 Advanced Indicator Optimization")
    print("=" * 80)
    print(f"📅 Period: {start_date} to {end_date}")
    print(f"📊 Watchlists: {', '.join([Path(w).name for w in watchlists])}")
    print()
    
    # Define parameter grid for indicators
    param_grid = {
        'vol_thresh': [1.1, 1.3, 1.5, 1.7],      # Volume confirmation threshold
        'atr_mult': [0.3, 0.5, 0.7, 1.0],        # Distance from breakout (ATR multiplier)
        'lookback': [15, 20, 25, 30],            # Lookback period for high
    }
    
    # Generate all combinations
    keys, values = zip(*param_grid.items())
    combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    
    print(f"🧪 Testing {len(combinations)} indicator configurations...")
    print(f"   This will take approximately {len(combinations) * len(watchlists) * 2} minutes")
    print()
    
    all_results = []
    
    for watchlist in watchlists:
        watchlist_name = Path(watchlist).stem
        print(f"\n📋 Testing on {watchlist_name}...")
        
        for i, params in enumerate(combinations):
            result = await test_indicator_config(watchlist, start_date, end_date, params)
            
            if result:
                result['watchlist'] = watchlist_name
                result['params'] = params
                all_results.append(result)
            
            # Progress indicator
            if (i + 1) % 10 == 0:
                print(f"   Progress: {i+1}/{len(combinations)}", end='\r')
        
        print(f"   ✓ Completed {watchlist_name}                    ")
    
    if not all_results:
        print("\n❌ No valid results found")
        return
    
    # Analyze results
    print("\n" + "=" * 80)
    print("📊 OPTIMIZATION RESULTS")
    print("=" * 80)
    
    # Sort by Sharpe Ratio (risk-adjusted returns)
    sorted_by_sharpe = sorted(all_results, key=lambda x: x['sharpe'], reverse=True)
    
    print(f"\n🏆 TOP 10 CONFIGURATIONS (by Sharpe Ratio):")
    print("-" * 80)
    print(f"{'Vol':<6} {'ATR':<6} {'Look':<6} {'WL':<12} {'Sigs':<6} {'Return':<10} {'Win%':<8} {'Sharpe':<8}")
    print("-" * 80)
    
    for res in sorted_by_sharpe[:10]:
        p = res['params']
        print(f"{p['vol_thresh']:<6.1f} {p['atr_mult']:<6.1f} {p['lookback']:<6} "
              f"{res['watchlist']:<12} {res['signals']:<6} "
              f"{res['return']:+.2f}%{'':<4} {res['win_rate']:.1f}%{'':<3} {res['sharpe']:.2f}")
    
    # Best configuration
    best = sorted_by_sharpe[0]
    print("\n" + "=" * 80)
    print("🎯 RECOMMENDED CONFIGURATION:")
    print("=" * 80)
    print(f"  Volume Threshold:  {best['params']['vol_thresh']}")
    print(f"  ATR Multiplier:    {best['params']['atr_mult']}")
    print(f"  Lookback Period:   {best['params']['lookback']}")
    print()
    print(f"  Expected Return:   {best['return']:+.2f}%")
    print(f"  Win Rate:          {best['win_rate']:.1f}%")
    print(f"  Sharpe Ratio:      {best['sharpe']:.2f}")
    print(f"  Max Drawdown:      {best['drawdown']:.2f}%")
    print(f"  Signals Generated: {best['signals']}")
    print()
    
    # Cross-validation: Test best config on other watchlist
    print("🔄 Cross-Validation:")
    best_config_results = [r for r in all_results if r['params'] == best['params']]
    for res in best_config_results:
        print(f"  {res['watchlist']}: {res['return']:+.2f}% return, {res['win_rate']:.1f}% win rate")
    
    # Save results
    results_df = pd.DataFrame(all_results)
    output_dir = Path('scanner_output/optimization')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    results_file = output_dir / f'indicator_optimization_{start_date}_{end_date}.csv'
    results_df.to_csv(results_file, index=False)
    print(f"\n💾 Full results saved to: {results_file}")
    
    return best['params']

if __name__ == "__main__":
    watchlists = [
        "input/watchlist3.txt",
        "input/watchlist4.txt"
    ]
    
    best_config = asyncio.run(run_indicator_optimization(
        watchlists,
        "2025-01-01",
        "2025-12-31"
    ))
