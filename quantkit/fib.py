"""
quantkit.fib
============
Fibonacci retracement scoring — pure pandas/numpy, no data fetching.

Caller provides a DataFrame with lowercase OHLCV columns
(open, high, low, close, volume) and at least 40 bars.

Quick start
-----------
    from quantkit.fib import detect_swing, score_bounce

    swing = detect_swing(df)
    if swing:
        result = score_bounce(df, swing)
        print(result['bounce_score'], result['nearest_fib'])

Score components (0–100)
------------------------
    +30  price within 2% of a classic Fib level (38.2%, 50%, 61.8%)
    +25  SMA 50/150/200 within 1.5% of that level (confluence)
    +15  Stage 2 context (SMA50 > SMA150 > SMA200 AND price > SMA200)
    +15  RSI in 35–50 reset zone
    +10  volume expansion: 3-day avg ≥ 1.2× 20-day avg
    + 5  level is 50% or 61.8% (golden pocket)

Math
----
    fib_price(r) = H − r × (H − L)
    retraced_pct = (H − C) / (H − L) × 100
    where H = swing high, L = swing low, C = current close,
          r ∈ {0.236, 0.382, 0.5, 0.618, 0.786}
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

FIB_RATIOS: List[float] = [0.236, 0.382, 0.5, 0.618, 0.786]

FIB_LABELS: Dict[float, str] = {
    0.236: '23.6%',
    0.382: '38.2%',
    0.5:   '50%',
    0.618: '61.8%',
    0.786: '78.6%',
}

CLASSIC_LEVELS = {0.382, 0.5, 0.618}   # primary bounce zone
GOLDEN_POCKET  = {0.5, 0.618}          # extra weight

DEFAULT_SWING_WINDOW = 120  # bars to search for the swing high


# ── RSI (Wilder) ─────────────────────────────────────────────────────────────

def _rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


# ── Swing detection ──────────────────────────────────────────────────────────

def detect_swing(df: pd.DataFrame, window: int = DEFAULT_SWING_WINDOW) -> Optional[Dict]:
    """Find the most recent swing high and the swing low that preceded it.

    Parameters
    ----------
    df     : OHLCV DataFrame with lowercase column names.
    window : bars to scan for the swing high (default 120).

    Returns
    -------
    Dict with keys:
        swing_high, swing_high_date, swing_low, swing_low_date, range
    or None if insufficient data or flat range.
    """
    if df is None or len(df) < 30:
        return None

    tail    = df.iloc[-window:] if len(df) >= window else df
    hi_idx  = tail['high'].idxmax()
    swing_high = float(tail['high'].loc[hi_idx])

    before_high = tail.loc[:hi_idx]
    if len(before_high) < 2:
        return None

    lo_idx  = before_high['low'].idxmin()
    swing_low = float(before_high['low'].loc[lo_idx])

    if swing_high <= swing_low:
        return None

    return {
        'swing_high':      swing_high,
        'swing_high_date': str(hi_idx.date()) if hasattr(hi_idx, 'date') else str(hi_idx),
        'swing_low':       swing_low,
        'swing_low_date':  str(lo_idx.date()) if hasattr(lo_idx, 'date') else str(lo_idx),
        'range':           swing_high - swing_low,
    }


# ── Fib level helpers ─────────────────────────────────────────────────────────

def fib_levels(swing: Dict) -> Dict[float, float]:
    """Return {ratio: price} for each Fibonacci level (retraced from high)."""
    hi  = swing['swing_high']
    rng = swing['range']
    return {r: hi - r * rng for r in FIB_RATIOS}


def nearest_fib_to_price(
    levels: Dict[float, float], price: float
) -> Tuple[float, float, float]:
    """Return (ratio, level_price, distance_pct) of the Fib level closest to price."""
    ratios = list(levels.keys())
    prices = [levels[r] for r in ratios]
    i      = int(np.argmin([abs(p - price) for p in prices]))
    ratio  = ratios[i]
    lp     = prices[i]
    dist   = (price - lp) / lp * 100 if lp else 0.0
    return ratio, lp, dist


# ── Composite bounce score ───────────────────────────────────────────────────

def score_bounce(df: pd.DataFrame, swing: Dict) -> Dict:
    """Compute composite bounce score (0–100) and full component breakdown.

    Parameters
    ----------
    df    : OHLCV DataFrame (at least 50+ bars recommended).
    swing : output of :func:`detect_swing`.

    Returns
    -------
    Dict with keys: current, swing_low, swing_high, swing_high_date,
        retraced_pct, nearest_fib, nearest_fib_ratio, nearest_fib_price,
        dist_to_fib_pct, sma_confluence, stage2, rsi, rsi_reset,
        vol_ratio_3d, vol_expansion, bounce_score.
    """
    close  = float(df['close'].iloc[-1])
    levels = fib_levels(swing)
    ratio, fib_price, dist_pct = nearest_fib_to_price(levels, close)

    retraced_pct = (swing['swing_high'] - close) / swing['range'] * 100

    sma50  = float(df['close'].rolling(50).mean().iloc[-1])  if len(df) >= 50  else np.nan
    sma150 = float(df['close'].rolling(150).mean().iloc[-1]) if len(df) >= 150 else np.nan
    sma200 = float(df['close'].rolling(200).mean().iloc[-1]) if len(df) >= 200 else np.nan

    def _within(a: float, b: float, pct: float) -> bool:
        if np.isnan(a) or np.isnan(b) or b == 0:
            return False
        return abs(a - b) / b * 100 <= pct

    sma_confluence = ''
    for tag, val in (('SMA50', sma50), ('SMA150', sma150), ('SMA200', sma200)):
        if _within(val, fib_price, 1.5):
            sma_confluence = tag
            break

    stage2 = (
        not np.isnan(sma50) and not np.isnan(sma150) and not np.isnan(sma200)
        and sma50 > sma150 > sma200
        and close > sma200
    )

    rsi_series = _rsi_wilder(df['close'])
    rsi        = float(rsi_series.iloc[-1]) if not rsi_series.empty else np.nan
    rsi_reset  = (not np.isnan(rsi)) and (35 <= rsi <= 50)

    vol20 = float(df['volume'].rolling(20).mean().iloc[-1]) if len(df) >= 20 else 0.0
    vol3  = float(df['volume'].iloc[-3:].mean())            if len(df) >= 3  else 0.0
    vol_expansion = vol20 > 0 and (vol3 / vol20) >= 1.2

    score = 0
    at_classic = ratio in CLASSIC_LEVELS and abs(dist_pct) <= 2.0
    if at_classic:
        score += 30
    if sma_confluence and at_classic:
        score += 25
    if stage2:
        score += 15
    if rsi_reset:
        score += 15
    if vol_expansion:
        score += 10
    if at_classic and ratio in GOLDEN_POCKET:
        score += 5

    return {
        'current':           round(close, 2),
        'swing_low':         round(swing['swing_low'], 2),
        'swing_high':        round(swing['swing_high'], 2),
        'swing_high_date':   swing['swing_high_date'],
        'retraced_pct':      round(retraced_pct, 1),
        'nearest_fib':       FIB_LABELS[ratio],
        'nearest_fib_ratio': ratio,
        'nearest_fib_price': round(fib_price, 2),
        'dist_to_fib_pct':   round(dist_pct, 2),
        'sma_confluence':    sma_confluence,
        'stage2':            stage2,
        'rsi':               round(rsi, 1) if not np.isnan(rsi) else None,
        'rsi_reset':         rsi_reset,
        'vol_ratio_3d':      round(vol3 / vol20, 2) if vol20 else 0.0,
        'vol_expansion':     vol_expansion,
        'bounce_score':      score,
    }


# ── Batch helper ─────────────────────────────────────────────────────────────

def scan_dataframes(
    symbol_dfs: Dict[str, pd.DataFrame], min_score: int = 60
) -> List[Dict]:
    """Score multiple symbols from a dict of DataFrames.

    Parameters
    ----------
    symbol_dfs : {'AAPL': df, 'MSFT': df, ...}
    min_score  : minimum bounce_score to include in output.

    Returns
    -------
    List of result dicts sorted by bounce_score descending.
    Each dict includes a 'symbol' key.
    """
    results: List[Dict] = []
    for sym, df in symbol_dfs.items():
        try:
            swing = detect_swing(df)
            if swing is None:
                continue
            info = score_bounce(df, swing)
            if info['bounce_score'] < min_score:
                continue
            info['symbol'] = sym
            results.append(info)
        except Exception as exc:
            logger.debug(f"{sym}: skip — {exc}")
    results.sort(key=lambda r: r['bounce_score'], reverse=True)
    return results
