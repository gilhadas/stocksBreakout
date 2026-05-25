"""
quantkit.portfolio
==================
Position exit evaluation and portfolio health monitoring.

Both modules accept a *config* dict so they work without a project-specific
config.py.  Pass your own dict or use the exported defaults as a starting point.

    from quantkit.portfolio import ExitEvaluator, DEFAULT_EXIT_CONFIG
    from quantkit.portfolio import assess_portfolio_health, DEFAULT_HEALTH_CONFIG

    evaluator = ExitEvaluator(config=DEFAULT_EXIT_CONFIG)
    result = evaluator.evaluate(df, symbol='AAPL', ...)
"""
from quantkit.portfolio.exit_logic import ExitEvaluator, DEFAULT_EXIT_CONFIG
from quantkit.portfolio.health import (
    assess_portfolio_health,
    DEFAULT_HEALTH_CONFIG,
    is_etf,
)

__all__ = [
    "ExitEvaluator",
    "DEFAULT_EXIT_CONFIG",
    "assess_portfolio_health",
    "DEFAULT_HEALTH_CONFIG",
    "is_etf",
]
