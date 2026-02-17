"""
yfinance Data Adapter
Provides historical market data from Yahoo Finance as fallback for IB
"""

import logging
import os
import hashlib
import pandas as pd
from typing import Optional
from datetime import datetime, timedelta
from pathlib import Path
import yfinance as yf

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.getenv('SCANNER_OUTPUT', 'scanner_output')) / 'cache'


class YFinanceAdapter:
    """Adapter to fetch historical data from Yahoo Finance"""

    def __init__(self, use_disk_cache: bool = True):
        self.cache = {}
        self.use_disk_cache = use_disk_cache
        if use_disk_cache:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    # ------------------------------------------------------------------
    # Disk cache helpers
    # ------------------------------------------------------------------
    def _cache_key(self, symbol: str, interval: str,
                   start_date: Optional[str], end_date: Optional[str]) -> str:
        """Build a deterministic cache filename."""
        safe_sym = symbol.replace(' ', '_').replace('/', '_')
        tag = f"{safe_sym}_{interval}_{start_date or 'P'}_{end_date or 'P'}"
        return tag

    def _cache_path(self, key: str) -> Path:
        return CACHE_DIR / f"{key}.parquet"

    def _is_cache_fresh(self, path: Path, end_date: Optional[str]) -> bool:
        """Decide if cached parquet is still usable.

        Rules:
          - If end_date is in the past → always fresh (historical data won't change).
          - If end_date is today or None (live) → fresh for 4 hours.
        """
        if not path.exists():
            return False

        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        age = datetime.now() - mtime

        if end_date:
            try:
                end_dt = pd.to_datetime(end_date).date()
                if end_dt < datetime.now().date():
                    return True            # historical — always valid
            except Exception:
                pass

        # Live / today data: stale after 4 hours
        return age < timedelta(hours=4)

    def _read_cache(self, key: str) -> Optional[pd.DataFrame]:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            logger.debug(f"Cache hit: {key} ({len(df)} bars)")
            return df
        except Exception as e:
            logger.debug(f"Cache read failed for {key}: {e}")
            return None

    def _write_cache(self, key: str, df: pd.DataFrame) -> None:
        path = self._cache_path(key)
        try:
            tmp = path.with_suffix('.tmp')
            df.to_parquet(tmp, engine='auto')
            tmp.replace(path)
        except Exception as e:
            logger.debug(f"Cache write failed for {key}: {e}")

    # ------------------------------------------------------------------
    # Main data fetch
    # ------------------------------------------------------------------
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
        interval = self._convert_timeframe(timeframe)

        # --- Disk cache check ---
        if self.use_disk_cache:
            ckey = self._cache_key(symbol, interval, start_date, end_date)
            cpath = self._cache_path(ckey)
            if self._is_cache_fresh(cpath, end_date):
                cached = self._read_cache(ckey)
                if cached is not None:
                    return cached

        try:
            # Determine period if dates not provided
            if not start_date or not end_date:
                period = self._get_period_from_timeframe(timeframe)

                logger.debug(f"Fetching {symbol} data: period={period}, interval={interval}")

                ticker_symbol = symbol.replace(' ', '-')
                ticker = yf.Ticker(ticker_symbol)
                df = ticker.history(period=period, interval=interval)
            else:
                logger.debug(f"Fetching {symbol} data: {start_date} to {end_date}, interval={interval}")

                ticker_symbol = symbol.replace(' ', '-')
                ticker = yf.Ticker(ticker_symbol)
                df = ticker.history(start=start_date, end=end_date, interval=interval)

            if df is None or len(df) == 0:
                logger.debug(f"No data returned for {symbol}")
                return None

            # Normalize column names to match IB format (lowercase)
            df.columns = df.columns.str.lower()

            # Remove timezone info to match IB format
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

            # Keep only OHLCV columns
            ohlcv = ['open', 'high', 'low', 'close', 'volume']
            df = df[[col for col in ohlcv if col in df.columns]]

            logger.debug(f"Retrieved {len(df)} bars for {symbol}")

            # --- Write to disk cache ---
            if self.use_disk_cache and len(df) > 0:
                self._write_cache(ckey, df)

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
    
    def get_fundamentals(self, symbol: str) -> dict:
        """
        Fetch fundamental data for a symbol (cached).

        Returns dict with keys:
            market_cap, float_shares, earnings_date, sector, industry
        """
        cache_key = f"fund_{symbol}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        result = {
            'market_cap': None,
            'float_shares': None,
            'earnings_date': None,
            'sector': '',
            'industry': '',
        }

        try:
            ticker = yf.Ticker(symbol.replace(' ', '-'))
            info = ticker.info or {}
            result['market_cap'] = info.get('marketCap')
            result['float_shares'] = info.get('floatShares')
            result['sector'] = info.get('sector', '')
            result['industry'] = info.get('industry', '')

            # Next earnings date
            cal = ticker.calendar
            if cal is not None:
                if isinstance(cal, dict):
                    ed = cal.get('Earnings Date')
                    if ed:
                        result['earnings_date'] = str(ed[0]) if isinstance(ed, list) else str(ed)
                elif hasattr(cal, 'iloc'):
                    try:
                        result['earnings_date'] = str(cal.iloc[0, 0])
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Fundamentals fetch failed for {symbol}: {e}")

        self.cache[cache_key] = result
        return result

    def clear_cache(self):
        """Clear in-memory cache"""
        self.cache.clear()

    def clear_disk_cache(self):
        """Remove all parquet files from scanner_output/cache/"""
        import shutil
        if CACHE_DIR.exists():
            count = sum(1 for _ in CACHE_DIR.glob('*.parquet'))
            shutil.rmtree(CACHE_DIR)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            logger.info(f"Cleared {count} cached files from {CACHE_DIR}")
