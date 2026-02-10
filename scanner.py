"""
Core breakout scanner logic
"""

import logging
from typing import Optional, Dict, Any
import pandas as pd
import numpy as np

from config import (MODES, REGIME_CONFIG, RR_GRADE_CONFIG, BB_TREND_FILTER,
                    WIN_PROBABILITY)
from indicators import (
    calculate_all_indicators,
    calculate_gap_percent,
    check_volume_divergence,
    check_candle_structure
)
from market_data import check_liquidity
from pattern_recognition import get_pattern_score

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
        
        # --- BB TREND GATE (V3) ---
        bb_trend = latest.get('BB_Trend', 'neutral')
        if BB_TREND_FILTER['enabled'] and BB_TREND_FILTER['reject_bearish']:
            if bb_trend == 'bearish':
                return None

        # --- PATTERN DETECTION (V3) ---
        has_bullish_pattern, has_bearish_pattern, pattern_target, pattern_names = \
            get_pattern_score(df.tail(30), symbol)

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
        use_legacy = kwargs.get('use_legacy_momentum', False)
        was_consolidating = self._check_consolidation(df, cfg, mode_name, use_legacy=use_legacy)
        
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
        use_structural = not kwargs.get('use_legacy_momentum', False)
        sl, tp, rr = self._calculate_rr(latest, cfg, mode_name, spread_pct,
                                         df=df, use_structural=use_structural,
                                         pattern_target=pattern_target)
        rr_ok = rr >= cfg['min_rr']

        # --- R:R GRADE + GRADE D REJECTION (V3) ---
        rr_grade = 'D'
        for grade in ['A', 'B', 'C']:
            if rr >= RR_GRADE_CONFIG[grade]['min_rr']:
                rr_grade = grade
                break
        if RR_GRADE_CONFIG['D']['reject'] and rr_grade == 'D':
            return None
        
        # --- REJECTION LOGGING (with score preview) ---
        rejection_reasons = self._collect_rejections(
            price_break, vol_confirm, trend_ok, rs_ok, vwap_ok,
            was_consolidating, liquid_ok, dist_confirm, candle_ok,
            rr_ok, vol_divergence, mode_name, cfg, stock_perf, spy_perf,
            dist_atr, rr
        )
        
        # Calculate score preview for near-misses (even if rejected)
        momentum_score_preview = latest.get('Momentum_Score', 0)
        conviction_score_preview = latest.get('Conviction_Score', 0)
        
        # Log all stocks that broke out but were rejected (near misses)
        if price_break and vol_confirm and rejection_reasons:
            self.rejection_reasons.append({
                'symbol': symbol,
                'price': round(latest['close'], 2),
                'vol_ratio': round(latest['Vol_Ratio'], 2),
                'momentum': int(momentum_score_preview) if not pd.isna(momentum_score_preview) else 0,
                'conviction': int(conviction_score_preview) if not pd.isna(conviction_score_preview) else 0,
                'rsi': int(latest.get('RSI', 0)),
                'mode': mode_name,
                'reasons': ', '.join(rejection_reasons),
            })
        
        # --- MOMENTUM INDICATORS ---
        rsi_val = latest.get('RSI', 50)
        macd_hist_val = latest.get('MACD_Hist', 0)
        macd_prev_hist = df['MACD_Hist'].iloc[-2] if len(df) >= 2 and 'MACD_Hist' in df.columns else 0
        adx_val = latest.get('ADX', 0)

        use_legacy = kwargs.get('use_legacy_momentum', False)
        if use_legacy:
            # V1: Original binary checks
            rsi_favorable = 40 <= rsi_val <= 75 if not pd.isna(rsi_val) else True
            macd_favorable = macd_hist_val > 0 if not pd.isna(macd_hist_val) else True
            adx_trending = adx_val > 20 if not pd.isna(adx_val) else True
        else:
            # V2: Tightened thresholds
            rsi_favorable = 45 <= rsi_val <= 70 if not pd.isna(rsi_val) else True
            macd_favorable = (macd_hist_val > 0 and macd_hist_val > macd_prev_hist) if not pd.isna(macd_hist_val) else True
            adx_trending = adx_val > 25 if not pd.isna(adx_val) else True

        # V2 composite scores
        momentum_score_val = latest.get('Momentum_Score', 0)
        conviction_score_val = latest.get('Conviction_Score', 0)
        momentum_strong = momentum_score_val >= 50 if not pd.isna(momentum_score_val) else False
        conviction_strong = conviction_score_val >= 40 if not pd.isna(conviction_score_val) else False

        # --- SIGNAL SCORING SYSTEM ---
        use_scoring = kwargs.get('use_scoring', False)

        if use_scoring:
            # Mandatory gate: price must break above previous high
            if not price_break or not liquid_ok:
                return None

            if use_legacy:
                # V1: 3 binary momentum checks
                checks = {
                    'vol_confirm': vol_confirm,
                    'trend_ok': trend_ok,
                    'dist_confirm': dist_confirm,
                    'candle_ok': candle_ok,
                    'rr_ok': rr_ok,
                    'no_vol_divergence': not vol_divergence,
                    'vwap_ok': vwap_ok,
                    'rsi_favorable': rsi_favorable,
                    'macd_favorable': macd_favorable,
                    'adx_trending': adx_trending,
                }
            else:
                # V2/V3: Composite momentum + conviction + pattern scores
                checks = {
                    'vol_confirm': vol_confirm,
                    'trend_ok': trend_ok,
                    'momentum_strong': momentum_strong,
                    'dist_confirm': dist_confirm,
                    'candle_ok': candle_ok,
                    'rr_ok': rr_ok,
                    'no_vol_divergence': not vol_divergence,
                    'conviction_strong': conviction_strong,
                    'has_bullish_pattern': has_bullish_pattern,
                }

            if mode_name != 'scalping':
                checks['rs_ok'] = rs_ok
                checks['consolidation'] = was_consolidating

            score, max_score, quality = self._calculate_signal_score(checks)

            if quality == 'REJECT':
                return None
        else:
            # Original all-or-nothing logic
            conditions = [
                price_break, vol_confirm, dist_confirm, trend_ok,
                vwap_ok, liquid_ok, candle_ok, rr_ok, not vol_divergence
            ]
            if mode_name != 'scalping':
                conditions.extend([rs_ok, was_consolidating])

            if not all(conditions):
                return None

            quality = self._determine_quality(mode_name, latest, df, has_gap_up)
            score = 100
        
        # --- WIN PROBABILITY ESTIMATION (V3) ---
        win_prob, win_grade = self._estimate_win_probability(
            trend_ok, momentum_strong, vol_confirm, has_bullish_pattern,
            bb_trend, rr_grade, conviction_strong
        )

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
            'RR_Grade': rr_grade,
            'WinProb': win_prob,
            'WinGrade': win_grade,
            'Patterns': ', '.join(pattern_names) if pattern_names else '',
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
    
    def _check_consolidation(self, df, cfg, mode_name: str, use_legacy: bool = False) -> bool:
        """Check consolidation before breakout"""
        if mode_name == 'scalping':
            return True

        min_cons_bars = cfg['min_consolidation_bars']

        if use_legacy:
            # V1: any single narrow bar counts
            return bool(df['Is_Consolidating'].iloc[-min_cons_bars-1:-1].any())

        # V2: Require 3+ consecutive narrow-range bars AND low volume
        check_window = df.iloc[-20:-1]  # Look at last 20 bars before current
        if len(check_window) < 3:
            return False

        is_narrow = check_window['Is_Consolidating']
        # Count max consecutive narrow bars
        max_consecutive = 0
        current_streak = 0
        for val in is_narrow:
            if val:
                current_streak += 1
                max_consecutive = max(max_consecutive, current_streak)
            else:
                current_streak = 0

        if max_consecutive < 3:
            return False

        # Check volume during consolidation is below average
        consol_vol = check_window.loc[is_narrow, 'volume'].mean() if is_narrow.any() else float('inf')
        avg_vol = df['volume'].rolling(20).mean().iloc[-2]
        if pd.isna(avg_vol) or pd.isna(consol_vol):
            return max_consecutive >= 3
        return consol_vol < avg_vol * 0.8
    
    def _calculate_rr(self, latest, cfg, mode_name: str, spread_pct: Optional[float],
                      df: pd.DataFrame = None, use_structural: bool = True,
                      pattern_target: float = 0.0) -> tuple:
        """Calculate stop loss, target, and risk/reward ratio"""
        atr_stop = latest['close'] - (cfg['sl_mult'] * latest['ATR'])

        # Structural stop: use swing low of last 20 bars (take the higher/tighter stop)
        if use_structural and df is not None and len(df) >= 20:
            swing_low = df['low'].iloc[-20:].min()
            sl = max(swing_low, atr_stop)  # Tighter stop = less risk
        else:
            sl = atr_stop

        atr_target = latest['close'] + (cfg['tp_mult'] * latest['ATR'])
        # Use the higher of ATR target and pattern-derived target
        tp = max(atr_target, pattern_target) if pattern_target > 0 else atr_target
        
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

    # --- Scoring weights for each check ---
    # V1 keys (legacy): rsi_favorable, macd_favorable, adx_trending, vwap_ok
    # V2 keys (new): momentum_strong, conviction_strong
    SCORING_WEIGHTS = {
        'vol_confirm': 16,
        'trend_ok': 16,
        'momentum_strong': 13,
        'dist_confirm': 10,
        'candle_ok': 8,
        'rr_ok': 10,
        'no_vol_divergence': 5,
        'conviction_strong': 8,
        'rs_ok': 8,
        'consolidation': 8,
        'has_bullish_pattern': 10,  # V3: Pattern confirmation
        # V1 legacy weights (used when use_legacy_momentum=True)
        'vwap_ok': 8,
        'rsi_favorable': 8,
        'macd_favorable': 7,
        'adx_trending': 5,
    }

    def _calculate_signal_score(self, checks: dict) -> tuple:
        """Calculate weighted signal score from boolean checks"""
        score = 0
        max_score = 0
        for key, passed in checks.items():
            weight = self.SCORING_WEIGHTS.get(key, 5)
            max_score += weight
            if passed:
                score += weight

        pct = (score / max_score * 100) if max_score > 0 else 0

        if pct >= 80:
            quality = 'PREMIUM'
        elif pct >= 65:
            quality = 'HIGH'
        elif pct >= 60:
            quality = 'STANDARD'
        else:
            quality = 'REJECT'

        return score, max_score, quality

    def _estimate_win_probability(self, trend_ok: bool, momentum_strong: bool,
                                   vol_confirm: bool, has_bullish_pattern: bool,
                                   bb_trend: str, rr_grade: str,
                                   conviction_strong: bool) -> tuple:
        """
        Estimate win probability based on confluence of signals.

        Returns:
            (probability: float 0-1, grade: str 'HIGH'|'MEDIUM'|'LOW')
        """
        cfg = WIN_PROBABILITY
        base = cfg['base_probability']
        bonus_per_signal = cfg['max_bonus'] / cfg['confluence_signals']

        signals_met = sum([
            trend_ok,
            momentum_strong,
            vol_confirm,
            has_bullish_pattern,
            bb_trend == 'bullish',
            rr_grade in ('A', 'B'),
            conviction_strong,
        ])

        probability = base + (signals_met * bonus_per_signal)
        probability = min(probability, base + cfg['max_bonus'])

        if probability >= cfg['high_threshold']:
            grade = 'HIGH'
        elif probability >= cfg['low_threshold']:
            grade = 'MEDIUM'
        else:
            grade = 'LOW'

        return round(probability, 3), grade

    def detect_pullback(self, df: pd.DataFrame, symbol: str, mode_name: str,
                        timeframe: str, spy_perf: float, **kwargs) -> Optional[Dict[str, Any]]:
        """
        Detect pullback re-entry after a prior breakout.
        Finds stocks that broke out recently and pulled back to support.
        """
        cfg = MODES[mode_name]
        lookback = kwargs.get('lookback') or cfg['lookback']

        min_bars = max(50, lookback + 20)
        if len(df) < min_bars:
            return None

        df = calculate_all_indicators(
            df, cfg['trend_type'], cfg.get('trend_period'), timeframe
        )

        latest = df.iloc[-1]

        # 1. Find if a breakout happened in the last 20 bars
        breakout_bar = None
        for i in range(-20, -1):
            if abs(i) >= len(df):
                continue
            bar = df.iloc[i]
            prev_high = df['high'].iloc[:i].rolling(lookback).max().iloc[-1] if len(df[:i]) > lookback else None
            if prev_high is None:
                continue
            if bar['close'] > prev_high and bar['Vol_Ratio'] >= cfg['vol_thresh']:
                breakout_bar = i
                break

        if breakout_bar is None:
            return None

        breakout_price = df.iloc[breakout_bar]['close']

        # 2. Price must have pulled back from the breakout high
        post_breakout = df.iloc[breakout_bar:]
        post_high = post_breakout['high'].max()
        pullback_depth = (post_high - latest['close']) / latest['ATR']

        if pullback_depth < 0.3 or pullback_depth > 4.0:
            return None  # Too shallow or too deep

        # 3. Price sitting near EMA support (8 or 21)
        ema8 = df['close'].ewm(span=8, adjust=False).mean().iloc[-1]
        ema21 = df['close'].ewm(span=21, adjust=False).mean().iloc[-1]
        near_ema = (
            abs(latest['close'] - ema8) / latest['ATR'] < 1.0 or
            abs(latest['close'] - ema21) / latest['ATR'] < 1.0
        )

        # 4. Volume contracting on pullback (healthy dip)
        recent_vol = df['volume'].iloc[-3:].mean()
        breakout_vol = df['volume'].iloc[breakout_bar]
        vol_contracting = recent_vol < breakout_vol * 0.7

        # 5. RSI reset (not overbought)
        rsi_val = latest.get('RSI', 50)
        rsi_reset = 35 <= rsi_val <= 60 if not pd.isna(rsi_val) else True

        # 6. Bullish candle forming
        bullish_candle = latest['close'] > latest['open']

        # 7. Still above breakout level
        above_breakout = latest['close'] >= breakout_price * 0.98

        # Score the pullback
        checks = {
            'near_ema': near_ema,
            'vol_contracting': vol_contracting,
            'rsi_reset': rsi_reset,
            'bullish_candle': bullish_candle,
            'above_breakout': above_breakout,
        }
        passed = sum(1 for v in checks.values() if v)

        if passed < 2:
            return None

        # Calculate R:R
        sl = min(ema21, latest['close'] - cfg['sl_mult'] * latest['ATR'])
        tp = post_high + (0.5 * latest['ATR'])  # Target above prior high
        risk = latest['close'] - sl
        reward = tp - latest['close']
        rr = reward / max(risk, 1e-6)

        if rr < cfg['min_rr']:
            return None

        quality = 'HIGH' if passed >= 4 else 'STANDARD'

        signal = {
            'Symbol': symbol,
            'Price': round(latest['close'], 2),
            'Vol': round(latest['Vol_Ratio'], 2),
            'Dist': round(pullback_depth, 2),
            'Stop': round(sl, 2),
            'Target': round(tp, 2),
            'R:R': round(rr, 2),
            'Gap%': 0,
            'Mode': mode_name,
            'Quality': quality,
            'Type': 'PULLBACK',
        }

        logger.info(
            f"🔄 PULLBACK {symbol} {mode_name.upper()} @ ${latest['close']:.2f} | "
            f"SL: ${sl:.2f} | TP: ${tp:.2f} | R:R={rr:.2f} | {quality}"
        )

        return signal

    def detect_bounce(self, df: pd.DataFrame, symbol: str, mode_name: str,
                      timeframe: str, **kwargs) -> Optional[Dict[str, Any]]:
        """
        Detect bounce/recovery signals for oversold stocks
        
        Criteria:
        1. Stock is down significantly from recent highs (oversold)
        2. Strong daily move (>3% gain)
        3. Volume spike (>1.5x average)
        4. RSI was oversold (<35) and is now recovering
        5. Price reclaiming key moving average (20 EMA)
        """
        cfg = MODES.get(mode_name, MODES['swing'])
        
        if len(df) < 50:
            return None
        
        # Calculate indicators if not present
        if 'ATR' not in df.columns:
            df = calculate_all_indicators(df, cfg['trend_type'], cfg.get('trend_period'), timeframe)
        
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        # --- BOUNCE CONDITIONS ---
        
        # 1. Strong daily gain (>3%)
        daily_gain = (latest['close'] - prev['close']) / prev['close']
        strong_move = daily_gain >= 0.03  # 3%+ move
        
        if not strong_move:
            logger.debug(f"BOUNCE {symbol}: daily gain {daily_gain:.2%} < 3%")
            return None
        
        # 2. Oversold condition - stock down from recent high
        high_20 = df['high'].rolling(20).max().iloc[-2]  # 20-day high before today
        drawdown = (prev['close'] - high_20) / high_20  # How far down from high
        was_beaten_down = drawdown <= -0.15  # At least 15% off highs
        
        if not was_beaten_down:
            return None
        
        # 3. Volume - adjust for time of day (market open 6.5 hours = 390 mins)
        #    If checking mid-day, extrapolate current volume to full-day equivalent
        vol_ratio = latest.get('Vol_Ratio', 1.0)
        if vol_ratio == 1.0 and 'Vol_Ratio' not in df.columns:
            vol_avg = df['volume'].rolling(20).mean().iloc[-1]
            
            # Time adjustment for intraday volume
            from datetime import datetime
            import pytz
            try:
                now = datetime.now(pytz.timezone('US/Eastern'))
                market_open = now.replace(hour=9, minute=30, second=0)
                market_close = now.replace(hour=16, minute=0, second=0)
                
                if market_open <= now <= market_close:
                    # Calculate what fraction of the day has passed
                    elapsed_mins = (now - market_open).seconds / 60
                    total_mins = 390  # 6.5 hours
                    day_fraction = elapsed_mins / total_mins
                    
                    if day_fraction > 0.1:  # At least 10% of day passed
                        # Extrapolate current volume to full-day equivalent
                        extrapolated_vol = latest['volume'] / day_fraction
                        vol_ratio = extrapolated_vol / vol_avg if vol_avg > 0 else 1.0
                    else:
                        vol_ratio = latest['volume'] / vol_avg if vol_avg > 0 else 1.0
                else:
                    # After market close, use actual volume
                    vol_ratio = latest['volume'] / vol_avg if vol_avg > 0 else 1.0
            except:
                # Fallback if timezone not available
                vol_ratio = latest['volume'] / vol_avg if vol_avg > 0 else 1.0
        
        vol_strong = vol_ratio >= 1.5  # Strong volume is a bonus
        vol_ok = vol_ratio >= 0.8  # At least 80% of average (not extremely low)
        
        # 4. RSI recovery from oversold
        rsi = latest.get('RSI', 50)
        rsi_prev = prev.get('RSI', prev.get('RSI', 50)) if 'RSI' in df.columns else 50
        rsi_recovering = rsi > rsi_prev and rsi < 60  # Rising but not overbought
        rsi_was_low = rsi_prev < 40 or rsi < 45  # Was in oversold territory
        
        # 5. Reclaiming moving average (20 EMA or similar)
        ema_20 = df['close'].ewm(span=20).mean().iloc[-1]
        reclaiming_ema = latest['close'] > ema_20 and prev['close'] <= ema_20
        above_ema = latest['close'] > ema_20
        
        # 6. MACD improving (histogram less negative or turning positive)
        macd_hist = latest.get('MACD_Hist', 0)
        macd_prev = df['MACD_Hist'].iloc[-2] if 'MACD_Hist' in df.columns else 0
        macd_improving = macd_hist > macd_prev
        
        # --- SCORING ---
        checks = {
            'strong_move': strong_move,           # Required
            'was_beaten_down': was_beaten_down,   # Required  
            'vol_ok': vol_ok,                     # Reasonable volume
            'vol_strong': vol_strong,             # Bonus: strong volume
            'rsi_recovering': rsi_recovering,
            'rsi_was_low': rsi_was_low,
            'above_ema': above_ema,
            'reclaiming_ema': reclaiming_ema,
            'macd_improving': macd_improving,
        }
        
        passed = sum(checks.values())
        
        # Need at least 4 conditions for a signal (strong move + beaten down + 2 others)
        if passed < 4:
            return None
        
        # --- RISK/REWARD ---
        atr = latest['ATR']
        
        # Stop below today's low or recent swing low
        recent_low = df['low'].tail(5).min()
        sl = min(latest['low'] - atr * 0.5, recent_low - atr * 0.25)
        
        # Target: retest of 20-day high or 2x risk
        risk = latest['close'] - sl
        tp = latest['close'] + risk * 2.0  # 2:1 R:R minimum
        
        # Alternative: target the 20-day high
        tp_to_high = high_20
        if tp_to_high > tp:
            tp = tp_to_high
        
        rr = (tp - latest['close']) / risk if risk > 0 else 0
        
        if rr < 1.5:
            return None
        
        # --- QUALITY ---
        if passed >= 7:
            quality = 'PREMIUM'
        elif passed >= 6:
            quality = 'HIGH'
        else:
            quality = 'STANDARD'
        
        signal = {
            'Symbol': symbol,
            'Price': round(latest['close'], 2),
            'Vol': round(vol_ratio, 2),
            'Dist': round(drawdown * 100, 1),  # Show as % drawdown
            'Stop': round(sl, 2),
            'Target': round(tp, 2),
            'R:R': round(rr, 2),
            'Gap%': round(daily_gain * 100, 1),
            'Mode': mode_name,
            'Quality': quality,
            'Type': 'BOUNCE',
            'RSI': round(rsi, 1),
        }
        
        logger.info(
            f"🔄 BOUNCE {symbol} @ ${latest['close']:.2f} (+{daily_gain:.1%}) | "
            f"Down {drawdown:.0%} from high | Vol: {vol_ratio:.1f}x | RSI: {rsi:.0f} | {quality}"
        )
        
        return signal
