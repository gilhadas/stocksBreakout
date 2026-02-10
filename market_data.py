"""
Market data fetching and processing module
"""

import asyncio
import logging
from typing import Optional, Tuple
import pandas as pd
from ib_insync import IB, Stock, util

from config import DATA_DURATION
from indicators import calculate_atr

logger = logging.getLogger(__name__)


class MarketDataHandler:
    """Handles all market data requests to Interactive Brokers with optional yfinance fallback"""

    def __init__(self, ib_connection: IB, yf_fallback: bool = False):
        self.ib = ib_connection
        self.ib_available = ib_connection is not None
        self.yf_fallback = yf_fallback
        self.spy_cache = {}

        if yf_fallback:
            from yfinance_adapter import YFinanceAdapter
            self.yf_adapter = YFinanceAdapter()
    
    async def get_historical_data(self, symbol: str, timeframe: str,
                                  exchange: str = 'SMART',
                                  currency: str = 'USD') -> Optional[pd.DataFrame]:
        """
        Fetch historical data for a symbol.
        Falls back to yfinance if IB is unavailable and yf_fallback is enabled.
        """
        ib_timeframe = self._normalize_timeframe(timeframe)
        df = None

        # Try IB first (if available)
        if self.ib_available:
            try:
                contract = Stock(symbol, exchange, currency)
                await self.ib.qualifyContractsAsync(contract)

                # Determine duration based on timeframe
                if 'W' in ib_timeframe or 'M' in ib_timeframe or 'week' in timeframe.lower():
                    duration = DATA_DURATION['weekly']
                elif 'day' in ib_timeframe or 'day' in timeframe.lower():
                    duration = DATA_DURATION['daily']
                elif '1 min' in ib_timeframe:
                    duration = DATA_DURATION['scalping']
                else:
                    duration = DATA_DURATION['intraday']

                bars = await self.ib.reqHistoricalDataAsync(
                    contract, '', duration, ib_timeframe, 'TRADES', True, 1
                )

                if bars:
                    df = util.df(bars).set_index('date')

            except Exception as e:
                logger.debug(f"IB fetch failed for {symbol}: {e}")

        # Fallback to yfinance
        if df is None and self.yf_fallback:
            if self.ib_available:
                logger.info(f"  ↪ yfinance fallback for {symbol}")
            df = self.yf_adapter.get_historical_data(symbol, ib_timeframe)

        return df
    
    def _normalize_timeframe(self, timeframe: str) -> str:
        """
        Normalize timeframe to IB format
        
        Converts user-friendly formats to IB API format
        """
        # Already in IB format
        if any(x in timeframe for x in ['secs', 'mins', 'hour', 'day', '1W', '1M']):
            return timeframe
        
        # Convert common formats
        tf_map = {
            '1 week': '1W',
            '1 month': '1M',
            'weekly': '1W',
            'monthly': '1M',
            'daily': '1 day',
            '1 minute': '1 min',
            '5 minutes': '5 mins',
            '15 minutes': '15 mins',
            '30 minutes': '30 mins',
            '1 hour': '1 hour',
            '4 hours': '4 hours',
        }
        
        return tf_map.get(timeframe.lower(), timeframe)
    
    async def get_bid_ask_spread(self, symbol: str, 
                                 exchange: str = 'SMART', 
                                 currency: str = 'USD') -> Optional[float]:
        """
        Get current bid-ask spread percentage
        """
        contract = None
        try:
            contract = Stock(symbol, exchange, currency)
            await self.ib.qualifyContractsAsync(contract)
            
            ticker = self.ib.reqMktData(contract, '', False, False)
            await asyncio.sleep(0.5)
            
            if ticker.bid and ticker.ask and ticker.bid > 0:
                spread_pct = ((ticker.ask - ticker.bid) / ticker.bid) * 100
                self._safe_cancel_mkt_data(contract)
                return spread_pct
            
            self._safe_cancel_mkt_data(contract)
            return None
            
        except Exception as e:
            logger.debug(f"Spread check failed for {symbol}: {e}")
            if contract:
                self._safe_cancel_mkt_data(contract)
            return None
    
    def _safe_cancel_mkt_data(self, contract):
        """Safely cancel market data subscription, ignoring errors if already cancelled"""
        try:
            self.ib.cancelMktData(contract)
        except Exception:
            pass  # Ignore Error 300 and similar benign errors
    
    async def get_spy_performance(self, timeframe: str,
                                  lookback: int) -> Tuple[float, float]:
        """
        Get SPY performance and volatility with caching.
        Falls back to yfinance if IB is unavailable.
        Returns: (performance, volatility)
        """
        cache_key = f"{timeframe}_{lookback}"

        if cache_key in self.spy_cache:
            return self.spy_cache[cache_key]

        df = None

        # Try IB first
        if self.ib_available:
            try:
                spy = Stock('SPY', 'ARCA', 'USD')
                duration = '300 D' if 'day' in timeframe else '10 D'

                bars = await self.ib.reqHistoricalDataAsync(
                    spy, '', duration, timeframe, 'TRADES', True, 1
                )

                if bars:
                    df = util.df(bars)

            except Exception as e:
                logger.warning(f"IB SPY fetch failed: {e}")

        # Fallback to yfinance
        if df is None and self.yf_fallback:
            ib_timeframe = self._normalize_timeframe(timeframe)
            df = self.yf_adapter.get_historical_data('SPY', ib_timeframe)

        if df is None or len(df) < lookback:
            return 0.0, 0.0

        # Performance
        perf = (df['close'].iloc[-1] / df['close'].iloc[-lookback]) - 1

        # Volatility (ATR%)
        df['hl'] = df['high'] - df['low']
        df['hc'] = (df['high'] - df['close'].shift()).abs()
        df['lc'] = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([df['hl'], df['hc'], df['lc']], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_pct = (atr / df['close']) * 100
        spy_vol = float(atr_pct.tail(lookback).mean())

        self.spy_cache[cache_key] = (perf, spy_vol)
        return perf, spy_vol
    
    def clear_cache(self):
        """Clear SPY performance cache"""
        self.spy_cache.clear()


def check_liquidity(df: pd.DataFrame, min_dollar_volume: float = 5_000_000) -> bool:
    """Check if stock has sufficient liquidity"""
    avg_dollar_volume = (df['close'] * df['volume']).tail(20).mean()
    return avg_dollar_volume >= min_dollar_volume
