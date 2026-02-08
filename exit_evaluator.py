"""
Exit evaluation logic for existing positions
"""

import logging
from typing import Dict, Any
import pandas as pd
import numpy as np

from config import MODES, MAX_HOLD_BARS
from indicators import calculate_all_indicators

logger = logging.getLogger(__name__)


class ExitEvaluator:
    """Evaluates exit conditions for existing positions"""
    
    def evaluate(self, df: pd.DataFrame, symbol: str, mode_name: str,
                entry_price: float, stop_price: float, target_price: float,
                timeframe: str, regime: str = "NORMAL",
                days_held: int = 0) -> Dict[str, Any]:
        """
        Evaluate exit conditions for a LONG position
        
        Returns:
            Dict with Action: HOLD | TRAIL | EXIT_PARTIAL | EXIT_FULL
        """
        cfg = MODES[mode_name]
        
        if len(df) < max(30, cfg.get('trend_period', 50)):
            return {
                'Symbol': symbol,
                'Mode': mode_name,
                'Action': 'HOLD',
                'Reason': 'Insufficient data',
                'Price': 0,
                'UnrealizedR': 0
            }
        
        # Calculate indicators
        df = calculate_all_indicators(
            df, cfg['trend_type'], cfg.get('trend_period'), timeframe
        )
        latest = df.iloc[-1]
        
        price = float(latest['close'])
        trend_line = float(latest['Trend_Line'])
        atr = float(latest['ATR'])
        vwap_val = latest.get('vwap', np.nan)
        
        unrealized_r = (price - entry_price) / max(entry_price - stop_price, 1e-6)
        
        # Collect exit signals (priority-ordered)
        exit_signals = []
        
        # 1. CRITICAL: Hard stop hit
        if price <= stop_price:
            exit_signals.append((
                'EXIT_FULL',
                f'Stop hit: {price:.2f} <= {stop_price:.2f}',
                100
            ))
        
        # 2. CRITICAL: Trend broken
        trend_broken = price < trend_line
        vwap_broken = (
            not pd.isna(vwap_val) and 
            price < vwap_val and 
            ('min' in timeframe or 'hour' in timeframe)
        )
        
        if trend_broken and (vwap_broken or mode_name == 'swing'):
            exit_signals.append((
                'EXIT_FULL',
                f'Trend broken: {price:.2f} < {trend_line:.2f}',
                90
            ))
        
        # 3. For swing: SMA150 support lost
        if mode_name == 'swing' and len(df) >= 150:
            sma150 = df['close'].rolling(150).mean().iloc[-1]
            if not np.isnan(sma150) and price < sma150 - 0.5 * atr:
                exit_signals.append((
                    'EXIT_FULL',
                    f'Lost SMA150: {price:.2f} < {sma150:.2f}',
                    85
                ))
        
        # 4. Reversal candle near target
        reversal_signal = self._check_reversal_candle(
            latest, target_price, atr, unrealized_r
        )
        if reversal_signal:
            exit_signals.append(reversal_signal)
        
        # 5. Volume divergence
        vol_div_signal = self._check_volume_divergence(
            df, price, entry_price, unrealized_r
        )
        if vol_div_signal:
            exit_signals.append(vol_div_signal)
        
        # 6. Choppy regime + no progress
        choppy_signal = self._check_choppy_conditions(
            df, price, regime, atr, cfg['lookback'], unrealized_r
        )
        if choppy_signal:
            exit_signals.append(choppy_signal)

        # 6b. Max hold period (V3)
        max_hold = MAX_HOLD_BARS.get(mode_name, 30)
        if max_hold > 0 and days_held >= max_hold:
            exit_signals.append((
                'EXIT_FULL',
                f'Max hold period reached ({days_held}/{max_hold} days)',
                55
            ))

        # 7. Trail stop suggestion
        trail_signal = self._check_trail_stop(
            df, mode_name, stop_price, atr, unrealized_r
        )
        if trail_signal:
            exit_signals.append(trail_signal)
        
        # Return highest priority signal
        if exit_signals:
            exit_signals.sort(key=lambda x: x[2], reverse=True)
            action, reason, _ = exit_signals[0]
            return {
                'Symbol': symbol,
                'Mode': mode_name,
                'Action': action,
                'Reason': reason,
                'Price': round(price, 2),
                'UnrealizedR': round(unrealized_r, 2)
            }
        
        # Default: HOLD
        return {
            'Symbol': symbol,
            'Mode': mode_name,
            'Action': 'HOLD',
            'Reason': f'Above stop & trend (R={unrealized_r:.2f})',
            'Price': round(price, 2),
            'UnrealizedR': round(unrealized_r, 2)
        }
    
    def _check_reversal_candle(self, latest, target_price: float, 
                               atr: float, unrealized_r: float):
        """Check for reversal candle near target"""
        high = latest['high']
        low = latest['low']
        close = latest['close']
        open_price = latest['open']
        
        rng = max(high - low, 1e-6)
        body_top = max(open_price, close)
        upper_wick = high - body_top
        
        near_target = close >= target_price * 0.97
        strong_reversal = (
            near_target and
            upper_wick > 0.5 * atr and
            close < body_top - 0.5 * rng
        )
        
        if strong_reversal and unrealized_r >= 1.0:
            return (
                'EXIT_PARTIAL',
                f'Reversal candle near target (R={unrealized_r:.2f})',
                70
            )
        return None
    
    def _check_volume_divergence(self, df, price: float, 
                                 entry_price: float, unrealized_r: float):
        """Check for volume divergence"""
        if len(df) < 10:
            return None
        
        recent_vol = df['volume'].tail(5).mean()
        prev_vol = df['volume'].iloc[-10:-5].mean()
        vol_divergence = price > entry_price * 1.05 and recent_vol < prev_vol * 0.7
        
        if vol_divergence and unrealized_r > 1.0:
            return ('EXIT_PARTIAL', 'Volume divergence', 60)
        return None
    
    def _check_choppy_conditions(self, df, price: float, regime: str,
                                 atr: float, lookback: int, unrealized_r: float):
        """Check for choppy market conditions"""
        if len(df) < lookback:
            return None
        
        recent_low = df['close'].iloc[-lookback:].min()
        recent_high = df['close'].iloc[-lookback:].max()
        in_middle_range = (
            recent_low < price < recent_high and
            (recent_high - recent_low) < 1.5 * atr
        )
        
        if regime == 'CHOPPY' and in_middle_range and unrealized_r < 0.5:
            return ('EXIT_FULL', 'Choppy regime, no progress', 50)
        return None
    
    def _check_trail_stop(self, df, mode_name: str, stop_price: float,
                         atr: float, unrealized_r: float):
        """Check if trailing stop should be raised"""
        trail_lookback = 5 if mode_name != 'swing' else 10
        
        if len(df) < trail_lookback:
            return None
        
        swing_low = df['low'].iloc[-trail_lookback:].min()
        proposed_trail_stop = swing_low - 0.5 * atr
        
        if proposed_trail_stop > stop_price and unrealized_r >= 1.0:
            return (
                'TRAIL',
                f'Trail to {proposed_trail_stop:.2f} (swing low - 0.5 ATR)',
                40
            )
        return None
