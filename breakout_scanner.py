import pandas as pd
import numpy as np
from datetime import datetime, time as dtime
import asyncio
import warnings
import nest_asyncio
import argparse
import logging
from pathlib import Path
from ib_insync import IB, Stock, util

from dataclasses import dataclass
from typing import Optional, Dict, Any, List

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
MODES = {
    'swing': {
        'lookback': 20,
        'vol_thresh': 1.3,
        'atr_mult': 0.5,
        'trend_type': 'SMA',
        'trend_period': 200,
        'sl_mult': 2.0,
        'tp_mult': 4.0,
        'min_consolidation_bars': 3
    },
    'daytrade': {
        'lookback': 10,
        'vol_thresh': 1.1,
        'atr_mult': 0.2,
        'trend_type': 'EMA',
        'trend_period': 9,
        'sl_mult': 1.5,
        'tp_mult': 3.0,
        'min_consolidation_bars': 2
    },
    'scalping': {
        'lookback': 5,
        'vol_thresh': 2.0,  # Need significant volume spike
        'atr_mult': 0.15,   # Tighter stops
        'trend_type': 'VWAP',  # VWAP is the trend
        'trend_period': None,  # Not used
        'sl_mult': 0.5,     # Very tight stop
        'tp_mult': 1.0,     # Quick profit target
        'min_consolidation_bars': 1,
        'max_spread_pct': 0.1,  # Max 0.1% spread
        'min_price': 5.0,   # Avoid penny stocks
        'max_price': 500.0  # Avoid super high price (slippage)
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
        return {
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
            **({'Spread%': round(self.spread_pct, 2)} if self.spread_pct is not None else {})
        }

class BreakoutScanner:
    def __init__(self, ib_connection):
        self.ib = ib_connection
        self.spy_cache = {}
        self.rejection_reasons = []
    def _check_trend(self, latest, df, cfg, mode_name: str) -> bool:
        if cfg['trend_type'] == 'VWAP':
            # For scalping: price must be above VWAP and VWAP rising
            if pd.isna(latest.get('vwap', np.nan)) or pd.isna(df['vwap'].iloc[-2]):
                return False
            return latest['close'] > latest['vwap'] and latest['vwap'] > df['vwap'].iloc[-2]
        return latest['close'] > latest['Trend_Line']

    def _check_vwap_position(self, latest, mode_name: str) -> bool:
        vwap_val = latest.get('vwap', np.nan)
        if pd.isna(vwap_val):
            return True
        if mode_name == 'scalping':
            return latest['close'] > vwap_val and latest['close'] > latest['open']
        if mode_name == 'daytrade':
            return latest['close'] > vwap_val
        return True

    def _check_consolidation(self, df, cfg, mode_name: str) -> bool:
        if mode_name == 'scalping':
            return True
        min_cons_bars = cfg['min_consolidation_bars']
        window = df['Is_Consolidating'].iloc[-min_cons_bars - 1:-1]
        return bool(window.any())

    def _compute_rr_levels(self, latest, cfg):
        atr = latest['ATR']
        price = latest['close']
        sl = price - (cfg['sl_mult'] * atr)
        tp = price + (cfg['tp_mult'] * atr)
        rr = (tp - price) / max(price - sl, 1e-6)
        return sl, tp, rr
        
    def detect_breakout(self, df, symbol, mode_name, timeframe, spy_perf, **kwargs):
        """Main breakout detection with comprehensive filters + RR + candle structure."""
        cfg = MODES[mode_name]
        lookback = kwargs.get('lookback') or cfg['lookback']
        vol_thresh = kwargs.get('vol_thresh') or cfg['vol_thresh']
        atr_mult = kwargs.get('atr_mult') or cfg['atr_mult']
        spread_pct = kwargs.get('spread_pct')

        # For scalping, check minimum data requirements
        min_bars = 100 if mode_name == 'scalping' else cfg.get('trend_period', 50)
        if len(df) < min_bars:
            return None

        # Calculate indicators (work on a copy to avoid side effects)
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

        # Candle structure filter: close near high, limited upper wick
        high = latest['high']
        low = latest['low']
        close = latest['close']
        rng = max(high - low, 1e-6)
        upper_wick = high - max(open(latest['open'], close), close)
        body_top_pct = (high - close) / rng  # distance from high

        # Tighter for swing, looser for scalping
        if mode_name == 'swing':
            max_wick_atr = 0.5
            max_body_top_pct = 0.3
        elif mode_name == 'daytrade':
            max_wick_atr = 0.75
            max_body_top_pct = 0.4
        else:  # scalping
            max_wick_atr = 1.0
            max_body_top_pct = 0.5

        candle_ok = (
            (upper_wick / latest['ATR']) <= max_wick_atr and
            body_top_pct <= max_body_top_pct
        )

        # --- FILTERS ---
        # 1. Relative Strength (skip for scalping - too slow)
        if mode_name != 'scalping':
            stock_perf = (latest['close'] / df['close'].iloc[-lookback]) - 1
            rs_ok = stock_perf > spy_perf
        else:
            stock_perf = 0.0
            rs_ok = True  # Not relevant for 1min scalps

        # 2. Trend Filter
        if cfg['trend_type'] == 'VWAP':
            # For scalping: price must be above VWAP AND VWAP rising
            trend_ok = (
                not pd.isna(latest['vwap']) and
                latest['close'] > latest['vwap'] and
                latest['vwap'] > df['vwap'].iloc[-2]
            )
        else:
            trend_ok = latest['close'] > latest['Trend_Line']

        # 3. VWAP position (critical for scalping and daytrade)
        vwap_ok = True
        if not pd.isna(latest['vwap']):
            if mode_name == 'scalping':
                # Must be above VWAP with momentum
                vwap_ok = latest['close'] > latest['vwap'] and latest['close'] > latest['open']
            elif mode_name == 'daytrade':
                vwap_ok = latest['close'] > latest['vwap']

        # 4. Consolidation before breakout (looser for scalping)
        min_cons_bars = cfg['min_consolidation_bars']
        was_consolidating = True
        if mode_name != 'scalping':
            was_consolidating = df['Is_Consolidating'].iloc[-min_cons_bars-1:-1].any()

        # 5. Liquidity
        liquid_ok = self.check_liquidity(df)

        # 6. Gap detection (not relevant for scalping)
        gap_percent = self.calculate_gap(df) if mode_name != 'scalping' else 0
        has_gap_up = gap_percent > 2.0

        # 7. Volume spike for scalping
        if mode_name == 'scalping':
            # Need immediate volume explosion
            recent_vol_spike = latest['Vol_Ratio'] > vol_thresh * 1.5
            vol_confirm = vol_confirm and recent_vol_spike

        # --- RISK / REWARD CALC ---
        sl = latest['close'] - (cfg['sl_mult'] * latest['ATR'])
        tp = latest['close'] + (cfg['tp_mult'] * latest['ATR'])

        # For scalping, account for spread in effective entry/stop
        if mode_name == 'scalping' and spread_pct is not None:
            # approximate spread in price terms
            spread_price = latest['close'] * (spread_pct / 100.0)
            entry_eff = latest['close'] + spread_price * 0.5
            sl_eff = sl - spread_price * 0.5
            rr = (tp - entry_eff) / max(entry_eff - sl_eff, 1e-6)
        else:
            rr = (tp - latest['close']) / max(latest['close'] - sl, 1e-6)

        # Per‑mode minimum R:R
        min_rr = {'swing': 2.0, 'daytrade': 1.5, 'scalping': 1.0}.get(mode_name, 1.0)
        rr_ok = rr >= min_rr

        # --- LOGGING REJECTIONS ---
        rejection_reasons = []
        if price_break and vol_confirm:
            if not trend_ok:
                if cfg['trend_type'] == 'VWAP':
                    rejection_reasons.append("Below VWAP or VWAP not rising")
                else:
                    rejection_reasons.append(f"Below {cfg['trend_period']} {cfg['trend_type']}")
            if not rs_ok and mode_name != 'scalping':
                rejection_reasons.append(f"Weaker than SPY ({stock_perf:.1%} vs {spy_perf:.1%})")
            if not vwap_ok:
                rejection_reasons.append("VWAP position poor")
            if not was_consolidating and mode_name != 'scalping':
                rejection_reasons.append("No consolidation")
            if not liquid_ok:
                rejection_reasons.append("Low liquidity")
            if not dist_confirm:
                rejection_reasons.append(f"Weak distance ({dist_atr:.2f} ATR)")
            if not candle_ok:
                rejection_reasons.append("Candle structure poor")
            if not rr_ok:
                rejection_reasons.append(f"R:R too low ({rr:.2f})")

        if rejection_reasons:
            self.rejection_reasons.append({
                'symbol': symbol,
                'price': latest['close'],
                'vol_ratio': latest['Vol_Ratio'],
                'mode': mode_name,
                'timeframe': timeframe,
                'reasons': ', '.join(rejection_reasons),
            })

        # --- FINAL CONDITIONS ---
        conditions = [
            price_break,
            vol_confirm,
            dist_confirm,
            trend_ok,
            vwap_ok,
            liquid_ok,
            candle_ok,
            rr_ok,
        ]
        if mode_name != 'scalping':
            conditions.extend([rs_ok, was_consolidating])

        if not all(conditions):
            return None

        # Scalping quality check
        quality = 'HIGH'
        if mode_name == 'scalping':
            if 'vwap' in df.columns and len(df['vwap']) >= 5 and not pd.isna(df['vwap'].iloc[-5]):
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

        if mode_name == 'scalping':
            signal['Spread%'] = spread_pct if spread_pct is not None else 0

        logger.info(
            f"🚀 SIGNAL: {symbol} {mode_name} at ${latest['close']:.2f} | "
            f"SL: ${sl:.2f} | TP: ${tp:.2f} | R:R={rr:.2f}"
        )

        return signal

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
            # For scalping, VWAP IS the trend
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

    def calculate_gap(self, df):
        """Calculates the percentage difference between today's open and yesterday's close"""
        if len(df) < 2:
            return 0
        prev_close = df['close'].iloc[-2]
        current_open = df['open'].iloc[-1]
        gap_pct = ((current_open - prev_close) / prev_close) * 100
        return round(gap_pct, 2)
    
    def calculate_vwap(self, df, timeframe):
        """Calculate VWAP properly - resets daily"""
        df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
        
        # Reset VWAP daily for intraday bars
        if hasattr(df.index, 'date'):
            df['date_only'] = df.index.date
            vwap = df.groupby('date_only').apply(
                lambda x: (x['typical_price'] * x['volume']).cumsum() / x['volume'].cumsum()
            ).reset_index(level=0, drop=True)
        else:
            # Single day or continuous - just cumulative
            vwap = (df['typical_price'] * df['volume']).cumsum() / df['volume'].cumsum()
        
        return vwap

    async def get_spy_performance(self, timeframe, lookback):
        """Get SPY performance with caching"""
        cache_key = f"{timeframe}_{lookback}"
        
        if cache_key in self.spy_cache:
            return self.spy_cache[cache_key]
        
        try:
            spy = Stock('SPY', 'ARCA', 'USD')
            duration = '300 D' if 'day' in timeframe else '10 D'
            bars = await self.ib.reqHistoricalDataAsync(spy, '', duration, timeframe, 'TRADES', True, 1)
            
            if not bars:
                return 0
            
            df = util.df(bars)
            perf = (df['close'].iloc[-1] / df['close'].iloc[-lookback]) - 1
            self.spy_cache[cache_key] = perf
            return perf
            
        except Exception as e:
            logger.warning(f"Failed to get SPY performance: {e}")
            return 0

    async def get_bid_ask_spread(self, contract):
        """Get current bid-ask spread for scalping liquidity check"""
        try:
            ticker = self.ib.reqMktData(contract, '', False, False)
            await asyncio.sleep(0.5)  # Wait for data
            
            if ticker.bid and ticker.ask and ticker.bid > 0:
                spread_pct = ((ticker.ask - ticker.bid) / ticker.bid) * 100
                self.ib.cancelMktData(contract)
                return spread_pct
            
            self.ib.cancelMktData(contract)
            return None
        except:
            return None


    def check_liquidity(self, df, min_dollar_volume=5_000_000):
        """Check if stock has sufficient liquidity"""
        avg_dollar_volume = (df['close'] * df['volume']).tail(20).mean()
        return avg_dollar_volume >= min_dollar_volume

    def detect_breakout(self, df, symbol, mode_name, timeframe, spy_perf, **kwargs):
        """Main breakout detection with comprehensive filters"""
        cfg = MODES[mode_name]
        lookback = kwargs.get('lookback') or cfg['lookback']
        vol_thresh = kwargs.get('vol_thresh') or cfg['vol_thresh']
        atr_mult = kwargs.get('atr_mult') or cfg['atr_mult']
        
        # For scalping, check minimum data requirements
        min_bars = 100 if mode_name == 'scalping' else cfg.get('trend_period', 50)
        if len(df) < min_bars:
            return None
        
        # Calculate indicators
        df = self.calculate_indicators(df, cfg['trend_type'], cfg.get('trend_period'), timeframe)
        
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
        
        # --- FILTERS ---
        # 1. Relative Strength (skip for scalping - too slow)
        if mode_name != 'scalping':
            stock_perf = (latest['close'] / df['close'].iloc[-lookback]) - 1
            rs_ok = stock_perf > spy_perf
        else:
            rs_ok = True  # Not relevant for 1min scalps
        
        # 2. Trend Filter
        if cfg['trend_type'] == 'VWAP':
            # For scalping: price must be above VWAP AND breaking up
            trend_ok = latest['close'] > latest['vwap'] and latest['vwap'] > df['vwap'].iloc[-2]
            vwap_rising = True
        else:
            trend_ok = latest['close'] > latest['Trend_Line']
            vwap_rising = True
        
        # 3. VWAP position (critical for scalping and daytrade)
        vwap_ok = True
        if not pd.isna(latest['vwap']):
            if mode_name == 'scalping':
                # Must be above VWAP with momentum
                vwap_ok = latest['close'] > latest['vwap'] and latest['close'] > latest['open']
            elif mode_name == 'daytrade':
                vwap_ok = latest['close'] > latest['vwap']
        
        # 4. Consolidation before breakout (looser for scalping)
        min_cons_bars = cfg['min_consolidation_bars']
        was_consolidating = df['Is_Consolidating'].iloc[-min_cons_bars-1:-1].any()
        
        # 5. Liquidity
        liquid_ok = self.check_liquidity(df)
        
        # 6. Gap detection (not relevant for scalping)
        gap_percent = self.calculate_gap(df) if mode_name != 'scalping' else 0
        has_gap_up = gap_percent > 2.0
        
        # 7. Volume spike for scalping
        if mode_name == 'scalping':
            # Need immediate volume explosion
            recent_vol_spike = latest['Vol_Ratio'] > vol_thresh * 1.5
            vol_confirm = vol_confirm and recent_vol_spike
        
        # --- LOGGING REJECTIONS ---
        rejection_reasons = []
        
        if price_break and vol_confirm:
            if not trend_ok:
                if cfg['trend_type'] == 'VWAP':
                    rejection_reasons.append("Below VWAP or VWAP not rising")
                else:
                    rejection_reasons.append(f"Below {cfg['trend_period']} {cfg['trend_type']}")
            if not rs_ok:
                rejection_reasons.append(f"Weaker than SPY ({stock_perf:.1%} vs {spy_perf:.1%})")
            if not vwap_ok:
                rejection_reasons.append("VWAP position poor")
            if not was_consolidating and mode_name != 'scalping':
                rejection_reasons.append("No consolidation")
            if not liquid_ok:
                rejection_reasons.append("Low liquidity")
            if not dist_confirm:
                rejection_reasons.append(f"Weak distance ({dist_atr:.2f} ATR)")
            
            if rejection_reasons:
                self.rejection_reasons.append({
                    'symbol': symbol,
                    'price': latest['close'],
                    'vol_ratio': latest['Vol_Ratio'],
                    'reasons': ', '.join(rejection_reasons)
                })
        
        # --- SIGNAL GENERATION ---
        conditions = [price_break, vol_confirm, dist_confirm, trend_ok, vwap_ok, liquid_ok]
        
        # Add optional filters based on mode
        if mode_name != 'scalping':
            conditions.extend([rs_ok, was_consolidating])
        
        if all(conditions):
            sl = latest['close'] - (cfg['sl_mult'] * latest['ATR'])
            tp = latest['close'] + (cfg['tp_mult'] * latest['ATR'])
            risk_reward = (tp - latest['close']) / (latest['close'] - sl)
            
            # Scalping quality check
            quality = 'HIGH'
            if mode_name == 'scalping':
                # Premium scalp: huge volume + VWAP rising fast
                vwap_momentum = (latest['vwap'] - df['vwap'].iloc[-5]) / latest['ATR']
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
                'R:R': round(risk_reward, 2),
                'Gap%': round(gap_percent, 2) if abs(gap_percent) > 0.5 else 0,
                'Mode': mode_name,
                'Quality': quality
            }
            
            if mode_name == 'scalping':
                signal['Spread%'] = kwargs.get('spread_pct', 0)
            
            logger.info(f"🚀 SIGNAL: {symbol} at ${latest['close']:.2f} | SL: ${sl:.2f} | TP: ${tp:.2f}")
            return signal
        
        return None

    async def scan_symbol_with_retry(self, symbol, mode, tf, vol, atr, spy_perf, max_retries=3):
        """Scan with automatic retry on failure"""
        for attempt in range(max_retries):
            try:
                contract = Stock(symbol, 'SMART', 'USD')
                await self.ib.qualifyContractsAsync(contract)
                
                # Check spread for scalping
                spread_pct = None
                if mode == 'scalping':
                    if spread_pct is None or spread_pct > MODES['scalping']['max_spread_pct']:
                        msg_spread = "None" if spread_pct is None else f"{spread_pct:.2f}%"
                        logger.debug(f"{symbol}: Spread too wide ({msg_spread})")
                        return None


                # Adjust duration based on timeframe
                if 'day' in tf:
                    duration = '365 D'
                elif 'min' in tf and '1 min' in tf:
                    duration = '2 D'  # For 1min bars, get 2 days
                else:
                    duration = '10 D'
                
                bars = await self.ib.reqHistoricalDataAsync(contract, '', duration, tf, 'TRADES', True, 1)
                
                if not bars:
                    return None
                
                df = util.df(bars).set_index('date')
                
                # Pass spread info to detection
                return self.detect_breakout(
                    df, symbol, mode, tf, spy_perf, 
                    vol_thresh=vol, atr_mult=atr, spread_pct=spread_pct
                )
                
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.warning(f"Failed {symbol} after {max_retries} attempts: {e}")
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
    
async def main(file_name, mode, vol, atr, tf, live=False):
    """Main scanner execution"""
    port = 7496 if live else 7497
    logger.info(f"Connecting to IB on port {port} ({'LIVE' if live else 'PAPER'})")
    
    # Scalping warning
    if mode == 'scalping' and live:
        logger.warning("⚠️  SCALPING ON LIVE ACCOUNT - High frequency trading risks apply!")
        response = input("Type 'YES' to continue with live scalping: ")
        if response != 'YES':
            logger.info("Aborted by user")
            return
    
    ib = IB()
    try:
        await ib.connectAsync('127.0.0.1', port, clientId=1)
        logger.info("✓ Connected to Interactive Brokers")
        # --- FIX FOR ERROR 10089 ---
        # This allows the script to use delayed data if live data is not subscribed
        ib.reqMarketDataType(3) 
        logger.info(f"✓ Connected to {'Live' if live else 'Paper'} account. Using Market Data Type: 3")
        # ---------------------------
        
        watchlist = get_watchlist_from_file(file_name)
        logger.info(f"Loaded {len(watchlist)} symbols from {file_name}")
        
        scanner = BreakoutScanner(ib)

        # Get market context (skip for scalping - too slow)
        if mode != 'scalping':
            spy_perf = await scanner.get_spy_performance(tf, MODES[mode]['lookback'])
            market_regime = "BULLISH" if spy_perf > 0.05 else "BEARISH" if spy_perf < -0.05 else "NEUTRAL"
            logger.info(f"--- Mode: {mode.upper()} | TF: {tf} | SPY: {spy_perf:.2%} | Regime: {market_regime} ---")
        else:
            spy_perf = 0
            logger.info(f"--- Mode: SCALPING | TF: {tf} | VWAP-based entries ---")

        results = []
        semaphore = asyncio.Semaphore(5)  # tune for IB pacing

        async def _scan_one(idx_sym):
            idx, sym = idx_sym
            async with semaphore:
                print(f"[{idx}/{len(watchlist)}] {sym:6}", end="\r")
                res = await scanner.scan_symbol_with_retry(sym, mode, tf, vol, atr, spy_perf)
                return res

        tasks = [_scan_one((i, sym)) for i, sym in enumerate(watchlist, 1)]
        results_raw = await asyncio.gather(*tasks)
        results = [r for r in results_raw if r]
        print()

        
        # Display results
        if results:
            logger.info(f"\n{'='*70}")
            logger.info(f" {mode.upper()} SIGNALS FOUND: {len(results)}")
            logger.info(f"{'='*70}")
            
            df_final = pd.DataFrame(results).sort_values(by='Vol', ascending=False)
            print(df_final.to_string(index=False))
            
            # Save to CSV
            output_file = f"signals_{mode}_{datetime.now():%Y%m%d_%H%M%S}.csv"
            df_final.to_csv(output_file, index=False)
            logger.info(f"\n✓ Signals saved to: {output_file}")
            
            # Scalping-specific warnings
            if mode == 'scalping':
                logger.warning("\n⚠️  SCALPING REMINDERS:")
                logger.warning("   • Exit at target or stop - no exceptions")
                logger.warning("   • Monitor spread widening during execution")
                logger.warning("   • Close all positions before market close")
                logger.warning("   • Watch for news events that spike volatility")
        else:
            logger.info("No signals found.")
        
        # Save rejection analysis
        if scanner.rejection_reasons:
            df_reject = pd.DataFrame(scanner.rejection_reasons)
            reject_file = f"rejections_{mode}_{datetime.now():%Y%m%d_%H%M%S}.csv"
            df_reject.to_csv(reject_file, index=False)
            logger.info(f"✓ Rejections saved to: {reject_file}")
        
    except Exception as e:
        logger.error(f"Scanner error: {e}", exc_info=True)
    finally:
        ib.disconnect()
        logger.info("✓ Disconnected from IB")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Breakout Scanner for Interactive Brokers')
    parser.add_argument('file', help='Path to watchlist file')
    parser.add_argument('--mode', choices=['swing', 'daytrade', 'scalping'], default='swing')
    parser.add_argument('--vol', type=float, help='Volume threshold override')
    parser.add_argument('--atr', type=float, help='ATR multiplier override')
    parser.add_argument('--tf', type=str, help='Timeframe override')
    parser.add_argument('--live', action='store_true', help='Use live account (default: paper)')
    
    args = parser.parse_args()
    
    # Default timeframes per mode if not specified
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
        loop.run_until_complete(main(args.file, args.mode, args.vol, args.atr, timeframe, args.live))
    finally:
        loop.close()
