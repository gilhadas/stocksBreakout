#!/usr/bin/env python3
"""
Claude Skill: /backtest
Run strategy backtests with comparison support
"""

import asyncio
import argparse
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


async def run_backtest_skill(
    period: str = '2024',
    mode: str = 'swing',
    compare: Optional[str] = None,
    optimize: Optional[str] = None,
    notify: bool = False,
) -> dict:
    """
    Run strategy backtest on historical data

    Args:
        period: Date range (e.g., '2024', '2025-01-01:2025-12-31', 'last-month')
        mode: Trading mode (swing, daytrade, longterm)
        compare: Comparison type (v1-vs-v2, configs, modes)
        optimize: Parameter to optimize (rsi_overbought, atr_mult, etc.)
        notify: Send notifications

    Returns:
        dict with backtest results: {return, sharpe, max_dd, win_rate, w_l_ratio, comparison}
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    try:
        # Parse dates
        if period == 'last-month':
            end_date = datetime.now().date()
            start_date = (datetime.now() - timedelta(days=30)).date()
        elif period == 'last-quarter':
            end_date = datetime.now().date()
            start_date = (datetime.now() - timedelta(days=90)).date()
        elif ':' in period:
            start_str, end_str = period.split(':')
            start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
        else:
            # Assume year like '2024'
            year = int(period)
            start_date = datetime(year, 1, 1).date()
            end_date = datetime(year, 12, 31).date()

        logger.info(f"Backtest period: {start_date} to {end_date}")
        logger.info(f"Mode: {mode}")
        if compare:
            logger.info(f"Comparison: {compare}")
        if optimize:
            logger.info(f"Optimizing: {optimize}")

        # Import backtest module
        from enhanced_backtest import run_backtest_v2

        if compare == 'v1-vs-v2':
            # Run both versions and compare
            return await run_comparison_backtest(
                start_date, end_date, mode
            )
        elif optimize:
            # Run parameter sweep
            return await run_optimization_backtest(
                start_date, end_date, mode, optimize
            )
        else:
            # Run single backtest
            result = await run_backtest_v2(
                start_date=str(start_date),
                end_date=str(end_date),
                mode=mode,
                use_legacy_momentum=False  # V2 by default
            )

            return {
                'success': True,
                'period': f"{start_date} to {end_date}",
                'mode': mode,
                'metrics': {
                    'return': f"{result.get('return', 0):.2f}%",
                    'sharpe': f"{result.get('sharpe', 0):.2f}",
                    'max_drawdown': f"{result.get('max_drawdown', 0):.2f}%",
                    'win_rate': f"{result.get('win_rate', 0):.1f}%",
                    'w_l_ratio': f"{result.get('w_l_ratio', 0):.2f}",
                    'trades': result.get('total_trades', 0)
                },
                'full_result': result
            }

    except Exception as e:
        logger.error(f"Backtest error: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }


async def run_comparison_backtest(start_date, end_date, mode: str) -> dict:
    """Compare V1 vs V2 scoring"""
    from enhanced_backtest import run_backtest_v2

    logger.info("Running V1 vs V2 comparison...")

    # V1 (legacy momentum)
    v1_result = await run_backtest_v2(
        start_date=str(start_date),
        end_date=str(end_date),
        mode=mode,
        use_legacy_momentum=True
    )

    # V2 (composite scoring)
    v2_result = await run_backtest_v2(
        start_date=str(start_date),
        end_date=str(end_date),
        mode=mode,
        use_legacy_momentum=False
    )

    improvement = {
        'return': v2_result.get('return', 0) - v1_result.get('return', 0),
        'sharpe': v2_result.get('sharpe', 0) - v1_result.get('sharpe', 0),
        'win_rate': v2_result.get('win_rate', 0) - v1_result.get('win_rate', 0),
    }

    return {
        'success': True,
        'comparison': 'v1 vs v2',
        'period': f"{start_date} to {end_date}",
        'mode': mode,
        'v1': {
            'return': f"{v1_result.get('return', 0):.2f}%",
            'sharpe': f"{v1_result.get('sharpe', 0):.2f}",
            'max_drawdown': f"{v1_result.get('max_drawdown', 0):.2f}%",
            'win_rate': f"{v1_result.get('win_rate', 0):.1f}%",
        },
        'v2': {
            'return': f"{v2_result.get('return', 0):.2f}%",
            'sharpe': f"{v2_result.get('sharpe', 0):.2f}",
            'max_drawdown': f"{v2_result.get('max_drawdown', 0):.2f}%",
            'win_rate': f"{v2_result.get('win_rate', 0):.1f}%",
        },
        'improvement': {
            'return': f"{improvement['return']:+.2f}%",
            'sharpe': f"{improvement['sharpe']:+.2f}",
            'win_rate': f"{improvement['win_rate']:+.1f}%"
        },
        'winner': 'V2' if improvement['return'] > 0 else 'V1'
    }


async def run_optimization_backtest(start_date, end_date, mode: str, param: str) -> dict:
    """Parameter optimization sweep"""
    logger.info(f"Optimizing parameter: {param}")

    # This would require parameter sweep logic
    # For now, return placeholder
    return {
        'success': True,
        'note': f"Optimization for {param} requires enhanced_backtest.py parameter sweep",
        'recommendation': 'Use beat_spy_optimizer.py for detailed parameter analysis'
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run backtest')
    parser.add_argument('--period', default='2024', help='Date range')
    parser.add_argument('--mode', default='swing', help='Trading mode')
    parser.add_argument('--compare', help='Comparison type (v1-vs-v2, configs, modes)')
    parser.add_argument('--optimize', help='Parameter to optimize')
    parser.add_argument('--notify', action='store_true', help='Send notifications')

    args = parser.parse_args()

    result = asyncio.run(run_backtest_skill(
        period=args.period,
        mode=args.mode,
        compare=args.compare,
        optimize=args.optimize,
        notify=args.notify
    ))

    import json
    print(json.dumps(result, indent=2, default=str))
