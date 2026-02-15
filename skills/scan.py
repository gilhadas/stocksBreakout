#!/usr/bin/env python3
"""
Claude Skill: /scan
Run trading scans with intelligent defaults
"""

import asyncio
import argparse
import logging
import os
from pathlib import Path
from typing import Optional

# Load .env from project root before any config imports
_env_file = Path(__file__).parent.parent / '.env'
if _env_file.exists():
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                # Strip 'export ' prefix if present
                if line.startswith('export '):
                    line = line[7:]
                key, _, value = line.partition('=')
                # Strip surrounding quotes
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key.strip(), value)

logger = logging.getLogger(__name__)


async def run_scan_skill(
    mode: str = 'swing',
    watchlist: Optional[str] = None,
    premium_only: bool = False,
    sentiment: bool = False,
    auto_positions: Optional[str] = None,
    notify: bool = False,
) -> dict:
    """
    Execute a breakout scan with smart defaults

    Args:
        mode: Trading mode (swing, daytrade, longterm, scalping)
        watchlist: Watchlist file (defaults to S&P_500.txt)
        premium_only: Export only PREMIUM signals
        sentiment: Enrich signals with sentiment data
        auto_positions: Auto-append signals to positions CSV
        notify: Send notifications

    Returns:
        dict with results: {signals_count, file, regime, summary}
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from breakout_scanner import connect_to_ib, main
    from orchestrator import ScannerOrchestrator
    from notifier import Notifier
    from utils import get_watchlist_from_file, classify_market_regime
    from config import MODES, IB_HOST, IB_PAPER_PORT, IB_CLIENT_ID

    # Default to S&P 500 if no watchlist specified
    if not watchlist:
        watchlist = 'input/S&P_500.txt'

    # Resolve watchlist path
    watchlist_path = Path(watchlist)
    if not watchlist_path.exists():
        watchlist_path = Path(__file__).parent.parent / watchlist

    if not watchlist_path.exists():
        return {
            'success': False,
            'error': f'Watchlist not found: {watchlist}',
            'signals_count': 0
        }

    ib = None
    try:
        # Connect to IB
        ib = await connect_to_ib(live=False)
        yf_fallback = (ib is None)

        # Create orchestrator
        orchestrator = ScannerOrchestrator(ib, yf_fallback=yf_fallback)

        # Create notifier
        notifier = Notifier() if notify else None

        # Load watchlist
        watchlist_symbols = get_watchlist_from_file(str(watchlist_path))
        if not watchlist_symbols:
            return {
                'success': False,
                'error': 'No symbols loaded from watchlist',
                'signals_count': 0
            }

        logger.info(f"Loaded {len(watchlist_symbols)} symbols from {watchlist_path.name}")

        # Scan
        timeframe = MODES[mode]['default_timeframe']
        lookback = MODES[mode]['lookback']

        results = await orchestrator.scan_watchlist(
            watchlist=watchlist_symbols,
            mode=mode,
            timeframe=timeframe,
            lookback=lookback,
            detect_bounces=True
        )

        # Get market regime
        spy_perf, spy_vol = await orchestrator.market_data.get_spy_performance(timeframe, lookback)
        regime = classify_market_regime(spy_perf, spy_vol)

        # Filter if premium_only
        if premium_only:
            results = [r for r in results if r.get('Quality') == 'PREMIUM']

        # Save results
        output_file = orchestrator.save_results(results, mode, 'signals')

        # Auto-append if requested
        if auto_positions and results:
            from utils import append_signals_to_positions
            append_signals_to_positions(results, auto_positions, mode)

        # Send notifications
        if notifier and results:
            watchlist_name = watchlist_path.stem
            notifier.send_all(
                subject=f"🚨 {mode.upper()} Breakout Signals [{watchlist_name}]",
                message=f"{len(results)} {mode} breakout signals detected from {watchlist_name}",
                signals=results,
                csv_path=str(output_file) if output_file else None
            )
            logger.info(f"Notifications sent for {len(results)} signals")

        # Summary
        summary = f"{len(results)} signals found"
        if regime:
            summary += f" | Regime: {regime} (SPY {spy_perf:+.2%})"

        return {
            'success': True,
            'signals_count': len(results),
            'file': str(output_file),
            'regime': regime,
            'spy_performance': f"{spy_perf:.2%}",
            'summary': summary,
            'notified': notify and bool(results),
            'results': results[:5] if results else []  # Top 5 for display
        }

    except Exception as e:
        logger.error(f"Scan error: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'signals_count': 0
        }

    finally:
        if ib:
            ib.disconnect()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run trading scan')
    parser.add_argument('--mode', default='swing', help='Trading mode')
    parser.add_argument('--watchlist', help='Watchlist file')
    parser.add_argument('--premium', action='store_true', help='PREMIUM signals only')
    parser.add_argument('--sentiment', action='store_true', help='Include sentiment')
    parser.add_argument('--auto-positions', help='Auto-append to positions file')
    parser.add_argument('--notify', action='store_true', help='Send notifications')

    args = parser.parse_args()

    result = asyncio.run(run_scan_skill(
        mode=args.mode,
        watchlist=args.watchlist,
        premium_only=args.premium,
        sentiment=args.sentiment,
        auto_positions=args.auto_positions,
        notify=args.notify
    ))

    import json
    print(json.dumps(result, indent=2))
