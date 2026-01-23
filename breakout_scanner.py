"""
Breakout Scanner for Interactive Brokers
Supports: Swing, Daytrade, and Scalping modes

Exit file format (CSV):
  symbol,mode,entry,stop,target,timeframe
  AAPL,swing,185.50,180.00,195.00,1 day
  NVDA,daytrade,520.30,515.00,530.00,15 mins
"""

import pandas as pd
import numpy as np
from datetime import datetime
import asyncio
import warnings
import nest_asyncio
import argparse
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
from ib_insync import IB, Stock, util

nest_asyncio.apply()
warnings.filterwarnings('ignore')

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(f'scanner_{datetime.now():%Y%m%d}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logging.getLogger('ib_insync').setLevel(logging.WARNING)

# --- Mode Configurations ---
MODES = {
    'swing': {
        'lookback': 20,
        'vol_thresh': 1.3,
        'atr_mult': 0.5,
        'trend_type': 'SMA',
        'trend_period': 200,
        'sl_mult': 2.0,
        'tp_mult': 4.0,
        'min_consolidation_bars': 3,
        'min_rr': 2.0,
        'max_wick_atr': 0.5,
        'max_body_top_pct': 0.3
    },
    'daytrade': {
        'lookback': 10,
        'vol_thresh': 1.1,
        'atr_mult': 0.2,
        'trend_type': 'EMA',
        'trend_period': 9,
        'sl_mult': 1.5,
        'tp_mult': 3.0,
        'min_consolidation_bars': 2,
        'min_rr': 1.5,
        'max_wick_atr': 0.75,
        'max_body_top_pct': 0.4
    },
    'scalping': {
        'lookback': 5,
        'vol_thresh': 2.0,
        'atr_mult': 0.15,
        'trend_type': 'VWAP',
        'trend_period': None,
        'sl_mult': 0.5,
        'tp_mult': 1.0,
        'min_consolidation_bars': 1,
        'min_rr': 1.0,
        'max_spread_pct': 0.1,
        'min_price': 5.0,
        'max_price': 500.0,
        'max_wick_atr': 1.0,
        'max_body_top_pct': 0.5
    }
}

# Regime configuration
REGIME_CONFIG = {
    'CHOPPY': {
        'vol_mult': 1.3,
        'atr_mult': 1.3,
        'description': 'Low momentum, high noise'
    },
    'EXPANSION': {
        'vol_mult': 0.9,
        'atr_mult': 0.9,
        'description': 'High momentum, trending'
    },
    'NORMAL': {
        'vol_mult': 1.0,
        'atr_mult': 1.0,
        'description': 'Standard conditions'
    }
}

@dataclass
class BreakoutSignal:
    symbol: str
    mode: str
    price: float
    vol_ratio: float
    dist_atr: float
    stop: float
    target: float
    rr: float
    gap_pct: float
    quality: str
    spread_pct: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            'Symbol': self.symbol,
            'Mode': self.mode,
            'Price': round(self.price, 2),
            'Vol': round(self.vol_ratio, 2),
            'Dist': round(self.dist_atr, 2),
            'Stop': round(self.stop, 2),
            'Target': round(self.target, 2),
            'R:R': round(self.rr, 2),
            'Gap%': round(self.gap_pct, 2) if abs(self.gap_pct) > 0.5 else 0,
            'Quality': self.quality,
        }
        if self.spread_pct is not None:
            result['Spread%'] = round(self.spread_pct, 2)
        return result


