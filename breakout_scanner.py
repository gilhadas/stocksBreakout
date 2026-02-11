#!/usr/bin/env python3
"""
Breakout Scanner - Main Entry Point
Supports long-term, swing, day trading, and scalping modes
"""

import asyncio
import argparse
import logging
import sys
from pathlib import Path

# Python 3.14 compatibility: Create event loop before importing ib_insync
# This prevents "RuntimeError: There is no current event loop" from eventkit
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_insync import IB

from config import IB_PAPER_PORT, IB_LIVE_PORT, IB_HOST, IB_CLIENT_ID, MODES, REGIME_CONFIG, OUTPUT_DIR
from utils import (
    get_watchlist_from_file,
    get_positions_from_file,
    classify_market_regime,
    setup_logging
)
from orchestrator import ScannerOrchestrator
from notifier import Notifier
from mock_trader import MockIBConnection, MockTrader, SimulationMode

logger = logging.getLogger(__name__)


async def connect_to_ib(live: bool = False, mock: bool = False, mock_mode: str = 'realistic') -> IB:
    """Connect to Interactive Brokers or Mock connection.

    Connection cascade: LIVE port → PAPER port → None (yfinance fallback)
    """
    if mock:
        logger.info(f"Using MOCK trading mode ({mock_mode})")
        ib = MockIBConnection(mode=mock_mode)
        await ib.connectAsync(IB_HOST, IB_PAPER_PORT, clientId=IB_CLIENT_ID)
        return ib

    # Build port order: if --live, try LIVE first then PAPER; otherwise PAPER first then LIVE
    if live:
        ports = [(IB_LIVE_PORT, 'LIVE', 1), (IB_PAPER_PORT, 'PAPER', 3)]
    else:
        ports = [(IB_PAPER_PORT, 'PAPER', 3), (IB_LIVE_PORT, 'LIVE', 1)]

    for port, label, mkt_data_type in ports:
        ib = IB()
        try:
            await ib.connectAsync(IB_HOST, port, clientId=IB_CLIENT_ID)
            ib.reqMarketDataType(mkt_data_type)
            logger.info(f"Connected to IB {label} (port {port})")
            return ib
        except Exception as e:
            logger.warning(f"IB {label} (port {port}) failed: {e}")

    logger.warning("All IB ports unavailable — falling back to yfinance (15-min delayed data)")
    return None


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
        atr_mult=args.atr,
        lookback=args.lookback,
        detect_bounces=getattr(args, 'bounce', False)
    )
    
    # Add sector name to every signal
    if results:
        from sentiment import get_sector_for_ticker
        for sig in results:
            ticker = sig.get('Symbol') or sig.get('symbol', '')
            if ticker:
                sig['Sector'] = get_sector_for_ticker(ticker)

    # Enrich signals with sentiment if requested
    if results and getattr(args, 'sentiment', False):
        from sentiment import check_sentiment
        logger.info("Enriching signals with sentiment data...")
        for sig in results:
            ticker = sig.get('Symbol') or sig.get('symbol', '')
            if ticker:
                sent = check_sentiment(ticker)
                sig['Sentiment'] = sent['sentiment']
                sig['Buzz'] = sent['buzz_score']

    # Display and save results
    if results:
        import pandas as pd

        logger.info(f"\n{'='*70}")
        logger.info(f" {args.mode.upper()} SIGNALS FOUND: {len(results)}")
        logger.info(f"{'='*70}\n")
        
        df = pd.DataFrame(results).sort_values(by='Vol', ascending=False)
        print(df.to_string(index=False))
        
        output_file = orchestrator.save_results(results, args.mode, 'signals')

        # Auto-append to positions file if requested
        if getattr(args, 'auto_positions', None):
            from utils import append_signals_to_positions
            append_signals_to_positions(results, args.auto_positions, args.mode)

        # Export PREMIUM tickers to watchlist file for re-evaluation scans
        if getattr(args, 'export_premium', None):
            premium_symbols = [
                sig.get('Symbol') or sig.get('symbol', '')
                for sig in results
                if sig.get('Quality') == 'PREMIUM'
            ]
            with open(args.export_premium, 'w') as f:
                f.write('\n'.join(premium_symbols))
            logger.info(f"Exported {len(premium_symbols)} PREMIUM tickers to {args.export_premium}")

        # Send notifications with CSV attachment
        mode_desc = MODES[args.mode]['description']
        watchlist_name = Path(args.file).stem  # Get filename without extension
        notifier.send_all(
            subject=f"🚨 {args.mode.upper()} Breakout Signals [{watchlist_name}]",
            message=f"{len(results)} {mode_desc} breakout signals detected from {watchlist_name}",
            signals=results,
            csv_path=output_file
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
        # Clear premium export file so subsequent scans don't use stale tickers
        if getattr(args, 'export_premium', None):
            with open(args.export_premium, 'w') as f:
                f.write('')
            logger.info(f"No signals — cleared {args.export_premium}")

    # Show and save rejections (near misses)
    rejections = orchestrator.get_rejection_reasons()
    if rejections:
        logger.info(f"\n{'='*90}")
        logger.info(f" NEAR MISSES: {len(rejections)} stocks broke out but were rejected")
        logger.info(f"{'='*90}")
        
        # Show top 10 rejections with score details
        rej_df = pd.DataFrame(rejections[:10])
        if 'momentum' in rej_df.columns:
            display_cols = ['symbol', 'price', 'vol_ratio', 'momentum', 'conviction', 'rsi', 'reasons']
            display_cols = [c for c in display_cols if c in rej_df.columns]
            print(rej_df[display_cols].to_string(index=False))
        else:
            print(rej_df.to_string(index=False))
        
        if len(rejections) > 10:
            logger.info(f"   ... and {len(rejections) - 10} more")
        
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


async def run_monitor_mode(orchestrator: ScannerOrchestrator, args, notifier):
    """Monitor open positions: fetch current prices, alert on drops"""
    import pandas as pd

    # Load positions from all specified files
    all_positions = []
    for fpath in args.monitor.split(','):
        fpath = fpath.strip()
        if not fpath:
            continue
        positions = get_positions_from_file(fpath)
        if positions:
            logger.info(f"Loaded {len(positions)} positions from {fpath}")
            all_positions.extend(positions)
        else:
            logger.info(f"No positions in {fpath}")

    if not all_positions:
        logger.info("No positions to monitor")
        return

    # Deduplicate by symbol (keep first occurrence)
    seen = set()
    unique_positions = []
    for pos in all_positions:
        if pos['symbol'] not in seen:
            seen.add(pos['symbol'])
            unique_positions.append(pos)
    all_positions = unique_positions

    logger.info(f"\n{'='*70}")
    logger.info(f" PORTFOLIO MONITOR: {len(all_positions)} positions")
    logger.info(f"{'='*70}\n")

    # Fetch current prices
    price_map = {}
    for pos in all_positions:
        symbol = pos['symbol']
        price = await orchestrator.market_data.get_current_price(symbol)
        if price is None:
            logger.warning(f"Could not fetch price for {symbol}")
        else:
            price_map[symbol] = price

    # Trail stops upward: update each position file
    from utils import update_position_stops
    for fpath in args.monitor.split(','):
        fpath = fpath.strip()
        if not fpath:
            continue
        updated = update_position_stops(fpath, price_map)
        for u in updated:
            logger.info(f"  ↑ {u['symbol']} stop: ${u['old_stop']:.2f} → ${u['new_stop']:.2f} (price ${u['price']:.2f})")

    # Reload positions after stop updates
    all_positions = []
    for fpath in args.monitor.split(','):
        fpath = fpath.strip()
        if fpath:
            all_positions.extend(get_positions_from_file(fpath))
    # Deduplicate again
    seen = set()
    unique_positions = []
    for pos in all_positions:
        if pos['symbol'] not in seen:
            seen.add(pos['symbol'])
            unique_positions.append(pos)
    all_positions = unique_positions

    # Calculate status with updated stops
    dashboard = []
    alerts = []

    for pos in all_positions:
        symbol = pos['symbol']
        price = price_map.get(symbol)
        if price is None:
            continue

        entry = pos['entry']
        stop = pos['stop']
        target = pos['target']

        pnl_pct = ((price - entry) / entry) * 100
        dist_to_stop_pct = ((price - stop) / price) * 100 if price > 0 else 0
        progress_pct = ((price - entry) / (target - entry)) * 100 if target != entry else 0

        # Classify status (NEAR_STOP at 0.5% since stops trail at 1%)
        if price <= stop:
            status = 'HIT_STOP'
        elif dist_to_stop_pct < 0.5:
            status = 'NEAR_STOP'
        elif pnl_pct < -2:
            status = 'FALLING'
        else:
            status = 'OK'

        row = {
            'Symbol': symbol,
            'mode': pos['mode'],
            'current': price,
            'entry': entry,
            'stop': stop,
            'target': target,
            'pnl_pct': pnl_pct,
            'dist_stop': dist_to_stop_pct,
            'progress': progress_pct,
            'status': status,
        }
        dashboard.append(row)

        if status != 'OK':
            alerts.append(row)

    # Display dashboard
    if dashboard:
        df = pd.DataFrame(dashboard)
        display_cols = ['Symbol', 'mode', 'current', 'entry', 'stop', 'target',
                        'pnl_pct', 'dist_stop', 'status']
        df_display = df[display_cols].copy()
        df_display['pnl_pct'] = df_display['pnl_pct'].apply(lambda x: f"{x:+.2f}%")
        df_display['dist_stop'] = df_display['dist_stop'].apply(lambda x: f"{x:.1f}%")
        df_display['current'] = df_display['current'].apply(lambda x: f"${x:.2f}")
        df_display['entry'] = df_display['entry'].apply(lambda x: f"${x:.2f}")
        df_display['stop'] = df_display['stop'].apply(lambda x: f"${x:.2f}")
        df_display['target'] = df_display['target'].apply(lambda x: f"${x:.2f}")
        print(df_display.to_string(index=False))

    # Send alerts if positions need attention
    if alerts:
        logger.warning(f"\n⚠️  {len(alerts)} position(s) need attention!")
        notifier.send_monitor_alert(alerts, dashboard)
    else:
        logger.info("\nAll positions OK")


async def run_simulation_mode(args, ib, data_source='auto'):
    """Run historical simulation on watchlist with real data"""
    from datetime import datetime
    import pandas as pd
    from yfinance_adapter import YFinanceAdapter
    
    logger.info("=" * 70)
    logger.info(" HISTORICAL SIMULATION MODE")
    logger.info("=" * 70)
    
    # Load watchlist
    watchlist = get_watchlist_from_file(args.file)
    if not watchlist:
        logger.error("No symbols loaded from watchlist")
        return
    
    logger.info(f"📊 Loaded {len(watchlist)} symbols from {args.file}")
    logger.info(f"📊 Simulation period: {args.sim_start} to {args.sim_end}")
    logger.info(f"📊 Mode: {args.mode.upper()}")
    
    # Determine data source
    use_yfinance = False
    if data_source == 'yfinance':
        use_yfinance = True
        logger.info(f"📊 Data source: Yahoo Finance (yfinance)")
    elif data_source == 'mock':
        logger.info(f"📊 Data source: Mock (random data)")
    elif data_source == 'auto' or data_source == 'ib':
        # Check if IB is actually connected (not mock)
        if hasattr(ib, 'mode') and ib.mode:
            # It's a mock connection, use yfinance instead
            use_yfinance = True
            logger.info(f"📊 Data source: Yahoo Finance (IB not available)")
        else:
            logger.info(f"📊 Data source: Interactive Brokers")
    
    # Create simulation
    # Import portfolio config
    from config import PORTFOLIO
    
    # Create simulation with portfolio config
    sim = SimulationMode(
        start_date=args.sim_start,
        end_date=args.sim_end,
        initial_capital=PORTFOLIO['initial_capital'],
        max_position_pct=PORTFOLIO['max_position_pct'],
        max_risk_pct=PORTFOLIO['max_risk_pct'],
        use_trailing_stop=PORTFOLIO.get('use_trailing_stop', False),
        trailing_stop_atr_mult=PORTFOLIO.get('trailing_stop_atr_mult', 2.0),
        trailing_stop_activation_pct=PORTFOLIO.get('trailing_stop_activation_pct', 0.0)
    )
    
    # Create orchestrator or yfinance adapter
    if use_yfinance:
        yf_adapter = YFinanceAdapter()
        logger.info("📊 Using Yahoo Finance for historical data")
    else:
        orchestrator = ScannerOrchestrator(ib)
    
    # Get timeframe
    timeframe = args.timeframe
    
    logger.info("📊 Scanning historical data for breakout signals...")
    
    # Generate signals by scanning historical data
    all_signals = []
    
    # Parse dates
    start_date = pd.to_datetime(args.sim_start)
    end_date = pd.to_datetime(args.sim_end)
    
    # For each symbol, scan historical data and find breakouts
    all_historical_data = {}
    
    for idx, symbol in enumerate(watchlist, 1):
        print(f"[{idx}/{len(watchlist)}] Scanning {symbol:6}...", end="\r")
        
        try:
            # Get historical data - need extra history for 200-day SMA and other indicators
            # Fetch from 2 years before simulation start to have enough context
            import datetime
            sim_start_dt = pd.to_datetime(args.sim_start)
            data_start = (sim_start_dt - pd.DateOffset(years=2)).strftime('%Y-%m-%d')
            
            if use_yfinance:
                df = yf_adapter.get_historical_data(
                    symbol, timeframe, 
                    start_date=data_start,  # Start 2 years earlier for context
                    end_date=args.sim_end
                )
            else:
                df = await orchestrator.market_data.get_historical_data(symbol, timeframe)
            
            # Cache full historical data for later use
            if df is not None:
                all_historical_data[symbol] = df
            
            if df is None or len(df) < 50:
                continue
            
            # Filter to simulation period for signal generation
            df_sim = df[(df.index >= start_date) & (df.index <= end_date)]
            
            if len(df_sim) < 10:
                continue
            
            # Scan each bar in the period for breakout signals
            for i in range(len(df_sim)):
                # Get data up to this point
                current_date = df_sim.index[i]
                df_up_to_date = df[df.index <= current_date]
                
                if len(df_up_to_date) < 50:
                    continue
                
                # Detect breakout
                if use_yfinance:
                    # Create temporary detector for yfinance data
                    from scanner import BreakoutDetector
                    detector = BreakoutDetector()
                    signal = detector.detect(
                        df_up_to_date,
                        symbol,
                        args.mode,
                        timeframe,
                        spy_perf=0.0,
                        regime='NORMAL',
                        use_scoring=True,
                        use_legacy_momentum=False,
                        vol_thresh=args.vol,
                        atr_mult=args.atr,
                        lookback=args.lookback
                    )
                else:
                    signal = orchestrator.detector.detect(
                        df_up_to_date,
                        symbol,
                        args.mode,
                        timeframe,
                        spy_perf=0.0,
                        regime='NORMAL',
                        use_scoring=True,
                        use_legacy_momentum=False,
                        vol_thresh=args.vol,
                        atr_mult=args.atr,
                        lookback=args.lookback
                    )
                
                if signal:
                    # Convert to simulation signal format (quantity will be calculated by risk management)
                    sim_signal = {
                        'date': current_date.strftime('%Y-%m-%d'),
                        'symbol': symbol,
                        'action': 'BUY',
                        'price': float(signal['Price']),
                        'stop_loss': float(signal['Stop']),
                        'take_profit': float(signal['Target'])
                    }
                    all_signals.append(sim_signal)
                    
                    # Get period start price for context
                    period_start_price = df_sim.iloc[0]['close'] if len(df_sim) > 0 else signal['Price']
                    
                    # Calculate gain from signal entry to end of simulation period
                    # This shows how the trade would have performed
                    end_price = df_sim.iloc[-1]['close'] if len(df_sim) > 0 else signal['Price']
                    gain_from_entry = ((end_price / signal['Price']) - 1) * 100
                    
                    logger.info(
                        f"   ✓ Signal: {symbol} @ ${signal['Price']:.2f} on {current_date.date()} "
                        f"(period start: ${period_start_price:.2f}, gain from entry: {gain_from_entry:+.1f}%, quality: {signal['Quality']})"
                    )
                    
                    # Don't break - continue scanning for more signals
                    # (removed the break statement to capture all signals)
        
        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"Failed to scan {symbol}: {e}")
            continue
    
    print()  # Clear progress line
    
    logger.info(f"📊 Generated {len(all_signals)} signals from historical data")
    
    if not all_signals:
        logger.warning("No signals found in simulation period. Try:")
        logger.warning("  - Longer date range")
        logger.warning("  - Different mode (swing, daytrade, etc.)")
        logger.warning("  - More symbols in watchlist")
        return
    
    # Collect end prices and historical data for each symbol (for closing positions and trailing stops)
    end_prices = {}
    historical_data = {}
    
    for symbol in set(sig['symbol'] for sig in all_signals):
        try:
            # key in all_historical_data might be different if symbol format changed? 
            # But we used 'symbol' variable in loop, so it should be same.
            df = all_historical_data.get(symbol)
            
            if df is None:
                # Fallback if not cached (shouldn't happen if signal generated)
                if use_yfinance:
                     df = yf_adapter.get_historical_data(symbol, timeframe, start_date=data_start, end_date=args.sim_end)
                else:
                     df = await orchestrator.market_data.get_historical_data(symbol, timeframe)
            
            if df is not None and len(df) > 0:
                # Get the last price in the simulation period
                df_sim = df[(df.index >= start_date) & (df.index <= end_date)]
                if len(df_sim) > 0:
                    end_prices[symbol] = float(df_sim.iloc[-1]['close'])
                    # Store full historical data for trailing stop calculations
                    historical_data[symbol] = df
        except Exception as e:
            logger.debug(f"Could not get end price for {symbol}: {e}")
    
    # Run simulation with end prices and historical data
    report = sim.run_simulation(all_signals, end_prices=end_prices, historical_data=historical_data)
    
    # Save report
    from pathlib import Path
    sim_dir = Path(OUTPUT_DIR, 'simulation_report')
    sim_dir.mkdir(parents=True, exist_ok=True)
    
    report_file = sim_dir / f'simulation_report_{args.sim_start}_{args.sim_end}.json'
    sim.trader.save_report(str(report_file))
    
    logger.info("✓ Simulation complete!")
    
    return report




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
  
  # Historical simulation (scans watchlist for signals)
  python main.py watchlist.txt --mode swing --simulate --sim-start 2025-01-01 --sim-end 2025-12-31
  
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
    parser.add_argument('--lookback', type=int, help='Lookback period override')
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
    parser.add_argument(
        '--level2',
        action='store_true',
        help='Enable Level 2 (Market Depth) analysis'
    )
    parser.add_argument(
        '--mock',
        action='store_true',
        help='Use mock trading (no real IB connection needed)'
    )
    parser.add_argument(
        '--mock-mode',
        choices=['realistic', 'optimistic', 'pessimistic'],
        default='realistic',
        help='Mock trading simulation mode'
    )
    parser.add_argument(
        '--simulate',
        action='store_true',
        help='Run historical simulation'
    )
    parser.add_argument(
        '--sim-start',
        type=str,
        help='Simulation start date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--sim-end',
        type=str,
        help='Simulation end date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--sim-data-source',
        choices=['auto', 'ib', 'yfinance', 'mock'],
        default='auto',
        help='Data source for simulation: auto (try IB, fallback to yfinance), ib (IB only), yfinance (Yahoo Finance), mock (random data)'
    )
    parser.add_argument(
        '--sim-mock',
        action='store_true',
        help='Use mock data for simulation (shorthand for --sim-data-source mock)'
    )
    parser.add_argument(
        '--bounce',
        action='store_true',
        help='Also detect bounce/recovery signals (oversold stocks showing strong recovery)'
    )
    parser.add_argument(
        '--sector-buzz',
        action='store_true',
        help='Run sector buzz analysis before scan (market context report)'
    )
    parser.add_argument(
        '--sentiment',
        action='store_true',
        help='Enrich each signal with web sentiment data (requires TAVILY_API_KEY)'
    )
    parser.add_argument(
        '--auto-positions',
        type=str,
        metavar='FILE',
        help='Auto-append PREMIUM signals to a positions CSV (for mock/test exit evaluation)'
    )
    parser.add_argument(
        '--export-premium',
        type=str,
        metavar='FILE',
        help='Export PREMIUM signal tickers to a watchlist file (for subsequent re-evaluation scans)'
    )
    parser.add_argument(
        '--monitor',
        type=str,
        metavar='FILES',
        help='Monitor positions for price drops. Comma-separated CSV files. '
             'Example: --monitor input/positions_swing_mock.csv,input/positions_daytrade_mock.csv'
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
    if args.mode == 'scalping' and args.live and not args.mock:
        logger.warning("⚠️  SCALPING ON LIVE ACCOUNT!")
        if not args.cron:
            response = input("Type 'YES' to continue: ")
            if response != 'YES':
                logger.info("Aborted by user")
                return
    
    if args.mode == 'scalping' and not args.live and not args.cron and not args.mock:
        logger.warning("⚠️  PAPER MODE = DELAYED DATA (15min lag)")
        logger.warning("    Not suitable for live scalping!")
    
    # Mock mode info
    if args.mock:
        logger.info("🔧 MOCK TRADING MODE")
        logger.info(f"   Simulation: {args.mock_mode}")
        logger.info("   No real orders will be placed")
        logger.info("   Perfect for testing strategies!")
        
        # Scalping warning for mock mode
        if args.mode == 'scalping':
            logger.warning("")
            logger.warning("⚠️  SCALPING + MOCK MODE WARNING:")
            logger.warning("   • yfinance provides limited 1-min data (5 days only)")
            logger.warning("   • No real-time bid/ask spreads available")
            logger.warning("   • VWAP calculations may be inaccurate")
            logger.warning("   • Signals may be unreliable or missing")
            logger.warning("")
            logger.warning("   For accurate scalping, use live IB connection:")
            logger.warning("   python3 breakout_scanner.py input/watchlist.txt --mode scalping --live")
            logger.warning("")
    
    # Simulation mode
    if args.simulate:
        if not args.sim_start or not args.sim_end:
            logger.error("❌ Simulation requires --sim-start and --sim-end dates")
            logger.error("   Example: --simulate --sim-start 2025-01-01 --sim-end 2025-12-31")
            return
        
        logger.info("📊 HISTORICAL SIMULATION MODE")
        logger.info(f"   Period: {args.sim_start} to {args.sim_end}")
        logger.info(f"   Mode: {args.mode.upper()}")
        
        # Determine data source
        data_source = 'mock' if args.sim_mock else args.sim_data_source
        
        # Connect to IB if needed
        if data_source == 'yfinance':
            # yfinance doesn't need IB connection, use mock placeholder
            ib = await connect_to_ib(args.live, mock=True, mock_mode='realistic')
        elif data_source == 'mock':
            # Mock mode
            ib = await connect_to_ib(args.live, mock=True, mock_mode='realistic')
        else:
            # 'ib' or 'auto' - try real IB connection, fallback to mock (which triggers yfinance)
            try:
                ib = await connect_to_ib(args.live, mock=False)
            except Exception as e:
                logger.warning(f"Could not connect to IB: {e}")
                logger.info("Falling back to yfinance data source")
                ib = await connect_to_ib(args.live, mock=True, mock_mode='realistic')
                data_source = 'yfinance'
        
        try:
            # Run simulation with selected data source
            return await run_simulation_mode(args, ib, data_source=data_source)
        finally:
            ib.disconnect()
            logger.info("✓ Disconnected from IB")
        
        return
    
    # Initialize notifier
    notifier = Notifier() if args.notify or args.cron else None
    if not notifier:
        # Create dummy notifier that does nothing
        class DummyNotifier:
            def send_all(self, *args, **kwargs): pass
            def send_exit_notification(self, *args, **kwargs): pass
            def send_monitor_alert(self, *args, **kwargs): pass
        notifier = DummyNotifier()
    
    # Sector buzz pre-scan report
    if getattr(args, 'sector_buzz', False):
        from sentiment import get_sector_buzz, format_sector_buzz
        logger.info("Running sector buzz analysis...")
        buzz_data = get_sector_buzz()
        print(format_sector_buzz(buzz_data))
        print()

    # Connect to IB (returns None if IB unavailable)
    ib = await connect_to_ib(args.live, args.mock, args.mock_mode)
    yf_fallback = (ib is None)
    if yf_fallback:
        logger.warning("Running with yfinance only (15-min delayed data)")

    try:
        # Create orchestrator with yfinance fallback if IB unavailable
        orchestrator = ScannerOrchestrator(ib, yf_fallback=yf_fallback)

        # Determine execution mode
        if args.monitor:
            # Portfolio monitoring mode
            await run_monitor_mode(orchestrator, args, notifier)
        elif args.both and args.exit_file:
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
        if ib is not None:
            ib.disconnect()
            logger.info("Disconnected from IB")


if __name__ == "__main__":
    asyncio.run(main())
