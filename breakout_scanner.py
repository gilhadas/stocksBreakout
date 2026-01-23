#!/usr/bin/env python3
"""
Breakout Scanner - Main Entry Point
Supports swing trading, day trading, and scalping modes
"""

import asyncio
import argparse
import logging
import sys
from ib_insync import IB

from config import IB_PAPER_PORT, IB_LIVE_PORT, IB_HOST, IB_CLIENT_ID, MODES, REGIME_CONFIG
from utils import (
    get_watchlist_from_file,
    get_positions_from_file,
    classify_market_regime,
    setup_logging
)
from orchestrator import ScannerOrchestrator

logger = logging.getLogger(__name__)


async def connect_to_ib(live: bool = False) -> IB:
    """Connect to Interactive Brokers"""
    port = IB_LIVE_PORT if live else IB_PAPER_PORT
    
    ib = IB()
    try:
        await ib.connectAsync(IB_HOST, port, clientId=IB_CLIENT_ID)
        logger.info(f"✓ Connected to IB ({'LIVE' if live else 'PAPER'})")
        
        # Set market data type
        if live:
            ib.reqMarketDataType(1)  # Real-time
        else:
            ib.reqMarketDataType(3)  # Delayed
        
        return ib
    
    except Exception as e:
        logger.error(f"Failed to connect to IB: {e}")
        logger.error("Make sure TWS or IB Gateway is running and API is enabled")
        sys.exit(1)


async def run_scan_mode(orchestrator: ScannerOrchestrator, args):
    """Execute scan mode"""
    # Load watchlist
    watchlist = get_watchlist_from_file(args.file)
    if not watchlist:
        logger.error("No symbols loaded from watchlist")
        return
    
    logger.info(f"Loaded {len(watchlist)} symbols from {args.file}")
    
    # Scan watchlist
    results = await orchestrator.scan_watchlist(
        watchlist=watchlist,
        mode=args.mode,
        timeframe=args.timeframe,
        vol_thresh=args.vol,
        atr_mult=args.atr
    )
    
    # Display and save results
    if results:
        import pandas as pd
        
        logger.info(f"\n{'='*70}")
        logger.info(f" {args.mode.upper()} SIGNALS FOUND: {len(results)}")
        logger.info(f"{'='*70}\n")
        
        df = pd.DataFrame(results).sort_values(by='Vol', ascending=False)
        print(df.to_string(index=False))
        
        orchestrator.save_results(results, args.mode, 'signals')
        
        # Scalping warnings
        if args.mode == 'scalping':
            logger.warning("\n⚠️  SCALPING REMINDERS:")
            logger.warning("   • Exit at target/stop - no exceptions")
            logger.warning("   • Monitor spread widening")
            logger.warning("   • Close all before market close")
            logger.warning("   • Watch for news events")
    else:
        logger.info("No signals found")
    
    # Save rejections
    rejections = orchestrator.get_rejection_reasons()
    if rejections:
        orchestrator.save_results(rejections, args.mode, 'rejections')
        logger.info("   (Showing only signals close to passing)")


async def run_exit_mode(orchestrator: ScannerOrchestrator, args):
    """Execute exit evaluation mode"""
    # Load positions
    positions = get_positions_from_file(args.exit_file)
    if not positions:
        logger.error("No positions loaded")
        return
    
    logger.info(f"Loaded {len(positions)} positions from {args.exit_file}")
    
    # Determine regime
    sample_mode = positions[0]['mode']
    if sample_mode != 'scalping':
        spy_perf, spy_vol = await orchestrator.market_data.get_spy_performance(
            args.timeframe, MODES[sample_mode]['lookback']
        )
        regime = classify_market_regime(spy_perf, spy_vol)
        regime_desc = REGIME_CONFIG[regime]['description']
        logger.info(
            f"Market: SPY {spy_perf:.2%}, Vol {spy_vol:.2f}% | "
            f"Regime: {regime} ({regime_desc})"
        )
    else:
        regime = 'INTRADAY'
    
    # Evaluate exits
    exit_results = await orchestrator.evaluate_exits(positions, regime)
    
    # Display and save results
    if exit_results:
        import pandas as pd
        
        logger.info(f"\n{'='*70}")
        logger.info(f" EXIT EVALUATION: {len(exit_results)} positions")
        logger.info(f"{'='*70}\n")
        
        df = pd.DataFrame(exit_results)
        print(df.to_string(index=False))
        
        orchestrator.save_results(exit_results, args.mode, 'exits')
    else:
        logger.info("No exit decisions generated")


async def main():
    """Main execution flow"""
    parser = argparse.ArgumentParser(
        description='Breakout Scanner for Interactive Brokers',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Swing trading scan
  python main.py watchlist.txt --mode swing
  
  # Day trade scan with custom volume threshold
  python main.py watchlist.txt --mode daytrade --vol 1.5
  
  # Scalping (1min bars)
  python main.py watchlist.txt --mode scalping
  
  # Exit evaluation
  python main.py watchlist.txt --mode swing --exit-file positions.csv
  
  # Live trading (CAREFUL!)
  python main.py watchlist.txt --mode swing --live
        """
    )
    
    parser.add_argument('file', help='Path to watchlist file')
    parser.add_argument(
        '--mode',
        choices=['swing', 'daytrade', 'scalping'],
        default='swing',
        help='Trading mode'
    )
    parser.add_argument('--vol', type=float, help='Volume threshold override')
    parser.add_argument('--atr', type=float, help='ATR multiplier override')
    parser.add_argument('--tf', type=str, help='Timeframe override')
    parser.add_argument(
        '--live',
        action='store_true',
        help='Use live account (default: paper)'
    )
    parser.add_argument(
        '--exit-file',
        type=str,
        help='CSV with positions for exit evaluation'
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging()
    
    # Get timeframe
    if args.tf:
        args.timeframe = args.tf
    else:
        args.timeframe = MODES[args.mode]['default_timeframe']
    
    # Scalping warnings
    if args.mode == 'scalping' and args.live:
        logger.warning("⚠️  SCALPING ON LIVE ACCOUNT!")
        response = input("Type 'YES' to continue: ")
        if response != 'YES':
            logger.info("Aborted by user")
            return
    
    if args.mode == 'scalping' and not args.live:
        logger.warning("⚠️  PAPER MODE = DELAYED DATA (15min lag)")
        logger.warning("    Not suitable for live scalping!")
    
    # Connect to IB
    ib = await connect_to_ib(args.live)
    
    try:
        # Create orchestrator
        orchestrator = ScannerOrchestrator(ib)
        
        # Run appropriate mode
        if args.exit_file:
            await run_exit_mode(orchestrator, args)
        else:
            await run_scan_mode(orchestrator, args)
    
    except Exception as e:
        logger.error(f"Scanner error: {e}", exc_info=True)
    
    finally:
        ib.disconnect()
        logger.info("✓ Disconnected from IB")


if __name__ == "__main__":
    asyncio.run(main())
