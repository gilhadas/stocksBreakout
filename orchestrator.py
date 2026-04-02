"""
Main orchestrator for scanning operations
Coordinates market data, detection, and exit evaluation
"""

import asyncio
import logging
import os
import sys
from typing import List, Dict, Optional
from datetime import datetime
from zoneinfo import ZoneInfo

_NY_TZ = ZoneInfo('America/New_York')
from pathlib import Path
import pandas as pd

from config import (MODES, MAX_CONCURRENT_REQUESTS, SCAN_DELAY, OUTPUT_DIR,
                    V9H_REGIME_GATE, SECTOR_EXCEPTION, VIX_CONFIG,
                    SURGE_DAY_CONFIG)
from market_data import MarketDataHandler
from scanner import BreakoutDetector
from exit_evaluator import ExitEvaluator
from level2_analyzer import Level2Analyzer
from utils import classify_market_regime, get_smoothed_regime, check_regime_cooldown
from sentiment import get_sector_buzz, get_sector_for_ticker

logger = logging.getLogger(__name__)


class ScannerOrchestrator:
    """Coordinates all scanning operations"""
    
    def __init__(self, ib_connection, use_level2: bool = False, yf_fallback: bool = False):
        self.market_data = MarketDataHandler(ib_connection, yf_fallback=yf_fallback)
        self.detector = BreakoutDetector()
        self.exit_evaluator = ExitEvaluator()
        self.level2_analyzer = Level2Analyzer(ib_connection) if use_level2 and ib_connection else None
        self.use_level2 = use_level2 and ib_connection is not None
        self._ensure_output_dir()
    
    def _ensure_output_dir(self):
        """Create output directory if it doesn't exist"""
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories
        for subdir in ['signals', 'exits', 'rejections', 'logs']:
            Path(OUTPUT_DIR, subdir).mkdir(exist_ok=True)
    
    async def scan_watchlist(self, watchlist: List[str], mode: str, 
                            timeframe: str, vol_thresh: Optional[float] = None,
                            atr_mult: Optional[float] = None,
                            lookback: Optional[int] = None,
                            detect_bounces: bool = False) -> List[Dict]:
        """
        Scan entire watchlist for breakout and optionally bounce signals
        
        Args:
            detect_bounces: If True, also detect bounce/recovery signals
        
        Returns:
            List of signal dictionaries
        """
        # Get market context
        if mode != 'scalping':
            # Use provided lookback or default from config
            lb = lookback if lookback else MODES[mode]['lookback']
            spy_perf, spy_vol = await self.market_data.get_spy_performance(
                timeframe, lb
            )
            # Load surge context from premarket_monitor (if today's file exists)
            surge_context = None
            if SURGE_DAY_CONFIG.get('enabled'):
                import json
                surge_file = Path(OUTPUT_DIR) / 'lists' / 'surge_context.json'
                if surge_file.exists():
                    try:
                        ctx = json.loads(surge_file.read_text())
                        if ctx.get('date') == datetime.now(_NY_TZ).strftime('%Y-%m-%d'):
                            surge_context = ctx
                            logger.info(
                                f"Surge context: SPY gap {ctx['spy_gap_pct']:+.1f}%, "
                                f"{ctx['num_gappers']} gappers"
                            )
                    except Exception as e:
                        logger.debug(f"Surge context load failed: {e}")

                # Fallback: no premarket file but SPY intraday move is strong
                if surge_context is None:
                    spy_intraday_pct = spy_perf * 100
                    if spy_intraday_pct >= SURGE_DAY_CONFIG['spy_intraday_fallback_pct']:
                        surge_context = {
                            'spy_gap_pct': spy_intraday_pct,
                            'num_gappers': 0,
                        }
                        logger.info(
                            f"Surge fallback: SPY intraday {spy_intraday_pct:+.1f}% "
                            f"(no premarket data)"
                        )

            raw_regime = classify_market_regime(spy_perf, spy_vol,
                                               surge_context=surge_context)
            regime, regime_debug = get_smoothed_regime(raw_regime)

            # V9-H: compute bear_macro once per scan (SPY < 200-day SMA)
            bear_macro = False
            if V9H_REGIME_GATE.get('enabled'):
                bear_macro = await self.market_data.get_spy_bear_macro()

            # Session counter for momentum_surge exceptions (reset per scan_watchlist call)
            self._ms_exception_count = 0

            # Log regime with smoothing info
            regime_str = f"Regime: {regime}"
            if regime != raw_regime:
                regime_str = (
                    f"Regime: {regime} (raw: {raw_regime}, "
                    f"pending {regime_debug['count']}/{regime_debug['threshold']})"
                )
            # Check post-regime-change cooldown
            cooldown_hours = V9H_REGIME_GATE.get('cooldown_hours', 12)
            cooldown_active, cooldown_remaining = check_regime_cooldown(cooldown_hours)

            log_suffix = ""
            if bear_macro:
                log_suffix += " | BEAR_MACRO"
            if cooldown_active:
                log_suffix += f" | COOLDOWN: {cooldown_remaining:.1f}h left"

            logger.info(
                f"Mode: {mode.upper()} | TF: {timeframe} | "
                f"SPY: {spy_perf:.2%} | Vol: {spy_vol:.2f}% | {regime_str}"
                + log_suffix
            )
        else:
            spy_perf, spy_vol, regime, bear_macro = 0.0, 0.0, 'INTRADAY', False
            cooldown_active = False
            logger.info(f"Mode: SCALPING | TF: {timeframe} | VWAP-based")

        # Economic calendar: detect FOMC / CPI / NFP days
        event_ctx = {'is_event_day': False, 'event_name': '', 'sizing_mult': 1.0}
        try:
            from economic_calendar import get_event_context
            event_ctx = get_event_context()
            if event_ctx['is_event_day']:
                logger.info(
                    f"EVENT DAY: {event_ctx['event_name']} — "
                    f"sizing mult {event_ctx['sizing_mult']:.0%}"
                )
        except Exception as e:
            logger.debug(f"Economic calendar check failed: {e}")

        # VIX-based sizing: reduce exposure in high-volatility environments
        vix_sizing_mult = 1.0
        if VIX_CONFIG.get('enabled') and mode != 'scalping':
            try:
                vix = await self.market_data.get_vix_level()
                if vix > 0:
                    if vix >= VIX_CONFIG['extreme']:
                        vix_sizing_mult = VIX_CONFIG['sizing_mult_extreme']
                        logger.info(f"VIX: {vix:.1f} (EXTREME) — sizing mult {vix_sizing_mult:.0%}")
                    elif vix >= VIX_CONFIG['elevated']:
                        vix_sizing_mult = VIX_CONFIG['sizing_mult_elevated']
                        logger.info(f"VIX: {vix:.1f} (ELEVATED) — sizing mult {vix_sizing_mult:.0%}")
                    else:
                        logger.info(f"VIX: {vix:.1f} (normal)")
            except Exception as e:
                logger.debug(f"VIX fetch failed: {e}")

        # V5: Pre-compute sector buzz once for entire scan
        sector_hot_map = {}
        sector_scores_map = {}   # full RS/buzz data for sector-exception gate
        try:
            buzz_data = get_sector_buzz()
            for s in buzz_data.get('sectors', []):
                sector_hot_map[s['sector']] = s['buzz'] >= 7
                sector_scores_map[s['sector']] = {
                    'rs_5d': s.get('rs_5d', 0),
                    'buzz': s.get('buzz', 0),
                }
            if sector_hot_map:
                hot = [k for k, v in sector_hot_map.items() if v]
                logger.info(f"Hot sectors: {', '.join(hot) if hot else 'none'}")
                # Log strong-RS sectors (potential rotation targets)
                strong_rs = [f"{k} RS={v['rs_5d']:+.1f}%"
                             for k, v in sector_scores_map.items()
                             if v['rs_5d'] >= 2.0]
                if strong_rs:
                    logger.info(f"Strong RS sectors: {', '.join(strong_rs)}")
        except Exception as e:
            logger.debug(f"Sector buzz pre-compute failed: {e}")

        # Scan symbols concurrently with rate limiting
        results = []
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        
        async def _scan_one(idx_sym):
            idx, symbol = idx_sym
            async with semaphore:
                if sys.stdout.isatty():
                    print(f"[{idx}/{len(watchlist)}] {symbol:6}", end="\r", flush=True)

                result = await self._scan_symbol(
                    symbol, mode, timeframe, spy_perf, regime,
                    vol_thresh, atr_mult, lookback, detect_bounces,
                    sector_hot_map=sector_hot_map,
                    sector_scores_map=sector_scores_map,
                    bear_macro=bear_macro,
                    cooldown_active=cooldown_active,
                    is_surge=(regime == 'SURGE'),
                )

                await asyncio.sleep(SCAN_DELAY)
                return result
        
        tasks = [_scan_one((i, sym)) for i, sym in enumerate(watchlist, 1)]
        results_raw = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for r in results_raw:
            if isinstance(r, Exception):
                logger.warning(f"Symbol scan failed: {r}")
            elif r:
                results.append(r)

        # SURGE cap: keep top N signals by score, stamp surge metadata
        if regime == 'SURGE' and SURGE_DAY_CONFIG.get('enabled'):
            max_surge = SURGE_DAY_CONFIG.get('max_signals_per_scan', 10)
            surge_mult = SURGE_DAY_CONFIG.get('pos_size_mult', 0.7)
            for sig in results:
                sig['Surge_Day'] = True
                sig['Surge_Sizing_Mult'] = surge_mult
            if len(results) > max_surge:
                quality_rank = {'GOLD': 4, 'PREMIUM': 3, 'HIGH': 2, 'STANDARD': 1}
                results.sort(
                    key=lambda s: quality_rank.get(s.get('Quality', ''), 0),
                    reverse=True
                )
                results = results[:max_surge]
                logger.info(f"SURGE CAP: kept top {len(results)} signals")

        # Stamp sizing context onto signals (for auto_portfolio)
        # Combine event-day and VIX multipliers (multiplicative)
        combined_sizing = event_ctx['sizing_mult'] * vix_sizing_mult
        if combined_sizing < 1.0 or event_ctx['is_event_day']:
            for sig in results:
                if event_ctx['is_event_day']:
                    sig['Event_Day'] = event_ctx['event_name']
                if combined_sizing < 1.0:
                    sig['Event_Sizing_Mult'] = round(combined_sizing, 2)

        print()  # Clear progress line

        # Missed-movers summary: log symbols that moved ≥5% but were not signaled
        signaled_symbols = {r['Symbol'] for r in results}
        missed_movers = []
        for symbol in watchlist:
            if symbol in signaled_symbols:
                continue
            try:
                df = await self.market_data.get_historical_data(symbol, timeframe)
                if df is not None and len(df) >= 2:
                    prev_close = df['close'].iloc[-2]
                    cur_close = df['close'].iloc[-1]
                    if prev_close > 0:
                        daily_pct = (cur_close - prev_close) / prev_close * 100
                        if daily_pct >= 5.0:
                            vol_ratio = df.iloc[-1].get('Vol_Ratio', 0)
                            if pd.isna(vol_ratio):
                                vol_avg = df['volume'].rolling(20).mean().iloc[-1]
                                vol_ratio = df['volume'].iloc[-1] / vol_avg if vol_avg > 0 else 0
                            missed_movers.append((symbol, daily_pct, cur_close, vol_ratio))
            except Exception:
                pass

        if missed_movers:
            missed_movers.sort(key=lambda x: x[1], reverse=True)
            logger.warning(
                f"📋 MISSED MOVERS: {len(missed_movers)} symbol(s) moved ≥5% but were NOT signaled:"
            )
            for sym, pct, price, vol in missed_movers[:15]:
                logger.warning(f"   {sym:8} +{pct:.1f}%  ${price:.2f}  vol={vol:.1f}x")

        return results
    
    async def _scan_symbol(self, symbol: str, mode: str, timeframe: str,
                          spy_perf: float, regime: str,
                          vol_thresh: Optional[float],
                          atr_mult: Optional[float],
                          lookback: Optional[int] = None,
                          detect_bounces: bool = False,
                          sector_hot_map: Optional[Dict] = None,
                          sector_scores_map: Optional[Dict] = None,
                          bear_macro: bool = False,
                          cooldown_active: bool = False,
                          is_surge: bool = False) -> Optional[Dict]:
        """Scan a single symbol with retry logic and optional Level 2 analysis"""
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                # Get historical data
                df = await self.market_data.get_historical_data(symbol, timeframe)
                if df is None:
                    logger.scan(f"{symbol}: skip — no data returned")
                    return None
                if len(df) < 50:
                    logger.scan(f"{symbol}: skip — insufficient bars ({len(df)} < 50)")
                    return None
                
                # Check spread for scalping (skip in mock/yfinance fallback — no real-time quotes)
                spread_pct = None
                if mode == 'scalping' and not self.market_data.yf_fallback:
                    spread_pct = await self.market_data.get_bid_ask_spread(symbol)
                    max_spread = MODES['scalping']['max_spread_pct']
                    if spread_pct is None or spread_pct > max_spread:
                        logger.scan(f"{symbol}: skip — spread {spread_pct} (max {max_spread}%)")
                        return None
                
                # V5: Resolve sector for this symbol
                sector_hot = False
                if sector_hot_map:
                    sym_sector = get_sector_for_ticker(symbol)
                    sector_hot = sector_hot_map.get(sym_sector, False)

                # Detect breakout
                signal = self.detector.detect(
                    df, symbol, mode, timeframe, spy_perf,
                    vol_thresh=vol_thresh,
                    atr_mult=atr_mult,
                    lookback=lookback,
                    spread_pct=spread_pct,
                    regime=regime,
                    use_scoring=True,
                    use_legacy_momentum=False,
                    use_v4_overextension=True,
                    sector_hot=sector_hot,
                    is_surge=is_surge,
                )
                
                # V5: Multi-TF confirmation for swing mode
                if signal and mode == 'swing' and signal.get('Quality') in ('GOLD', 'PREMIUM'):
                    try:
                        weekly_df = await self.market_data.get_historical_data(symbol, '1W')
                        if weekly_df is not None and len(weekly_df) >= 10:
                            weekly_sma10 = weekly_df['close'].rolling(10).mean().iloc[-1]
                            if not pd.isna(weekly_sma10) and weekly_df['close'].iloc[-1] < weekly_sma10:
                                old_q = signal['Quality']
                                signal['Quality'] = 'PREMIUM' if old_q == 'GOLD' else 'HIGH'
                                logger.debug(f"{symbol}: {old_q}→{signal['Quality']} (weekly SMA10 disagrees)")
                    except Exception:
                        pass  # Don't kill the signal if weekly fetch fails

                # If signal found and Level 2 enabled, analyze depth
                if signal and self.use_level2:
                    depth = await self.level2_analyzer.get_market_depth(symbol)
                    
                    if depth:
                        # Evaluate entry quality
                        quality, reason = self.level2_analyzer.evaluate_entry_quality(depth)
                        
                        # Check breakout confirmation
                        confirmed = self.level2_analyzer.check_breakout_confirmation(
                            depth, signal['Price']
                        )
                        
                        # Add Level 2 data to signal
                        signal['Level2_Quality'] = quality
                        signal['Level2_Reason'] = reason
                        signal['Level2_Imbalance'] = depth['imbalance']
                        signal['Level2_Confirmed'] = confirmed
                        
                        # Reject if poor depth quality
                        if quality == 'POOR' or not confirmed:
                            logger.info(
                                f"   ❌ {symbol} rejected by Level 2: {reason}"
                            )
                            return None
                        
                        # Upgrade signal quality if excellent depth
                        if quality == 'EXCELLENT' and signal['Quality'] == 'HIGH':
                            signal['Quality'] = 'PREMIUM'
                            logger.info(f"   ⭐ {symbol} upgraded to PREMIUM (Level 2)")
                
                # Sector-rotation exception: allow PREMIUM+ breakouts in
                # sectors with strong relative strength, even when the broad
                # regime gate would normally block.
                sector_exception = False
                if (SECTOR_EXCEPTION.get('enabled') and signal
                        and sector_scores_map
                        and (bear_macro or regime in ('BEARISH', 'RED_MARKET'))):
                    sym_sector = get_sector_for_ticker(symbol)
                    sc = sector_scores_map.get(sym_sector, {})
                    quality_ok = signal.get('Quality') in ('GOLD', 'PREMIUM')
                    if (sc.get('rs_5d', 0) >= SECTOR_EXCEPTION['min_rs_5d']
                            and sc.get('buzz', 0) >= SECTOR_EXCEPTION['min_buzz']
                            and quality_ok):
                        sector_exception = True
                        signal['Sector_Exception'] = True
                        logger.info(
                            f"   SECTOR EXCEPTION: {symbol} ({sym_sector} "
                            f"RS={sc['rs_5d']:+.1f}%, buzz={sc['buzz']}) "
                            f"— allowed through regime gate"
                        )

                # Post-regime-change cooldown — suppress non-GOLD signals
                if cooldown_active and signal and not sector_exception:
                    exempt_qualities = V9H_REGIME_GATE.get(
                        'cooldown_exempt_quality', ['GOLD']
                    )
                    if signal.get('Quality') not in exempt_qualities:
                        logger.info(
                            f"   COOLDOWN: {symbol} {signal.get('Quality', '')} "
                            f"signal suppressed (regime changed recently)"
                        )
                        signal = None

                # V9-H regime gate — applied after initial breakout detection
                if V9H_REGIME_GATE.get('enabled') and not sector_exception:
                    if bear_macro:
                        # Structural bear (SPY < SMA200): GOLD breakouts only
                        # — with a narrow exception for momentum surges (3x vol + 5% move)
                        if signal and signal.get('Quality') != 'GOLD':
                            msex = V9H_REGIME_GATE.get('momentum_surge_exception', {})
                            vol_ok  = signal.get('Vol_Ratio', 0) >= msex.get('min_vol_ratio', 3.0)
                            move_ok = abs(signal.get('Gap%', 0) or 0) >= msex.get('min_move_pct', 5.0)
                            type_ok = signal.get('Type') not in msex.get('blocked_types', [])
                            qual_ok = signal.get('Quality') in msex.get('allowed_qualities', [])
                            cap_ok  = getattr(self, '_ms_exception_count', 0) < msex.get('max_per_day', 2)

                            if msex.get('enabled') and vol_ok and move_ok and type_ok and qual_ok and cap_ok:
                                signal['MomentumException'] = True
                                signal['PosSizeMult'] = msex.get('pos_size_mult', 0.5)
                                self._ms_exception_count = getattr(self, '_ms_exception_count', 0) + 1
                                logger.info(
                                    f"   🔥 MOMENTUM EXCEPTION: {symbol} {signal.get('Quality')} "
                                    f"Vol={signal.get('Vol_Ratio', 0):.1f}x "
                                    f"Gap={signal.get('Gap%', 0):.1f}% "
                                    f"— passed bear_macro gate (half-size, "
                                    f"{self._ms_exception_count}/{msex.get('max_per_day', 2)} today)"
                                )
                            else:
                                signal = None
                        # bear_macro: no cascade to BOUNCE/SMA20_CROSS, but allow
                        # continuation detection (it has its own quality checks)
                        if signal is None:
                            # Try continuation only — skip bounce/sma20_cross in bear macro
                            signal = self.detector.detect_continuation(
                                df, symbol, mode, timeframe, spy_perf
                            )
                            return signal
                    elif regime == 'BEARISH':
                        # Short-term pullback: breakouts still allowed, but no BOUNCE/SMA20_CROSS
                        if signal is not None:
                            # Breakout found — let it through
                            pass
                        else:
                            # No breakout — allow continuation, block BOUNCE/SMA20_CROSS
                            signal = self.detector.detect_continuation(
                                df, symbol, mode, timeframe, spy_perf
                            )
                            return signal

                # If no breakout signal, try alternative detectors (cascade)
                if signal is None and detect_bounces:
                    signal = self.detector.detect_bounce(
                        df, symbol, mode, timeframe
                    )

                if signal is None:
                    signal = self.detector.detect_continuation(
                        df, symbol, mode, timeframe, spy_perf
                    )

                if signal is None:
                    signal = self.detector.detect_sma20_cross(
                        df, symbol, mode, timeframe, spy_perf
                    )

                return signal
            
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.warning(f"Failed {symbol}: {e}")
                    return None
                await asyncio.sleep(1)
        
        return None
    
    async def evaluate_exits(self, positions: List[Dict], 
                            regime: str = 'NORMAL') -> List[Dict]:
        """
        Evaluate exit conditions for existing positions
        
        Args:
            positions: List of position dicts with keys:
                       symbol, mode, entry, stop, target, timeframe
            regime: Market regime (CHOPPY/EXPANSION/NORMAL)
        
        Returns:
            List of exit decision dictionaries
        """
        logger.info(f"Evaluating {len(positions)} positions (Regime: {regime})")
        
        exit_results = []
        
        for pos in positions:
            symbol = pos['symbol']
            mode = pos['mode']
            timeframe = pos['timeframe']
            
            # Get historical data
            df = await self.market_data.get_historical_data(symbol, timeframe)
            if df is None or len(df) < 30:
                logger.warning(f"No data for {symbol}")
                continue
            
            # Compute days held from entry_date (if available)
            days_held = 0
            entry_date_str = pos.get('entry_date', '')
            if entry_date_str:
                try:
                    entry_dt = datetime.strptime(entry_date_str, '%Y-%m-%d')
                    days_held = (datetime.now(ZoneInfo('America/New_York')).replace(tzinfo=None) - entry_dt).days
                except ValueError:
                    pass

            # Evaluate exit
            decision = self.exit_evaluator.evaluate(
                df=df,
                symbol=symbol,
                mode_name=mode,
                entry_price=pos['entry'],
                stop_price=pos['stop'],
                target_price=pos['target'],
                timeframe=timeframe,
                regime=regime,
                days_held=days_held,
                signal_type=pos.get('signal_type', '')
            )
            
            exit_results.append(decision)
        
        return exit_results
    
    def get_rejection_reasons(self) -> List[Dict]:
        """Get list of rejection reasons for analysis"""
        return self.detector.rejection_reasons
    
    def save_results(self, results: List[Dict], mode: str, 
                    result_type: str = 'signals') -> str:
        """
        Save results to CSV file in appropriate subdirectory
        
        Args:
            results: List of result dictionaries
            mode: Trading mode
            result_type: 'signals', 'exits', or 'rejections'
        
        Returns:
            Output filename (full path)
        """
        if not results:
            return ""
        
        timestamp = datetime.now(_NY_TZ).strftime('%Y%m%d_%H%M%S')
        filename = f"{result_type}_{mode}_{timestamp}.csv"
        
        # Save to appropriate subdirectory
        subdir = result_type if result_type in ['signals', 'exits', 'rejections'] else 'signals'
        filepath = os.path.join(OUTPUT_DIR, subdir, filename)
        
        df = pd.DataFrame(results)
        df.to_csv(filepath, index=False)

        # Mirror to S3 so mobile app / Streamlit Cloud stay in sync
        try:
            from utils import _is_cloud, _s3_fs
            if _is_cloud():
                s3_key = f"{subdir}/{filename}"
                _s3_fs().put(filepath, f"stocks-breakout-scanner-s3-bucket/{s3_key}")
                logger.info(f"↑ S3 sync: {s3_key}")
        except Exception as _e:
            logger.warning(f"S3 sync skipped: {_e}")

        logger.info(f"✓ Saved {len(results)} {result_type} to: {filepath}")
        return filepath
