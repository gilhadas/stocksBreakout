"""
Core breakout scanner logic
"""

import logging
from typing import Optional, Dict, Any
import pandas as pd
import numpy as np

from config import (MODES, REGIME_CONFIG, RR_GRADE_CONFIG, RR_GRADE_SCORES,
                    BB_TREND_FILTER, WIN_PROBABILITY, SCORING_WEIGHTS, SCORE_THRESHOLDS,
                    TREND_CONFIRM, TENSION_CONFIG, SUPERTREND_CONFIG, PINNED_RANGE_CONFIG,
                    SLOW_GRIND_CONFIG)
from indicators import (
    calculate_all_indicators,
    calculate_gap_percent,
    check_volume_divergence,
    check_candle_structure,
    check_pinned_range,
    compute_volume_profile,
)
from market_data import check_liquidity
from pattern_recognition import get_pattern_score

logger = logging.getLogger(__name__)


class BreakoutDetector:
    """Detects breakout signals based on technical analysis"""

    def __init__(self):
        self.rejection_reasons = []
        # Instance-level copy of weights from config.py (optimizer output)
        self.scoring_weights = dict(SCORING_WEIGHTS)
        self._load_score_adjustments()
        self.winprob_calibration = self._load_winprob_calibration()

    def _load_winprob_calibration(self):
        """Load empirical WinProb table (calibrate_winprob.py output).

        Returns the bucket dict {SIGNAL_TYPE|QUALITY: {win_prob, n, ...}} or
        None — in which case detect() keeps the confluence heuristic and the
        cascade detectors emit no WinProb (pre-calibration behavior).
        """
        import json
        from pathlib import Path
        from config import WINPROB_CALIBRATION

        if not WINPROB_CALIBRATION.get('enabled'):
            return None
        path = Path(WINPROB_CALIBRATION.get('path', 'scanner_output/winprob_calibration.json'))
        if not path.exists():
            return None
        try:
            buckets = json.loads(path.read_text()).get('buckets', {})
            if buckets:
                logger.info(f"WinProb calibration: {len(buckets)} buckets loaded from {path}")
                return buckets
        except Exception as e:
            logger.debug(f"WinProb calibration load failed: {e}")
        return None

    def _calibrated_winprob(self, signal_type: str, quality: str):
        """Empirical (win_prob, grade) for signal_type × quality, or None.

        Buckets are fitted from champion-exit backtest trade logs; a missing
        bucket means the sample was too thin to publish — caller falls back
        to its heuristic (or omits WinProb entirely).
        """
        if not self.winprob_calibration:
            return None
        bucket = self.winprob_calibration.get(f"{signal_type}|{quality}")
        if not bucket:
            return None
        prob = float(bucket['win_prob'])
        if prob >= WIN_PROBABILITY['high_threshold']:
            grade = 'HIGH'
        elif prob >= WIN_PROBABILITY['low_threshold']:
            grade = 'MEDIUM'
        else:
            grade = 'LOW'
        return round(prob, 3), grade
    
    def _load_score_adjustments(self):
        """Load weight recommendations from learning loop (read-only, conservative)."""
        import json
        from pathlib import Path
        from config import OUTPUT_DIR

        adj_path = Path(OUTPUT_DIR) / 'score_adjustments.json'
        if not adj_path.exists():
            return

        try:
            data = json.loads(adj_path.read_text())
            recs = data.get('weight_recommendations', [])
            applied = []

            # Handle list format: [{feature, current_weight, recommended_weight, reason}]
            if isinstance(recs, list):
                for rec in recs:
                    feature = rec.get('feature', '')
                    if feature not in self.scoring_weights:
                        continue
                    current = rec.get('current_weight', 0)
                    recommended = rec.get('recommended_weight', current)
                    adj = recommended - current
                    # Conservative: cap adjustments to +/-3 points
                    adj = max(-3, min(3, adj))
                    if adj != 0:
                        old = self.scoring_weights[feature]
                        self.scoring_weights[feature] = max(1, old + adj)
                        applied.append(f"{feature}: {old}→{self.scoring_weights[feature]}")

            # Handle dict format: {feature: {adjustment: N}}
            elif isinstance(recs, dict):
                for feature, rec in recs.items():
                    if feature not in self.scoring_weights or not isinstance(rec, dict):
                        continue
                    adj = rec.get('adjustment', 0)
                    adj = max(-3, min(3, adj))
                    if adj != 0:
                        old = self.scoring_weights[feature]
                        self.scoring_weights[feature] = max(1, old + adj)
                        applied.append(f"{feature}: {old}→{self.scoring_weights[feature]}")

            if applied:
                logger.info(f"Learning loop: applied {len(applied)} adjustments — {', '.join(applied)}")
            else:
                logger.info(f"Learning loop: loaded {adj_path}, no adjustments needed")
        except Exception as e:
            logger.debug(f"Learning loop: failed to load {adj_path}: {e}")

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
            logger.scan(f"{symbol}: skip — insufficient bars ({len(df)} < {min_bars})")
            return None

        # Stale data guard: reject if last bar is >7 calendar days old.
        # In backtest mode pass reference_date=sim_date so the guard uses the
        # simulation date instead of today (avoids rejecting all historical data).
        if hasattr(df.index, 'date'):
            from datetime import date as _date
            last_bar_date = df.index[-1].date() if hasattr(df.index[-1], 'date') else None
            ref_date = kwargs.get('reference_date', _date.today())
            if isinstance(ref_date, str):
                ref_date = _date.fromisoformat(ref_date)
            elif hasattr(ref_date, 'date'):
                ref_date = ref_date.date()
            if last_bar_date and (ref_date - last_bar_date).days > 7:
                logger.debug(f"{symbol}: stale data (last bar {last_bar_date})")
                return None

        # Calculate indicators
        df = calculate_all_indicators(
            df, cfg['trend_type'], cfg.get('trend_period'), timeframe
        )

        # Pinned/compressed-range check (deal-pin veto, see PINNED_RANGE_CONFIG) —
        # computed once up front since it's used by the GOLD/PREMIUM downgrade below.
        pinned_range, pinned_range_pct, pinned_atr_pct = False, 0.0, 0.0
        if PINNED_RANGE_CONFIG.get('enabled'):
            pinned_range, pinned_range_pct, pinned_atr_pct = check_pinned_range(
                df, PINNED_RANGE_CONFIG['lookback_days'],
                PINNED_RANGE_CONFIG['max_range_pct'], PINNED_RANGE_CONFIG['max_atr_pct']
            )

        # V15: Supertrend (ATR-band trend filter) — computed on-demand only for the
        # intraday modes that use it (scalping/daytrade), keeping the recursive O(n)
        # pass off the swing/longterm hot path. Feeds the `supertrend_bull` check below.
        if (mode_name in SUPERTREND_CONFIG.get('modes', ())
                and SUPERTREND_CONFIG.get('enabled')):
            try:
                from quantkit.indicators import calculate_supertrend
                df['Supertrend'], df['Supertrend_Dir'] = calculate_supertrend(
                    df, SUPERTREND_CONFIG['period'], SUPERTREND_CONFIG['multiplier']
                )
            except Exception as e:
                logger.debug(f"{symbol}: supertrend calc failed: {e}")

        # Surge day mode: relaxed thresholds for broad market gap-ups
        is_surge = kwargs.get('is_surge', False)
        if is_surge:
            from config import SURGE_DAY_CONFIG as _sc

        # Get values
        prev_high = df['high'].rolling(lookback).max().iloc[-2]
        latest = df.iloc[-1]

        # Surge day: use previous bar's high instead of rolling max.
        # On broad surge days after selloffs, price won't break the rolling high
        # but a strong gap above yesterday's high is a valid momentum signal.
        if is_surge:
            prev_high = df['high'].iloc[-2]
        
        # Scalping price filter
        if mode_name == 'scalping':
            if latest['close'] < cfg['min_price'] or latest['close'] > cfg['max_price']:
                logger.scan(f"{symbol}: skip — price ${latest['close']:.2f} outside scalping range [{cfg['min_price']}, {cfg['max_price']}]")
                return None
        
        # --- CORE BREAKOUT LOGIC ---
        price_break = latest['close'] > prev_high
        vol_confirm = latest['Vol_Ratio'] >= vol_thresh
        dist_atr = (latest['close'] - prev_high) / latest['ATR']
        dist_confirm = dist_atr >= atr_mult

        # Near-miss: within 0.5% below the breakout level + volume confirmed
        near_miss_breakout = (
            not price_break and vol_confirm and prev_high > 0
            and latest['close'] / prev_high >= 0.995
        )
        
        # Candle structure
        candle_ok, upper_wick, body_top_pct = check_candle_structure(
            latest, latest['ATR'], cfg['max_wick_atr'], cfg['max_body_top_pct']
        )
        
        # --- BB TREND GATE (V3) ---
        bb_trend = latest.get('BB_Trend', 'neutral')
        if BB_TREND_FILTER['enabled'] and BB_TREND_FILTER['reject_bearish']:
            if bb_trend == 'bearish':
                logger.scan(f"{symbol}: skip — bearish BB trend")
                return None

        # --- PATTERN DETECTION (V3) + VCP (V10) ---
        has_bullish_pattern, has_bearish_pattern, pattern_target, pattern_names, \
            pattern_vol_confirmed, vcp_quality, vcp_data = \
            get_pattern_score(df.tail(90), symbol, mode=mode_name)

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

        # V4: Over-extension distance from SMA
        sma_dist_pct = 0.0
        trend_line_val = latest.get('Trend_Line', 0)
        if trend_line_val > 0:
            sma_dist_pct = ((latest['close'] - trend_line_val) / trend_line_val) * 100

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

        # 9. Momentum surge: explosive gap, intraday, OR daily move + high volume (no consolidation needed)
        intraday_move_pct = (
            (latest['close'] - latest['open']) / latest['open'] * 100
            if latest.get('open', 0) > 0 else 0.0
        )
        # daily_move: prev close → current close (catches gap+run stocks like RCAT)
        daily_move_pct = (
            (latest['close'] - df['close'].iloc[-2]) / df['close'].iloc[-2] * 100
            if len(df) >= 2 and df['close'].iloc[-2] > 0 else 0.0
        )
        # Surge day: lower move & vol thresholds to catch broad gap-ups
        if is_surge:
            _ms_move = _sc.get('move_thresh_pct', 3.0)
            _ms_vol = _sc.get('vol_ratio_min', 1.5)
        else:
            _ms_move, _ms_vol = 4.0, 2.0
        momentum_surge = (
            gap_percent >= _ms_move or intraday_move_pct >= _ms_move
            or daily_move_pct >= _ms_move
        ) and latest['Vol_Ratio'] >= _ms_vol
        
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
                                         pattern_target=pattern_target,
                                         vcp_data=vcp_data)
        rr_ok = rr >= cfg['min_rr']

        # --- R:R GRADE + GRADE D REJECTION (V3) ---
        # Check from highest threshold down: B(≥2.0) → C(≥1.5) → A(≥0.66) → D
        rr_grade = 'D'
        for grade in ['B', 'C', 'A']:
            if rr >= RR_GRADE_CONFIG[grade]['min_rr']:
                rr_grade = grade
                break
        if RR_GRADE_CONFIG['D']['reject'] and rr_grade == 'D':
            logger.scan(f"{symbol}: skip — R:R grade D (rr={rr:.2f})")
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
        # Log stocks that are within 0.5% of the breakout level (near-miss threshold)
        elif near_miss_breakout:
            gap_to_break = (prev_high - latest['close']) / prev_high * 100
            self.rejection_reasons.append({
                'symbol': symbol,
                'price': round(latest['close'], 2),
                'vol_ratio': round(latest['Vol_Ratio'], 2),
                'momentum': int(momentum_score_preview) if not pd.isna(momentum_score_preview) else 0,
                'conviction': int(conviction_score_preview) if not pd.isna(conviction_score_preview) else 0,
                'rsi': int(latest.get('RSI', 0)),
                'mode': mode_name,
                'prev_high': round(prev_high, 2),  # breakout trigger level for near_miss_watch
                'reasons': f'Near miss ({gap_to_break:.2f}% below breakout {prev_high:.2f}) — watch for re-scan',
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

        # V5: Pre-compute values used in both scoring and non-scoring paths
        high_52w = df['high'].rolling(min(252, len(df))).max().iloc[-1]
        low_52w  = df['low'].rolling(min(252, len(df))).min().iloc[-1]
        dist_from_52w = ((high_52w - latest['close']) / high_52w) * 100 if high_52w > 0 else 100
        near_52w_high = dist_from_52w <= 5.0
        rsi_bull_div = bool(latest.get('RSI_Bull_Div', False))
        sector_hot = kwargs.get('sector_hot', False)

        # V8: Minervini Stage 2 Trend Template (weight=0 — optimizer eliminated as scorer,
        # but still computed for CSV diagnostics and used as screener gate in backtest)
        from indicators import calculate_minervini_template
        _minervini_conditions, minervini_score = calculate_minervini_template(
            df, high_52w=float(high_52w), low_52w=float(low_52w)
        )
        minervini_pass = minervini_score >= 7

        # --- SIGNAL SCORING SYSTEM ---
        use_scoring = kwargs.get('use_scoring', True)
        sr_data: dict = {}   # V11: populated inside use_scoring block
        tension = None       # V14: populated inside use_scoring block (Tension Index)

        if use_scoring:
            # Mandatory gate: price must break above previous high.
            # Momentum surge stocks bypass the liquidity gate — high volume IS the liquidity signal.
            if not price_break or (not liquid_ok and not momentum_surge):
                if not price_break:
                    logger.scan(f"{symbol}: skip — no price break (close={latest['close']:.2f} vs prev_high={prev_high:.2f})")
                else:
                    logger.scan(f"{symbol}: skip — low liquidity (no momentum surge to bypass)")
                return None

            if use_legacy:
                # V1: 3 binary momentum checks
                checks = {
                    'vol_confirm': vol_confirm,
                    'trend_ok': trend_ok,
                    'dist_confirm': dist_confirm,
                    'candle_ok': candle_ok,
                    'rr_ok': RR_GRADE_SCORES.get(rr_grade, 0.0),  # V9: B(≥2.0)=best, C(≥1.5)=good, A(≥0.66)=marginal
                    'no_vol_divergence': not vol_divergence,
                    'vwap_ok': vwap_ok,
                    'rsi_favorable': rsi_favorable,
                    'macd_favorable': macd_favorable,
                    'adx_trending': adx_trending,
                    'has_bullish_pattern': has_bullish_pattern,  # V12: pattern checks for all variants
                }
            else:
                # V2/V3: Composite momentum + conviction + pattern scores
                checks = {
                    'vol_confirm': vol_confirm,
                    'trend_ok': trend_ok,
                    'momentum_strong': momentum_strong,
                    'dist_confirm': dist_confirm,
                    'candle_ok': candle_ok,
                    'rr_ok': RR_GRADE_SCORES.get(rr_grade, 0.0),  # V9: B(≥2.0)=best, C(≥1.5)=good, A(≥0.66)=marginal
                    'no_vol_divergence': not vol_divergence,
                    'conviction_strong': conviction_strong,
                    'has_bullish_pattern': has_bullish_pattern,
                }

            # V5: New scoring checks
            checks['near_52w_high'] = near_52w_high
            checks['rsi_divergence'] = rsi_bull_div
            checks['sector_momentum'] = sector_hot

            # V6: Pattern volume confirmation
            checks['pattern_vol_confirmed'] = pattern_vol_confirmed

            # V7: Momentum surge
            checks['momentum_surge'] = momentum_surge

            # V8: Minervini Stage 2 Trend Template — proportional (0/8–8/8 → 0–15 pts)
            checks['minervini_template'] = minervini_score / 8.0

            # V10: VCP quality (proportional 0.0-1.0, only added when detected)
            if vcp_quality > 0:
                checks['vcp_quality'] = vcp_quality

            # V11: Support & Resistance levels (uses cached df — no extra fetch)
            # Mode-adaptive tolerance: tighter for intraday, looser for swing/longterm
            _sr_tol = {'scalping': 0.005, 'daytrade': 0.010, 'swing': 0.015, 'longterm': 0.020}
            from pattern_recognition import detect_sr_levels
            sr_data = detect_sr_levels(
                df,
                current_price=float(latest['close']),
                atr=float(latest['ATR']) if not pd.isna(latest.get('ATR', float('nan'))) else 1.0,
                tolerance_pct=_sr_tol.get(mode_name, 0.015),
            )
            checks['sr_breakout'] = sr_data['breaking_resistance']
            checks['at_key_support'] = sr_data['at_key_support']
            checks['trendline_break'] = sr_data['breaking_trendline']

            if mode_name != 'scalping':
                checks['rs_ok'] = rs_ok
                # Momentum surge / VCP / high-momentum plays bypass consolidation
                from config import MOMENTUM_OVERRIDE as _mo_cfg
                _mo_max_rsi = _sc.get('mo_max_rsi', 85) if is_surge else _mo_cfg['max_rsi']
                _mo_min_vol = _sc.get('mo_min_vol', 1.5) if is_surge else _mo_cfg['min_vol_ratio']
                _momentum_override = (
                    latest.get('Momentum_Score', 0) >= _mo_cfg['min_momentum']
                    and latest['Vol_Ratio'] >= _mo_min_vol
                    and latest.get('RSI', 50) < _mo_max_rsi
                )
                checks['consolidation'] = (
                    was_consolidating or momentum_surge
                    or is_surge  # Surge day bypasses consolidation entirely
                    or (vcp_quality > 0.3) or _momentum_override
                )

            # V4: Over-extension check (swing/longterm only)
            use_v4 = kwargs.get('use_v4_overextension', True)
            v4_thresholds = None
            if use_v4 and mode_name in ('swing', 'longterm'):
                from config import V4_OVEREXTENSION_FILTER
                if V4_OVEREXTENSION_FILTER.get('enabled'):
                    v4_thresholds = V4_OVEREXTENSION_FILTER.get('max_sma_dist_pct', {}).get(mode_name)
                    if v4_thresholds:
                        if sma_dist_pct > v4_thresholds['reject']:
                            logger.scan(f"{symbol}: skip — blow-off {sma_dist_pct:.1f}% from SMA")
                            return None
                        not_overextended = sma_dist_pct <= v4_thresholds['mild']
                        checks['not_overextended'] = not_overextended

            # V12: Aroon oscillator confirmation (uptrend strength check)
            try:
                from indicators import calculate_aroon
                from config import AROON_N, AROON_CONFIRM_THRESHOLD
                _aroon = calculate_aroon(df, n=AROON_N)
                _aroon_osc = float(_aroon['aroon_osc'].iloc[-1]) if not pd.isna(_aroon['aroon_osc'].iloc[-1]) else 0.0
                checks['aroon_confirm'] = _aroon_osc > AROON_CONFIRM_THRESHOLD
            except Exception:
                pass  # insufficient history or missing columns — skip silently

            # V15: Supertrend filter — require the ATR-band trend to agree with the
            # long (direction bullish + price above the line). Scalping/daytrade only;
            # the canonical whipsaw filter for tight-stop intraday entries.
            if (mode_name in SUPERTREND_CONFIG.get('modes', ())
                    and SUPERTREND_CONFIG.get('enabled')
                    and 'Supertrend_Dir' in df.columns):
                _st_dir = latest.get('Supertrend_Dir', 0)
                _st_line = latest.get('Supertrend', np.nan)
                checks['supertrend_bull'] = bool(
                    _st_dir == 1 and (pd.isna(_st_line) or latest['close'] > _st_line)
                )

            # V14: Tension Index — "coiled spring" composite (compression + volume
            # consensus + market/sector confirmation + fractal alignment). Added as a
            # proportional 0.0-1.0 check; sub-scores surfaced on the signal for transparency.
            # (`tension` is initialized to None before the use_scoring block.)
            if mode_name != 'scalping' and TENSION_CONFIG.get('enabled'):
                try:
                    from quantkit.tension import compute_tension_index, TensionConfig
                    _tcfg = TensionConfig(
                        w_compression=TENSION_CONFIG['w_compression'],
                        w_volume=TENSION_CONFIG['w_volume'],
                        w_confirmation=TENSION_CONFIG['w_confirmation'],
                        w_fractal=TENSION_CONFIG['w_fractal'],
                        gate_fractal=TENSION_CONFIG['gate_fractal'],
                        gate_fakeout=TENSION_CONFIG['gate_fakeout'],
                    )
                    tension = compute_tension_index(
                        df,
                        daily_df=kwargs.get('daily_df'),
                        spy_df=kwargs.get('spy_df'),
                        sector_df=kwargs.get('sector_df'),
                        regime=regime,
                        spy_perf=spy_perf,
                        timeframe=timeframe,
                        cfg=_tcfg,
                    )
                    checks['tension_index'] = tension['tension_index']
                except Exception as e:
                    logger.debug(f"{symbol}: tension index failed: {e}")

            _surge_thresholds = _sc.get('score_thresholds') if is_surge else None
            score, max_score, quality = self._calculate_signal_score(
                checks, score_thresholds_override=_surge_thresholds
            )

            if quality == 'REJECT':
                logger.scan(f"{symbol}: skip — score too low ({score}/{max_score})")
                return None

            # --- GOLD HARD GATES ---
            # Downgrade GOLD → PREMIUM if 5 strict conditions not all met
            if quality == 'GOLD':
                gold_gates = [
                    rr >= 3.0,                                    # R:R >= 3
                    latest['close'] > latest.get('Trend_Line', 0),  # Above SMA trend
                    latest['Vol_Ratio'] >= 2.0,                   # Volume >= 2x
                    near_52w_high,                                # Within 5% of 52w high
                    sector_hot,                                   # Sector momentum
                ]
                if not all(gold_gates):
                    quality = 'PREMIUM'
                    logger.debug(f"{symbol}: GOLD→PREMIUM (failed {5 - sum(gold_gates)}/5 hard gates)")

            # --- PREMIUM HARD GATES ---
            # Downgrade PREMIUM → HIGH if minimum conditions not met
            if quality == 'PREMIUM':
                target_upside_pct = ((tp - latest['close']) / latest['close']) * 100

                if mode_name in ('swing', 'longterm'):
                    # Swing/longterm: min 5% upside + above SMA 150
                    price_above_trend = latest['close'] > latest.get('Trend_Line', 0)
                    if target_upside_pct < 5.0:
                        quality = 'HIGH'
                        logger.debug(f"{symbol}: PREMIUM→HIGH (target upside {target_upside_pct:.1f}% < 5%)")
                    elif not price_above_trend:
                        quality = 'HIGH'
                        logger.debug(f"{symbol}: PREMIUM→HIGH (price below SMA 150)")
                    elif use_v4 and v4_thresholds and sma_dist_pct > v4_thresholds.get('heavy', 20):
                        quality = 'HIGH'
                        logger.debug(f"{symbol}: PREMIUM→HIGH (over-extended {sma_dist_pct:.1f}% from SMA)")

                elif mode_name == 'daytrade':
                    # Daytrade: min 2% upside + above daily SMA 20
                    # Intraday df has 15-min bars, so fetch daily data for SMA 20
                    price_above_sma20 = True  # default: don't penalize if fetch fails
                    try:
                        import yfinance as yf
                        daily = yf.Ticker(symbol.replace(' ', '-')).history(period='30d')
                        if len(daily) >= 20:
                            sma_20 = daily['Close'].rolling(20).mean().iloc[-1]
                            price_above_sma20 = latest['close'] > sma_20
                    except Exception:
                        pass
                    if target_upside_pct < 2.0:
                        quality = 'HIGH'
                        logger.debug(f"{symbol}: PREMIUM→HIGH (target upside {target_upside_pct:.1f}% < 2%)")
                    elif not price_above_sma20:
                        quality = 'HIGH'
                        logger.debug(f"{symbol}: PREMIUM→HIGH (price below daily SMA 20)")
                    # V9: Stop-distance gate — stops < 1% away trigger on normal intraday noise
                    if quality == 'PREMIUM':
                        stop_dist_pct = (latest['close'] - sl) / latest['close'] * 100
                        if stop_dist_pct < 1.0:
                            quality = 'HIGH'
                            logger.debug(f"{symbol}: PREMIUM→HIGH (stop too tight {stop_dist_pct:.2f}% < 1%)")

            # V14: Tension fractal-contradiction downgrade — a breakout that fights the
            # daily trend is structurally weaker; drop one quality tier.
            if (tension and TENSION_CONFIG.get('downgrade_on_contradiction')
                    and tension.get('fractal_contradiction')):
                _downgrade = {'GOLD': 'PREMIUM', 'PREMIUM': 'HIGH', 'HIGH': 'STANDARD'}
                if quality in _downgrade:
                    old_q = quality
                    quality = _downgrade[quality]
                    logger.debug(f"{symbol}: {old_q}→{quality} (tension fractal contradiction)")

            # Pinned/compressed-range veto — a stock this quiet (tight absolute range
            # + collapsed ATR, e.g. a cash-merger target pinned near the deal price)
            # cannot be a genuine Stage 2 breakout. Cap below PREMIUM/GOLD regardless
            # of how SMA/MACD/RSI happen to read near a flat price. See PINNED_RANGE_CONFIG.
            if pinned_range and quality in ('GOLD', 'PREMIUM'):
                old_q = quality
                quality = 'HIGH'
                logger.debug(
                    f"{symbol}: {old_q}→HIGH (pinned/compressed range: "
                    f"{pinned_range_pct:.1f}% range, {pinned_atr_pct:.2f}% ATR over "
                    f"{PINNED_RANGE_CONFIG['lookback_days']}d)"
                )
        else:
            # Original all-or-nothing logic
            condition_names = ['price_break', 'vol_confirm', 'dist_confirm', 'trend_ok',
                               'vwap_ok', 'liquid_ok', 'candle_ok', 'rr_ok', 'no_vol_div']
            condition_vals  = [price_break, vol_confirm, dist_confirm, trend_ok,
                               vwap_ok, liquid_ok, candle_ok, rr_ok, not vol_divergence]
            if mode_name != 'scalping':
                condition_names.extend(['rs_ok', 'consolidation'])
                condition_vals.extend([rs_ok, was_consolidating])

            if not all(condition_vals):
                failed = [n for n, v in zip(condition_names, condition_vals) if not v]
                logger.scan(f"{symbol}: skip — failed: {', '.join(failed)}")
                return None

            quality = self._determine_quality(mode_name, latest, df, has_gap_up)
            score = 100
        
        # --- WIN PROBABILITY ESTIMATION (V3) ---
        win_prob, win_grade = self._estimate_win_probability(
            trend_ok, momentum_strong, vol_confirm, has_bullish_pattern,
            bb_trend, rr_grade, conviction_strong
        )
        # Calibrated override: empirical WR by type×quality (quality is final
        # here — hard-gate downgrades already applied above)
        _cal = self._calibrated_winprob('Momentum' if momentum_surge else 'BREAKOUT', quality)
        if _cal:
            win_prob, win_grade = _cal

        # V13: Upgrade target using S/R resistance (computed after _calculate_rr)
        if sr_data and sr_data.get('nearest_resistance'):
            sr_res = float(sr_data['nearest_resistance'])
            if sr_res > latest['close'] * 1.02 and sr_res > tp:
                tp = sr_res
                rr = (tp - latest['close']) / max(latest['close'] - sl, 1e-6)

        # Build signal
        signal = {
            'Symbol': symbol,
            'Price': round(latest['close'], 2),
            'Vol': round(latest['Vol_Ratio'], 2),
            'Dist': round(dist_atr, 2),
            'SMA_Dist%': round(sma_dist_pct, 1),
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
            'Near52wHigh': near_52w_high if use_scoring else False,
            'RSI_BullDiv': rsi_bull_div if use_scoring else False,
            'PatternVolConf': pattern_vol_confirmed if use_scoring else False,
            'Type': 'Momentum' if momentum_surge else '',
            'RSI': round(float(rsi_val), 1) if not pd.isna(rsi_val) else '',
            'MinerviniScore': minervini_score,   # V8: 0-8 conditions met
            'VCP': vcp_quality > 0,                # V10: VCP pattern detected
            'VCP_Quality': round(vcp_quality, 2) if vcp_quality > 0 else '',
            'VCP_Pivot': round(vcp_data['pivot_point'], 2) if vcp_data else '',
            'VCP_Contractions': vcp_data.get('num_contractions', '') if vcp_data else '',
            # V11: S/R levels
            'SR_Resistance':     sr_data.get('nearest_resistance', '') if use_scoring else '',
            'SR_Res_Strength':   sr_data.get('resistance_strength', '') if use_scoring else '',
            'SR_Support':        sr_data.get('nearest_support', '') if use_scoring else '',
            'SR_Sup_Strength':   sr_data.get('support_strength', '') if use_scoring else '',
            'SR_Break':          sr_data.get('breaking_resistance', False) if use_scoring else False,
            # V11b: Angled trendlines + channel
            'SR_TL_Resistance':  sr_data.get('trendline_resistance',   '') if use_scoring else '',
            'SR_TL_Support':     sr_data.get('trendline_support',      '') if use_scoring else '',
            'SR_InChannel':      sr_data.get('in_channel',             False) if use_scoring else False,
            'SR_Channel_Dir':    sr_data.get('channel_direction',      '') if use_scoring else '',
            'SR_Channel_Width%': sr_data.get('channel_width_pct',      '') if use_scoring else '',
            'SR_TL_Break':       sr_data.get('breaking_trendline',     False) if use_scoring else False,
            # V12: Raw check booleans for weight optimizer re-scoring
            'Checks':            {k: bool(v) for k, v in checks.items()} if use_scoring else {},
            # V14: Tension Index — composite + sub-scores + state
            'Tension':           round(tension['tension_index'], 3) if tension else '',
            'Tension_State':     tension['state'] if tension else '',
            'Tension_C':         round(tension['compression'], 2) if tension else '',
            'Tension_V':         round(tension['volume_consensus'], 2) if tension else '',
            'Tension_F':         round(tension['confirmation'], 2) if tension else '',
            'Tension_A':         round(tension['fractal_alignment'], 2) if tension else '',
            'Tension_Silence':   tension['point_of_silence'] if tension else False,
        }
        
        if mode_name == 'scalping' and spread_pct is not None:
            signal['Spread%'] = round(spread_pct, 2)
        if mode_name in ('scalping', 'daytrade'):
            signal['EMA9']      = round(float(latest.get('EMA_9', 0)), 2) if not pd.isna(latest.get('EMA_9', np.nan)) else ''
            signal['EMA21']     = round(float(latest.get('EMA_21', 0)), 2) if not pd.isna(latest.get('EMA_21', np.nan)) else ''
            signal['StochRSI_K'] = round(float(latest.get('StochRSI_K', 0)), 1) if not pd.isna(latest.get('StochRSI_K', np.nan)) else ''
            signal['StochRSI_D'] = round(float(latest.get('StochRSI_D', 0)), 1) if not pd.isna(latest.get('StochRSI_D', np.nan)) else ''
            # V15: Supertrend transparency (+1 bullish / -1 bearish; line = trailing stop)
            signal['ST_Dir']  = int(latest.get('Supertrend_Dir', 0)) if not pd.isna(latest.get('Supertrend_Dir', np.nan)) else ''
            signal['ST_Line'] = round(float(latest.get('Supertrend', 0)), 2) if not pd.isna(latest.get('Supertrend', np.nan)) else ''

        logger.info(
            f"🚀 {symbol} {mode_name.upper()} @ ${latest['close']:.2f} | "
            f"SL: ${sl:.2f} | TP: ${tp:.2f} | R:R={rr:.2f} | {quality}"
        )
        
        return signal
    
    def _check_trend(self, latest, df, cfg, mode_name: str) -> bool:
        """Check trend condition"""
        if cfg['trend_type'] == 'VWAP':
            vwap_ok = (
                not pd.isna(latest.get('vwap')) and
                latest['close'] > latest['vwap'] and
                latest['vwap'] > df['vwap'].iloc[-2]
            )
            # Scalping: also require EMA 9 > EMA 21 (fast uptrend)
            if mode_name == 'scalping':
                ema9  = latest.get('EMA_9', np.nan)
                ema21 = latest.get('EMA_21', np.nan)
                if not pd.isna(ema9) and not pd.isna(ema21):
                    return vwap_ok and ema9 > ema21
            return vwap_ok
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

        # Check volume during consolidation is below average (use same window)
        consol_vol = check_window.loc[is_narrow, 'volume'].mean() if is_narrow.any() else float('inf')
        avg_vol = check_window['volume'].mean()
        if pd.isna(avg_vol) or pd.isna(consol_vol):
            return max_consecutive >= 3
        return consol_vol < avg_vol * 0.8
    
    def _calculate_rr(self, latest, cfg, mode_name: str, spread_pct: Optional[float],
                      df: pd.DataFrame = None, use_structural: bool = True,
                      pattern_target: float = 0.0,
                      vcp_data: dict = None) -> tuple:
        """Calculate stop loss, target, and risk/reward ratio"""
        # Scalping: fixed-cent stops (1-2¢) instead of ATR-based
        sl_fixed = cfg.get('sl_fixed_cents', 0)
        tp_fixed = cfg.get('tp_fixed_cents', 0)
        if mode_name == 'scalping' and sl_fixed > 0:
            sl = latest['close'] - (sl_fixed / 100.0)
            tp = latest['close'] + (tp_fixed / 100.0) if tp_fixed > 0 else latest['close'] + (sl_fixed * 3 / 100.0)
            if spread_pct is not None:
                spread_price = latest['close'] * (spread_pct / 100.0)
                entry_eff = latest['close'] + spread_price * 0.5
                sl_eff = sl - spread_price * 0.5
                rr = (tp - entry_eff) / max(entry_eff - sl_eff, 1e-6)
            else:
                rr = (tp - latest['close']) / max(latest['close'] - sl, 1e-6)
            return sl, tp, rr

        atr_stop = latest['close'] - (cfg['sl_mult'] * latest['ATR'])

        # Structural stop: use swing low of last 20 bars (take the higher/tighter stop)
        if use_structural and df is not None and len(df) >= 20:
            swing_low = df['low'].iloc[-20:].min()
            sl = max(swing_low, atr_stop)  # Tighter stop = less risk
        else:
            sl = atr_stop

        # V10: VCP stop override — low of final contraction is more precise
        if vcp_data and vcp_data.get('vcp_stop'):
            sl = max(sl, vcp_data['vcp_stop'])  # Use tighter (higher) stop

        # V10: VCP target override — measured move from pivot
        if vcp_data and vcp_data.get('breakout_target', 0) > 0:
            pattern_target = max(pattern_target, vcp_data['breakout_target'])

        atr_target = latest['close'] + (cfg['tp_mult'] * latest['ATR'])
        # Use the higher of ATR target and pattern-derived target
        tp = max(atr_target, pattern_target) if pattern_target > 0 else atr_target

        # Fibonacci extension from consolidation range (1.618 × range)
        if df is not None and len(df) >= 20:
            consol_high = df['high'].iloc[-20:-1].max()
            consol_low = df['low'].iloc[-20:-1].min()
            if consol_high > consol_low:
                fib_ext = consol_high + 1.618 * (consol_high - consol_low)
                if fib_ext > tp:
                    tp = fib_ext

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
                    # Bonus: Stochastic RSI not overbought → cleaner entry
                    stoch_k = latest.get('StochRSI_K', 50)
                    if not pd.isna(stoch_k) and stoch_k < 80:
                        return 'GOLD'
                    return 'PREMIUM'
            # EMA 9 > EMA 21 + vol expansion → HIGH+
            ema9  = latest.get('EMA_9', np.nan)
            ema21 = latest.get('EMA_21', np.nan)
            if (not pd.isna(ema9) and not pd.isna(ema21)
                    and ema9 > ema21 and latest['Vol_Ratio'] > 1.5):
                return 'HIGH'
            return 'STANDARD'

        return 'PREMIUM' if has_gap_up else 'HIGH'

    # --- Scoring weights for each check ---
    # Scoring weights are imported from config.SCORING_WEIGHTS (optimizer output).
    # Legacy V1 keys not in config are given default weight 5 via .get() fallback.

    def _calculate_signal_score(self, checks: dict,
                               score_thresholds_override: dict | None = None) -> tuple:
        """Calculate weighted signal score from boolean checks.
        Float values in [0.0, 1.0] contribute proportionally (e.g., minervini_template).

        Args:
            score_thresholds_override: Optional dict with GOLD/PREMIUM/HIGH/STANDARD
                thresholds to use instead of config defaults (used for surge day).
        """
        score = 0
        max_score = 0
        for key, passed in checks.items():
            weight = self.scoring_weights.get(key, 0)
            max_score += weight
            if isinstance(passed, float) and not isinstance(passed, bool):
                score += weight * passed  # proportional: 6/8 → 0.75 × 15 = 11.25 pts
            elif passed:
                score += weight

        pct = (score / max_score * 100) if max_score > 0 else 0

        # Quality thresholds — surge day uses relaxed thresholds from config
        th = score_thresholds_override or SCORE_THRESHOLDS
        if pct >= th.get('GOLD', 99):
            quality = 'GOLD'
        elif pct >= th.get('PREMIUM', 69):
            quality = 'PREMIUM'
        elif pct >= th.get('HIGH', 65):
            quality = 'HIGH'
        elif pct >= th.get('STANDARD', 50):
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
            'SMA_Dist%': round(((latest['close'] - latest.get('Trend_Line', latest['close'])) / max(latest.get('Trend_Line', latest['close']), 1)) * 100, 1),
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
        
        # Hard volume floor — bounce recovery starts on lower volume than breakouts,
        # but < 15% of average is genuinely dead (no institutional interest).
        if vol_ratio < 0.15:
            return None

        vol_strong = vol_ratio >= 1.5  # Strong volume is a bonus
        vol_ok = vol_ratio >= 1.0      # At least average volume (raised from 0.8)

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
        # Bounce quality is more lenient than breakout — deep pullback recoveries
        # often start on low volume (vol_strong rarely true at bounce start).
        # Compensate by weighting the recovery indicators more.
        if passed >= 6:
            quality = 'PREMIUM'
        elif passed >= 5:
            quality = 'HIGH'
        else:
            quality = 'STANDARD'
        
        signal = {
            'Symbol': symbol,
            'Price': round(latest['close'], 2),
            'Vol': round(vol_ratio, 2),
            'Dist': round(drawdown * 100, 1),  # Show as % drawdown
            'SMA_Dist%': round(((latest['close'] - latest.get('Trend_Line', latest['close'])) / max(latest.get('Trend_Line', latest['close']), 1)) * 100, 1),
            'Stop': round(sl, 2),
            'Target': round(tp, 2),
            'R:R': round(rr, 2),
            'Gap%': round(daily_gain * 100, 1),
            'Mode': mode_name,
            'Quality': quality,
            'Type': 'BOUNCE',
            'RSI': round(rsi, 1),
        }
        _cal = self._calibrated_winprob('BOUNCE', quality)
        if _cal:
            signal['WinProb'], signal['WinGrade'] = _cal
        
        logger.info(
            f"🔄 BOUNCE {symbol} @ ${latest['close']:.2f} (+{daily_gain:.1%}) | "
            f"Down {drawdown:.0%} from high | Vol: {vol_ratio:.1f}x | RSI: {rsi:.0f} | {quality}"
        )

        return signal

    def detect_continuation(self, df: pd.DataFrame, symbol: str, mode_name: str,
                            timeframe: str, spy_perf: float = 0.0,
                            **kwargs) -> Optional[Dict[str, Any]]:
        """
        Detect multi-day continuation surges (Day 2/3 follow-through).

        Catches stocks that already broke out and are surging for consecutive
        days with sustained volume — the pattern the standard breakout detector
        misses because prev_high keeps moving up.

        Criteria:
          1. 3+ consecutive green candles (closes > opens)
          2. Each close higher than prior close (staircase pattern)
          3. Cumulative move >= 8% over the streak
          4. Sustained volume >= 1.2x average across the streak
          5. Price above SMA 20 (short-term trend intact)
          6. RSI not extremely overbought (< 80)
        """
        cfg = MODES.get(mode_name, MODES['swing'])

        if len(df) < 50:
            return None

        if 'ATR' not in df.columns:
            df = calculate_all_indicators(
                df, cfg['trend_type'], cfg.get('trend_period'), timeframe
            )

        # Count consecutive green candles from the latest bar backward
        streak = 0
        for i in range(len(df) - 1, max(len(df) - 10, 0), -1):
            bar = df.iloc[i]
            prev_bar = df.iloc[i - 1] if i > 0 else None
            green = bar['close'] > bar['open']
            higher_close = prev_bar is not None and bar['close'] > prev_bar['close']
            if green and (streak == 0 or higher_close):
                streak += 1
            else:
                break

        if streak < 3:
            return None

        # Cumulative move over the streak
        streak_start = df.iloc[-(streak + 1)]  # bar before streak began
        latest = df.iloc[-1]
        cum_move_pct = (latest['close'] - streak_start['close']) / streak_start['close'] * 100

        if cum_move_pct < 8.0:
            return None

        # Average volume ratio across the streak
        streak_bars = df.iloc[-streak:]
        vol_avg_20 = df['volume'].rolling(20).mean().iloc[-(streak + 1)]
        if vol_avg_20 > 0:
            streak_vol_ratio = streak_bars['volume'].mean() / vol_avg_20
        else:
            streak_vol_ratio = 1.0

        if streak_vol_ratio < 1.2:
            return None

        # SMA 20 check — price must be above short-term trend
        sma_20 = df['close'].rolling(20).mean().iloc[-1]
        if pd.isna(sma_20) or latest['close'] < sma_20:
            return None

        # RSI guard — avoid chasing into extreme overbought
        rsi = latest.get('RSI', 50)
        if not pd.isna(rsi) and rsi > 80:
            return None

        # ── Resistance awareness (S/R + Volume Profile) ─────────────────
        atr = latest['ATR']
        current_price = float(latest['close'])

        # 1. Structural S/R — reuse the same detect_sr_levels from main detect()
        _sr_tol = {'scalping': 0.005, 'daytrade': 0.010, 'swing': 0.015, 'longterm': 0.020}
        from pattern_recognition import detect_sr_levels
        sr_data = detect_sr_levels(
            df, current_price=current_price,
            atr=float(atr) if not pd.isna(atr) else 1.0,
            tolerance_pct=_sr_tol.get(mode_name, 0.015),
        )
        nearest_res = sr_data.get('nearest_resistance')
        res_strength = sr_data.get('resistance_strength', 0)
        breaking_res = sr_data.get('breaking_resistance', False)

        # 2. Volume Profile — detect high-volume nodes above price
        vp = compute_volume_profile(df, lookback=60)
        hvn_above = [n for n in vp['high_volume_nodes'] if n > current_price]
        # Nearest HVN ceiling within 2 ATR
        hvn_ceiling = None
        if hvn_above and not pd.isna(atr) and atr > 0:
            nearest_hvn = min(hvn_above)
            if (nearest_hvn - current_price) <= 2.0 * atr:
                hvn_ceiling = nearest_hvn

        # Approaching resistance: within 1.5% of a tested structural level (not breaking through)
        approaching_structural = (
            nearest_res is not None
            and res_strength >= 2
            and not breaking_res
            and 0 < (nearest_res - current_price) / current_price <= 0.015
        )
        # Approaching volume ceiling: surging into a high-volume node
        approaching_volume = hvn_ceiling is not None

        # Reject: approaching strong structural resistance AND volume ceiling
        if approaching_structural and approaching_volume and res_strength >= 3:
            logger.info(
                f"❌ CONTINUATION {symbol} REJECTED — approaching resistance "
                f"${nearest_res:.2f} ({res_strength} touches) + HVN ${hvn_ceiling:.2f}"
            )
            return None

        # Flag resistance proximity for quality downgrade later
        _resistance_headwind = approaching_structural or approaching_volume

        # Relative strength vs SPY (optional bonus)
        stock_perf = cum_move_pct / 100
        rs_ok = stock_perf > spy_perf if mode_name != 'scalping' else True

        # Risk/Reward
        # Stop below streak start or 1.5 ATR below current
        sl = max(streak_start['low'], latest['close'] - 1.5 * atr)
        # Target: project the streak's average daily gain forward
        avg_daily_gain = cum_move_pct / streak
        tp = latest['close'] * (1 + avg_daily_gain * 2 / 100)  # 2 more days projected
        risk = latest['close'] - sl
        reward = tp - latest['close']
        rr = reward / max(risk, 1e-6)

        if rr < 1.0:
            return None

        # Quality scoring
        checks = {
            'streak_long': streak >= 4,
            'cum_move_big': cum_move_pct >= 12,
            'vol_strong': streak_vol_ratio >= 1.8,
            'rs_ok': rs_ok,
            'rsi_healthy': 50 <= rsi <= 70 if not pd.isna(rsi) else True,
            'above_sma20': True,  # already validated
        }
        passed = sum(checks.values())

        if passed >= 5:
            quality = 'PREMIUM'
        elif passed >= 3:
            quality = 'HIGH'
        else:
            quality = 'STANDARD'

        # Downgrade quality if surging into resistance (structural OR volume)
        if _resistance_headwind:
            downgrade = {'PREMIUM': 'HIGH', 'HIGH': 'STANDARD'}
            if quality in downgrade:
                old_q = quality
                quality = downgrade[quality]
                res_reason = []
                if approaching_structural:
                    res_reason.append(f"S/R ${nearest_res:.2f} ({res_strength}T)")
                if approaching_volume:
                    res_reason.append(f"HVN ${hvn_ceiling:.2f}")
                logger.info(
                    f"⚠️ CONTINUATION {symbol} downgraded {old_q}→{quality} — "
                    f"resistance headwind: {', '.join(res_reason)}"
                )

        signal = {
            'Symbol': symbol,
            'Price': round(latest['close'], 2),
            'Vol': round(streak_vol_ratio, 2),
            'Dist': round(cum_move_pct, 1),
            'SMA_Dist%': round(((latest['close'] - sma_20) / sma_20) * 100, 1),
            'Stop': round(sl, 2),
            'Target': round(tp, 2),
            'R:R': round(rr, 2),
            'Gap%': round(avg_daily_gain, 1),
            'Mode': mode_name,
            'Quality': quality,
            'Type': 'CONTINUATION',
            'RSI': round(rsi, 1) if not pd.isna(rsi) else 0,
            'Streak': streak,
            'SR_Resistance': round(nearest_res, 2) if nearest_res else '',
            'SR_Res_Strength': res_strength,
            'VPOC': round(vp['vpoc'], 2),
            'HVN_Ceiling': round(hvn_ceiling, 2) if hvn_ceiling else '',
        }
        _cal = self._calibrated_winprob('CONTINUATION', quality)
        if _cal:
            signal['WinProb'], signal['WinGrade'] = _cal

        logger.info(
            f"🚀 CONTINUATION {symbol} @ ${latest['close']:.2f} | "
            f"{streak}-day streak +{cum_move_pct:.1f}% | "
            f"Vol: {streak_vol_ratio:.1f}x | RSI: {rsi:.0f} | {quality}"
        )

        return signal

    def detect_slow_grind(self, df: pd.DataFrame, symbol: str, mode_name: str,
                          timeframe: str, spy_perf: float = 0.0,
                          **kwargs) -> Optional[Dict[str, Any]]:
        """
        Detect a steady grinding uptrend — new highs on most days, but no
        single dramatic breakout candle and no near-unbroken green streak.

        Every other detector needs a sharp trigger: a decisive break above
        resistance (detect), an oversold snap-back (detect_bounce), a fresh
        SMA20 cross, 3+ CONSECUTIVE green candles with an 8%+ move
        (detect_continuation — a single red day resets its streak counter to
        zero), or a full gate stack in one bar (detect_trend_confirm). A stock
        that grinds up over weeks with the occasional red day mixed in
        satisfies none of them — this is the gap CLAUDE.md's 2026-08 review
        found (NOW +31.5% in August, zero signals of any type all month).

        Criteria (SLOW_GRIND_CONFIG):
          1. Net return over the lookback >= min_cum_return_pct
          2. Majority (>= min_up_day_ratio) of days in the lookback are up
             days — deliberately NOT requiring a near-unbroken streak
          3. Close within near_high_pct of the lookback's own high — still
             near its highs, not round-tripping back down
          4. Close above SMA20, and SMA20 itself rising over the lookback
          5. RSI in [rsi_min, rsi_max] — healthy but not blown off
          6. Average volume ratio over the lookback >= min_vol_ratio
        """
        cfg = SLOW_GRIND_CONFIG
        lookback = cfg['lookback_days']

        if len(df) < max(60, lookback + 25):
            return None

        mcfg = MODES.get(mode_name, MODES['swing'])
        if 'ATR' not in df.columns:
            df = calculate_all_indicators(
                df, mcfg['trend_type'], mcfg.get('trend_period'), timeframe
            )

        latest = df.iloc[-1]
        window = df.iloc[-lookback:]
        start = df.iloc[-(lookback + 1)]

        cum_return_pct = (latest['close'] - start['close']) / start['close'] * 100
        if cum_return_pct < cfg['min_cum_return_pct']:
            return None

        closes = window['close'].to_numpy()
        prior_closes = df['close'].iloc[-(lookback + 1):-1].to_numpy()
        up_days = int((closes > prior_closes).sum())
        up_day_ratio = up_days / lookback
        if up_day_ratio < cfg['min_up_day_ratio']:
            return None

        lookback_high = float(window['high'].max())
        if latest['close'] < lookback_high * (1 - cfg['near_high_pct'] / 100):
            return None

        sma20 = df['close'].rolling(20).mean()
        sma20_now = sma20.iloc[-1]
        sma20_then = sma20.iloc[-(lookback + 1)]
        if pd.isna(sma20_now) or pd.isna(sma20_then):
            return None
        if latest['close'] < sma20_now or sma20_now <= sma20_then:
            return None

        rsi = latest.get('RSI', 50)
        if pd.isna(rsi) or not (cfg['rsi_min'] <= rsi <= cfg['rsi_max']):
            return None

        vol_avg_ref = df['volume'].rolling(20).mean().iloc[-(lookback + 1)]
        vol_ratio = (float(window['volume'].mean()) / vol_avg_ref
                     if vol_avg_ref and not pd.isna(vol_avg_ref) else 1.0)
        if vol_ratio < cfg['min_vol_ratio']:
            return None

        atr = latest['ATR']
        if pd.isna(atr):
            return None
        lookback_low = float(window['low'].min())
        sl = max(lookback_low, latest['close'] - cfg['atr_stop_mult'] * atr)
        risk = latest['close'] - sl
        if risk <= 0:
            return None
        tp = latest['close'] + risk * cfg['target_rr']
        rr = cfg['target_rr']

        rs_ok = (cum_return_pct / 100) > spy_perf if mode_name != 'scalping' else True

        checks = {
            'return_strong':   cum_return_pct >= cfg['min_cum_return_pct'] * 1.5,
            'up_ratio_strong': up_day_ratio >= 0.65,
            'rs_ok':           rs_ok,
            'rsi_mid':         55 <= rsi <= 70,
            'vol_confirmed':   vol_ratio >= 1.3,
        }
        passed = sum(checks.values())
        if passed >= 4:
            quality = 'PREMIUM'
        elif passed >= 2:
            quality = 'HIGH'
        else:
            quality = 'STANDARD'

        signal = {
            'Symbol':      symbol,
            'Price':       round(latest['close'], 2),
            'Vol':         round(vol_ratio, 2),
            'Dist':        round(cum_return_pct, 1),
            'SMA_Dist%':   round(((latest['close'] - sma20_now) / sma20_now) * 100, 1),
            'Stop':        round(sl, 2),
            'Target':      round(tp, 2),
            'R:R':         round(rr, 2),
            'Gap%':        round(cum_return_pct / lookback, 2),
            'Mode':        mode_name,
            'Quality':     quality,
            'Type':        'SLOW_GRIND',
            'RSI':         round(rsi, 1),
            'UpDayRatio':  round(up_day_ratio, 2),
        }
        _cal = self._calibrated_winprob('SLOW_GRIND', quality)
        if _cal:
            signal['WinProb'], signal['WinGrade'] = _cal

        logger.info(
            f"🐢 SLOW_GRIND {symbol} @ ${latest['close']:.2f} | "
            f"{lookback}d +{cum_return_pct:.1f}% | UpDays: {up_day_ratio:.0%} | "
            f"Vol: {vol_ratio:.1f}x | RSI: {rsi:.0f} | {quality}"
        )

        return signal

    def _trend_confirm_gates(self, df: pd.DataFrame, i: int,
                             sma150: pd.Series, sma50: pd.Series) -> Optional[Dict[str, Any]]:
        """
        Score the TREND_CONFIRM gates for the bar at index i. Returns a dict
        with bool flags G1–G7 and supporting numeric values (ext_pct, vol_ratio,
        rsi, etc.).  Returns None if i is too early or core data is missing.
        """
        if i < 160:
            return None
        if pd.isna(sma150.iloc[i]) or pd.isna(sma50.iloc[i]):
            return None
        row = df.iloc[i]
        if pd.isna(row.get('RSI')) or pd.isna(row.get('MACD')) or pd.isna(row.get('MACD_Signal')):
            return None

        cross_lb = TREND_CONFIRM['sma_cross_lookback']
        slope_lb = TREND_CONFIRM['sma_slope_lookback']
        m_lb     = TREND_CONFIRM['macd_cross_lookback']

        # G1: above SMA150
        g1 = bool(row['close'] > sma150.iloc[i])
        # G2: SMA150 slope up over slope_lb bars
        g2 = bool(i >= slope_lb and sma150.iloc[i] > sma150.iloc[i - slope_lb])
        # G3: fresh SMA150 cross (within cross_lb) OR golden cross active
        cl_w  = df['close'].iloc[i - cross_lb:i + 1].to_numpy()
        sm_w  = sma150.iloc[i - cross_lb:i + 1].to_numpy()
        fresh = bool(((cl_w[:-1] <= sm_w[:-1]) & (cl_w[1:] > sm_w[1:])).any())
        golden = bool(sma50.iloc[i] > sma150.iloc[i])
        g3 = fresh or golden
        # G4: MACD bull cross within m_lb OR persistent (>signal AND rising over 3 bars)
        m_arr = df['MACD'].iloc[i - m_lb:i + 1].to_numpy()
        s_arr = df['MACD_Signal'].iloc[i - m_lb:i + 1].to_numpy()
        m_crossed = bool(((m_arr[:-1] <= s_arr[:-1]) & (m_arr[1:] > s_arr[1:])).any())
        m_persistent = bool(
            row['MACD'] > row['MACD_Signal']
            and i >= 3 and df['MACD'].iloc[i] > df['MACD'].iloc[i - 3]
        )
        g4 = m_crossed or m_persistent
        # G5: RSI in [rsi_min, rsi_max]
        g5 = bool(TREND_CONFIRM['rsi_min'] <= row['RSI'] <= TREND_CONFIRM['rsi_max'])
        # G6: volume >= vol_ratio_min × 20-bar avg
        vol_avg = df['volume'].rolling(20).mean().iloc[i]
        vol_ratio = float(row['volume'] / vol_avg) if (vol_avg and not pd.isna(vol_avg)) else 0.0
        g6 = vol_ratio >= TREND_CONFIRM['vol_ratio_min']
        # G7: not blow-off (price not >ext_max above SMA50)
        ext_pct = (row['close'] - sma50.iloc[i]) / sma50.iloc[i]
        g7 = bool(ext_pct < TREND_CONFIRM['blow_off_max'])

        return {
            'G1': g1, 'G2': g2, 'G3': g3, 'G4': g4, 'G5': g5, 'G6': g6, 'G7': g7,
            'fresh': fresh, 'golden': golden, 'vol_ratio': vol_ratio,
            'ext_pct': ext_pct, 'rsi': float(row['RSI']),
            'score': sum([g1, g2, g3, g4, g5, g6, g7]),
        }

    def detect_trend_confirm(self, df: pd.DataFrame, symbol: str, mode_name: str,
                             timeframe: str, spy_perf: float = 0.0,
                             **kwargs) -> Optional[Dict[str, Any]]:
        """
        Detect classical trend-confirmation breakouts.

        Two firing paths:

        Path A (single-day breakout): all 7 gates pass on the latest bar.
          Quality: GOLD if vol >= vol_ratio_gold AND golden cross; else PREMIUM.

        Path B (mature trend / institutional accumulation): 6+ gates pass on
          the latest bar AND ≥ persistent_min_days of the last persistent_lookback
          bars also scored ≥ 6, AND the only allowed missing gates are G2 (slope)
          and G6 (volume).  Captures mega-cap names that rally on average volume
          (AMD, NVDA, MU in April 2026).
          Quality: PREMIUM only (no GOLD — trend matured without a confirming
          volume spike, slightly lower conviction than Path A).

        Both paths require G1, G3, G4, G5, G7 strictly on the latest bar,
        plus a minimum 20-day RS vs SPY and a minimum R:R.
        """
        if not TREND_CONFIRM.get('enabled'):
            return None
        if mode_name not in TREND_CONFIRM.get('enabled_modes', []):
            return None

        cfg = MODES.get(mode_name, MODES['swing'])
        if len(df) < 160:
            return None
        if 'ATR' not in df.columns:
            df = calculate_all_indicators(
                df, cfg['trend_type'], cfg.get('trend_period'), timeframe
            )

        sma150 = df['Trend_Line']
        sma50  = df['close'].rolling(50).mean()
        last   = self._trend_confirm_gates(df, len(df) - 1, sma150, sma50)
        if last is None:
            return None

        # Hard prerequisites for any firing: G1, G3, G4, G5, G7 must be True.
        # G2 (slope) and G6 (volume) may relax under Path B.
        hard_ok = last['G1'] and last['G3'] and last['G4'] and last['G5'] and last['G7']

        # Pinned/compressed-range veto (see PINNED_RANGE_CONFIG) — reuses the same
        # helper as the main breakout path so both detectors agree on one definition
        # of "pinned" (CLAUDE.md §20's one-filter lesson). TREND_CONFIRM only ever
        # emits PREMIUM/GOLD, so this fully blocks the detector for a pinned stock
        # rather than downgrading it, matching the main-path GOLD/PREMIUM veto's intent.
        if hard_ok and PINNED_RANGE_CONFIG.get('enabled'):
            _pinned, _, _ = check_pinned_range(
                df, PINNED_RANGE_CONFIG['lookback_days'],
                PINNED_RANGE_CONFIG['max_range_pct'], PINNED_RANGE_CONFIG['max_atr_pct']
            )
            hard_ok = hard_ok and not _pinned

        if not hard_ok:
            return None

        enabled_paths = TREND_CONFIRM.get('enabled_paths', ['A', 'B'])

        # Path A: all 7 pass
        path_a = ('A' in enabled_paths) and (last['score'] == 7)

        # Path B: persistent setup — N of last K bars have score >= 6 AND
        # the gates that fail today are only among {G2, G6}.
        path_b = False
        if 'B' in enabled_paths and not path_a:
            failing_today = [k for k in ('G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7') if not last[k]]
            allowed_to_fail = set(failing_today).issubset({'G2', 'G6'})
            if allowed_to_fail and last['score'] >= 6:
                lb     = TREND_CONFIRM['persistent_lookback']
                need   = TREND_CONFIRM['persistent_min_days']
                qual   = 0
                for off in range(1, lb + 1):
                    prev = self._trend_confirm_gates(df, len(df) - 1 - off, sma150, sma50)
                    if prev and prev['score'] >= 6 and prev['G1'] and prev['G3'] and prev['G7']:
                        qual += 1
                path_b = qual >= need

        if not (path_a or path_b):
            return None

        # RS vs SPY (shared by both paths)
        rs_lookback = min(20, len(df) - 1)
        stock_perf = (df['close'].iloc[-1] - df['close'].iloc[-rs_lookback - 1]) / df['close'].iloc[-rs_lookback - 1]
        if (stock_perf - spy_perf) < TREND_CONFIRM['rs_min']:
            return None

        # Stop / target / R:R
        latest = df.iloc[-1]
        recent_low_5 = float(df['low'].iloc[-5:].min())
        stop = max(float(sma50.iloc[-1]), recent_low_5) * 0.98
        risk = float(latest['close']) - stop
        if risk <= 0:
            return None
        target = float(latest['close']) + TREND_CONFIRM['rr_target_mult'] * risk
        rr = (target - float(latest['close'])) / max(risk, 1e-6)
        if rr < TREND_CONFIRM['min_rr']:
            return None

        # Quality
        if path_a:
            quality = 'GOLD' if (last['vol_ratio'] >= TREND_CONFIRM['vol_ratio_gold']
                                 and last['golden']) else 'PREMIUM'
            path_label = 'A'
        else:
            quality = 'PREMIUM'                            # Path B never GOLD
            path_label = 'B'

        sma150_dist_pct = ((latest['close'] - sma150.iloc[-1]) / sma150.iloc[-1]) * 100

        signal = {
            'Symbol':    symbol,
            'Price':     round(float(latest['close']), 2),
            'Vol':       round(last['vol_ratio'], 2),
            'Dist':      round(stock_perf * 100, 1),
            'SMA_Dist%': round(sma150_dist_pct, 1),
            'Stop':      round(stop, 2),
            'Target':    round(target, 2),
            'R:R':       round(rr, 2),
            'Gap%':      round(last['ext_pct'] * 100, 1),
            'Mode':      mode_name,
            'Quality':   quality,
            'Type':      'TREND_CONFIRM',
            'RSI':       round(last['rsi'], 1),
            'GoldenCross': last['golden'],
            'FreshCross':  last['fresh'],
            'TC_Path':     path_label,
            'TC_Score':    last['score'],
        }
        _cal = self._calibrated_winprob('TREND_CONFIRM', quality)
        if _cal:
            signal['WinProb'], signal['WinGrade'] = _cal

        logger.info(
            f"📈 TREND_CONFIRM/{path_label} {symbol} @ ${latest['close']:.2f} | "
            f"score {last['score']}/7 | "
            f"SMA150 {'fresh' if last['fresh'] else 'sustained'}"
            f"{', golden' if last['golden'] else ''} | "
            f"RSI {last['rsi']:.0f} | Vol {last['vol_ratio']:.2f}x | "
            f"Ext {last['ext_pct']*100:.1f}% | R:R={rr:.2f} | {quality}"
        )
        return signal

    def detect_sma20_cross(self, df: pd.DataFrame, symbol: str, mode_name: str,
                           timeframe: str, spy_perf: float = 0.0,
                           **kwargs) -> Optional[Dict[str, Any]]:
        """
        Detect price crossing above SMA 20 for the first time in N days.

        Catches stocks transitioning from downtrend to uptrend at the
        earliest possible signal — before a full breakout develops.
        Example: SNDK crossing above SMA 20 after multi-day decline.

        Criteria:
          1. Price closed ABOVE SMA 20 today
          2. Price was BELOW SMA 20 for at least 3 of the last 5 days
          3. Volume >= 2.5x on the crossing day (raised from 1.8x — 1.5-1.8 zone is weakest tier)
          4. SMA 20 slope is flattening or turning up (not steeply falling)
          5. RSI < 48 OR RSI 55-68 (avoid weak 48-55 transitional zone, empirically 33-38% WR)
          6. Price above SMA 50 (broader trend support) — HARD GATE
          7. Bullish candle on cross day — HARD GATE
        """
        cfg = MODES.get(mode_name, MODES['swing'])

        if len(df) < 50:
            return None

        if 'ATR' not in df.columns:
            df = calculate_all_indicators(
                df, cfg['trend_type'], cfg.get('trend_period'), timeframe
            )

        # Pinned/compressed-range check (deal-pin veto, see PINNED_RANGE_CONFIG) —
        # same primitive detect()/detect_trend_confirm() already gate on. A stock
        # frozen near a delisting/take-private price (e.g. CWAN pinned at $24.56
        # since its July 2026 buyout) trivially satisfies "above SMA20/SMA50" with
        # near-zero ATR, so this detector needs the same veto those two have.
        pinned_range, pinned_range_pct, pinned_atr_pct = False, 0.0, 0.0
        if PINNED_RANGE_CONFIG.get('enabled'):
            pinned_range, pinned_range_pct, pinned_atr_pct = check_pinned_range(
                df, PINNED_RANGE_CONFIG['lookback_days'],
                PINNED_RANGE_CONFIG['max_range_pct'], PINNED_RANGE_CONFIG['max_atr_pct']
            )

        latest = df.iloc[-1]
        sma_20 = df['close'].rolling(20).mean()

        if sma_20.iloc[-1] is None or pd.isna(sma_20.iloc[-1]):
            return None

        # 1. Price above SMA 20 today
        price_above = latest['close'] > sma_20.iloc[-1]
        if not price_above:
            return None

        # 2. Was below SMA 20 for at least 3 of last 5 days (before today)
        days_below = 0
        for i in range(-6, -1):
            if abs(i) <= len(df) and not pd.isna(sma_20.iloc[i]):
                if df['close'].iloc[i] < sma_20.iloc[i]:
                    days_below += 1
        if days_below < 3:
            return None

        # 3. Volume confirmation — raised to 2.5x (analysis: 2.5-3.0 = 57% WR, 1.8-2.0 = 50% WR,
        #    but 1.5-1.8 zone is the worst at 20% WR — skip transitional zone entirely)
        vol_ratio = latest.get('Vol_Ratio', 1.0)
        if vol_ratio < 2.5:
            return None

        # 4. SMA 20 slope — must be flattening or turning up
        sma_slope = (sma_20.iloc[-1] - sma_20.iloc[-5]) / sma_20.iloc[-5] * 100
        sma_turning_up = sma_slope > -1.0  # Not steeply declining

        if not sma_turning_up:
            return None

        # 5. RSI sweet-spot gate — multi-day analysis (Mar 4–16, 226 signals):
        #    RSI 48-55 is the WEAK zone (33-38% WR). Avoid it.
        #    RSI < 48: 71% WR (momentum building from mild oversold)
        #    RSI 55-68: 52-75% WR (strong upward momentum confirmed)
        #    Gate: NOT in the 48-55 transitional zone.
        rsi = latest.get('RSI', 50)
        rsi_ok = (rsi < 48 or 55 <= rsi <= 68) if not pd.isna(rsi) else True
        if not rsi_ok:
            return None

        # 6. SMA 50 — HARD GATE (was optional bonus)
        sma_50 = df['close'].rolling(50).mean().iloc[-1]
        above_sma50 = latest['close'] > sma_50 if not pd.isna(sma_50) else False
        if not above_sma50:
            return None

        # 7. Bullish candle on the cross — HARD GATE (was optional bonus)
        bullish_candle = latest['close'] > latest['open']
        if not bullish_candle:
            return None

        # 8. Distance from SMA 20 (freshness of cross — closer = better)
        cross_dist_pct = ((latest['close'] - sma_20.iloc[-1]) / sma_20.iloc[-1]) * 100

        # Risk/Reward
        atr = latest['ATR']
        sl = max(sma_20.iloc[-1] - 0.5 * atr, latest['low'] - 0.3 * atr)
        # Target: SMA 50 or 2.5x risk
        risk = latest['close'] - sl
        tp_rr = latest['close'] + risk * 2.5
        tp_sma50 = sma_50 if not pd.isna(sma_50) and sma_50 > latest['close'] else tp_rr
        tp = max(tp_rr, tp_sma50)
        rr = (tp - latest['close']) / max(risk, 1e-6)

        if rr < 1.5:
            return None

        # SMA 200 trend health
        sma_200 = df['close'].rolling(200).mean().iloc[-1] if len(df) >= 200 else None
        above_sma200 = latest['close'] > sma_200 if sma_200 and not pd.isna(sma_200) else False

        # Quality scoring — bullish_candle & above_sma50 are now hard gates,
        # replaced with higher-bar checks for differentiation.
        # NOTE: gate requires vol >= 2.5, so vol_very_strong uses >= 4.0 to actually
        # differentiate exceptional volume (institutional conviction) from just-passing.
        checks = {
            'vol_very_strong': vol_ratio >= 4.0,    # exceptional volume (4x+ avg) vs just meeting gate (2.5x)
            'above_sma200': above_sma200,            # long-term trend intact
            'fresh_cross': cross_dist_pct < 1.0,     # tight to SMA20 (was 2.0)
            'sma_slope_positive': sma_slope > 0,     # SMA20 turning up
            'rsi_sweet_spot': rsi < 48 or 58 <= rsi <= 68,  # momentum build OR strong trend (avoids weak 48-55)
        }
        passed = sum(checks.values())

        if passed >= 4:
            quality = 'PREMIUM'
        elif passed >= 3:
            quality = 'HIGH'
        else:
            quality = 'STANDARD'

        # Pinned/compressed-range veto — see PINNED_RANGE_CONFIG and the note above
        # where pinned_range is computed. Mirrors detect()'s downgrade exactly.
        if pinned_range and quality in ('GOLD', 'PREMIUM'):
            old_q = quality
            quality = 'HIGH'
            logger.debug(
                f"{symbol}: {old_q}→HIGH (pinned/compressed range: "
                f"{pinned_range_pct:.1f}% range, {pinned_atr_pct:.2f}% ATR over "
                f"{PINNED_RANGE_CONFIG['lookback_days']}d)"
            )

        signal = {
            'Symbol': symbol,
            'Price': round(latest['close'], 2),
            'Vol': round(vol_ratio, 2),
            'Dist': round(cross_dist_pct, 1),
            'SMA_Dist%': round(cross_dist_pct, 1),
            'Stop': round(sl, 2),
            'Target': round(tp, 2),
            'R:R': round(rr, 2),
            'Gap%': 0,
            'Mode': mode_name,
            'Quality': quality,
            'Type': 'SMA20_CROSS',
            'RSI': round(rsi, 1) if not pd.isna(rsi) else 0,
        }
        _cal = self._calibrated_winprob('SMA20_CROSS', quality)
        if _cal:
            signal['WinProb'], signal['WinGrade'] = _cal

        logger.info(
            f"📈 SMA20 CROSS {symbol} @ ${latest['close']:.2f} | "
            f"Vol: {vol_ratio:.1f}x | RSI: {rsi:.0f} | "
            f"SMA slope: {sma_slope:+.1f}% | {quality}"
        )

        return signal
