
import asyncio
import argparse
import logging
import sys
from datetime import datetime
import pandas as pd
from typing import List, Dict
import itertools
from breakout_scanner import run_simulation_mode, connect_to_ib
from config import MODES

# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s | %(levelname)s | %(message)s',
    stream=sys.stdout
)
logging.getLogger('optimize_strategy').setLevel(logging.INFO)
logger = logging.getLogger(__name__)

class OptimizationRunner:
    def __init__(self, watchlist_file: str, mode: str, start_date: str, end_date: str):
        self.watchlist_file = watchlist_file
        self.mode = mode
        self.start_date = start_date
        self.end_date = end_date
        self.results = []

    async def run_optimization(self, param_grid: Dict[str, List]):
        """Run optimization across parameter grid"""
        # Generate all combinations
        keys = param_grid.keys()
        values = param_grid.values()
        combinations = list(itertools.product(*values))
        
        print(f"🚀 Starting optimization with {len(combinations)} combinations...")
        print(f"📅 Period: {self.start_date} to {self.end_date}")
        print(f"📋 Watchlist: {self.watchlist_file}")
        print("-" * 60)
        print(f"{'#':<3} | {'Vol':<5} | {'ATR':<5} | {'Lookback':<8} | {'Return':<8} | {'Win Rate':<8} | {'Trades':<6}")
        print("-" * 60)

        # Base args mock
        args = argparse.Namespace(
            file=self.watchlist_file,
            mode=self.mode,
            timeframe=MODES[self.mode]['default_timeframe'], # Use default from config
            live=False,
            mock=True, # Use mock connection
            mock_mode='realistic',
            simulate=True,
            sim_start=self.start_date,
            sim_end=self.end_date,
            sim_data_source='yfinance', # Use Yahoo Finance for historical data
            sim_mock=False,
            notify=False,
            cron=False,
            exit_file=None,
            both=False,
            level2=False,
            tf=None
        )

        # Connect to Mock IB once (needed for orchestrator but not used for data if yfinance)
        ib = await connect_to_ib(mock=True, mock_mode='realistic')

        try:
            for i, combo in enumerate(combinations, 1):
                params = dict(zip(keys, combo))
                
                # Update args
                args.vol = params['vol_thresh']
                args.atr = params['atr_mult']
                args.lookback = params['lookback']
                
                # Run simulation
                # Suppress stdout to keep progress clean
                # sys.stdout = open(os.devnull, 'w') 
                # (Actually, better not to suppress entirely, just rely on log level)
                
                try:
                    report = await run_simulation_mode(args, ib, data_source='yfinance')
                except Exception as e:
                    logger.error(f"Simulation failed for {params}: {e}")
                    report = None
                
                # Parse result
                if report:
                    metrics = {
                        'vol_thresh': params['vol_thresh'],
                        'atr_mult': params['atr_mult'],
                        'lookback': params['lookback'],
                        'total_return': report.get('total_return', 0.0),
                        'win_rate': report.get('win_rate', 0.0),
                        'total_trades': report.get('total_trades', 0),
                        'max_drawdown': report.get('max_drawdown', 0.0),
                        'sharpe': report.get('sharpe_ratio', 0.0)
                    }
                else:
                    # No signals found
                    metrics = {
                        'vol_thresh': params['vol_thresh'],
                        'atr_mult': params['atr_mult'],
                        'lookback': params['lookback'],
                        'total_return': 0.0,
                        'win_rate': 0.0,
                        'total_trades': 0,
                        'max_drawdown': 0.0,
                        'sharpe': 0.0
                    }
                
                self.results.append(metrics)
                    
                print(f"{i:<3} | {params['vol_thresh']:<5.1f} | {params['atr_mult']:<5.1f} | {params['lookback']:<8} | "
                      f"{metrics['total_return']:>7.1f}% | {metrics['win_rate']:>7.1f}% | {metrics['total_trades']:<6}")

        finally:
            ib.disconnect()

        self._print_summary()

    def _print_summary(self):
        if not self.results:
            print("\n❌ No results generated.")
            return

        df = pd.DataFrame(self.results)
        
        print("\n" + "=" * 60)
        print("🏆 OPTIMIZATION RESULTS (Top 5 by Return)")
        print("=" * 60)
        print(df.sort_values('total_return', ascending=False).head(5).to_string(index=False))
        
        print("\n" + "=" * 60)
        print("🛡️  SAFEST (Top 5 by Max Drawdown)")
        print("=" * 60)
        print(df.sort_values('max_drawdown', ascending=False).head(5).to_string(index=False))

        # Save to file
        import os
        from pathlib import Path
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = output_dir / f"optimization_results_{timestamp}.csv"
        df.to_csv(filename, index=False)
        print(f"\n💾 Full results saved to {filename}")

async def main():
    # Define parameter grid
    param_grid = {
        'vol_thresh': [1.3, 2.0],
        'atr_mult': [0.5, 1.5],
        'lookback': [15]  # Keep lookback constant to see effect of vol/atr
    }

    # Configuration
    watchlist = "input/watchlist2.txt"
    start_date = "2024-01-01"
    end_date = "2024-12-31" # Full year test
    mode = "swing"

    runner = OptimizationRunner(watchlist, mode, start_date, end_date)
    await runner.run_optimization(param_grid)

if __name__ == "__main__":
    # Fix for Python 3.14 event loop issue
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
        
    asyncio.run(main())
