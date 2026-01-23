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
    """Handles all market data requests to Interactive Brokers"""
    
    def __init__(self, ib_connection: IB):
        self.ib = ib_connection
        self.spy_cache = {}
    
    async def get_historical_data(self, symbol: str, timeframe: str, 
                                  exchange: str = 'SMART', 
                                  currency: str = 'USD') -> Optional[pd.DataFrame]:
        """
        Fetch historical data for a symbol
        """
        try:
            contract = Stock(symbol, exchange, currency)
            await self.ib.qualifyContractsAsync(contract)
            
            # Determine duration based on timeframe
            if 'day' in timeframe:
                duration = DATA_DURATION['daily']
            elif 'min' in timeframe and '1 min' in timeframe:
                duration = DATA_DURATION['scalping']
            else:
                duration = DATA_DURATION['intraday']
            
            bars = await self.ib.reqHistoricalDataAsync(
                contract, '', duration, timeframe, 'TRADES', True, 1
            )
            
            if not bars:
                return None
            
            df = util.df(bars).set_index('date')
            return df
            
        except Exception as e:
            logger.debug(f"Failed to fetch data for {symbol}: {e}")
            return None
    
    async def get_bid_ask_spread(self, symbol: str, 
                                 exchange: str = 'SMART', 
                                 currency: str = 'USD') -> Optional[float]:
        """
        Get current bid-ask spread percentage
        """
        try:
            contract = Stock(symbol, exchange, currency)
            await self.ib.qualifyContractsAsync(contract)
            
            ticker = self.ib.reqMktData(contract, '', False, False)
            await asyncio.sleep(0.5)
            
            if ticker.bid and ticker.ask and ticker.bid > 0:
                spread_pct = ((ticker.ask - ticker.bid) / ticker.bid) * 100
                self.ib.cancelMktData(contract)
                return spread_pct
            
            self.ib.cancelMktData(contract)
            return None
            
        except Exception as e:
            logger.debug(f"Spread check failed for {symbol}: {e}")
            return None
    
    async def get_spy_performance(self, timeframe: str, 
                                  lookback: int) -> Tuple[float, float]:
        """
        Get SPY performance and volatility with caching
        Returns: (performance, volatility)
        """
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
            
        except Exception as e:
            logger.warning(f"Failed to get SPY performance: {e}")
            return 0.0, 0.0
    
    def clear_cache(self):
        """Clear SPY performance cache"""
        self.spy_cache.clear()


def check_liquidity(df: pd.DataFrame, min_dollar_volume: float = 5_000_000) -> bool:
    """Check if stock has sufficient liquidity"""
    avg_dollar_volume = (df['close'] * df['volume']).tail(20).mean()
    return avg_dollar_volume >= min_dollar_volume
