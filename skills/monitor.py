#!/usr/bin/env python3
"""
Claude Skill: /monitor
Portfolio monitoring with real-time price tracking
"""

import asyncio
import argparse
import logging
from pathlib import Path
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


async def run_monitor_skill(
    positions_files: Optional[List[str]] = None,
    interval: int = 15,  # minutes
    once: bool = False,  # Run once instead of continuous
) -> dict:
    """
    Monitor open positions for price drops and stop loss alerts

    Args:
        positions_files: CSV files with open positions
        interval: Check interval in minutes
        once: Run once instead of continuous loop

    Returns:
        dict with monitoring results: {positions, alerts, dashboard}
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    try:
        from breakout_scanner import connect_to_ib
        from orchestrator import ScannerOrchestrator
        from utils import get_positions_from_file

        # Default: try portfolio.json first, fall back to CSV files
        use_portfolio = False
        all_positions = []

        if not positions_files or positions_files == ['portfolio']:
            try:
                from portfolio import Portfolio
                p = Portfolio()
                all_positions = p.get_positions_as_exit_format()
                if all_positions:
                    use_portfolio = True
                    logger.info(f"Loaded {len(all_positions)} positions from portfolio.json")
            except Exception:
                pass

            if not use_portfolio:
                positions_files = [
                    'input/positions_swing_mock.csv',
                    'input/positions_daytrade_mock.csv'
                ]

        if not use_portfolio:
            # Resolve CSV paths
            resolved_files = []
            for f in positions_files:
                fpath = Path(f)
                if not fpath.exists():
                    fpath = Path(__file__).parent.parent / f
                if fpath.exists():
                    resolved_files.append(str(fpath))

            if not resolved_files:
                return {
                    'success': False,
                    'error': 'No position files found',
                    'positions_count': 0
                }

            for file in resolved_files:
                positions = get_positions_from_file(file)
                all_positions.extend(positions)

        # Connect to IB
        ib = await connect_to_ib(live=False)
        yf_fallback = (ib is None)

        # Create orchestrator
        orchestrator = ScannerOrchestrator(ib, yf_fallback=yf_fallback)

        if not all_positions:
            return {
                'success': True,
                'message': 'No open positions to monitor',
                'positions_count': 0
            }

        logger.info(f"Monitoring {len(all_positions)} positions")

        # Fetch current prices
        alerts = []
        dashboard = []

        for pos in all_positions:
            symbol = pos['symbol']
            entry = pos['entry']
            stop = pos['stop']
            target = pos['target']

            # Get current price
            current_price = await orchestrator.market_data.get_current_price(symbol)

            if current_price is None:
                logger.warning(f"Failed to fetch price for {symbol}")
                continue

            # Calculate metrics
            pnl = current_price - entry
            pnl_pct = (pnl / entry) * 100
            distance_to_stop = current_price - stop
            distance_to_stop_pct = (distance_to_stop / current_price) * 100

            # Classify status
            if current_price <= stop:
                status = 'HIT_STOP'
            elif distance_to_stop_pct < 2:
                status = 'NEAR_STOP'
            elif pnl_pct < -2:
                status = 'FALLING'
            else:
                status = 'OK'

            position_data = {
                'symbol': symbol,
                'mode': pos['mode'],
                'entry': entry,
                'current': current_price,
                'stop': stop,
                'target': target,
                'pnl': f"{pnl:+.2f}",
                'pnl_pct': f"{pnl_pct:+.2f}%",
                'distance_to_stop': f"{distance_to_stop:+.2f}",
                'distance_to_stop_pct': f"{distance_to_stop_pct:.2f}%",
                'status': status
            }

            dashboard.append(position_data)

            if status in ('HIT_STOP', 'NEAR_STOP', 'FALLING'):
                alerts.append(position_data)

        return {
            'success': True,
            'positions_monitored': len(all_positions),
            'alerts_count': len(alerts),
            'alerts': alerts,
            'dashboard': dashboard,
            'timestamp': __import__('datetime').datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Monitor error: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }

    finally:
        if ib:
            ib.disconnect()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Monitor positions')
    parser.add_argument('--positions', nargs='+', help='Position CSV files')
    parser.add_argument('--interval', type=int, default=15, help='Check interval (minutes)')
    parser.add_argument('--once', action='store_true', help='Run once instead of loop')

    args = parser.parse_args()

    result = asyncio.run(run_monitor_skill(
        positions_files=args.positions,
        interval=args.interval,
        once=args.once
    ))

    import json
    print(json.dumps(result, indent=2, default=str))
