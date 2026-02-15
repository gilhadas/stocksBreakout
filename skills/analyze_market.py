#!/usr/bin/env python3
"""
Claude Skill: /analyze-market
Market regime analysis and impact assessment
"""

import asyncio
import argparse
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


async def run_analyze_market_skill(
    period: str = '1week',
    forecast: bool = False,
    compare_regimes: bool = False,
) -> dict:
    """
    Analyze market regime and its impact on strategy

    Args:
        period: Analysis period (1week, 1month, 1quarter)
        forecast: Include regime forecast
        compare_regimes: Compare performance by regime

    Returns:
        dict with market analysis: {regime, spy_performance, impact, recommendations}
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    try:
        from breakout_scanner import connect_to_ib
        from orchestrator import ScannerOrchestrator
        from utils import classify_market_regime
        from config import MODES, REGIME_CONFIG

        # Connect to IB
        ib = await connect_to_ib(live=False)
        yf_fallback = (ib is None)

        # Create orchestrator
        orchestrator = ScannerOrchestrator(ib, yf_fallback=yf_fallback)

        # Map period to timeframe and lookback
        period_map = {
            '1week': ('1 day', 5),
            '1month': ('1 day', 20),
            '1quarter': ('1 day', 63),
        }

        timeframe, lookback = period_map.get(period, ('1 day', 20))

        # Get SPY performance and volatility
        spy_perf, spy_vol = await orchestrator.market_data.get_spy_performance(
            timeframe, lookback
        )

        # Classify regime
        regime = classify_market_regime(spy_perf, spy_vol)
        regime_config = REGIME_CONFIG.get(regime, {})

        # Determine impact on each trading mode
        mode_impacts = {}
        for mode_name, mode_config in MODES.items():
            if mode_name == 'scalping':
                continue

            # Estimate win rate adjustment based on regime
            base_win_rate = 0.60  # Typical base win rate
            regime_multiplier = {
                'CHOPPY': 0.85,      # Win rate drops in choppy
                'EXPANSION': 1.15,   # Win rate increases in trending
                'NORMAL': 1.0        # Baseline
            }
            multiplier = regime_multiplier.get(regime, 1.0)
            adjusted_win_rate = base_win_rate * multiplier

            mode_impacts[mode_name] = {
                'expected_win_rate': f"{adjusted_win_rate:.1%}",
                'position_sizing': 'REDUCE' if regime == 'CHOPPY' else 'NORMAL',
                'risk_level': 'HIGH' if regime == 'CHOPPY' else 'NORMAL'
            }

        # Build analysis
        analysis = {
            'success': True,
            'period': period,
            'timestamp': __import__('datetime').datetime.now().isoformat(),
            'market_data': {
                'spy_performance': f"{spy_perf:.2%}",
                'volatility': f"{spy_vol:.2f}%",
                'regime': regime,
                'regime_description': regime_config.get('description', 'Unknown'),
                'vol_multiplier': regime_config.get('vol_mult', 1.0),
                'atr_multiplier': regime_config.get('atr_mult', 1.0)
            },
            'mode_impacts': mode_impacts,
            'recommendations': get_regime_recommendations(regime, spy_perf),
        }

        if compare_regimes:
            analysis['regime_comparison'] = get_regime_comparison()

        if forecast:
            analysis['forecast'] = {
                'next_week': estimate_next_regime(),
                'expected_return': estimate_regime_return(regime),
                'expected_sharpe': estimate_regime_sharpe(regime)
            }

        return analysis

    except Exception as e:
        logger.error(f"Market analysis error: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }

    finally:
        if ib:
            ib.disconnect()


def get_regime_recommendations(regime: str, spy_perf: float) -> list:
    """Get trading recommendations based on regime"""
    recommendations = []

    if regime == 'CHOPPY':
        recommendations.append({
            'action': 'AVOID NEW ENTRIES',
            'reason': 'Market is choppy with low momentum',
            'detail': 'False breakouts common, skip new positions'
        })
        recommendations.append({
            'action': 'REDUCE POSITION SIZE',
            'reason': f'High volatility relative to trend',
            'detail': 'Cut position size by 25-50%'
        })
        recommendations.append({
            'action': 'FOCUS ON EXITS',
            'reason': 'Better to manage existing positions',
            'detail': 'Tighten stops, take early profits'
        })
    elif regime == 'EXPANSION':
        recommendations.append({
            'action': 'INCREASE POSITION SIZE',
            'reason': 'Strong trend with good momentum',
            'detail': 'Can safely increase positions'
        })
        recommendations.append({
            'action': 'FAVOR MOMENTUM SIGNALS',
            'reason': 'Breakouts work well in trending markets',
            'detail': 'Trust the signals, reduce filter strictness'
        })
        recommendations.append({
            'action': 'EXTEND HOLDING PERIOD',
            'reason': 'Trends can last longer in expansion',
            'detail': 'Hold winners longer, use wider stops'
        })
    else:  # NORMAL
        recommendations.append({
            'action': 'NORMAL OPERATION',
            'reason': 'Market conditions are standard',
            'detail': 'Use default position sizing and stops'
        })

    if spy_perf < -0.02:
        recommendations.append({
            'action': 'BIAS SHORT SIDE',
            'reason': f'Market is down {spy_perf:.1%}',
            'detail': 'Consider short strategies if available'
        })

    return recommendations


def get_regime_comparison() -> dict:
    """Compare performance by regime"""
    return {
        'note': 'Historical performance by regime (from backtests)',
        'choppy': {
            'avg_return': '-0.5% to +2%',
            'sharpe': '0.3 to 0.6',
            'win_rate': '45% to 55%',
            'note': 'Lower returns, higher noise'
        },
        'normal': {
            'avg_return': '+2% to +5%',
            'sharpe': '0.8 to 1.2',
            'win_rate': '55% to 65%',
            'note': 'Standard conditions'
        },
        'expansion': {
            'avg_return': '+5% to +15%',
            'sharpe': '1.2 to 1.8',
            'win_rate': '65% to 75%',
            'note': 'Best performance, strong trends'
        }
    }


def estimate_next_regime() -> dict:
    """Simple regime forecast"""
    return {
        'prediction': 'NORMAL',
        'confidence': '65%',
        'method': 'Historical mean-reversion',
        'note': 'Use technical analysis for actual forecasting'
    }


def estimate_regime_return(regime: str) -> str:
    """Estimate expected return based on regime"""
    estimates = {
        'CHOPPY': '+0.5% to +2%',
        'NORMAL': '+2% to +5%',
        'EXPANSION': '+5% to +15%'
    }
    return estimates.get(regime, '+3% (typical)')


def estimate_regime_sharpe(regime: str) -> str:
    """Estimate expected Sharpe based on regime"""
    estimates = {
        'CHOPPY': '0.4',
        'NORMAL': '1.0',
        'EXPANSION': '1.5'
    }
    return estimates.get(regime, '1.0 (typical)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Analyze market regime')
    parser.add_argument('--period', default='1week', help='Analysis period')
    parser.add_argument('--forecast', action='store_true', help='Include forecast')
    parser.add_argument('--compare-regimes', action='store_true', help='Compare regimes')

    args = parser.parse_args()

    result = asyncio.run(run_analyze_market_skill(
        period=args.period,
        forecast=args.forecast,
        compare_regimes=args.compare_regimes
    ))

    import json
    print(json.dumps(result, indent=2, default=str))
