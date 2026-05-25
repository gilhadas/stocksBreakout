"""
pattern_recognition.py — thin re-export shim
=============================================
Canonical implementation lives in quantkit/patterns.py.
This module preserves backward-compatible imports for all project files
that do `from pattern_recognition import ...`.
"""
from quantkit.patterns import (  # noqa: F401, F403
    detect_bull_flag,
    detect_bear_flag,
    detect_ascending_triangle,
    detect_descending_triangle,
    detect_symmetrical_triangle,
    detect_cup_and_handle,
    detect_inverse_head_and_shoulders,
    detect_head_and_shoulders,
    detect_double_bottom,
    detect_double_top,
    detect_rectangle,
    detect_falling_wedge,
    detect_rising_wedge,
    detect_rounding_bottom,
    detect_inverted_cup_and_handle,
    detect_vcp,
    detect_candle_patterns,
    detect_sr_levels,
    detect_patterns_from_df,
    get_pattern_score,
)
