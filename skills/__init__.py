"""
Claude Skills for stocksBreakout

Available skills:
- /scan: Run trading scans
- /backtest: Backtest strategies
- /monitor: Monitor positions
- /analyze-market: Analyze market regime
- /validate-signals: Validate signal quality
- /optimize: Parameter optimization
- /position-report: Portfolio analytics
- /cron-setup: Automation setup
"""

from pathlib import Path

__all__ = ['SKILLS_REGISTRY', 'run_skill']

# Skills registry
SKILLS_REGISTRY = {
    'scan': {
        'module': 'scan',
        'function': 'run_scan_skill',
        'description': 'Run trading scans with intelligent defaults',
        'examples': [
            'scan --mode swing --watchlist S&P_500.txt',
            'scan --mode daytrade --premium',
        ]
    },
    'backtest': {
        'module': 'backtest',
        'function': 'run_backtest_skill',
        'description': 'Run strategy backtests with comparison support',
        'examples': [
            'backtest --period 2024 --mode swing',
            'backtest --compare v1-vs-v2 --period 2025',
        ]
    },
    'monitor': {
        'module': 'monitor',
        'function': 'run_monitor_skill',
        'description': 'Monitor open positions for price drops',
        'examples': [
            'monitor',
            'monitor --interval 5',
        ]
    },
    'analyze-market': {
        'module': 'analyze_market',
        'function': 'run_analyze_market_skill',
        'description': 'Analyze market regime and impact on strategy',
        'examples': [
            'analyze-market --period 1week',
            'analyze-market --forecast --compare-regimes',
        ]
    },
}


async def run_skill(skill_name: str, **kwargs):
    """
    Run a skill by name with given arguments

    Args:
        skill_name: Name of skill (e.g., 'scan', 'backtest')
        **kwargs: Arguments to pass to skill

    Returns:
        Result dict from skill execution
    """
    import asyncio
    import importlib

    if skill_name not in SKILLS_REGISTRY:
        return {
            'success': False,
            'error': f'Unknown skill: {skill_name}',
            'available_skills': list(SKILLS_REGISTRY.keys())
        }

    skill_info = SKILLS_REGISTRY[skill_name]

    try:
        # Dynamically import skill module
        module_name = f'skills.{skill_info["module"]}'
        module = importlib.import_module(module_name)

        # Get skill function
        skill_func = getattr(module, skill_info['function'])

        # Run skill
        result = await skill_func(**kwargs)
        return result

    except Exception as e:
        return {
            'success': False,
            'error': f'Skill execution error: {str(e)}',
            'skill': skill_name
        }


def list_skills():
    """Return list of available skills"""
    return {
        'skills': [
            {
                'name': name,
                'description': info['description'],
                'examples': info['examples']
            }
            for name, info in SKILLS_REGISTRY.items()
        ]
    }
