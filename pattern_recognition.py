"""
Pattern Recognition Module
Detects technical chart patterns for breakout confirmation.
Ported from StocksAgent with lowercase column names for stocksBreakout.
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple


def _avg_volume(df: pd.DataFrame, window: int = 20) -> float:
    """Average volume over last N bars (or all if fewer)."""
    vol = df['volume'].values
    n = min(window, len(vol))
    return float(np.mean(vol[-n:])) if n > 0 else 1.0


def detect_bull_flag(df: pd.DataFrame, ticker: str = "") -> Optional[Dict]:
    """
    Detect bull flag pattern (continuation pattern).

    Bull flag characteristics:
    1. Pole: Sharp price increase (>5% in 1-3 days) on high volume
    2. Flag: Consolidation with slight downward drift on declining volume
    3. Breakout: Price breaking above flag resistance on rising volume
    """
    try:
        if len(df) < 10:
            return None

        recent = df.tail(20).copy()
        avg_vol = _avg_volume(df)

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

                        # Volume confirmation
                        pole_vol = recent.iloc[i:i + pole_window]['volume'].mean()
                        flag_vol = flag_data['volume'].mean()
                        breakout_vol = recent.iloc[-1]['volume']
                        vol_confirmed = (
                            pole_vol > avg_vol * 1.2 and   # pole on high vol
                            flag_vol < avg_vol * 0.9 and   # flag on low vol
                            breakout_vol > avg_vol         # breakout on rising vol
                        )
                        conf = min(0.95, 0.6 + (pole_gain / 100))
                        if vol_confirmed:
                            conf = min(0.95, conf + 0.10)

                        return {
                            'name': 'Bull Flag',
                            'type': 'continuation',
                            'bullish': True,
                            'bearish': False,
                            'confidence': conf,
                            'volume_confirmed': vol_confirmed,
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
        avg_vol = _avg_volume(df)

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

                        pole_vol = recent.iloc[i:i + pole_window]['volume'].mean()
                        flag_vol = flag_data['volume'].mean()
                        breakout_vol = recent.iloc[-1]['volume']
                        vol_confirmed = (
                            pole_vol > avg_vol * 1.2 and
                            flag_vol < avg_vol * 0.9 and
                            breakout_vol > avg_vol
                        )
                        conf = min(0.95, 0.6 + (pole_loss / 100))
                        if vol_confirmed:
                            conf = min(0.95, conf + 0.10)

                        return {
                            'name': 'Bear Flag',
                            'type': 'continuation',
                            'bullish': False,
                            'bearish': True,
                            'confidence': conf,
                            'volume_confirmed': vol_confirmed,
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

                # Volume should contract during triangle, spike on breakout
                avg_vol = _avg_volume(df)
                mid_vol = recent['volume'].iloc[5:-3].mean() if len(recent) > 8 else avg_vol
                breakout_vol = recent.iloc[-1]['volume']
                vol_confirmed = mid_vol < avg_vol * 0.9 and breakout_vol > avg_vol
                conf = 0.80 if vol_confirmed else 0.70

                return {
                    'name': 'Ascending Triangle',
                    'type': 'consolidation',
                    'bullish': True,
                    'bearish': False,
                    'confidence': conf,
                    'volume_confirmed': vol_confirmed,
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

                avg_vol = _avg_volume(df)
                mid_vol = recent['volume'].iloc[5:-3].mean() if len(recent) > 8 else avg_vol
                breakout_vol = recent.iloc[-1]['volume']
                vol_confirmed = mid_vol < avg_vol * 0.9 and breakout_vol > avg_vol
                conf = 0.80 if vol_confirmed else 0.70

                return {
                    'name': 'Descending Triangle',
                    'type': 'consolidation',
                    'bullish': False,
                    'bearish': True,
                    'confidence': conf,
                    'volume_confirmed': vol_confirmed,
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
                avg_vol = _avg_volume(df)
                mid_vol = recent['volume'].iloc[3:-3].mean() if len(recent) > 6 else avg_vol
                vol_confirmed = mid_vol < avg_vol * 0.85
                conf = 0.75 if vol_confirmed else 0.65

                return {
                    'name': 'Symmetrical Triangle',
                    'type': 'consolidation',
                    'bullish': None,
                    'bearish': None,
                    'confidence': conf,
                    'volume_confirmed': vol_confirmed,
                    'current_price': round(current_price, 2),
                    'range': round(range_pct, 2),
                    'risk_level': 'medium'
                }

        return None

    except Exception:
        return None


def detect_cup_and_handle(df: pd.DataFrame, ticker: str = "") -> Optional[Dict]:
    """
    Detect cup and handle pattern (bullish continuation).

    Structure (over ~30-60 bars):
    1. Left lip: prior high
    2. Cup: rounded bottom (U-shape, not V)
    3. Right lip: price recovers near left lip
    4. Handle: small pullback (flag-like) off the right lip
    5. Breakout: price pushes above both lips

    Uses a 40-bar window: first 30 for the cup, last 10 for the handle.
    """
    try:
        if len(df) < 40:
            return None

        window = df.tail(50).copy()
        closes = window['close'].values
        highs = window['high'].values
        lows = window['low'].values

        # Find the highest point in the first third (left lip)
        first_third = len(closes) // 3
        left_lip_idx = np.argmax(highs[:first_third])
        left_lip = highs[left_lip_idx]

        # Find the lowest point in the middle (cup bottom)
        mid_start = first_third
        mid_end = 2 * first_third
        cup_bottom_idx = mid_start + np.argmin(lows[mid_start:mid_end])
        cup_bottom = lows[cup_bottom_idx]

        # Cup depth should be 10-35% from lip
        cup_depth_pct = (left_lip - cup_bottom) / left_lip * 100
        if cup_depth_pct < 8 or cup_depth_pct > 40:
            return None

        # Right lip: highest point in last third (before final 5 bars for handle)
        right_section = highs[mid_end:-5] if len(highs) > mid_end + 5 else highs[mid_end:]
        if len(right_section) < 3:
            return None
        right_lip = right_section.max()

        # Right lip should be within 5% of left lip (cup is symmetric-ish)
        lip_diff_pct = abs(right_lip - left_lip) / left_lip * 100
        if lip_diff_pct > 8:
            return None

        # U-shape check: cup bottom should be lower than both lips
        if cup_bottom >= left_lip * 0.95 or cup_bottom >= right_lip * 0.95:
            return None

        # Handle: last 5-10 bars should pull back slightly from right lip
        handle = closes[-8:]
        handle_high = handle.max()
        handle_low = handle.min()
        handle_pullback_pct = (handle_high - handle_low) / handle_high * 100

        # Handle should be a small pullback (2-12%), not a crash
        if handle_pullback_pct < 1 or handle_pullback_pct > 15:
            return None

        current_price = closes[-1]
        lip_level = max(left_lip, right_lip)

        # Current price should be near or above the lip (breakout zone)
        if current_price < lip_level * 0.95:
            return None

        target = lip_level + (lip_level - cup_bottom)

        # Volume: should be low at cup bottom, rising on right lip + handle breakout
        volumes = window['volume'].values
        avg_vol = _avg_volume(df)
        cup_bottom_vol = volumes[cup_bottom_idx] if cup_bottom_idx < len(volumes) else avg_vol
        breakout_vol = volumes[-1]
        vol_confirmed = cup_bottom_vol < avg_vol and breakout_vol > avg_vol * 1.1
        conf = 0.85 if vol_confirmed else 0.75

        return {
            'name': 'Cup and Handle',
            'type': 'continuation',
            'bullish': True,
            'bearish': False,
            'confidence': conf,
            'volume_confirmed': vol_confirmed,
            'cup_depth_pct': round(cup_depth_pct, 2),
            'handle_pullback_pct': round(handle_pullback_pct, 2),
            'resistance': round(lip_level, 2),
            'breakout_target': round(target, 2),
            'current_price': round(current_price, 2),
            'risk_level': 'low',
        }

    except Exception:
        return None


def detect_inverse_head_and_shoulders(df: pd.DataFrame, ticker: str = "") -> Optional[Dict]:
    """
    Detect inverse head and shoulders (bullish reversal).

    Structure:
    1. Left shoulder: a low, then bounce up to neckline
    2. Head: a lower low, then bounce back up to neckline
    3. Right shoulder: a higher low (similar depth to left shoulder)
    4. Neckline: resistance connecting the two bounce highs
    5. Breakout above neckline

    Scans last 40 bars, finds 3 swing lows.
    """
    try:
        if len(df) < 30:
            return None

        window = df.tail(45).copy()
        closes = window['close'].values
        lows = window['low'].values
        highs = window['high'].values
        n = len(closes)

        # Find swing lows (local minima with >= 3-bar lookback/forward)
        swing_lows = []
        for i in range(4, n - 4):
            if lows[i] == min(lows[i-4:i+5]):
                swing_lows.append((i, lows[i]))

        if len(swing_lows) < 3:
            return None

        # Try combinations of 3 swing lows
        for li in range(len(swing_lows) - 2):
            ls_idx, ls_low = swing_lows[li]         # left shoulder
            for hi in range(li + 1, len(swing_lows) - 1):
                h_idx, h_low = swing_lows[hi]       # head
                for ri in range(hi + 1, len(swing_lows)):
                    rs_idx, rs_low = swing_lows[ri]  # right shoulder

                    # Head must be the lowest
                    if h_low >= ls_low or h_low >= rs_low:
                        continue

                    # Shoulders should be roughly similar depth (within 30%)
                    shoulder_avg = (ls_low + rs_low) / 2
                    if abs(ls_low - rs_low) / shoulder_avg > 0.15:
                        continue

                    # Spacing: head should be roughly centered
                    left_gap = h_idx - ls_idx
                    right_gap = rs_idx - h_idx
                    if left_gap < 3 or right_gap < 3:
                        continue
                    if max(left_gap, right_gap) > 3 * min(left_gap, right_gap):
                        continue

                    # Neckline: connect the bounce highs between shoulders and head
                    neck_left = max(highs[ls_idx:h_idx])
                    neck_right = max(highs[h_idx:rs_idx])
                    neckline = (neck_left + neck_right) / 2

                    # Head depth below neckline should be meaningful (>5%)
                    head_depth = (neckline - h_low) / neckline * 100
                    if head_depth < 5:
                        continue

                    current_price = closes[-1]

                    # Price should be near or above neckline
                    if current_price < neckline * 0.97:
                        continue

                    # Right shoulder should be recent (within last 15 bars)
                    if n - rs_idx > 15:
                        continue

                    target = neckline + (neckline - h_low)

                    # Volume: should increase from left shoulder → right shoulder → breakout
                    volumes = window['volume'].values
                    avg_vol = _avg_volume(df)
                    ls_vol = volumes[ls_idx] if ls_idx < len(volumes) else avg_vol
                    rs_vol = volumes[rs_idx] if rs_idx < len(volumes) else avg_vol
                    breakout_vol = volumes[-1]
                    vol_confirmed = rs_vol > ls_vol * 0.8 and breakout_vol > avg_vol
                    conf = 0.90 if vol_confirmed else 0.80

                    return {
                        'name': 'Inverse Head & Shoulders',
                        'type': 'reversal',
                        'bullish': True,
                        'bearish': False,
                        'confidence': conf,
                        'volume_confirmed': vol_confirmed,
                        'neckline': round(neckline, 2),
                        'head_low': round(h_low, 2),
                        'head_depth_pct': round(head_depth, 2),
                        'breakout_target': round(target, 2),
                        'current_price': round(current_price, 2),
                        'risk_level': 'low',
                    }

        return None

    except Exception:
        return None


def detect_head_and_shoulders(df: pd.DataFrame, ticker: str = "") -> Optional[Dict]:
    """
    Detect head and shoulders (bearish reversal).

    Structure: two swing highs (shoulders) with a higher high (head) between them.
    Neckline connects the two trough lows. Breakdown below neckline.
    """
    try:
        if len(df) < 30:
            return None

        window = df.tail(45).copy()
        closes = window['close'].values
        highs = window['high'].values
        lows = window['low'].values
        n = len(closes)

        # Find swing highs
        swing_highs = []
        for i in range(4, n - 4):
            if highs[i] == max(highs[i-4:i+5]):
                swing_highs.append((i, highs[i]))

        if len(swing_highs) < 3:
            return None

        for li in range(len(swing_highs) - 2):
            ls_idx, ls_high = swing_highs[li]
            for hi in range(li + 1, len(swing_highs) - 1):
                h_idx, h_high = swing_highs[hi]
                for ri in range(hi + 1, len(swing_highs)):
                    rs_idx, rs_high = swing_highs[ri]

                    # Head must be the highest
                    if h_high <= ls_high or h_high <= rs_high:
                        continue

                    # Shoulders roughly similar (within 15%)
                    shoulder_avg = (ls_high + rs_high) / 2
                    if abs(ls_high - rs_high) / shoulder_avg > 0.15:
                        continue

                    left_gap = h_idx - ls_idx
                    right_gap = rs_idx - h_idx
                    if left_gap < 3 or right_gap < 3:
                        continue
                    if max(left_gap, right_gap) > 3 * min(left_gap, right_gap):
                        continue

                    # Neckline from trough lows
                    neck_left = min(lows[ls_idx:h_idx])
                    neck_right = min(lows[h_idx:rs_idx])
                    neckline = (neck_left + neck_right) / 2

                    head_height = (h_high - neckline) / neckline * 100
                    if head_height < 5:
                        continue

                    current_price = closes[-1]

                    # Price near or below neckline
                    if current_price > neckline * 1.03:
                        continue

                    if n - rs_idx > 15:
                        continue

                    target = neckline - (h_high - neckline)

                    volumes = window['volume'].values
                    avg_vol = _avg_volume(df)
                    breakout_vol = volumes[-1]
                    vol_confirmed = breakout_vol > avg_vol
                    conf = 0.90 if vol_confirmed else 0.80

                    return {
                        'name': 'Head & Shoulders',
                        'type': 'reversal',
                        'bullish': False,
                        'bearish': True,
                        'confidence': conf,
                        'volume_confirmed': vol_confirmed,
                        'neckline': round(neckline, 2),
                        'head_high': round(h_high, 2),
                        'head_height_pct': round(head_height, 2),
                        'breakdown_target': round(target, 2),
                        'current_price': round(current_price, 2),
                        'risk_level': 'high',
                    }

        return None

    except Exception:
        return None


def detect_double_bottom(df: pd.DataFrame, ticker: str = "") -> Optional[Dict]:
    """
    Detect double bottom pattern (bullish reversal — W shape).

    Structure:
    1. First trough: price drops to support
    2. Bounce: price recovers to a resistance level (neckline)
    3. Second trough: price drops again to ~same support level
    4. Breakout above the neckline
    """
    try:
        if len(df) < 20:
            return None

        window = df.tail(40).copy()
        lows = window['low'].values
        highs = window['high'].values
        closes = window['close'].values
        n = len(closes)

        # Find swing lows
        swing_lows = []
        for i in range(3, n - 3):
            if lows[i] == min(lows[i-3:i+4]):
                swing_lows.append((i, lows[i]))

        if len(swing_lows) < 2:
            return None

        # Check pairs
        for i in range(len(swing_lows) - 1):
            idx1, low1 = swing_lows[i]
            for j in range(i + 1, len(swing_lows)):
                idx2, low2 = swing_lows[j]

                gap = idx2 - idx1
                if gap < 5 or gap > 30:
                    continue

                # Both bottoms within 3% of each other
                avg_low = (low1 + low2) / 2
                if abs(low1 - low2) / avg_low > 0.03:
                    continue

                # Neckline: highest point between the two bottoms
                neckline = max(highs[idx1:idx2])

                # Pattern height should be meaningful (>5%)
                height_pct = (neckline - avg_low) / avg_low * 100
                if height_pct < 4:
                    continue

                current_price = closes[-1]

                # Second bottom should be recent
                if n - idx2 > 12:
                    continue

                # Price should be recovering toward neckline
                if current_price < avg_low:
                    continue

                # Near or above neckline for breakout
                if current_price >= neckline * 0.97:
                    target = neckline + (neckline - avg_low)

                    # Volume: 2nd bottom should have lower vol (less selling),
                    # breakout bar should have high volume
                    volumes = window['volume'].values
                    avg_vol = _avg_volume(df)
                    vol1 = volumes[idx1] if idx1 < len(volumes) else avg_vol
                    vol2 = volumes[idx2] if idx2 < len(volumes) else avg_vol
                    breakout_vol = volumes[-1]
                    vol_confirmed = vol2 < vol1 * 1.1 and breakout_vol > avg_vol
                    conf = 0.85 if vol_confirmed else 0.75

                    return {
                        'name': 'Double Bottom',
                        'type': 'reversal',
                        'bullish': True,
                        'bearish': False,
                        'confidence': conf,
                        'volume_confirmed': vol_confirmed,
                        'support': round(avg_low, 2),
                        'neckline': round(neckline, 2),
                        'height_pct': round(height_pct, 2),
                        'breakout_target': round(target, 2),
                        'current_price': round(current_price, 2),
                        'risk_level': 'low',
                    }

        return None

    except Exception:
        return None


def detect_double_top(df: pd.DataFrame, ticker: str = "") -> Optional[Dict]:
    """
    Detect double top pattern (bearish reversal — M shape).

    Structure:
    1. First peak: price rallies to resistance
    2. Pullback: price drops to support (neckline)
    3. Second peak: price rallies again to ~same resistance
    4. Breakdown below neckline
    """
    try:
        if len(df) < 20:
            return None

        window = df.tail(40).copy()
        highs = window['high'].values
        lows = window['low'].values
        closes = window['close'].values
        n = len(closes)

        # Find swing highs
        swing_highs = []
        for i in range(3, n - 3):
            if highs[i] == max(highs[i-3:i+4]):
                swing_highs.append((i, highs[i]))

        if len(swing_highs) < 2:
            return None

        for i in range(len(swing_highs) - 1):
            idx1, high1 = swing_highs[i]
            for j in range(i + 1, len(swing_highs)):
                idx2, high2 = swing_highs[j]

                gap = idx2 - idx1
                if gap < 5 or gap > 30:
                    continue

                # Both tops within 3%
                avg_high = (high1 + high2) / 2
                if abs(high1 - high2) / avg_high > 0.03:
                    continue

                # Neckline: lowest between the peaks
                neckline = min(lows[idx1:idx2])

                height_pct = (avg_high - neckline) / neckline * 100
                if height_pct < 4:
                    continue

                current_price = closes[-1]

                if n - idx2 > 12:
                    continue

                if current_price > avg_high:
                    continue

                # Near or below neckline for breakdown
                if current_price <= neckline * 1.03:
                    target = neckline - (avg_high - neckline)

                    volumes = window['volume'].values
                    avg_vol = _avg_volume(df)
                    breakout_vol = volumes[-1]
                    vol_confirmed = breakout_vol > avg_vol
                    conf = 0.85 if vol_confirmed else 0.75

                    return {
                        'name': 'Double Top',
                        'type': 'reversal',
                        'bullish': False,
                        'bearish': True,
                        'confidence': conf,
                        'volume_confirmed': vol_confirmed,
                        'resistance': round(avg_high, 2),
                        'neckline': round(neckline, 2),
                        'height_pct': round(height_pct, 2),
                        'breakdown_target': round(target, 2),
                        'current_price': round(current_price, 2),
                        'risk_level': 'high',
                    }

        return None

    except Exception:
        return None


def detect_rectangle(df: pd.DataFrame, ticker: str = "") -> Optional[Dict]:
    """
    Detect rectangle / range-bound pattern.

    Price oscillates between flat support and flat resistance with at
    least 2 touches on each side.  Bullish breakout if price pushes above
    resistance; bearish breakdown if price drops below support.
    """
    try:
        if len(df) < 15:
            return None

        recent = df.tail(30).copy()
        highs = recent['high'].values
        lows = recent['low'].values
        closes = recent['close'].values

        # Flat resistance: multiple highs within 2% band
        sorted_highs = np.sort(highs)[::-1]
        resistance = np.median(sorted_highs[:5])
        touches_r = sum(1 for h in highs if abs(h - resistance) / resistance < 0.02)

        # Flat support: multiple lows within 2% band
        sorted_lows = np.sort(lows)
        support = np.median(sorted_lows[:5])
        touches_s = sum(1 for l in lows if abs(l - support) / support < 0.02)

        if touches_r < 2 or touches_s < 2:
            return None

        # Range should be meaningful but not too wide (3-12%)
        range_pct = (resistance - support) / support * 100
        if range_pct < 3 or range_pct > 15:
            return None

        # Highs and lows should both be roughly flat (no strong trend)
        x = np.arange(len(highs))
        high_slope = np.polyfit(x, highs, 1)[0] / resistance * 100
        low_slope = np.polyfit(x, lows, 1)[0] / support * 100
        if abs(high_slope) > 0.3 or abs(low_slope) > 0.3:
            return None

        current_price = closes[-1]
        rect_height = resistance - support

        # Volume: should be low during range, spike on breakout/breakdown
        avg_vol = _avg_volume(df)
        range_vol = recent['volume'].iloc[3:-1].mean() if len(recent) > 4 else avg_vol
        breakout_vol = recent.iloc[-1]['volume']
        vol_confirmed = range_vol < avg_vol * 0.9 and breakout_vol > avg_vol

        # Bullish breakout
        if current_price >= resistance * 0.98:
            target = resistance + rect_height
            conf = 0.80 if vol_confirmed else 0.65
            return {
                'name': 'Rectangle Breakout',
                'type': 'consolidation',
                'bullish': True,
                'bearish': False,
                'confidence': conf,
                'volume_confirmed': vol_confirmed,
                'support': round(support, 2),
                'resistance': round(resistance, 2),
                'touches_support': touches_s,
                'touches_resistance': touches_r,
                'range_pct': round(range_pct, 2),
                'breakout_target': round(target, 2),
                'current_price': round(current_price, 2),
                'risk_level': 'medium',
            }

        # Bearish breakdown
        if current_price <= support * 1.02:
            target = support - rect_height
            conf = 0.80 if vol_confirmed else 0.65
            return {
                'name': 'Rectangle Breakdown',
                'type': 'consolidation',
                'bullish': False,
                'bearish': True,
                'confidence': conf,
                'volume_confirmed': vol_confirmed,
                'support': round(support, 2),
                'resistance': round(resistance, 2),
                'touches_support': touches_s,
                'touches_resistance': touches_r,
                'range_pct': round(range_pct, 2),
                'breakdown_target': round(target, 2),
                'current_price': round(current_price, 2),
                'risk_level': 'high',
            }

        return None

    except Exception:
        return None


# ------------------------------------------------------------------
# VCP (Volatility Contraction Pattern) Detection — V10
# ------------------------------------------------------------------

def _find_swing_points(highs, lows, order: int = 3):
    """Find local swing highs and swing lows using N-bar lookback/lookahead.

    Returns:
        swing_highs: list of (index, price) for local maxima
        swing_lows:  list of (index, price) for local minima
    """
    n = len(highs)
    swing_highs = []
    swing_lows = []
    for i in range(order, n - order):
        # Swing high: higher than all neighbours within `order` bars
        if highs[i] == max(highs[i - order:i + order + 1]):
            swing_highs.append((i, float(highs[i])))
        # Swing low: lower than all neighbours within `order` bars
        if lows[i] == min(lows[i - order:i + order + 1]):
            swing_lows.append((i, float(lows[i])))
    return swing_highs, swing_lows


def detect_vcp(df: pd.DataFrame, ticker: str = "", mode: str = "swing") -> Optional[Dict]:
    """Detect Minervini Volatility Contraction Pattern (VCP).

    Looks for 2+ progressively shallower pullbacks with volume dry-up,
    forming a tight pivot point before a breakout.

    Args:
        df: DataFrame with lowercase OHLCV columns
        ticker: Stock ticker (for logging)
        mode: Trading mode — determines lookback window and thresholds

    Returns:
        Pattern dict with VCP-specific fields, or None if not detected.
    """
    try:
        from config import VCP_CONFIG
    except ImportError:
        return None

    if not VCP_CONFIG.get('enabled', True):
        return None

    # --- Merge mode-specific overrides ---
    cfg = dict(VCP_CONFIG)
    overrides = VCP_CONFIG.get('mode_overrides', {}).get(mode, {})
    cfg.update(overrides)

    min_bars = cfg['bar_windows'].get(mode, 60)
    if len(df) < min_bars:
        return None

    window = df.iloc[-min_bars:]
    highs = window['high'].values
    lows = window['low'].values
    closes = window['close'].values
    volumes = window['volume'].values
    n = len(window)

    # --- Step 1: Find swing highs and swing lows ---
    # Use order=3 for daily bars, order=5 for intraday (more noise)
    swing_order = 5 if mode in ('daytrade', 'scalping') else 3
    swing_highs, swing_lows = _find_swing_points(highs, lows, order=swing_order)

    if len(swing_highs) < 2 or len(swing_lows) < 1:
        return None

    # --- Step 2: Find the base high (left side of VCP) ---
    # Take the highest swing high as the anchor / pivot resistance
    base_idx, base_high = max(swing_highs, key=lambda x: x[1])

    # Need at least 1 swing low AFTER the base high
    lows_after_base = [(i, p) for i, p in swing_lows if i > base_idx]
    highs_after_base = [(i, p) for i, p in swing_highs if i > base_idx]

    if not lows_after_base:
        return None

    # --- Step 3: Identify contractions ---
    # A contraction = pullback from a high to a low, then recovery back toward resistance
    contractions = []
    prev_high_price = base_high
    prev_high_idx = base_idx

    # Alternate between swing lows and swing highs after the base
    all_swings = sorted(
        [(i, p, 'low') for i, p in lows_after_base] +
        [(i, p, 'high') for i, p in highs_after_base],
        key=lambda x: x[0]
    )

    expecting = 'low'  # First we expect a pullback (swing low)
    current_contraction = {'high': base_high, 'high_idx': base_idx}

    for idx, price, swing_type in all_swings:
        if expecting == 'low' and swing_type == 'low':
            pullback_pct = (current_contraction['high'] - price) / current_contraction['high'] * 100
            if pullback_pct > 0:
                current_contraction['low'] = price
                current_contraction['low_idx'] = idx
                current_contraction['pullback_pct'] = pullback_pct
                # Calculate average volume during this contraction
                vol_slice = volumes[current_contraction['high_idx']:idx + 1]
                current_contraction['avg_volume'] = float(np.mean(vol_slice)) if len(vol_slice) > 0 else 0
                expecting = 'high'

        elif expecting == 'high' and swing_type == 'high':
            current_contraction['recovery_high'] = price
            current_contraction['recovery_idx'] = idx
            contractions.append(dict(current_contraction))
            # Set up next contraction from this recovery high
            current_contraction = {'high': price, 'high_idx': idx}
            expecting = 'low'

    # Handle incomplete final contraction (pullback with no recovery yet = tight area)
    if 'low' in current_contraction and 'pullback_pct' in current_contraction:
        # Use current price as the "recovery" — the breakout attempt
        current_contraction['recovery_high'] = float(closes[-1])
        current_contraction['recovery_idx'] = n - 1
        contractions.append(dict(current_contraction))

    if len(contractions) < cfg['min_contractions']:
        return None

    # --- Step 4: Validate progressive tightening ---
    pullback_pcts = [c['pullback_pct'] for c in contractions]

    # First pullback must be within bounds
    first_pb = pullback_pcts[0]
    if first_pb < cfg['first_pullback_min_pct'] or first_pb > cfg['first_pullback_max_pct']:
        return None

    # Each subsequent pullback must be shallower (allow some tolerance)
    for i in range(1, len(pullback_pcts)):
        ratio = pullback_pcts[i] / pullback_pcts[i - 1]
        if ratio > 0.95:  # Not contracting enough (must shrink by at least 5%)
            return None

    # --- Step 5: Higher lows ---
    contraction_lows = [c['low'] for c in contractions]
    for i in range(1, len(contraction_lows)):
        if contraction_lows[i] < contraction_lows[i - 1] * 0.99:  # 1% tolerance
            return None

    # --- Step 6: Volume dry-up ---
    contraction_vols = [c['avg_volume'] for c in contractions if c['avg_volume'] > 0]
    vol_dryup = True
    if len(contraction_vols) >= 2:
        for i in range(1, len(contraction_vols)):
            if contraction_vols[i] > contraction_vols[i - 1] * cfg['vol_dryup_threshold']:
                # Volume didn't decrease enough in one transition — not a deal breaker
                # but reduces quality
                pass
        # Overall: final contraction volume vs first
        vol_dryup = contraction_vols[-1] < contraction_vols[0] * cfg['vol_dryup_threshold']

    # --- Step 7: Pivot point and tight area ---
    # Pivot = highest swing high from the contractions (resistance)
    recovery_highs = [base_high] + [c.get('recovery_high', 0) for c in contractions]
    pivot = max(recovery_highs)

    # Tight area: range of last 5 bars (or fewer)
    tail_bars = min(5, n)
    tail_high = float(np.max(highs[-tail_bars:]))
    tail_low = float(np.min(lows[-tail_bars:]))
    tight_range_pct = (tail_high - tail_low) / tail_low * 100 if tail_low > 0 else 999

    # --- Step 8: Proximity check ---
    current_price = float(closes[-1])
    dist_to_pivot_pct = (pivot - current_price) / pivot * 100  # positive = below pivot
    above_pivot_pct = (current_price - pivot) / pivot * 100  # positive = above pivot

    # Must be within proximity or have broken above (but not chasing)
    if dist_to_pivot_pct > cfg['pivot_proximity_pct'] and above_pivot_pct < 0:
        return None  # Too far below pivot
    if above_pivot_pct > cfg['max_chase_pct']:
        return None  # Already ran too far above pivot — don't chase

    # --- Step 9: Quality score (0.0 - 1.0) ---
    quality = 0.0
    num_contractions = len(contractions)

    # Number of contractions (2=base, 3=good, 4+=excellent)
    quality += min(0.25, (num_contractions - 1) * 0.083)

    # Decay quality: how much each pullback shrinks relative to prior
    decay_ratios = [pullback_pcts[i] / pullback_pcts[i - 1] for i in range(1, len(pullback_pcts))]
    avg_decay = float(np.mean(decay_ratios)) if decay_ratios else 1.0
    quality += 0.25 * (1.0 - avg_decay)  # Lower decay ratio = better quality

    # Volume dry-up quality
    if len(contraction_vols) >= 2 and contraction_vols[0] > 0:
        vol_decline = 1.0 - (contraction_vols[-1] / contraction_vols[0])
        quality += 0.20 * max(0.0, vol_decline)

    # Tight area quality (tighter = better)
    if tight_range_pct <= cfg['final_tight_range_pct']:
        quality += 0.15 * max(0.0, 1.0 - tight_range_pct / cfg['final_tight_range_pct'])

    # Proximity to pivot (closer = better, above pivot = best)
    if above_pivot_pct >= 0:
        quality += 0.15  # Already breaking out — full proximity score
    else:
        quality += 0.15 * max(0.0, 1.0 - abs(dist_to_pivot_pct) / cfg['pivot_proximity_pct'])

    quality = min(1.0, quality)

    # Minimum quality gate
    if quality < 0.15:
        return None

    # --- Stop and target ---
    final_low = contraction_lows[-1]
    vcp_stop = final_low * (1 - cfg['stop_buffer_pct'] / 100)
    # Measured move: pivot + depth of first contraction
    first_contraction_depth = base_high - contraction_lows[0]
    breakout_target = pivot + first_contraction_depth

    return {
        'name': 'VCP',
        'type': 'consolidation',
        'bullish': True,
        'bearish': False,
        'confidence': min(0.95, 0.65 + quality * 0.30),
        'volume_confirmed': vol_dryup,
        'vcp_quality': round(quality, 3),
        'num_contractions': num_contractions,
        'pullback_depths': [round(p, 1) for p in pullback_pcts],
        'pivot_point': round(pivot, 2),
        'vcp_stop': round(vcp_stop, 2),
        'breakout_target': round(breakout_target, 2),
        'tight_range_pct': round(tight_range_pct, 2),
        'current_price': round(current_price, 2),
        'risk_level': 'low',
    }


# ------------------------------------------------------------------
# Candlestick Pattern Detection
# ------------------------------------------------------------------

def _body(row):
    """Absolute candle body size."""
    return abs(row['close'] - row['open'])

def _upper_shadow(row):
    return row['high'] - max(row['close'], row['open'])

def _lower_shadow(row):
    return min(row['close'], row['open']) - row['low']

def _is_bullish(row):
    return row['close'] > row['open']

def _is_bearish(row):
    return row['close'] < row['open']

def _candle_range(row):
    return row['high'] - row['low']


def detect_candle_patterns(df: pd.DataFrame, ticker: str = "") -> List[Dict]:
    """
    Detect single and multi-candle reversal/continuation patterns
    on the last few bars.

    Returns list of pattern dicts (same schema as chart patterns).
    """
    if len(df) < 5:
        return []

    results = []
    recent = df.tail(5)
    c = recent.iloc[-1]   # current candle
    p = recent.iloc[-2]   # previous candle
    pp = recent.iloc[-3]  # two candles ago

    rng = _candle_range(c)
    if rng == 0:
        return results

    body_c = _body(c)
    lower_c = _lower_shadow(c)
    upper_c = _upper_shadow(c)

    # Volume context for candle confirmation
    avg_vol = _avg_volume(df)
    c_vol = c.get('volume', 0) if hasattr(c, 'get') else c['volume']
    candle_vol_high = c_vol > avg_vol * 1.1  # above-average volume confirms candle

    # ---- Hammer (bullish reversal) ----
    # Small body at the top, long lower shadow >= 2x body, tiny upper shadow
    if (body_c > 0
            and lower_c >= 2 * body_c
            and upper_c <= body_c * 0.3
            and _is_bearish(p)):
        results.append({
            'name': 'Hammer',
            'type': 'candle',
            'bullish': True,
            'bearish': False,
            'confidence': 0.70 if candle_vol_high else 0.60,
            'volume_confirmed': candle_vol_high,
            'current_price': round(c['close'], 2),
            'risk_level': 'low',
        })

    # ---- Inverted Hammer (bullish reversal after downtrend) ----
    # Small body at the bottom, long upper shadow >= 2x body, tiny lower shadow
    if (body_c > 0
            and upper_c >= 2 * body_c
            and lower_c <= body_c * 0.3
            and _is_bearish(p)):
        results.append({
            'name': 'Inverted Hammer',
            'type': 'candle',
            'bullish': True,
            'bearish': False,
            'confidence': 0.65 if candle_vol_high else 0.55,
            'volume_confirmed': candle_vol_high,
            'current_price': round(c['close'], 2),
            'risk_level': 'low',
        })

    # ---- Hanging Man (bearish reversal after uptrend) ----
    # Same shape as hammer but after an uptrend
    if (body_c > 0
            and lower_c >= 2 * body_c
            and upper_c <= body_c * 0.3
            and _is_bullish(p)
            and _is_bullish(pp)):
        results.append({
            'name': 'Hanging Man',
            'type': 'candle',
            'bullish': False,
            'bearish': True,
            'confidence': 0.65 if candle_vol_high else 0.55,
            'volume_confirmed': candle_vol_high,
            'current_price': round(c['close'], 2),
            'risk_level': 'medium',
        })

    # ---- Bullish Engulfing ----
    # Previous candle red, current candle green and body fully engulfs previous body
    body_p = _body(p)
    if (body_c > 0 and body_p > 0
            and _is_bearish(p)
            and _is_bullish(c)
            and c['open'] <= p['close']
            and c['close'] >= p['open']
            and body_c > body_p):
        results.append({
            'name': 'Bullish Engulfing',
            'type': 'candle',
            'bullish': True,
            'bearish': False,
            'confidence': 0.80 if candle_vol_high else 0.70,
            'volume_confirmed': candle_vol_high,
            'current_price': round(c['close'], 2),
            'risk_level': 'low',
        })

    # ---- Bearish Engulfing ----
    # Previous candle green, current candle red and body fully engulfs previous body
    if (body_c > 0 and body_p > 0
            and _is_bullish(p)
            and _is_bearish(c)
            and c['open'] >= p['close']
            and c['close'] <= p['open']
            and body_c > body_p):
        results.append({
            'name': 'Bearish Engulfing',
            'type': 'candle',
            'bullish': False,
            'bearish': True,
            'confidence': 0.80 if candle_vol_high else 0.70,
            'volume_confirmed': candle_vol_high,
            'current_price': round(c['close'], 2),
            'risk_level': 'medium',
        })

    # ---- Bullish Harami ----
    # Previous large red candle, current small green candle contained inside it
    if (body_c > 0 and body_p > 0
            and _is_bearish(p)
            and _is_bullish(c)
            and c['open'] >= p['close']
            and c['close'] <= p['open']
            and body_c < body_p * 0.6):
        results.append({
            'name': 'Bullish Harami',
            'type': 'candle',
            'bullish': True,
            'bearish': False,
            'confidence': 0.65 if candle_vol_high else 0.55,
            'volume_confirmed': candle_vol_high,
            'current_price': round(c['close'], 2),
            'risk_level': 'low',
        })

    # ---- Bearish Harami ----
    # Previous large green candle, current small red candle contained inside it
    if (body_c > 0 and body_p > 0
            and _is_bullish(p)
            and _is_bearish(c)
            and c['open'] <= p['close']
            and c['close'] >= p['open']
            and body_c < body_p * 0.6):
        results.append({
            'name': 'Bearish Harami',
            'type': 'candle',
            'bullish': False,
            'bearish': True,
            'confidence': 0.65 if candle_vol_high else 0.55,
            'volume_confirmed': candle_vol_high,
            'current_price': round(c['close'], 2),
            'risk_level': 'medium',
        })

    # ---- Morning Star (3-candle bullish reversal) ----
    # 1st: large red, 2nd: small body (gap down), 3rd: large green closing into 1st body
    body_pp = _body(pp)
    if (body_pp > 0 and body_c > 0
            and _is_bearish(pp)
            and _body(p) < body_pp * 0.4
            and _is_bullish(c)
            and c['close'] > (pp['open'] + pp['close']) / 2
            and body_c > body_pp * 0.5):
        results.append({
            'name': 'Morning Star',
            'type': 'candle',
            'bullish': True,
            'bearish': False,
            'confidence': 0.85 if candle_vol_high else 0.75,
            'volume_confirmed': candle_vol_high,
            'current_price': round(c['close'], 2),
            'risk_level': 'low',
        })

    # ---- Evening Star (3-candle bearish reversal) ----
    # 1st: large green, 2nd: small body (gap up), 3rd: large red closing into 1st body
    if (body_pp > 0 and body_c > 0
            and _is_bullish(pp)
            and _body(p) < body_pp * 0.4
            and _is_bearish(c)
            and c['close'] < (pp['open'] + pp['close']) / 2
            and body_c > body_pp * 0.5):
        results.append({
            'name': 'Evening Star',
            'type': 'candle',
            'bullish': False,
            'bearish': True,
            'confidence': 0.85 if candle_vol_high else 0.75,
            'volume_confirmed': candle_vol_high,
            'current_price': round(c['close'], 2),
            'risk_level': 'high',
        })

    # ---- Doji (indecision — bullish if after downtrend) ----
    if body_c <= rng * 0.1 and rng > 0:
        # Check context: after 3 red candles → bullish doji
        prior_bearish = sum(1 for i in range(-4, -1) if _is_bearish(recent.iloc[i]))
        prior_bullish = sum(1 for i in range(-4, -1) if _is_bullish(recent.iloc[i]))
        if prior_bearish >= 2:
            results.append({
                'name': 'Bullish Doji',
                'type': 'candle',
                'bullish': True,
                'bearish': False,
                'confidence': 0.60 if candle_vol_high else 0.50,
                'volume_confirmed': candle_vol_high,
                'current_price': round(c['close'], 2),
                'risk_level': 'low',
            })
        elif prior_bullish >= 2:
            results.append({
                'name': 'Bearish Doji',
                'type': 'candle',
                'bullish': False,
                'bearish': True,
                'confidence': 0.60 if candle_vol_high else 0.50,
                'volume_confirmed': candle_vol_high,
                'current_price': round(c['close'], 2),
                'risk_level': 'medium',
            })

    return results


def detect_patterns_from_df(df: pd.DataFrame, ticker: str = "", mode: str = "swing") -> List[Dict]:
    """
    Run all pattern detectors on a pre-fetched DataFrame.

    Args:
        df: DataFrame with lowercase OHLCV columns (open, high, low, close, volume)
        ticker: Stock ticker (for display)
        mode: Trading mode (for VCP bar window sizing)

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
        detect_cup_and_handle,
        detect_inverse_head_and_shoulders,
        detect_head_and_shoulders,
        detect_double_bottom,
        detect_double_top,
        detect_rectangle,
    ]

    for detector in detectors:
        result = detector(df, ticker)
        if result:
            patterns.append(result)

    # VCP detector (needs mode parameter for bar window sizing)
    vcp_result = detect_vcp(df, ticker, mode=mode)
    if vcp_result:
        patterns.append(vcp_result)

    # Candlestick patterns (single & multi-candle)
    candle_patterns = detect_candle_patterns(df, ticker)
    patterns.extend(candle_patterns)

    return patterns


