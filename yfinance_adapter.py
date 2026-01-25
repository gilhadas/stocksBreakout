"""
yfinance Data Adapter
Provides historical market data from Yahoo Finance as fallback for IB
"""

import logging
import pandas as pd
from typing import Optional
from datetime import datetime, timedelta
import yfinance as yf

logger = logging.getLogger(__name__)


class YFinanceAdapter:
    """Adapter to fetch historical data from Yahoo Finance"""
    
    def __init__(self):
        self.cache = {}
    
    def get_historical_data(self, symbol: str, timeframe: str, 
                           start_date: Optional[str] = None,
                           end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        """
        Fetch historical data from Yahoo Finance
        
        Args:
            symbol: Stock symbol
            timeframe: IB-style timeframe ('1 day', '1 hour', '15 mins', etc.)
            start_date: Start date (YYYY-MM-DD) or None for auto-calculate
            end_date: End date (YYYY-MM-DD) or None for today
        
        Returns:
            DataFrame with columns: open, high, low, close, volume
            Index: datetime
        """
        try:
            # Convert IB timeframe to yfinance interval
            interval = self._convert_timeframe(timeframe)
            
            # Determine period if dates not provided
            if not start_date or not end_date:
                period = self._get_period_from_timeframe(timeframe)
                
                logger.debug(f"Fetching {symbol} data: period={period}, interval={interval}")
                
                ticker = yf.Ticker(symbol)
                df = ticker.history(period=period, interval=interval)
            else:
                logger.debug(f"Fetching {symbol} data: {start_date} to {end_date}, interval={interval}")
                
                ticker = yf.Ticker(symbol)
                df = ticker.history(start=start_date, end=end_date, interval=interval)
            
            if df is None or len(df) == 0:
                logger.debug(f"No data returned for {symbol}")
                return None
            
            # Normalize column names to match IB format (lowercase)
            df.columns = df.columns.str.lower()
            
            # Remove timezone info to match IB format
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            
            # Rename columns to match IB exactly
            column_map = {
                'open': 'open',
                'high': 'high', 
                'low': 'low',
                'close': 'close',
                'volume': 'volume'
            }
            
            # Keep only OHLCV columns
            df = df[[col for col in column_map.keys() if col in df.columns]]
            
            logger.debug(f"Retrieved {len(df)} bars for {symbol}")
            return df
            
        except Exception as e:
            logger.debug(f"Failed to fetch {symbol} from yfinance: {e}")
            return None
    
    def _convert_timeframe(self, ib_timeframe: str) -> str:
        """
        Convert IB timeframe to yfinance interval
        
        IB: '1 min', '5 mins', '15 mins', '1 hour', '1 day', '1W', '1M'
        yfinance: '1m', '5m', '15m', '1h', '1d', '1wk', '1mo'
        """
        timeframe_map = {
            '1 min': '1m',
            '2 mins': '2m',
            '5 mins': '5m',
            '15 mins': '15m',
            '30 mins': '30m',
            '1 hour': '1h',
            '2 hours': '2h',
            '4 hours': '4h',
            '1 day': '1d',
            '1W': '1wk',
            '1M': '1mo',
        }
        
        return timeframe_map.get(ib_timeframe, '1d')
    
    def _get_period_from_timeframe(self, timeframe: str) -> str:
        """
        Get appropriate period for timeframe
        
        Returns yfinance period: '1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', 'max'
        """
        if 'min' in timeframe.lower():
            return '5d'  # 5 days for intraday
        elif 'hour' in timeframe.lower():
            return '1mo'  # 1 month for hourly
        elif 'day' in timeframe.lower() or timeframe == '1 day':
            return '2y'  # 2 years for daily
        elif timeframe == '1W':
            return '5y'  # 5 years for weekly
        elif timeframe == '1M':
            return 'max'  # Max for monthly
        else:
            return '1y'  # Default to 1 year
    
    def get_bid_ask_spread(self, symbol: str) -> Optional[float]:
        """
        Get current bid-ask spread (not available in yfinance)
        Returns None as yfinance doesn't provide real-time bid/ask
        """
        logger.debug(f"Bid-ask spread not available from yfinance for {symbol}")
        return None
    
    def clear_cache(self):
        """Clear data cache"""
        self.cache.clear()
