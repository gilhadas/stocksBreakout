"""
quantkit.regime
===============
Market regime detection — pure pandas logic, no data fetching.

Caller provides a DataFrame of SPY (or any market-proxy) daily bars.
Accepts both yfinance-style uppercase columns (Open, High, Low, Close, Volume)
and lowercase OHLCV columns.

Quick start
-----------
    from quantkit.regime import detect_regime, suggest_params

    regime, metrics = detect_regime(spy_df)   # 'bull' | 'bear' | 'mixed'
    params = suggest_params('swing', regime)

Regime rules
------------
    bull  — trend_score ≥ 1  AND vol_score ≤ 1  AND win_rate > 55%
    bear  — trend_score ≤ -1 AND vol_score ≥ 1
    mixed — everything else

Trend score
    +2 : price > SMA50 > SMA200  (strong uptrend)
    +1 : price > SMA50 only
    -1 : price < SMA50 only
    -2 : price < SMA50 < SMA200  (strong downtrend)

Volatility score
    2 : ATR% > 2.5 OR BB-width > 10  (high volatility)
    1 : ATR% > 1.5 OR BB-width > 6   (moderate)
    0 : otherwise                     (low)
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


# ── Regime Parameter Sets ────────────────────────────────────────────────────
# Optimized from backtests (Mar 2026)

REGIME_PARAMS: Dict[str, Dict[str, Dict]] = {
    'swing': {
        'bull': {
            'vol_thresh':     0.85,
            'atr_mult':       0.78,
            'sl_mult':        3.04,
            'tp_mult':        12.11,
            'min_rr':         0.53,
            'minervini_min':  3,
            'quality_filter': 'PREMIUM',
            'description':    'Aggressive: tight stops, wide targets (Bull 2023: +18%)',
        },
        'mixed': {
            'vol_thresh':     0.90,
            'atr_mult':       0.75,
            'sl_mult':        3.00,
            'tp_mult':        10.00,
            'min_rr':         0.55,
            'minervini_min':  2,
            'quality_filter': 'HIGH',
            'description':    'Balanced: mixed volatility (Mixed 2024H1: +29%)',
        },
        'bear': {
            'vol_thresh':     1.20,
            'atr_mult':       0.85,
            'sl_mult':        2.50,
            'tp_mult':        6.00,
            'min_rr':         0.80,
            'minervini_min':  5,
            'quality_filter': 'PREMIUM',
            'description':    'Defensive: wider stops, tight targets',
        },
    },
    'daytrade': {
        'bull': {
            'vol_thresh':     0.95,
            'atr_mult':       0.60,
            'sl_mult':        3.01,
            'tp_mult':        5.47,
            'min_rr':         0.82,
            'minervini_min':  1,
            'quality_filter': 'STANDARD',
            'description':    'Aggressive: fast entries, frequent exits (Bull 2023: +23%)',
        },
        'mixed': {
            'vol_thresh':     1.00,
            'atr_mult':       0.65,
            'sl_mult':        3.00,
            'tp_mult':        5.00,
            'min_rr':         0.80,
            'minervini_min':  2,
            'quality_filter': 'HIGH',
            'description':    'Balanced: moderate turnover (Mixed 2024H1: +24%)',
        },
        'bear': {
            'vol_thresh':     1.30,
            'atr_mult':       0.75,
            'sl_mult':        2.50,
            'tp_mult':        4.00,
            'min_rr':         1.00,
            'minervini_min':  4,
            'quality_filter': 'PREMIUM',
            'description':    'Defensive: high RR, few trades',
        },
    },
}


# ── Column normalisation helper ───────────────────────────────────────────────

def _series(df: pd.DataFrame, col_upper: str, col_lower: str) -> pd.Series:
    """Return a plain Series regardless of column casing or MultiIndex."""
    if col_lower in df.columns:
        s = df[col_lower]
    elif col_upper in df.columns:
        s = df[col_upper]
    else:
        raise KeyError(f"Column '{col_upper}' / '{col_lower}' not found in DataFrame")
    return s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s


# ── Core detection ────────────────────────────────────────────────────────────

def detect_regime(df: pd.DataFrame) -> Tuple[str, Dict]:
    """Detect market regime from a daily OHLCV DataFrame (SPY or equivalent).

    Parameters
    ----------
    df : DataFrame with at least 20 rows.  Accepts uppercase (yfinance) or
         lowercase (normalised) OHLCV columns.  MultiIndex columns are handled.

    Returns
    -------
    (regime, metrics)
        regime  : 'bull' | 'bear' | 'mixed'
        metrics : dict of intermediate values for diagnostics.
    """
    if df is None or len(df) < 20:
        return 'mixed', {}

    close = _series(df, 'Close', 'close')
    high  = _series(df, 'High',  'high')
    low   = _series(df, 'Low',   'low')
    open_ = _series(df, 'Open',  'open')
    vol   = _series(df, 'Volume','volume')

    current_price = float(close.iloc[-1])

    # ── Trend ─────────────────────────────────────────────────────────────────
    sma50_val  = float(close.rolling(50).mean().iloc[-1])  if len(df) >= 50  else current_price
    sma200_val = float(close.rolling(200).mean().iloc[-1]) if len(df) >= 200 else current_price

    if current_price > sma50_val and sma50_val > sma200_val:
        trend_score = 2
    elif current_price > sma50_val:
        trend_score = 1
    elif current_price < sma50_val and sma50_val < sma200_val:
        trend_score = -2
    elif current_price < sma50_val:
        trend_score = -1
    else:
        trend_score = 0

    # ── Volatility ────────────────────────────────────────────────────────────
    atr_raw  = (high - low).rolling(14).mean().iloc[-1]
    atr      = float(atr_raw) if pd.notna(atr_raw) else 0.0
    atr_pct  = (atr / current_price * 100) if atr > 0 else 0.0

    bb_mid_raw = close.rolling(20).mean().iloc[-1]
    bb_std_raw = close.rolling(20).std().iloc[-1]
    bb_mid = float(bb_mid_raw) if pd.notna(bb_mid_raw) else 0.0
    bb_std = float(bb_std_raw) if pd.notna(bb_std_raw) else 0.0
    bb_width = (2 * bb_std / bb_mid * 100) if bb_mid > 0 else 0.0

    if atr_pct > 2.5 or bb_width > 10:
        vol_score = 2
    elif atr_pct > 1.5 or bb_width > 6:
        vol_score = 1
    else:
        vol_score = 0

    # ── Win-rate proxy ────────────────────────────────────────────────────────
    win_rate = float((close > open_).sum()) / len(close)

    # ── Volume trend ──────────────────────────────────────────────────────────
    avg_vol_recent = float(vol.iloc[-20:].mean()) if len(vol) >= 20 else 0.0
    avg_vol_hist   = float(vol.iloc[-60:-20].mean()) if len(vol) >= 60 else avg_vol_recent
    vol_trend      = avg_vol_recent / avg_vol_hist if avg_vol_hist > 0 else 1.0

    # ── Classification ────────────────────────────────────────────────────────
    if trend_score >= 1 and vol_score <= 1 and win_rate > 0.55:
        regime = 'bull'
    elif trend_score <= -1 and vol_score >= 1:
        regime = 'bear'
    else:
        regime = 'mixed'

    metrics = {
        'price':       current_price,
        'sma50':       sma50_val,
        'sma200':      sma200_val,
        'atr_pct':     round(atr_pct, 3),
        'bb_width':    round(bb_width, 3),
        'win_rate':    round(win_rate, 3),
        'vol_trend':   round(vol_trend, 3),
        'trend_score': trend_score,
        'vol_score':   vol_score,
    }

    return regime, metrics


# ── Parameter lookup ──────────────────────────────────────────────────────────

def suggest_params(mode: str, regime: str) -> Dict:
    """Return a copy of the optimised parameter dict for mode + regime.

    Parameters
    ----------
    mode   : 'swing' | 'daytrade'  (keys in REGIME_PARAMS)
    regime : 'bull' | 'bear' | 'mixed'

    Returns
    -------
    Dict of params, or empty dict if combination not found.
    """
    params = REGIME_PARAMS.get(mode, {}).get(regime, {})
    if not params:
        logger.warning("No params for mode=%s, regime=%s", mode, regime)
        return {}
    return params.copy()
