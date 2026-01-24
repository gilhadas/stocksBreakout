#!/usr/bin/env python3
"""
Breakout Scanner - Main Entry Point
Supports long-term, swing, day trading, and scalping modes
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
from notifier import Notifier

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


async def run_scan_mode(orchestrator: ScannerOrchestrator, args, notifier: Notifier):
    """Execute scan mode"""
    # Load watchlist
    watchlist = get_watchlist_from_file(args.file)
    if not watchlist:
        logger.error("No symbols loaded from watchlist")
        return []
    
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
        
        output_file = orchestrator.save_results(results, args.mode, 'signals')
        
        # Send notifications
        mode_desc = MODES[args.mode]['description']
        notifier.send_all(
            subject=f"🚨 {args.mode.upper()} Breakout Signals",
            message=f"{len(results)} {mode_desc} breakout signals detected",
            signals=results
        )
        
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
        logger.info("   (Saved near-miss signals for analysis)")
    
    return results


async def run_exit_mode(orchestrator: ScannerOrchestrator, args, notifier: Notifier):
    """Execute exit evaluation mode"""
    # Load positions
    positions = get_positions_from_file(args.exit_file)
    if not positions:
        logger.error("No positions loaded")
        return []
    
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
        
        # Send exit notifications
        notifier.send_exit_notification(exit_results)
    else:
        logger.info("No exit decisions generated")
    
    return exit_results


async def run_combined_mode(orchestrator: ScannerOrchestrator, args, notifier: Notifier):
    """Run both scan and exit in same execution"""
    logger.info("="*70)
    logger.info(" COMBINED MODE: BREAKOUT SCAN + EXIT EVALUATION")
    logger.info("="*70)
    
    # Run breakout scan
    logger.info("\n[1/2] Running breakout scan...")
    scan_results = await run_scan_mode(orchestrator, args, notifier)
    
    # Run exit evaluation
    logger.info("\n[2/2] Running exit evaluation...")
    exit_results = await run_exit_mode(orchestrator, args, notifier)
    
    # Summary
    logger.info(f"\n{'='*70}")
    logger.info(" COMBINED SCAN SUMMARY")
    logger.info(f"{'='*70}")
    logger.info(f"New signals found: {len(scan_results)}")
    logger.info(f"Positions evaluated: {len(exit_results)}")
    
    actionable_exits = [r for r in exit_results if r['Action'] != 'HOLD']
    if actionable_exits:
        logger.info(f"⚠️  {len(actionable_exits)} positions need attention!")


async def main():
    """Main execution flow"""
    parser = argparse.ArgumentParser(
        description='Breakout Scanner for Interactive Brokers',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Long-term position trading (weekly bars)
  python main.py watchlist.txt --mode longterm
  
  # Swing trading scan
  python main.py watchlist.txt --mode swing
  
  # Day trade scan with custom volume threshold
  python main.py watchlist.txt --mode daytrade --vol 1.5
  
  # Scalping (1min bars)
  python main.py watchlist.txt --mode scalping
  
  # Exit evaluation only
  python main.py watchlist.txt --mode swing --exit-file positions.csv
  
  # Combined: scan + exit evaluation
  python main.py watchlist.txt --mode swing --exit-file positions.csv --both
  
  # Cron mode (silent, notifications only)
  python main.py watchlist.txt --mode swing --cron
  
  # Live trading (CAREFUL!)
  python main.py watchlist.txt --mode swing --live
        """
    )
    
    parser.add_argument('file', help='Path to watchlist file')
    parser.add_argument(
        '--mode',
        choices=['longterm', 'swing', 'daytrade', 'scalping'],
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
    parser.add_argument(
        '--both',
        action='store_true',
        help='Run both breakout scan and exit evaluation'
    )
    parser.add_argument(
        '--cron',
        action='store_true',
        help='Cron mode: minimal output, send notifications only'
    )
    parser.add_argument(
        '--notify',
        action='store_true',
        help='Enable notifications (requires config.py setup)'
    )
    
    args = parser.parse_args()
    
    # Setup logging
    if args.cron:
        # Cron mode: only errors to console, everything to log file
        import logging
        from pathlib import Path
        from config import OUTPUT_DIR
        from datetime import datetime
        
        log_dir = Path(OUTPUT_DIR, 'logs')
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f'scanner_{datetime.now():%Y%m%d}.log'
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s | %(levelname)s | %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stderr)  # Only errors to console
            ]
        )
        logging.getLogger().handlers[1].setLevel(logging.ERROR)
        logging.getLogger('ib_insync').setLevel(logging.WARNING)
    else:
        setup_logging()
    
    # Get timeframe
    if args.tf:
        args.timeframe = args.tf
    else:
        args.timeframe = MODES[args.mode]['default_timeframe']
    
    # Scalping warnings
    if args.mode == 'scalping' and args.live:
        logger.warning("⚠️  SCALPING ON LIVE ACCOUNT!")
        if not args.cron:
            response = input("Type 'YES' to continue: ")
            if response != 'YES':
                logger.info("Aborted by user")
                return
    
    if args.mode == 'scalping' and not args.live and not args.cron:
        logger.warning("⚠️  PAPER MODE = DELAYED DATA (15min lag)")
        logger.warning("    Not suitable for live scalping!")
    
    # Initialize notifier
    notifier = Notifier() if args.notify or args.cron else None
    if not notifier:
        # Create dummy notifier that does nothing
        class DummyNotifier:
            def send_all(self, *args, **kwargs): pass
            def send_exit_notification(self, *args, **kwargs): pass
        notifier = DummyNotifier()
    
    # Connect to IB
    ib = await connect_to_ib(args.live)
    
    try:
        # Create orchestrator
        orchestrator = ScannerOrchestrator(ib)
        
        # Determine execution mode
        if args.both and args.exit_file:
            # Combined mode
            await run_combined_mode(orchestrator, args, notifier)
        elif args.exit_file:
            # Exit evaluation only
            await run_exit_mode(orchestrator, args, notifier)
        else:
            # Breakout scan only
            await run_scan_mode(orchestrator, args, notifier)
    
    except Exception as e:
        logger.error(f"Scanner error: {e}", exc_info=True)
        if args.notify or args.cron:
            notifier.send_all(
                subject="❌ Scanner Error",
                message=f"Scanner encountered an error: {str(e)}"
            )
    
    finally:
        ib.disconnect()
        logger.info("✓ Disconnected from IB")


if __name__ == "__main__":
    asyncio.run(main())