class BreakoutScanner:
    def __init__(self, ib_connection):
        self.ib = ib_connection
        self.spy_cache = {}
        self.rejection_reasons = []
        self._indicator_cache = {}

    def calculate_indicators(self, df, trend_type, trend_period, timeframe):
        """Calculate all technical indicators"""
        # ATR
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        df['ATR'] = np.max(ranges, axis=1).rolling(14).mean()
        
        # Volume
        df['Vol_MA'] = df['volume'].rolling(20).mean()
        df['Vol_Ratio'] = df['volume'] / df['Vol_MA']
        
        # Trend Filter
        if trend_type == 'VWAP':
            df['Trend_Line'] = self.calculate_vwap(df, timeframe)
        elif trend_type == 'EMA':
            df['Trend_Line'] = df['close'].ewm(span=trend_period, adjust=False).mean()
        else:
            df['Trend_Line'] = df['close'].rolling(trend_period).mean()
        
        # VWAP - Always calculate for intraday
        if 'min' in timeframe or 'hour' in timeframe:
            df['vwap'] = self.calculate_vwap(df, timeframe)
        else:
            df['vwap'] = np.nan
        
        # Bollinger Bands Width for consolidation
        bb_ma = df['close'].rolling(20).mean()
        bb_std = df['close'].rolling(20).std()
        df['BB_Width'] = ((bb_std * 2) / bb_ma) * 100
        df['Avg_BB_Width'] = df['BB_Width'].rolling(20).mean()
        df['Is_Consolidating'] = df['BB_Width'] < (df['Avg_BB_Width'] * 0.6)
        
        return df

    def calculate_vwap(self, df, timeframe):
        """Calculate VWAP properly - resets daily"""
        df = df.copy()
        df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
        
        # Reset VWAP daily for intraday bars
        if hasattr(df.index, 'date'):
            df['date_only'] = df.index.date
            vwap = df.groupby('date_only').apply(
                lambda x: (x['typical_price'] * x['volume']).cumsum() / x['volume'].cumsum()
            ).reset_index(level=0, drop=True)
        else:
            vwap = (df['typical_price'] * df['volume']).cumsum() / df['volume'].cumsum()
        
        return vwap

    def calculate_gap(self, df):
        """Calculate opening gap percentage"""
        if len(df) < 2:
            return 0.0
        prev_close = df['close'].iloc[-2]
        current_open = df['open'].iloc[-1]
        gap_pct = ((current_open - prev_close) / prev_close) * 100
        return float(gap_pct)

    def check_liquidity(self, df, min_dollar_volume=5_000_000):
        """Check if stock has sufficient liquidity"""
        avg_dollar_volume = (df['close'] * df['volume']).tail(20).mean()
        return avg_dollar_volume >= min_dollar_volume

    async def get_spy_performance(self, timeframe, lookback) -> Tuple[float, float]:
        """Get SPY performance and volatility with caching"""
        cache_key = f"{timeframe}_{lookback}"
        
        if cache_key in self.spy_cache:
            return self.spy_cache[cache_key]
        
        try:
            spy = Stock('SPY', 'ARCA', 'USD')
            duration = '300 D' if 'day' in timeframe else '10 D'
            bars = await self.ib.reqHistoricalDataAsync(
                spy, '', duration, timeframe, 'TRADES', True, 1
            )
            
            if not bars:
                return 0.0, 0.0
            
            df = util.df(bars)
            perf = (df['close'].iloc[-1] / df['close'].iloc[-lookback]) - 1

            # Calculate volatility (ATR%)
            df['hl'] = df['high'] - df['low']
            df['hc'] = (df['high'] - df['close'].shift()).abs()
            df['lc'] = (df['low'] - df['close'].shift()).abs()
            tr = pd.concat([df['hl'], df['hc'], df['lc']], axis=1).max(axis=1)
            atr = tr.rolling(14).mean()
            atr_pct = (atr / df['close']) * 100
            spy_vol = float(atr_pct.tail(lookback).mean())

            self.spy_cache[cache_key] = (perf, spy_vol)
            return perf, spy_vol
            
        except Exception as e:
            logger.warning(f"Failed to get SPY performance: {e}")
            return 0.0, 0.0

    async def get_bid_ask_spread(self, contract):
        """Get current bid-ask spread for scalping liquidity check"""
        try:
            ticker = self.ib.reqMktData(contract, '', False, False)
            await asyncio.sleep(0.5)
            
            if ticker.bid and ticker.ask and ticker.bid > 0:
                spread_pct = ((ticker.ask - ticker.bid) / ticker.bid) * 100
                self.ib.cancelMktData(contract)
                return spread_pct
            
            self.ib.cancelMktData(contract)
            return None
        except Exception as e:
            logger.debug(f"Spread check failed: {e}")
            return None

    def detect_breakout(self, df, symbol, mode_name, timeframe, spy_perf, **kwargs):
        """Main breakout detection with comprehensive filters"""
        cfg = MODES[mode_name]
        regime = kwargs.get('regime', 'NORMAL')
        
        # Apply regime adjustments (work with copies)
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
        df = self.calculate_indicators(df.copy(), cfg['trend_type'], cfg.get('trend_period'), timeframe)

        # Get values
        prev_high = df['high'].rolling(lookback).max().iloc[-2]
        latest = df.iloc[-1]

        # Scalping-specific price filter
        if mode_name == 'scalping':
            if latest['close'] < cfg['min_price'] or latest['close'] > cfg['max_price']:
                return None

        # --- CORE BREAKOUT LOGIC ---
        price_break = latest['close'] > prev_high
        vol_confirm = latest['Vol_Ratio'] >= vol_thresh
        dist_atr = (latest['close'] - prev_high) / latest['ATR']
        dist_confirm = dist_atr >= atr_mult

        # --- CANDLE STRUCTURE FILTER (FIXED) ---
        high = latest['high']
        low = latest['low']
        close = latest['close']
        open_price = latest['open']
        
        rng = max(high - low, 1e-6)
        body_top = max(open_price, close)  # FIX: removed incorrect open() call
        body_bottom = min(open_price, close)
        upper_wick = high - body_top
        body_top_pct = (high - close) / rng

        candle_ok = (
            (upper_wick / latest['ATR']) <= cfg['max_wick_atr'] and
            body_top_pct <= cfg['max_body_top_pct']
        )

        # --- FILTERS ---
        # 1. Relative Strength (skip for scalping)
        if mode_name != 'scalping':
            stock_perf = (latest['close'] / df['close'].iloc[-lookback]) - 1
            rs_ok = stock_perf > spy_perf
        else:
            stock_perf = 0.0
            rs_ok = True

        # 2. Trend Filter
        if cfg['trend_type'] == 'VWAP':
            trend_ok = (
                not pd.isna(latest.get('vwap')) and
                latest['close'] > latest['vwap'] and
                latest['vwap'] > df['vwap'].iloc[-2]
            )
        else:
            trend_ok = latest['close'] > latest['Trend_Line']

        # 3. VWAP position
        vwap_ok = True
        if not pd.isna(latest.get('vwap')):
            if mode_name == 'scalping':
                vwap_ok = latest['close'] > latest['vwap'] and latest['close'] > open_price
            elif mode_name == 'daytrade':
                vwap_ok = latest['close'] > latest['vwap']

        # 4. Consolidation
        was_consolidating = True
        if mode_name != 'scalping':
            min_cons_bars = cfg['min_consolidation_bars']
            was_consolidating = df['Is_Consolidating'].iloc[-min_cons_bars-1:-1].any()

        # 5. Liquidity
        liquid_ok = self.check_liquidity(df)

        # 6. Gap detection
        gap_percent = self.calculate_gap(df) if mode_name != 'scalping' else 0.0
        has_gap_up = gap_percent > 2.0

        # 7. Volume spike for scalping
        if mode_name == 'scalping':
            recent_vol_spike = latest['Vol_Ratio'] > vol_thresh * 1.5
            vol_confirm = vol_confirm and recent_vol_spike

        # 8. Volume divergence check
        vol_divergence = False
        if len(df) >= 10:
            recent_vol = df['volume'].tail(5).mean()
            prev_vol = df['volume'].iloc[-10:-5].mean()
            price_increasing = latest['close'] > df['close'].iloc[-5]
            vol_divergence = price_increasing and recent_vol < prev_vol * 0.7

        # --- RISK / REWARD CALC (FIXED for scalping spread) ---
        sl = latest['close'] - (cfg['sl_mult'] * latest['ATR'])
        tp = latest['close'] + (cfg['tp_mult'] * latest['ATR'])

        if mode_name == 'scalping' and spread_pct is not None:
            spread_price = latest['close'] * (spread_pct / 100.0)
            entry_eff = latest['close'] + spread_price * 0.5
            sl_eff = sl - spread_price * 0.5
            rr = (tp - entry_eff) / max(entry_eff - sl_eff, 1e-6)
        else:
            rr = (tp - latest['close']) / max(latest['close'] - sl, 1e-6)

        rr_ok = rr >= cfg['min_rr']

        # --- REJECTION LOGGING (only close calls) ---
        rejection_reasons = []
        if price_break and vol_confirm:
            if not trend_ok:
                rejection_reasons.append(
                    "Below VWAP or not rising" if cfg['trend_type'] == 'VWAP'
                    else f"Below {cfg['trend_period']} {cfg['trend_type']}"
                )
            if not rs_ok and mode_name != 'scalping':
                rejection_reasons.append(f"Weaker RS ({stock_perf:.1%} vs {spy_perf:.1%})")
            if not vwap_ok:
                rejection_reasons.append("VWAP position poor")
            if not was_consolidating and mode_name != 'scalping':
                rejection_reasons.append("No consolidation")
            if not liquid_ok:
                rejection_reasons.append("Low liquidity")
            if not dist_confirm:
                rejection_reasons.append(f"Weak distance ({dist_atr:.2f} ATR)")
            if not candle_ok:
                rejection_reasons.append("Poor candle structure")
            if not rr_ok:
                rejection_reasons.append(f"Low R:R ({rr:.2f})")
            if vol_divergence:
                rejection_reasons.append("Volume divergence")

            # Only log if it was close (2 or fewer issues)
            if len(rejection_reasons) <= 2:
                self.rejection_reasons.append({
                    'symbol': symbol,
                    'price': round(latest['close'], 2),
                    'vol_ratio': round(latest['Vol_Ratio'], 2),
                    'mode': mode_name,
                    'timeframe': timeframe,
                    'reasons': ', '.join(rejection_reasons),
                })

        # --- FINAL CONDITIONS ---
        conditions = [
            price_break, vol_confirm, dist_confirm, trend_ok,
            vwap_ok, liquid_ok, candle_ok, rr_ok, not vol_divergence
        ]
        if mode_name != 'scalping':
            conditions.extend([rs_ok, was_consolidating])

        if not all(conditions):
            return None

        # --- QUALITY SCORING ---
        quality = 'HIGH'
        if mode_name == 'scalping':
            if len(df['vwap']) >= 5 and not pd.isna(df['vwap'].iloc[-5]):
                vwap_momentum = (latest['vwap'] - df['vwap'].iloc[-5]) / max(latest['ATR'], 1e-6)
                if latest['Vol_Ratio'] > 3.0 and vwap_momentum > 0.5:
                    quality = 'PREMIUM'
        elif has_gap_up:
            quality = 'PREMIUM'

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

    def evaluate_exit(self, df, symbol, mode_name, entry_price, stop_price,
                     target_price, timeframe, regime="NORMAL"):
        """
        Evaluate exit conditions for existing LONG position.
        Returns dict with Action: HOLD | TRAIL | EXIT_PARTIAL | EXIT_FULL
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

        df = self.calculate_indicators(df.copy(), cfg['trend_type'], cfg.get('trend_period'), timeframe)
        latest = df.iloc[-1]

        price = float(latest['close'])
        trend_line = float(latest['Trend_Line'])
        atr = float(latest['ATR'])
        vwap_val = latest.get('vwap', np.nan)

        unrealized_r = (price - entry_price) / max(entry_price - stop_price, 1e-6)

        # Priority-ordered exit checks
        exit_signals = []

        # 1. CRITICAL: Hard stop hit
        if price <= stop_price:
            exit_signals.append(('EXIT_FULL', f'Stop hit: {price:.2f} <= {stop_price:.2f}', 100))

        # 2. CRITICAL: Trend broken
        trend_broken = price < trend_line
        vwap_broken = (not pd.isna(vwap_val)) and price < vwap_val and ('min' in timeframe or 'hour' in timeframe)
        
        if trend_broken and (vwap_broken or mode_name == 'swing'):
            exit_signals.append(('EXIT_FULL', f'Trend broken: {price:.2f} < {trend_line:.2f}', 90))

        # 3. For swing: SMA150 support lost
        if mode_name == 'swing' and len(df) >= 150:
            sma150 = df['close'].rolling(150).mean().iloc[-1]
            if not np.isnan(sma150) and price < sma150 - 0.5 * atr:
                exit_signals.append(('EXIT_FULL', f'Lost SMA150: {price:.2f} < {sma150:.2f}', 85))

        # 4. Reversal candle near target
        high = latest['high']
        low = latest['low']
        close = latest['close']
        open_price = latest['open']
        
        rng = max(high - low, 1e-6)
        body_top = max(open_price, close)
        upper_wick = high - body_top
        
        near_target = price >= target_price * 0.97
        strong_reversal = (
            near_target and
            upper_wick > 0.5 * atr and
            close < body_top - 0.5 * rng
        )
        
        if strong_reversal and unrealized_r >= 1.0:
            exit_signals.append(('EXIT_PARTIAL', f'Reversal candle near target (R={unrealized_r:.2f})', 70))

        # 5. Volume divergence
        if len(df) >= 10:
            recent_vol = df['volume'].tail(5).mean()
            prev_vol = df['volume'].iloc[-10:-5].mean()
            vol_divergence = price > entry_price * 1.05 and recent_vol < prev_vol * 0.7
            
            if vol_divergence and unrealized_r > 1.0:
                exit_signals.append(('EXIT_PARTIAL', 'Volume divergence', 60))

        # 6. Choppy regime + no progress
        lookback = cfg['lookback']
        if len(df) >= lookback:
            recent_low = df['close'].iloc[-lookback:].min()
            recent_high = df['close'].iloc[-lookback:].max()
            in_middle_range = recent_low < price < recent_high and (recent_high - recent_low) < 1.5 * atr
            
            if regime == 'CHOPPY' and in_middle_range and unrealized_r < 0.5:
                exit_signals.append(('EXIT_FULL', 'Choppy regime, no progress', 50))

        # 7. Trail stop suggestion
        trail_lookback = 5 if mode_name != 'swing' else 10
        if len(df) >= trail_lookback:
            swing_low = df['low'].iloc[-trail_lookback:].min()
            proposed_trail_stop = swing_low - 0.5 * atr
            
            if proposed_trail_stop > stop_price and unrealized_r >= 1.0:
                exit_signals.append(
                    ('TRAIL', f'Trail to {proposed_trail_stop:.2f} (swing low - 0.5 ATR)', 40)
                )

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

    async def scan_symbol_with_retry(self, symbol, mode, tf, vol, atr, spy_perf,
                                    regime="NORMAL", max_retries=3):
        """Scan with automatic retry on failure"""
        for attempt in range(max_retries):
            try:
                contract = Stock(symbol, 'SMART', 'USD')
                await self.ib.qualifyContractsAsync(contract)

                # Check spread for scalping
                spread_pct = None
                if mode == 'scalping':
                    spread_pct = await self.get_bid_ask_spread(contract)
                    max_spread = MODES['scalping']['max_spread_pct']
                    if spread_pct is None or spread_pct > max_spread:
                        return None

                # Adjust duration based on timeframe
                if 'day' in tf:
                    duration = '365 D'
                elif 'min' in tf and '1 min' in tf:
                    duration = '2 D'
                else:
                    duration = '10 D'

                bars = await self.ib.reqHistoricalDataAsync(
                    contract, '', duration, tf, 'TRADES', True, 1
                )
                if not bars:
                    return None

                df = util.df(bars).set_index('date')

                return self.detect_breakout(
                    df, symbol, mode, tf, spy_perf,
                    vol_thresh=vol, atr_mult=atr, spread_pct=spread_pct, regime=regime
                )

            except Exception as e:
                if attempt == max_retries - 1:
                    logger.debug(f"Failed {symbol}: {e}")
                    return None
                await asyncio.sleep(1)

        return None


def get_watchlist_from_file(file_path):
    """Load watchlist from file"""
    watchlist = []
    try:
        with open(file_path, 'r') as f:
            for line in f.read().splitlines():
                line = line.strip()
                if not line or line.startswith('###'):
                    continue
                for s in line.split(','):
                    s = s.strip()
                    if s and not s.startswith('###'):
                        clean = s.split(':')[-1]
                        if clean == 'BRK.B':
                            clean = 'BRK B'
                        if not (clean.startswith('XL') and len(clean) <= 4):
                            watchlist.append(clean)
        return list(set(watchlist))
    except Exception as e:
        logger.error(f"Failed to load watchlist: {e}")
        return []


def get_positions_from_file(file_path):
    """Load positions from CSV"""
    import csv
    positions = []
    try:
        with open(file_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    positions.append({
                        'symbol': row['symbol'].strip(),
                        'mode': row['mode'].strip(),
                        'entry': float(row['entry']),
                        'stop': float(row['stop']),
                        'target': float(row['target']),
                        'timeframe': row['timeframe'].strip(),
                    })
                except Exception as e:
                    logger.warning(f"Skip bad row: {row} ({e})")
    except Exception as e:
        logger.error(f"Failed to load positions: {e}")
    return positions

async def main(file_name, mode, vol, atr, tf, live=False, exit_file=None):
    """Main scanner execution"""
    port = 7496 if live else 7497
    
    # Scalping + live warning
    if mode == 'scalping' and live:
        logger.warning("⚠️  SCALPING ON LIVE - High frequency risks!")
        response = input("Type 'YES' to continue: ")
        if response != 'YES':
            logger.info("Aborted")
            return
    
    # Delayed data warning
    if mode == 'scalping' and not live:
        logger.warning("⚠️  PAPER MODE = DELAYED DATA (15min lag)")
        logger.warning("    Not suitable for real scalping!")
    
    ib = IB()
    try:
        await ib.connectAsync('127.0.0.1', port, clientId=1)
        logger.info(f"✓ Connected to IB ({'LIVE' if live else 'PAPER'})")
        
        # Set market data type
        if live:
            ib.reqMarketDataType(1)  # Real-time (requires subscription)
        else:
            ib.reqMarketDataType(3)  # Delayed data for paper
        
        scanner = BreakoutScanner(ib)
        
        # --- EXIT EVALUATION MODE ---
        if exit_file:
            positions = get_positions_from_file(exit_file)
            logger.info(f"Loaded {len(positions)} positions from {exit_file}")
            
            if not positions:
                logger.warning("No positions to evaluate")
                return
            
            # Determine regime
            sample_mode = positions[0]['mode']
            if sample_mode != 'scalping':
                spy_perf, spy_vol = await scanner.get_spy_performance(
                    tf, MODES[sample_mode]['lookback']
                )
                if abs(spy_perf) < 0.01 and spy_vol < 1.0:
                    regime = "CHOPPY"
                elif abs(spy_perf) > 0.05 and spy_vol > 2.0:
                    regime = "EXPANSION"
                else:
                    regime = "NORMAL"
                logger.info(f"Market regime: {regime} (SPY: {spy_perf:.2%}, Vol: {spy_vol:.2f}%)")
            else:
                regime = "INTRADAY"
            
            exit_results = []
            for pos in positions:
                sym = pos['symbol']
                pos_mode = pos['mode']
                pos_tf = pos['timeframe']
                
                contract = Stock(sym, 'SMART', 'USD')
                await ib.qualifyContractsAsync(contract)
                
                # Get historical data
                if 'day' in pos_tf:
                    duration = '365 D'
                elif 'min' in pos_tf and '1 min' in pos_tf:
                    duration = '2 D'
                else:
                    duration = '10 D'
                
                bars = await ib.reqHistoricalDataAsync(
                    contract, '', duration, pos_tf, 'TRADES', True, 1
                )
                if not bars:
                    logger.warning(f"No data for {sym}")
                    continue
                
                df_pos = util.df(bars).set_index('date')
                
                decision = scanner.evaluate_exit(
                    df=df_pos,
                    symbol=sym,
                    mode_name=pos_mode,
                    entry_price=pos['entry'],
                    stop_price=pos['stop'],
                    target_price=pos['target'],
                    timeframe=pos_tf,
                    regime=regime
                )
                exit_results.append(decision)
            
            # Display and save exit decisions
            if exit_results:
                logger.info(f"\n{'='*70}")
                logger.info(f" EXIT EVALUATION RESULTS: {len(exit_results)}")
                logger.info(f"{'='*70}\n")
                
                df_exit = pd.DataFrame(exit_results)
                print(df_exit.to_string(index=False))
                
                # Save to CSV
                exit_output = f"exit_decisions_{datetime.now():%Y%m%d_%H%M%S}.csv"
                df_exit.to_csv(exit_output, index=False)
                logger.info(f"\n✓ Exit decisions saved to: {exit_output}")
            else:
                logger.info("No exit decisions generated")
            
            return
        
        # --- SCAN MODE ---
        watchlist = get_watchlist_from_file(file_name)
        logger.info(f"Loaded {len(watchlist)} symbols from {file_name}")
        
        # Get market context
        if mode != 'scalping':
            spy_perf, spy_vol = await scanner.get_spy_performance(
                tf, MODES[mode]['lookback']
            )
            
            # Regime classification
            if abs(spy_perf) < 0.01 and spy_vol < 1.0:
                regime = "CHOPPY"
            elif abs(spy_perf) > 0.05 and spy_vol > 2.0:
                regime = "EXPANSION"
            else:
                regime = "NORMAL"
            
            regime_desc = REGIME_CONFIG[regime]['description']
            logger.info(
                f"--- Mode: {mode.upper()} | TF: {tf} | "
                f"SPY: {spy_perf:.2%} | Vol: {spy_vol:.2f}% ---"
            )
            logger.info(f"--- Regime: {regime} ({regime_desc}) ---")
        else:
            spy_perf, spy_vol, regime = 0.0, 0.0, "INTRADAY"
            logger.info(f"--- Mode: SCALPING | TF: {tf} | VWAP-based ---")
        
        # Scan with semaphore for rate limiting
        results = []
        semaphore = asyncio.Semaphore(5)
        
        async def _scan_one(idx_sym):
            idx, sym = idx_sym
            async with semaphore:
                print(f"[{idx}/{len(watchlist)}] {sym:6}", end="\r")
                res = await scanner.scan_symbol_with_retry(
                    sym, mode, tf, vol, atr, spy_perf, regime=regime
                )
                return res
        
        tasks = [_scan_one((i, sym)) for i, sym in enumerate(watchlist, 1)]
        results_raw = await asyncio.gather(*tasks)
        results = [r for r in results_raw if r]
        print()  # Clear progress line
        
        # Display results
        if results:
            logger.info(f"\n{'='*70}")
            logger.info(f" {mode.upper()} SIGNALS FOUND: {len(results)}")
            logger.info(f"{'='*70}\n")
            
            df_final = pd.DataFrame(results).sort_values(by='Vol', ascending=False)
            print(df_final.to_string(index=False))
            
            # Save to CSV
            output_file = f"signals_{mode}_{datetime.now():%Y%m%d_%H%M%S}.csv"
            df_final.to_csv(output_file, index=False)
            logger.info(f"\n✓ Signals saved to: {output_file}")
            
            # Scalping warnings
            if mode == 'scalping':
                logger.warning("\n⚠️  SCALPING REMINDERS:")
                logger.warning("   • Exit at target/stop - no exceptions")
                logger.warning("   • Monitor spread widening during execution")
                logger.warning("   • Close all positions before market close")
                logger.warning("   • Watch for news events")
        else:
            logger.info("No signals found")
        
        # Save rejection analysis (only close calls)
        if scanner.rejection_reasons:
            df_reject = pd.DataFrame(scanner.rejection_reasons)
            reject_file = f"rejections_{mode}_{datetime.now():%Y%m%d_%H%M%S}.csv"
            df_reject.to_csv(reject_file, index=False)
            logger.info(f"✓ Rejections saved to: {reject_file}")
            logger.info(f"   (Showing only signals that were close to passing)")
        
    except Exception as e:
        logger.error(f"Scanner error: {e}", exc_info=True)
    finally:
        ib.disconnect()
        logger.info("✓ Disconnected from IB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Breakout Scanner for Interactive Brokers',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Swing trading scan
  python scanner.py watchlist.txt --mode swing
  
  # Daytrade scan with custom volume threshold
  python scanner.py watchlist.txt --mode daytrade --vol 1.5
  
  # Scalping (1min bars)
  python scanner.py watchlist.txt --mode scalping
  
  # Exit evaluation for existing positions
  python scanner.py watchlist.txt --mode swing --exit-file positions.csv
  
  # Live trading (CAREFUL!)
  python scanner.py watchlist.txt --mode swing --live
        """
    )
    parser.add_argument('file', help='Path to watchlist file')
    parser.add_argument('--mode', choices=['swing', 'daytrade', 'scalping'],
                       default='swing', help='Trading mode')
    parser.add_argument('--vol', type=float, help='Volume threshold override')
    parser.add_argument('--atr', type=float, help='ATR multiplier override')
    parser.add_argument('--tf', type=str, help='Timeframe override')
    parser.add_argument('--live', action='store_true',
                       help='Use live account (default: paper)')
    parser.add_argument('--exit-file', type=str,
                       help='CSV file with positions for exit evaluation')
    
    args = parser.parse_args()
    
    # Default timeframes
    if args.tf:
        timeframe = args.tf
    else:
        timeframe = {
            'swing': '1 day',
            'daytrade': '15 mins',
            'scalping': '1 min'
        }[args.mode]
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            main(args.file, args.mode, args.vol, args.atr, timeframe,
                 args.live, args.exit_file)
        )
    finally:
        loop.close()
