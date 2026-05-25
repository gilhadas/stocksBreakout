"""
quantkit.portfolio.exit_logic
=============================
Exit evaluation for long positions — decoupled from any project config.

Pass your own ``config`` dict or use ``DEFAULT_EXIT_CONFIG`` as a template.
The evaluator computes technical indicators internally using quantkit.indicators.

Quick start
-----------
    from quantkit.portfolio.exit_logic import ExitEvaluator, DEFAULT_EXIT_CONFIG

    evaluator = ExitEvaluator()

    result = evaluator.evaluate(
        df,
        symbol='AAPL',
        mode_cfg=DEFAULT_EXIT_CONFIG,
        entry_price=180.0,
        stop_price=170.0,
        target_price=210.0,
        timeframe='1d',
    )
    print(result['Action'], result['Reason'])   # 'HOLD' | 'TRAIL' | 'EXIT_PARTIAL' | 'EXIT_FULL'

Config keys
-----------
    trend_type       : 'EMA' | 'SMA'
    trend_period     : int
    atr_mult         : float  (ATR multiplier for trailing stop width)
    sl_mult          : float  (initial stop: entry − sl_mult × ATR)
    tp_mult          : float  (take-profit: entry + tp_mult × ATR)
    min_rr           : float  (minimum R:R to stay in trade)
    trail_activation : float  (R multiple at which trailing stop activates)
    partial_exit_r   : float  (R multiple for partial-exit trigger)
    partial_exit_pct : float  (fraction to sell on partial exit, 0.0–1.0)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from quantkit.indicators import calculate_all_indicators

logger = logging.getLogger(__name__)


# ── Default config ────────────────────────────────────────────────────────────

DEFAULT_EXIT_CONFIG: Dict[str, Any] = {
    'trend_type':       'EMA',
    'trend_period':     21,
    'atr_mult':         0.75,
    'sl_mult':          3.0,
    'tp_mult':          10.0,
    'min_rr':           0.55,
    'trail_activation': 1.5,    # activate trail once unrealized R ≥ 1.5
    'partial_exit_r':   2.0,    # partial exit at 2R
    'partial_exit_pct': 0.5,    # sell 50% on partial
}


# ── Evaluator ─────────────────────────────────────────────────────────────────

class ExitEvaluator:
    """Evaluate exit conditions for an active long position.

    Parameters
    ----------
    min_data_bars : Minimum bars needed before generating signals (default 30).
    """

    def __init__(self, min_data_bars: int = 30) -> None:
        self.min_data_bars = min_data_bars

    def evaluate(
        self,
        df: pd.DataFrame,
        symbol: str,
        mode_cfg: Optional[Dict[str, Any]] = None,
        entry_price: float = 0.0,
        stop_price: float = 0.0,
        target_price: float = 0.0,
        timeframe: str = '1d',
        regime: str = 'NORMAL',
        days_held: int = 0,
        tp_reached: bool = False,
        signal_type: str = '',
    ) -> Dict[str, Any]:
        """Evaluate exit conditions for a LONG position.

        Parameters
        ----------
        df            : OHLCV DataFrame (lowercase columns).
        symbol        : Ticker for logging.
        mode_cfg      : Config dict (see module docstring).  Defaults to DEFAULT_EXIT_CONFIG.
        entry_price   : Fill price at entry.
        stop_price    : Current hard stop level.
        target_price  : Original take-profit level.
        timeframe     : Bar timeframe string (e.g. '1d', '5min').
        regime        : Market regime label (informational).
        days_held     : Calendar days since entry.
        tp_reached    : Whether TP has been hit already.
        signal_type   : Original signal type label.

        Returns
        -------
        Dict with keys: Symbol, Mode, Action, Reason, Price,
                        UnrealizedR, DaysHeld.
        Action is one of: HOLD | TRAIL | EXIT_PARTIAL | EXIT_FULL
        """
        cfg = mode_cfg or DEFAULT_EXIT_CONFIG

        def _hold(reason: str, price: float = 0.0) -> Dict:
            return {
                'Symbol':      symbol,
                'Action':      'HOLD',
                'Reason':      reason,
                'Price':       price,
                'UnrealizedR': 0.0,
                'DaysHeld':    days_held,
            }

        min_bars = max(self.min_data_bars, cfg.get('trend_period', 21))
        if len(df) < min_bars:
            return _hold('Insufficient data')

        df = calculate_all_indicators(
            df,
            cfg.get('trend_type', 'EMA'),
            cfg.get('trend_period', 21),
            timeframe,
        )
        latest = df.iloc[-1]

        price      = float(latest['close'])
        trend_line = float(latest.get('Trend_Line', price))
        atr        = float(latest.get('ATR', price * 0.02))
        rsi        = float(latest.get('RSI', 50))

        # Guard against near-zero risk (stale stop or rounding)
        risk         = max(entry_price - stop_price, entry_price * 0.01)
        unrealized_r = (price - entry_price) / risk

        # ── TP reached → activate trailing stop ──────────────────────────────
        if not tp_reached and price >= target_price and price > entry_price:
            tp_reached = True

        if tp_reached:
            trail_stop = price - cfg.get('atr_mult', 0.75) * atr
            if price < trail_stop or price < trend_line:
                return {
                    'Symbol':      symbol,
                    'Action':      'EXIT_FULL',
                    'Reason':      f'TP+trail: price {price:.2f} < trail {trail_stop:.2f}',
                    'Price':       price,
                    'UnrealizedR': unrealized_r,
                    'DaysHeld':    days_held,
                }
            return {
                'Symbol':      symbol,
                'Action':      'TRAIL',
                'Reason':      f'TP reached, trailing stop at {trail_stop:.2f}',
                'Price':       price,
                'UnrealizedR': unrealized_r,
                'DaysHeld':    days_held,
            }

        # ── Hard stop ─────────────────────────────────────────────────────────
        if price <= stop_price:
            return {
                'Symbol':      symbol,
                'Action':      'EXIT_FULL',
                'Reason':      f'Stop hit: {price:.2f} ≤ {stop_price:.2f}',
                'Price':       price,
                'UnrealizedR': unrealized_r,
                'DaysHeld':    days_held,
            }

        # ── Partial exit at partial_exit_r ────────────────────────────────────
        if unrealized_r >= cfg.get('partial_exit_r', 2.0):
            return {
                'Symbol':      symbol,
                'Action':      'EXIT_PARTIAL',
                'Reason':      f'{unrealized_r:.1f}R — partial exit {int(cfg.get("partial_exit_pct", 0.5)*100)}%',
                'Price':       price,
                'UnrealizedR': unrealized_r,
                'DaysHeld':    days_held,
            }

        # ── Bearish signals → exit ────────────────────────────────────────────
        if rsi > 75:
            return {
                'Symbol':      symbol,
                'Action':      'EXIT_FULL',
                'Reason':      f'RSI overbought: {rsi:.1f}',
                'Price':       price,
                'UnrealizedR': unrealized_r,
                'DaysHeld':    days_held,
            }

        if price < trend_line and unrealized_r < 0:
            return {
                'Symbol':      symbol,
                'Action':      'EXIT_FULL',
                'Reason':      f'Below trend line and negative R ({unrealized_r:.1f}R)',
                'Price':       price,
                'UnrealizedR': unrealized_r,
                'DaysHeld':    days_held,
            }

        # ── Trail activation (but TP not yet hit) ─────────────────────────────
        if unrealized_r >= cfg.get('trail_activation', 1.5):
            trail_stop = price - cfg.get('atr_mult', 0.75) * atr
            return {
                'Symbol':      symbol,
                'Action':      'TRAIL',
                'Reason':      f'{unrealized_r:.1f}R — trail at {trail_stop:.2f}',
                'Price':       price,
                'UnrealizedR': unrealized_r,
                'DaysHeld':    days_held,
            }

        return _hold(f'Hold — {unrealized_r:.1f}R, RSI {rsi:.0f}', price)
