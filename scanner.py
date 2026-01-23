"""
Core breakout scanner logic
"""

import logging
from typing import Optional, Dict, Any
import pandas as pd
import numpy as np

from config import MODES, REGIME_CONFIG
from indicators import (
    calculate_all_indicators, 
    calculate_gap_percent,
    check_volume_divergence,
    check_candle_structure
)
from market_data import check_liquidity

logger = logging.getLogger(__name__)


class BreakoutDetector:
    """Detects breakout signals based on technical analysis"""
    
    def __init__(self):
        self.rejection_reasons = []
    
    def detect(self, df: pd.DataFrame, symbol: str, mode_name: str, 
              timeframe: str, spy_perf: float, **kwargs) -> Optional[Dict[str, Any]]:
        """
        Main breakout detection logic
        
        Args:
            df: Historical price data
            symbol: Stock symbol
            mode_name: Trading mode (swing/daytrade/scalping)
            timeframe: Bar timeframe
            spy_perf: SPY performance for RS calculation
            **kwargs: Additional parameters (vol_thresh, atr_mult, spread_pct, regime)
        
        Returns:
            Signal dictionary or None
        """
        cfg = MODES[mode_name]
        regime = kwargs.get('regime', 'NORMAL')
        
        # Apply regime adjustments
        regime_cfg = REGIME_CONFIG.get(regime, REGIME_CONFIG['NORMAL'])
        vol_thresh = (kwargs.get('vol_thresh') or cfg['vol_thresh']) * regime_cfg['vol_mult']
        atr_mult = (kwargs.get('atr_mult') or cfg['atr_mult']) * regime_cfg['atr_mult']
        
        lookback = kwargs.get('lookback') or cfg['lookback']
        spread_pct = kwargs.get('spread_pct')
        
        # Minimum data requirements
        min_bars = 100 if mode_name == 'scalping' else cfg.get('trend_period', 50)
        if len(df) < min_bars:
            return None
        
        # Calculate indicators
        df = calculate_all_indicators(
            df, cfg['trend_type'], cfg.get('trend_period'), timeframe
        )
        
        # Get values
        prev_high = df['high'].rolling(lookback).max().iloc[-2]
        latest = df.iloc[-1]
        
        # Scalping price filter
        if mode_name == 'scalping':
            if latest['close'] < cfg['min_price'] or latest['close'] > cfg['max_price']:
                return None
        
        # --- CORE BREAKOUT LOGIC ---
        price_break = latest['close'] > prev_high
        vol_confirm = latest['Vol_Ratio'] >= vol_thresh
        dist_atr = (latest['close'] - prev_high) / latest['ATR']
        dist_confirm = dist_atr >= atr_mult
        
        # Candle structure
        candle_ok, upper_wick, body_top_pct = check_candle_structure(
            latest, latest['ATR'], cfg['max_wick_atr'], cfg['max_body_top_pct']
        )
        
        # --- FILTERS ---
        # 1. Relative Strength
        if mode_name != 'scalping':
            stock_perf = (latest['close'] / df['close'].iloc[-lookback]) - 1
            rs_ok = stock_perf > spy_perf
        else:
            stock_perf = 0.0
            rs_ok = True
        
        # 2. Trend Filter
        trend_ok = self._check_trend(latest, df, cfg, mode_name)
        
        # 3. VWAP position
        vwap_ok = self._check_vwap_position(latest, mode_name)
        
        # 4. Consolidation
        was_consolidating = self._check_consolidation(df, cfg, mode_name)
        
        # 5. Liquidity
        liquid_ok = check_liquidity(df)
        
        # 6. Gap
        gap_percent = calculate_gap_percent(df) if mode_name != 'scalping' else 0.0
        has_gap_up = gap_percent > 2.0
        
        # 7. Volume spike for scalping
        if mode_name == 'scalping':
            recent_vol_spike = latest['Vol_Ratio'] > vol_thresh * 1.5
            vol_confirm = vol_confirm and recent_vol_spike
        
        # 8. Volume divergence
        vol_divergence = check_volume_divergence(df)
        
        # --- RISK/REWARD CALCULATION ---
        sl, tp, rr = self._calculate_rr(latest, cfg, mode_name, spread_pct)
        rr_ok = rr >= cfg['min_rr']
        
        # --- REJECTION LOGGING ---
        rejection_reasons = self._collect_rejections(
            price_break, vol_confirm, trend_ok, rs_ok, vwap_ok,
            was_consolidating, liquid_ok, dist_confirm, candle_ok,
            rr_ok, vol_divergence, mode_name, cfg, stock_perf, spy_perf,
            dist_atr, rr
        )
        
        if rejection_reasons and len(rejection_reasons) <= 2:
            self.rejection_reasons.append({
                'symbol': symbol,
                'price': round(latest['close'], 2),
                'vol_ratio': round(latest['Vol_Ratio'], 2),
                'mode': mode_name,
                'timeframe': timeframe,
                'reasons': ', '.join(rejection_reasons),
            })
        
        # --- FINAL CHECK ---
        conditions = [
            price_break, vol_confirm, dist_confirm, trend_ok,
            vwap_ok, liquid_ok, candle_ok, rr_ok, not vol_divergence
        ]
        if mode_name != 'scalping':
            conditions.extend([rs_ok, was_consolidating])
        
        if not all(conditions):
            return None
        
        # --- QUALITY SCORING ---
        quality = self._determine_quality(mode_name, latest, df, has_gap_up)
        
        # Build signal
        signal = {
            'Symbol': symbol,
            'Price': round(latest['close'], 2),
            'Vol': round(latest['Vol_Ratio'], 2),
            'Dist': round(dist_atr, 2),
            'Stop': round(sl, 2),
            'Target': round(tp, 2),
            'R:R': round(rr, 2),
            'Gap%': round(gap_percent, 2) if abs(gap_percent) > 0.5 else 0,
            'Mode': mode_name,
            'Quality': quality,
        }
        
        if mode_name == 'scalping' and spread_pct is not None:
            signal['Spread%'] = round(spread_pct, 2)
        
        logger.info(
            f"🚀 {symbol} {mode_name.upper()} @ ${latest['close']:.2f} | "
            f"SL: ${sl:.2f} | TP: ${tp:.2f} | R:R={rr:.2f} | {quality}"
        )
        
        return signal
    
    def _check_trend(self, latest, df, cfg, mode_name: str) -> bool:
        """Check trend condition"""
        if cfg['trend_type'] == 'VWAP':
            return (
                not pd.isna(latest.get('vwap')) and
                latest['close'] > latest['vwap'] and
                latest['vwap'] > df['vwap'].iloc[-2]
            )
        return latest['close'] > latest['Trend_Line']
    
    def _check_vwap_position(self, latest, mode_name: str) -> bool:
        """Check VWAP position"""
        vwap_val = latest.get('vwap', np.nan)
        if pd.isna(vwap_val):
            return True
        
        if mode_name == 'scalping':
            return latest['close'] > vwap_val and latest['close'] > latest['open']
        elif mode_name == 'daytrade':
            return latest['close'] > vwap_val
        return True
    
    def _check_consolidation(self, df, cfg, mode_name: str) -> bool:
        """Check consolidation before breakout"""
        if mode_name == 'scalping':
            return True
        
        min_cons_bars = cfg['min_consolidation_bars']
        return bool(df['Is_Consolidating'].iloc[-min_cons_bars-1:-1].any())
    
    def _calculate_rr(self, latest, cfg, mode_name: str, spread_pct: Optional[float]) -> tuple:
        """Calculate stop loss, target, and risk/reward ratio"""
        sl = latest['close'] - (cfg['sl_mult'] * latest['ATR'])
        tp = latest['close'] + (cfg['tp_mult'] * latest['ATR'])
        
        if mode_name == 'scalping' and spread_pct is not None:
            spread_price = latest['close'] * (spread_pct / 100.0)
            entry_eff = latest['close'] + spread_price * 0.5
            sl_eff = sl - spread_price * 0.5
            rr = (tp - entry_eff) / max(entry_eff - sl_eff, 1e-6)
        else:
            rr = (tp - latest['close']) / max(latest['close'] - sl, 1e-6)
        
        return sl, tp, rr
    
    def _collect_rejections(self, price_break, vol_confirm, trend_ok, rs_ok,
                           vwap_ok, was_consolidating, liquid_ok, dist_confirm,
                           candle_ok, rr_ok, vol_divergence, mode_name, cfg,
                           stock_perf, spy_perf, dist_atr, rr) -> list:
        """Collect rejection reasons"""
        reasons = []
        
        if not (price_break and vol_confirm):
            return reasons
        
        if not trend_ok:
            if cfg['trend_type'] == 'VWAP':
                reasons.append("Below VWAP or not rising")
            else:
                reasons.append(f"Below {cfg['trend_period']} {cfg['trend_type']}")
        
        if not rs_ok and mode_name != 'scalping':
            reasons.append(f"Weaker RS ({stock_perf:.1%} vs {spy_perf:.1%})")
        
        if not vwap_ok:
            reasons.append("VWAP position poor")
        
        if not was_consolidating and mode_name != 'scalping':
            reasons.append("No consolidation")
        
        if not liquid_ok:
            reasons.append("Low liquidity")
        
        if not dist_confirm:
            reasons.append(f"Weak distance ({dist_atr:.2f} ATR)")
        
        if not candle_ok:
            reasons.append("Poor candle structure")
        
        if not rr_ok:
            reasons.append(f"Low R:R ({rr:.2f})")
        
        if vol_divergence:
            reasons.append("Volume divergence")
        
        return reasons
    
    def _determine_quality(self, mode_name: str, latest, df, has_gap_up: bool) -> str:
        """Determine signal quality"""
        if mode_name == 'scalping':
            if len(df['vwap']) >= 5 and not pd.isna(df['vwap'].iloc[-5]):
                vwap_momentum = (latest['vwap'] - df['vwap'].iloc[-5]) / max(latest['ATR'], 1e-6)
                if latest['Vol_Ratio'] > 3.0 and vwap_momentum > 0.5:
                    return 'PREMIUM'
            return 'HIGH'
        
        return 'PREMIUM' if has_gap_up else 'HIGH'
