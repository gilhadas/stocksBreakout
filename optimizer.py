import asyncio
import pandas as pd
import itertools
from tabulate import tabulate
from config import PORTFOLIO, MODES
from mock_trader import SimulationMode
from breakout_scanner import BreakoutScanner
from yfinance_adapter import YFinanceAdapter
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

async def run_optimization(watchlist_path, start_date, end_date):
    print(f"🚀 Starting Strategy Optimization (Deep Learning Data)...")
    print(f"📅 Period: {start_date} to {end_date}")
    
    # 1. Load Data & Generate Base Signals
    print("\n1️⃣  Loading Data & Generating Signals...")
    scanner = BreakoutScanner()
    yf_adapter = YFinanceAdapter()
    
    # Load symbols
    with open(watchlist_path, 'r') as f:
        symbols = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    
    all_signals = []
    historical_data = {}
    end_prices = {}
    
    # Scan symbols
    for symbol in symbols:
        try:
            # Get data
            df = yf_adapter.get_historical_data(symbol, '1d', start_date="2024-01-01") # Lookback for indicators
            if df is None or len(df) == 0: continue
            
            # Store for simulation
            historical_data[symbol] = df
            
            # Get end price
            df_sim = df[(df.index >= start_date) & (df.index <= end_date)]
            if len(df_sim) > 0:
                end_prices[symbol] = float(df_sim.iloc[-1]['close'])
            
            # Generate signals (using Swing mode defaults for initial detection)
            scanner_signals = scanner.scan_symbol(symbol, df, mode='swing')
            if scanner_signals:
                for sig in scanner_signals:
                    # Filter by date
                    sig_date = pd.to_datetime(sig['Date'])
                    if start_date <= sig_date.strftime('%Y-%m-%d') <= end_date:
                        all_signals.append({
                            'date': sig['Date'],
                            'symbol': symbol,
                            'action': 'BUY',
                            'price': sig['Price'],
                            'entry_price': sig['Price'], # For reference
                            'stop_loss': sig['Stop'],    # Initial SL
                            'take_profit': sig['Target'] # Initial TP
                        })
        except Exception as e:
            print(f"Error processing {symbol}: {e}")

    print(f"✅ Generated {len(all_signals)} signals for optimization")
    
    if not all_signals:
        return

    # 2. Define Parameter Grid
    param_grid = {
        'trailing_stop_atr_mult': [2.0, 3.0, 4.0, 5.0],
        'trailing_stop_activation_pct': [0.03, 0.05, 0.07, 0.10],
        'use_trailing_stop': [True],  # We want to optimize this, so assume True
        'max_risk_pct': [0.01, 0.02], # Test 1% and 2% risk
    }
    
    # Generate combinations
    keys, values = zip(*param_grid.items())
    combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    
    # Add a "Baseline" (No Trailing Stop) combination
    combinations.append({
        'use_trailing_stop': False,
        'trailing_stop_atr_mult': 0,
        'trailing_stop_activation_pct': 0,
        'max_risk_pct': 0.01
    })
    
    print(f"\n2️⃣  Running {len(combinations)} Simulations...")
    
    results = []
    
    for i, params in enumerate(combinations):
        sim = SimulationMode(
            start_date=start_date,
            end_date=end_date,
            initial_capital=100000,
            max_position_pct=0.10, # Allow up to 10% to let risk_pct be the constraint
            max_risk_pct=params['max_risk_pct'],
            use_trailing_stop=params['use_trailing_stop'],
            trailing_stop_atr_mult=params.get('trailing_stop_atr_mult', 0),
            trailing_stop_activation_pct=params.get('trailing_stop_activation_pct', 0)
        )
        
        # Run simulation
        # Note: SimulationMode modifies signals list in place? No, it reads.
        # But MockTrader modifies trade objects? Yes.
        # We need fresh MockTrader each time (handled by SimulationMode init)
        report = sim.run_simulation(all_signals, end_prices=end_prices, historical_data=historical_data)
        
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
    table_data = []
    for res in sorted_by_ret[:10]: # Top 10
        p = res['params']
        desc = "NO TRAIL" if not p['use_trailing_stop'] else f"Trail {p['trailing_stop_atr_mult']}x ATR (Act: {p['trailing_stop_activation_pct']:.0%})"
        table_data.append([
            desc,
            f"{p['max_risk_pct']:.0%}",
            f"{res['return']:+.2f}%",
            f"{res['drawdown']:.2f}%",
            f"{res['win_rate']:.1f}%",
            f"{res['sharpe']:.2f}"
        ])
        
    print(tabulate(table_data, headers=["Strategy", "Risk", "Return", "Drawdown", "Win Rate", "Sharpe"], tablefmt="grid"))
    
    # Recommendation
    best = sorted_by_ret[0]
    print(f"\n🏆 Best Strategy: {best['return']:.2f}% Return")
    print(f"   Risk: {best['params']['max_risk_pct']:.0%}")
    if best['params']['use_trailing_stop']:
        print(f"   Trailing Stop: {best['params']['trailing_stop_atr_mult']}x ATR")
        print(f"   Activation: {best['params']['trailing_stop_activation_pct']:.0%}")
    else:
        print("   Trailing Stop: DISABLED (Fixed Targets)")

if __name__ == "__main__":
    asyncio.run(run_optimization("input/watchlist4.txt", "2025-01-01", "2025-12-31"))
