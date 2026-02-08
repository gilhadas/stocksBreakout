"""
Pattern Recognition Module
Detects technical chart patterns for breakout confirmation.
Ported from StocksAgent with lowercase column names for stocksBreakout.
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple


def detect_bull_flag(df: pd.DataFrame, ticker: str = "") -> Optional[Dict]:
    """
    Detect bull flag pattern (continuation pattern).

    Bull flag characteristics:
    1. Pole: Sharp price increase (>5% in 1-3 days)
    2. Flag: Consolidation with slight downward drift
    3. Breakout: Price breaking above flag resistance
    """
    try:
        if len(df) < 10:
            return None

        recent = df.tail(20).copy()

        pole_window = 5
        for i in range(len(recent) - pole_window - 3):
            pole_start = recent.iloc[i]['close']
            pole_end = recent.iloc[i + pole_window]['close']
            pole_gain = ((pole_end - pole_start) / pole_start) * 100

            if pole_gain > 5:
                flag_data = recent.iloc[i + pole_window:i + pole_window + 5]

                if len(flag_data) < 3:
                    continue

                flag_high = flag_data['high'].max()
                flag_low = flag_data['low'].min()
                flag_range = ((flag_high - flag_low) / flag_low) * 100

                if flag_range < 5:
                    current_price = recent.iloc[-1]['close']

                    if current_price >= flag_high * 0.98:
                        target_price = current_price + (pole_end - pole_start)

                        return {
                            'name': 'Bull Flag',
                            'type': 'continuation',
                            'bullish': True,
                            'bearish': False,
                            'confidence': min(0.95, 0.6 + (pole_gain / 100)),
                            'pole_gain': round(pole_gain, 2),
                            'breakout_target': round(target_price, 2),
                            'current_price': round(current_price, 2),
                            'risk_level': 'medium'
                        }

        return None

    except Exception:
        return None


def detect_bear_flag(df: pd.DataFrame, ticker: str = "") -> Optional[Dict]:
    """
    Detect bear flag pattern (bearish continuation).
    """
    try:
        if len(df) < 10:
            return None

        recent = df.tail(20).copy()

        pole_window = 5
        for i in range(len(recent) - pole_window - 3):
            pole_start = recent.iloc[i]['close']
            pole_end = recent.iloc[i + pole_window]['close']
            pole_loss = ((pole_start - pole_end) / pole_start) * 100

            if pole_loss > 5:
                flag_data = recent.iloc[i + pole_window:i + pole_window + 5]

                if len(flag_data) < 3:
                    continue

                flag_high = flag_data['high'].max()
                flag_low = flag_data['low'].min()
                flag_range = ((flag_high - flag_low) / flag_low) * 100

                if flag_range < 5:
                    current_price = recent.iloc[-1]['close']

                    if current_price <= flag_low * 1.02:
                        target_price = current_price - (pole_start - pole_end)

                        return {
                            'name': 'Bear Flag',
                            'type': 'continuation',
                            'bullish': False,
                            'bearish': True,
                            'confidence': min(0.95, 0.6 + (pole_loss / 100)),
                            'pole_loss': round(pole_loss, 2),
                            'breakdown_target': round(target_price, 2),
                            'current_price': round(current_price, 2),
                            'risk_level': 'high'
                        }

        return None

    except Exception:
        return None


def detect_ascending_triangle(df: pd.DataFrame, ticker: str = "") -> Optional[Dict]:
    """
    Detect ascending triangle pattern (bullish).
    """
    try:
        if len(df) < 15:
            return None

        recent = df.tail(30).copy()

        highs = recent['high'].values

        resistance_candidates = []
        for i in range(len(highs) - 5):
            window = highs[i:i+10]
            if len(window) < 10:
                continue
            max_price = window.max()

            touches = sum(1 for h in window if abs(h - max_price) / max_price < 0.02)

            if touches >= 2:
                resistance_candidates.append(max_price)

        if not resistance_candidates:
            return None

        resistance = np.median(resistance_candidates)

        lows = recent['low'].values[-10:]
        if len(lows) < 5:
            return None

        x = np.arange(len(lows))
        slope = np.polyfit(x, lows, 1)[0]

        if slope > 0:
            current_price = recent.iloc[-1]['close']

            if current_price >= resistance * 0.97:
                triangle_height = resistance - lows[0]
                target = resistance + triangle_height

                return {
                    'name': 'Ascending Triangle',
                    'type': 'consolidation',
                    'bullish': True,
                    'bearish': False,
                    'confidence': 0.75,
                    'resistance': round(resistance, 2),
                    'breakout_target': round(target, 2),
                    'current_price': round(current_price, 2),
                    'risk_level': 'medium'
                }

        return None

    except Exception:
        return None


def detect_descending_triangle(df: pd.DataFrame, ticker: str = "") -> Optional[Dict]:
    """
    Detect descending triangle pattern (bearish).
    """
    try:
        if len(df) < 15:
            return None

        recent = df.tail(30).copy()

        lows = recent['low'].values

        support_candidates = []
        for i in range(len(lows) - 5):
            window = lows[i:i+10]
            if len(window) < 10:
                continue
            min_price = window.min()

            touches = sum(1 for l in window if abs(l - min_price) / min_price < 0.02)

            if touches >= 2:
                support_candidates.append(min_price)

        if not support_candidates:
            return None

        support = np.median(support_candidates)

        highs = recent['high'].values[-10:]
        if len(highs) < 5:
            return None

        x = np.arange(len(highs))
        slope = np.polyfit(x, highs, 1)[0]

        if slope < 0:
            current_price = recent.iloc[-1]['close']

            if current_price <= support * 1.03:
                triangle_height = highs[0] - support
                target = support - triangle_height

                return {
                    'name': 'Descending Triangle',
                    'type': 'consolidation',
                    'bullish': False,
                    'bearish': True,
                    'confidence': 0.75,
                    'support': round(support, 2),
                    'breakdown_target': round(target, 2),
                    'current_price': round(current_price, 2),
                    'risk_level': 'high'
                }

        return None

    except Exception:
        return None


def detect_symmetrical_triangle(df: pd.DataFrame, ticker: str = "") -> Optional[Dict]:
    """
    Detect symmetrical triangle pattern (neutral, breakout direction matters).
    """
    try:
        if len(df) < 15:
            return None

        recent = df.tail(20).copy()

        highs = recent['high'].values
        lows = recent['low'].values

        x = np.arange(len(highs))
        high_slope = np.polyfit(x, highs, 1)[0]
        low_slope = np.polyfit(x, lows, 1)[0]

        if high_slope < 0 and low_slope > 0:
            current_price = recent.iloc[-1]['close']

            recent_high = highs[-5:].max()
            recent_low = lows[-5:].min()

            range_pct = ((recent_high - recent_low) / recent_low) * 100

            if range_pct < 5:
                return {
                    'name': 'Symmetrical Triangle',
                    'type': 'consolidation',
                    'bullish': None,
                    'bearish': None,
                    'confidence': 0.70,
                    'current_price': round(current_price, 2),
                    'range': round(range_pct, 2),
                    'risk_level': 'medium'
                }

        return None

    except Exception:
        return None


def detect_patterns_from_df(df: pd.DataFrame, ticker: str = "") -> List[Dict]:
    """
    Run all pattern detectors on a pre-fetched DataFrame.

    Args:
        df: DataFrame with lowercase OHLCV columns (open, high, low, close, volume)
        ticker: Stock ticker (for display)

    Returns:
        List of detected patterns
    """
    patterns = []

    detectors = [
        detect_bull_flag,
        detect_bear_flag,
        detect_ascending_triangle,
        detect_descending_triangle,
        detect_symmetrical_triangle,
    ]

    for detector in detectors:
        result = detector(df, ticker)
        if result:
            patterns.append(result)

    return patterns


def get_pattern_score(df: pd.DataFrame, ticker: str = "") -> Tuple[bool, bool, float, List[str]]:
    """
    Get pattern scoring summary for integration with breakout scanner.

    Args:
        df: DataFrame with lowercase OHLCV columns
        ticker: Stock ticker

    Returns:
        Tuple of (has_bullish, has_bearish, best_target, pattern_names)
        - has_bullish: True if any bullish pattern detected
        - has_bearish: True if any bearish pattern detected
        - best_target: Highest breakout target price from patterns (0.0 if none)
        - pattern_names: List of detected pattern names
    """
    patterns = detect_patterns_from_df(df, ticker)

    has_bullish = False
    has_bearish = False
    best_target = 0.0
    pattern_names = []

    for p in patterns:
        pattern_names.append(p['name'])

        if p.get('bullish'):
            has_bullish = True
        if p.get('bearish'):
            has_bearish = True

        target = p.get('breakout_target', 0.0)
        if target and target > best_target:
            best_target = target

    return has_bullish, has_bearish, best_target, pattern_names
