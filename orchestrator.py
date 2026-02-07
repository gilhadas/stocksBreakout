"""
Main orchestrator for scanning operations
Coordinates market data, detection, and exit evaluation
"""

import asyncio
import logging
import os
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path
import pandas as pd

from config import MODES, MAX_CONCURRENT_REQUESTS, SCAN_DELAY, OUTPUT_DIR
from market_data import MarketDataHandler
from scanner import BreakoutDetector
from exit_evaluator import ExitEvaluator
from level2_analyzer import Level2Analyzer
from utils import classify_market_regime

logger = logging.getLogger(__name__)


class ScannerOrchestrator:
    """Coordinates all scanning operations"""
    
    def __init__(self, ib_connection, use_level2: bool = False):
        self.market_data = MarketDataHandler(ib_connection)
        self.detector = BreakoutDetector()
        self.exit_evaluator = ExitEvaluator()
        self.level2_analyzer = Level2Analyzer(ib_connection) if use_level2 else None
        self.use_level2 = use_level2
        self._ensure_output_dir()
    
    def _ensure_output_dir(self):
        """Create output directory if it doesn't exist"""
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories
        for subdir in ['signals', 'exits', 'rejections', 'logs']:
            Path(OUTPUT_DIR, subdir).mkdir(exist_ok=True)
    
    async def scan_watchlist(self, watchlist: List[str], mode: str, 
                            timeframe: str, vol_thresh: Optional[float] = None,
                            atr_mult: Optional[float] = None) -> List[Dict]:
        """
        Scan entire watchlist for breakout signals
        
        Returns:
            List of signal dictionaries
        """
        # Get market context
        if mode != 'scalping':
            spy_perf, spy_vol = await self.market_data.get_spy_performance(
                timeframe, MODES[mode]['lookback']
            )
            regime = classify_market_regime(spy_perf, spy_vol)
            
            logger.info(
                f"Mode: {mode.upper()} | TF: {timeframe} | "
                f"SPY: {spy_perf:.2%} | Vol: {spy_vol:.2f}% | Regime: {regime}"
            )
        else:
            spy_perf, spy_vol, regime = 0.0, 0.0, 'INTRADAY'
            logger.info(f"Mode: SCALPING | TF: {timeframe} | VWAP-based")
        
        # Scan symbols concurrently with rate limiting
        results = []
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        
        async def _scan_one(idx_sym):
            idx, symbol = idx_sym
            async with semaphore:
                print(f"[{idx}/{len(watchlist)}] {symbol:6}", end="\r")
                
                result = await self._scan_symbol(
                    symbol, mode, timeframe, spy_perf, regime,
                    vol_thresh, atr_mult
                )
                
                await asyncio.sleep(SCAN_DELAY)
                return result
        
        tasks = [_scan_one((i, sym)) for i, sym in enumerate(watchlist, 1)]
        results_raw = await asyncio.gather(*tasks)
        results = [r for r in results_raw if r]
        
        print()  # Clear progress line
        
        return results
    
    async def _scan_symbol(self, symbol: str, mode: str, timeframe: str,
                          spy_perf: float, regime: str,
                          vol_thresh: Optional[float], 
                          atr_mult: Optional[float]) -> Optional[Dict]:
        """Scan a single symbol with retry logic and optional Level 2 analysis"""
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                # Get historical data
                df = await self.market_data.get_historical_data(symbol, timeframe)
                if df is None or len(df) < 50:
                    return None
                
                # Check spread for scalping
                spread_pct = None
                if mode == 'scalping':
                    spread_pct = await self.market_data.get_bid_ask_spread(symbol)
                    max_spread = MODES['scalping']['max_spread_pct']
                    if spread_pct is None or spread_pct > max_spread:
                        return None
                
                # Detect breakout
                signal = self.detector.detect(
                    df, symbol, mode, timeframe, spy_perf,
                    vol_thresh=vol_thresh,
                    atr_mult=atr_mult,
                    spread_pct=spread_pct,
                    regime=regime,
                    use_scoring=True,
                    use_legacy_momentum=False
                )
                
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
                
                return signal
            
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.debug(f"Failed {symbol}: {e}")
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
            
            # Evaluate exit
            decision = self.exit_evaluator.evaluate(
                df=df,
                symbol=symbol,
                mode_name=mode,
                entry_price=pos['entry'],
                stop_price=pos['stop'],
                target_price=pos['target'],
                timeframe=timeframe,
                regime=regime
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
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{result_type}_{mode}_{timestamp}.csv"
        
        # Save to appropriate subdirectory
        subdir = result_type if result_type in ['signals', 'exits', 'rejections'] else 'signals'
        filepath = os.path.join(OUTPUT_DIR, subdir, filename)
        
        df = pd.DataFrame(results)
        df.to_csv(filepath, index=False)
        
        logger.info(f"✓ Saved {len(results)} {result_type} to: {filepath}")
        return filepath