def get_pattern_score(df: pd.DataFrame, ticker: str = "", mode: str = "swing") -> tuple:
    """
    Get pattern scoring summary for integration with breakout scanner.

    Args:
        df: DataFrame with lowercase OHLCV columns
        ticker: Stock ticker
        mode: Trading mode (for VCP detection)

    Returns:
        Tuple of (has_bullish, has_bearish, best_target, pattern_names, volume_confirmed,
                  vcp_quality, vcp_data)
        - has_bullish: True if any bullish pattern detected
        - has_bearish: True if any bearish pattern detected
        - best_target: Highest breakout target price from patterns (0.0 if none)
        - pattern_names: List of detected pattern names
        - volume_confirmed: True if any pattern has volume confirmation
        - vcp_quality: VCP quality score 0.0-1.0 (0.0 if no VCP detected)
        - vcp_data: VCP pattern dict or None
    """
    patterns = detect_patterns_from_df(df, ticker, mode)

    has_bullish = False
    has_bearish = False
    best_target = 0.0
    pattern_names = []
    any_vol_confirmed = False
    vcp_quality = 0.0
    vcp_data = None

    for p in patterns:
        pattern_names.append(p['name'])

        if p.get('bullish'):
            has_bullish = True
        if p.get('bearish'):
            has_bearish = True
        if p.get('volume_confirmed'):
            any_vol_confirmed = True

        target = p.get('breakout_target', 0.0)
        if target and target > best_target:
            best_target = target

        # Extract VCP-specific data
        if p.get('name') == 'VCP':
            vcp_quality = p.get('vcp_quality', 0.0)
            vcp_data = p

    return has_bullish, has_bearish, best_target, pattern_names, any_vol_confirmed, vcp_quality, vcp_data
