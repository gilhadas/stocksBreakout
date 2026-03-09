"""
Alpaca Data Adapter
Provides historical and real-time market data via alpaca-py (free tier).
Implements the same interface as YFinanceAdapter so it's a drop-in replacement.

Requires env vars:
  ALPACA_API_KEY     — from alpaca.markets (free account)
  ALPACA_SECRET_KEY  — from alpaca.markets (free account)
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# IB-style timeframe → (alpaca TimeFrame, bars needed for each mode)
_TF_MAP = {
    '1 min':   ('1Min',  200),   # scalping  — 200 bars ≈ 3 sessions
    '5 mins':  ('5Min',  100),   # intraday
    '15 mins': ('15Min', 100),   # intraday
    '30 mins': ('30Min',  80),
    '1 hour':  ('1Hour', 100),
    '1 day':   ('1Day',  365),
    '1W':      ('1Week',  52),
    '1M':      ('1Month', 24),
}

# bars needed → calendar days to fetch (rough factor)
_DAYS_FACTOR = {
    '1Min':   1.5,    # intraday: 1 bar = 1 min; 200 bars ≈ ~3 sessions × 390 min
    '5Min':   2.5,
    '15Min':  4.0,
    '30Min':  6.0,
    '1Hour':  15.0,
    '1Day':   1.4,    # daily: add ~40% for weekends/holidays
    '1Week':  8.0,
    '1Month': 40.0,
}


class AlpacaAdapter:
    """Fetch OHLCV data from Alpaca Markets (free tier, no IB needed)."""

    def __init__(self):
        self._api_key    = os.environ.get('ALPACA_API_KEY', '')
        self._secret_key = os.environ.get('ALPACA_SECRET_KEY', '')
        self._client     = None   # lazy-init

    # ------------------------------------------------------------------
    # Lazy client init
    # ------------------------------------------------------------------
    def _get_client(self):
        if self._client is None:
            from alpaca.data.historical import StockHistoricalDataClient
            if self._api_key and self._secret_key:
                self._client = StockHistoricalDataClient(self._api_key, self._secret_key)
            else:
                # Unauthenticated client — free data with rate limits
                self._client = StockHistoricalDataClient()
                logger.warning("Alpaca: no API keys — using unauthenticated client (rate-limited)")
        return self._client

    # ------------------------------------------------------------------
    # Public interface (same signature as YFinanceAdapter)
    # ------------------------------------------------------------------
    def get_historical_data(self, symbol: str, timeframe: str,
                            start_date: Optional[str] = None,
                            end_date: Optional[str] = None) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV bars from Alpaca.

        Args:
            symbol:    e.g. 'AAPL'
            timeframe: IB-style string — '1 min', '1 day', '15 mins', etc.
            start_date / end_date: 'YYYY-MM-DD' or None (auto-calculated)

        Returns:
            DataFrame with lowercase columns: open, high, low, close, volume
            Index: tz-aware datetime
        """
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        alpaca_tf_str, bars_needed = _TF_MAP.get(timeframe, ('1Day', 365))
        alpaca_tf = self._parse_timeframe(alpaca_tf_str)

        # Calculate date range
        end_dt   = pd.Timestamp(end_date)   if end_date   else pd.Timestamp.now(tz='UTC')
        if start_date:
            start_dt = pd.Timestamp(start_date)
        else:
            days = bars_needed * _DAYS_FACTOR.get(alpaca_tf_str, 2.0)
            start_dt = end_dt - timedelta(days=int(days))

        # Ensure tz-aware
        if start_dt.tzinfo is None:
            start_dt = start_dt.tz_localize('UTC')
        if end_dt.tzinfo is None:
            end_dt = end_dt.tz_localize('UTC')

        try:
            client = self._get_client()
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=alpaca_tf,
                start=start_dt,
                end=end_dt,
                feed='iex',          # free feed; use 'sip' with paid plan
                adjustment='all',    # splits + dividends
            )
            bars = client.get_stock_bars(req)
            df = bars.df

            if df is None or df.empty:
                logger.debug(f"Alpaca: no data for {symbol} ({timeframe})")
                return None

            # Alpaca returns MultiIndex (symbol, timestamp) — drop symbol level
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(symbol, level='symbol')

            df.index.name = 'date'
            df = df.rename(columns={
                'open': 'open', 'high': 'high', 'low': 'low',
                'close': 'close', 'volume': 'volume',
            })
            df = df[['open', 'high', 'low', 'close', 'volume']].copy()
            df = df.sort_index()

            logger.debug(f"Alpaca: {symbol} {timeframe} — {len(df)} bars "
                         f"({df.index[0]} → {df.index[-1]})")
            return df

        except Exception as e:
            logger.debug(f"Alpaca fetch failed for {symbol}: {e}")
            return None

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Return latest trade price from Alpaca."""
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestTradeRequest
            client = self._get_client()
            req = StockLatestTradeRequest(symbol_or_symbols=symbol, feed='iex')
            trade = client.get_stock_latest_trade(req)
            price = trade[symbol].price
            return float(price) if price else None
        except Exception as e:
            logger.debug(f"Alpaca price fetch failed for {symbol}: {e}")
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_timeframe(tf_str: str):
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        mapping = {
            '1Min':   TimeFrame.Minute,
            '5Min':   TimeFrame(5,  TimeFrameUnit.Minute),
            '15Min':  TimeFrame(15, TimeFrameUnit.Minute),
            '30Min':  TimeFrame(30, TimeFrameUnit.Minute),
            '1Hour':  TimeFrame.Hour,
            '1Day':   TimeFrame.Day,
            '1Week':  TimeFrame.Week,
            '1Month': TimeFrame.Month,
        }
        return mapping.get(tf_str, TimeFrame.Day)
