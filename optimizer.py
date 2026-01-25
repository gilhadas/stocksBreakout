import asyncio
import pandas as pd
import itertools
import logging

# Python 3.14 compatibility: Create event loop before importing ib_insync
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# from tabulate import tabulate
from config import PORTFOLIO, MODES
from mock_trader import SimulationMode
# from breakout_scanner import BreakoutScanner # Removed non-existent import
from scanner import BreakoutDetector
from yfinance_adapter import YFinanceAdapter
import logging

# Configure logging
logging.basicConfig(level=logging.ERROR, format='%(message)s') # Reduce noise
logger = logging.getLogger(__name__)

async def run_optimization(watchlist_path, start_date, end_date):
    print(f"🚀 Starting Strategy Optimization (Deep Learning Data)...")
    print(f"📅 Period: {start_date} to {end_date}")
    
    # 1. Load Data & Generate Base Signals
    print("\n1️⃣  Loading Data & Generating Signals...")
    detector = BreakoutDetector() # Use Detector directly
    yf_adapter = YFinanceAdapter()
    
    # Load symbols
    with open(watchlist_path, 'r') as f:
        content = f.read()
        
    # Split by comma and newlines, clean up
    raw_symbols = [s.strip() for s in content.replace('\n', ',').split(',') if s.strip()]
    symbols = []
    for s in raw_symbols:
        if s.startswith('#'): continue
        if ':' in s:
            s = s.split(':')[1] # Strip exchange prefix
        symbols.append(s)
    
    # Remove duplicates
    symbols = sorted(list(set(symbols)))
    
    all_signals = []
    historical_data = {}
    end_prices = {}
    
    print(f"   Scanning {len(symbols)} symbols...")
    
    # Scan symbols
    for symbol in symbols:
        try:
            # Get data with ample lookback
            df = yf_adapter.get_historical_data(symbol, '1d', start_date="2024-01-01", end_date=end_date)
            if df is None or len(df) == 0: continue
            
            # Store for simulation
            historical_data[symbol] = df
            
            # Get end price
            # df_sim = df[(df.index >= start_date) & (df.index <= end_date)]
            # end_prices[symbol] = float(df_sim.iloc[-1]['close']) if len(df_sim) > 0 else 0
            # Better: use the very last close available in the df (which is up to end_date)
            end_prices[symbol] = float(df.iloc[-1]['close'])
            
            # Generate signals by iterating through dates
            # This mimics the simulation scanning loop in breakout_scanner.py
            sim_dates = pd.date_range(start=start_date, end=end_date, freq='B')
            
            for sim_date in sim_dates:
                # Filter data up to this date
                mask = df.index <= sim_date
                # Optimization: Slice using integer indexing if possible, or just copy
                # df_slice = df.loc[:sim_date] # Can be slow in loop
                # Faster: finding index location?
                # Just use boolean mask for correctness now
                df_slice = df[mask]
                
                if len(df_slice) < 50: continue
                
                # Check if sim_date is actually in the index (avoid scanning non-trading days if freq='B' is imperfect)
                # (df index is already trading days)
                latest_date = df_slice.index[-1]
                if latest_date.date() != sim_date.date():
                    continue
                
                # Detect
                # Passing spy_perf=0.0 to act as 'neutral market' or skip RS check
                sig = detector.detect(df_slice, symbol, 'swing', '1d', spy_perf=0.0)
                
                if sig:
                    all_signals.append({
                        'date': sim_date, # Use sim_date as signal date
                        'symbol': symbol,
                        'action': 'BUY',
                        'price': sig['Price'],
                        'entry_price': sig['Price'], 
                        'stop_loss': sig['Stop'],
                        'take_profit': sig['Target']
                    })
                    print(f"    + {symbol} signal on {sim_date.date()}")
                    
        except Exception as e:
            print(f"Error processing {symbol}: {e}")

    print(f"✅ Generated {len(all_signals)} signals for optimization")
    
    if not all_signals:
        return

    # 2. Define Parameter Grid
    param_grid = {
        'sl_width_multiplier': [1.0, 1.5, 2.0, 3.0], # Test standard, 50% wider, 2x wider, 3x wider
        'trailing_stop_atr_mult': [3.0, 4.0],
        'trailing_stop_activation_pct': [0.05, 0.10],
        'use_trailing_stop': [True],
        'max_risk_pct': [0.01], 
    }
    
    # Generate combinations
    keys, values = zip(*param_grid.items())
    combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    
    # Add Baseline
    combinations.append({
        'sl_width_multiplier': 1.0,
        'use_trailing_stop': False,
        'trailing_stop_atr_mult': 0,
        'trailing_stop_activation_pct': 0,
        'max_risk_pct': 0.01
    })
    
    print(f"\n2️⃣  Running {len(combinations)} Simulations...")
    
    results = []
    
    for i, params in enumerate(combinations):
        # Adjust signals for this simulation based on sl_width_multiplier
        # We need to deep copy signals to not affect other runs
        sim_signals = []
        sl_mult = params['sl_width_multiplier']
        
        for s in all_signals:
            # Calculate new wide stop
            # Original SL dist = Entry - Stop
            original_risk = s['entry_price'] - s['stop_loss']
            new_risk = original_risk * sl_mult
            new_stop = s['entry_price'] - new_risk
            
            # Create modified signal
            s_new = s.copy()
            s_new['stop_loss'] = new_stop
            # We keep Target same? Or extend it too? Usually Target stays or extends?
            # Let's keep Target fixed to improve Win Rate (Wide Stop, Fixed Target = High Win Rate)
            sim_signals.append(s_new)

        sim = SimulationMode(
            start_date=start_date,
            end_date=end_date,
            initial_capital=100000,
            max_position_pct=0.10,
            max_risk_pct=params['max_risk_pct'],
            use_trailing_stop=params['use_trailing_stop'],
            trailing_stop_atr_mult=params.get('trailing_stop_atr_mult', 0),
            trailing_stop_activation_pct=params.get('trailing_stop_activation_pct', 0)
        )
        
        # Run simulation
        # Note: SimulationMode modifies signals list in place? No, it reads.
        # But MockTrader modifies trade objects? Yes.
        # We need fresh MockTrader each time (handled by SimulationMode init)
        report = sim.run_simulation(sim_signals, end_prices=end_prices, historical_data=historical_data)
        
        # Calculate Score (Custom Metric: Return / MaxDrawdown) - "Safe Growth Score"
        # Avoid division by zero
        dd = abs(report['max_drawdown'])
        if dd < 0.1: dd = 0.1
        score = report['total_return'] / dd
        
        results.append({
            'params': params,
            'return': report['total_return'],
            'win_rate': report['win_rate'],
            'drawdown': report['max_drawdown'],
            'sharpe': report['sharpe_ratio'],
            'trades': report['total_trades'],
            'score': score
        })
        
        # Simple progress bar
        print(".", end="", flush=True)
    
    print("\n\n3️⃣  Optimization Results:")
    
    # Sort by Total Return
    sorted_by_ret = sorted(results, key=lambda x: x['return'], reverse=True)
    
    # Prepare table
    print(f"{'Strategy':<35} {'StopMult':<8} {'Risk':<6} {'Return':<10} {'DD':<8} {'Win%':<6} {'Sharpe':<6}")
    print("-" * 85)
    for res in sorted_by_ret[:10]: # Top 10
        p = res['params']
        desc = "NO TRAIL" if not p['use_trailing_stop'] else f"Trail {p['trailing_stop_atr_mult']}x ATR (Act: {p['trailing_stop_activation_pct']:.0%})"
        print(f"{desc:<35} {p['sl_width_multiplier']:<8.1f} {p['max_risk_pct']:.0%}{'':<4} {res['return']:+.2f}%{'':<4} {res['drawdown']:.2f}%{'':<3} {res['win_rate']:.1f}%{'':<2} {res['sharpe']:.2f}")
    
    # Recommendation
    best = sorted_by_ret[0]
    print(f"\n🏆 Best Strategy: {best['return']:.2f}% Return")
    print(f"   Stop Loss Width: {best['params']['sl_width_multiplier']}x Standard")
    if best['params']['use_trailing_stop']:
        print(f"   Trailing Stop: {best['params']['trailing_stop_atr_mult']}x ATR")
        print(f"   Activation: {best['params']['trailing_stop_activation_pct']:.0%}")
    else:
        print("   Trailing Stop: DISABLED (Fixed Targets)")

if __name__ == "__main__":
    asyncio.run(run_optimization("input/watchlist3.txt", "2025-01-01", "2025-12-31"))
