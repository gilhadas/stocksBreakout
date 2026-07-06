"""
quantkit — Quantitative Trading Toolkit
========================================
A collection of reusable quant-finance modules extracted from a professional
breakout-scanning system.  Pure pandas/numpy core, optional extras for
FinBERT and Finnhub.

Modules
-------
    quantkit.indicators   — ATR, RSI, MACD, BB, VWAP, ADX, Aroon, StochRSI,
                            Volume Profile, Minervini Template, composite scores
    quantkit.patterns     — 16 chart patterns + 11 candlestick patterns + VCP
                            + Support/Resistance level detection
    quantkit.fib          — Fibonacci retracement scoring (0–100)
    quantkit.regime       — Market regime detection (bull / bear / mixed)
    quantkit.sentiment    — FinBERT financial sentiment + Finnhub buzz
    quantkit.portfolio    — Exit evaluation + portfolio health assessment

Quick start
-----------
    import quantkit.indicators as ind
    import quantkit.patterns   as pat

    df = ...   # OHLCV DataFrame with lowercase columns

    df = ind.calculate_all_indicators(df, trend_type='EMA',
                                      trend_period=21, timeframe='1d')
    patterns = pat.detect_patterns_from_df(df)

    from quantkit.fib import detect_swing, score_bounce
    swing = detect_swing(df)
    score = score_bounce(df, swing) if swing else None

Install
-------
    # Editable (from the repo root):
    pip install -e .

    # From GitHub:
    pip install git+https://github.com/gilhadas/stocksBreakout

    # With FinBERT support:
    pip install "git+https://github.com/gilhadas/stocksBreakout[sentiment]"
"""

__version__ = "1.0.0"
__author__  = "Gil Hadas"

# Convenience re-exports (avoid deep imports for the most common use cases)
from quantkit.indicators import (
    calculate_atr,
    calculate_rsi,
    calculate_macd,
    calculate_bollinger_bands,
    calculate_vwap,
    calculate_adx,
    calculate_aroon,
    calculate_stochastic_rsi,
    calculate_supertrend,
    calculate_momentum_score,
    calculate_breakout_conviction,
    calculate_minervini_template,
    compute_volume_profile,
    detect_rsi_divergence,
    calculate_all_indicators,
)

from quantkit.patterns import (
    detect_patterns_from_df,
    get_pattern_score,
)

from quantkit.fib import (
    detect_swing,
    score_bounce,
    fib_levels,
    nearest_fib_to_price,
    scan_dataframes as fib_scan,
)

from quantkit.regime import (
    detect_regime,
    suggest_params,
    REGIME_PARAMS,
)

from quantkit.tension import (
    compute_tension_index,
    TensionConfig,
)

__all__ = [
    # indicators
    "calculate_atr",
    "calculate_rsi",
    "calculate_macd",
    "calculate_bollinger_bands",
    "calculate_vwap",
    "calculate_adx",
    "calculate_aroon",
    "calculate_stochastic_rsi",
    "calculate_supertrend",
    "calculate_momentum_score",
    "calculate_breakout_conviction",
    "calculate_minervini_template",
    "compute_volume_profile",
    "detect_rsi_divergence",
    "calculate_all_indicators",
    # patterns
    "detect_patterns_from_df",
    "get_pattern_score",
    # fib
    "detect_swing",
    "score_bounce",
    "fib_levels",
    "nearest_fib_to_price",
    "fib_scan",
    # regime
    "detect_regime",
    "suggest_params",
    "REGIME_PARAMS",
    # tension
    "compute_tension_index",
    "TensionConfig",
]
