"""
Market data fetching and processing module
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Tuple
from zoneinfo import ZoneInfo
import pandas as pd
from ib_insync import IB, Stock, util

from config import DATA_DURATION
from indicators import calculate_atr

logger = logging.getLogger(__name__)


class MarketDataHandler:
    """Handles all market data requests to Interactive Brokers with Alpaca/yfinance fallback"""

    def __init__(self, ib_connection: IB, yf_fallback: bool = False):
        self.ib = ib_connection
        self.ib_available = ib_connection is not None
        self.yf_fallback = yf_fallback
        self.spy_cache = {}
        self.series_cache = {}  # (symbol, timeframe) → OHLCV df, per session (Tension Index context)
        self.cached_market_data = None  # Set via set_cached_data() from job_launcher

        if yf_fallback:
            # Prefer Alpaca (real-time free data) over yfinance (15-min delayed)
            try:
                from alpaca_adapter import AlpacaAdapter
                self.yf_adapter = AlpacaAdapter()
                self.data_source_name = "Alpaca Markets"
                logger.info("📊 Fallback data source: Alpaca Markets")
            except Exception:
                from yfinance_adapter import YFinanceAdapter
                self.yf_adapter = YFinanceAdapter()
                self.data_source_name = "yfinance"
                logger.info("📊 Fallback data source: yfinance")
        else:
            self.data_source_name = "Interactive Brokers"

    def set_cached_data(self, cached_data):
        """Set cached market data from job_launcher.py batch execution."""
        self.cached_market_data = cached_data
        if cached_data:
            logger.info(f"✓ Market data cache activated: {len(cached_data.symbols)} symbols")

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
                logger.warning(f"IB fetch failed for {symbol}: {e}")

        # Fallback to yfinance
        if df is None and self.yf_fallback:
            if self.ib_available:
                logger.info(f"  ↪ yfinance fallback for {symbol}")
            df = self.yf_adapter.get_historical_data(symbol, ib_timeframe)

        return df

    async def get_market_series(self, symbol: str,
                                timeframe: str = '1 day') -> Optional[pd.DataFrame]:
        """Fetch an OHLCV series for market/sector context, cached per session.

        Thin cached wrapper over ``get_historical_data`` used by the Tension
        Index to obtain SPY / sector-ETF / a symbol's daily series without
        re-fetching the same ETF for every ticker in a scan. Returns None on
        failure (callers degrade gracefully).
        """
        cache_key = (symbol.upper(), timeframe)
        if cache_key in self.series_cache:
            return self.series_cache[cache_key]
        try:
            df = await self.get_historical_data(symbol, timeframe)
        except Exception as e:
            logger.debug(f"get_market_series failed for {symbol} {timeframe}: {e}")
            df = None
        self.series_cache[cache_key] = df  # cache None too, to avoid retry storms
        return df

    async def get_current_price(self, symbol: str,
                                exchange: str = 'SMART',
                                currency: str = 'USD') -> Optional[float]:
        """Fetch current/latest price for a symbol (lightweight, for monitoring)"""
        # Try IB real-time first
        if self.ib_available:
            contract = None
            try:
                contract = Stock(symbol, exchange, currency)
                await self.ib.qualifyContractsAsync(contract)
                ticker = self.ib.reqMktData(contract, '', False, False)
                await asyncio.sleep(0.5)
                price = ticker.last or ticker.close
                self._safe_cancel_mkt_data(contract)
                if price and price > 0:
                    return float(price)
            except Exception as e:
                logger.debug(f"IB price fetch failed for {symbol}: {e}")
                if contract:
                    self._safe_cancel_mkt_data(contract)

        # Always try yfinance as fallback for current price (even if IB connected,
        # real-time quotes may require additional subscription)
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol.replace(' ', '-'))
            info = ticker.fast_info
            price = info.get('lastPrice') or info.get('previousClose')
            if price and price > 0:
                return float(price)
        except Exception as e:
            logger.debug(f"yfinance price fetch failed for {symbol}: {e}")

        return None

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
        cache_key = f"{timeframe}_{lookback}_{datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')}"

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

    async def get_regime_spy_performance(self) -> Tuple[float, float]:
        """Get SPY performance for regime classification only.

        Always uses 15 daily bars regardless of scan timeframe, and skips
        today's incomplete bar when markets are still open. This prevents:
        - Intraday (15-min) scans from triggering RED_MARKET on 75-min dips
        - Incomplete daily bars during market hours from distorting regime
        """
        _NY = ZoneInfo('America/New_York')
        now_et = datetime.now(_NY)
        # After 16:00 ET the daily bar is closed; before that use previous close
        use_today = now_et.hour >= 16
        LOOKBACK = 15

        cache_key = f"regime_{now_et.strftime('%Y-%m-%d')}_{use_today}"
        if cache_key in self.spy_cache:
            return self.spy_cache[cache_key]

        df = None
        if self.ib_available:
            try:
                spy = Stock('SPY', 'ARCA', 'USD')
                bars = await self.ib.reqHistoricalDataAsync(
                    spy, '', '300 D', '1 day', 'TRADES', True, 1
                )
                if bars:
                    df = util.df(bars)
            except Exception as e:
                logger.warning(f"IB SPY regime fetch failed: {e}")

        if df is None and self.yf_fallback:
            df = self.yf_adapter.get_historical_data('SPY', '1 day')

        if df is None or len(df) < LOOKBACK + 2:
            return 0.0, 0.0

        # last_idx: use today's close after market, previous close during hours
        last_idx = -1 if use_today else -2
        perf = (df['close'].iloc[last_idx] / df['close'].iloc[last_idx - LOOKBACK]) - 1

        df2 = df.copy()
        df2['hl'] = df2['high'] - df2['low']
        df2['hc'] = (df2['high'] - df2['close'].shift()).abs()
        df2['lc'] = (df2['low'] - df2['close'].shift()).abs()
        tr = pd.concat([df2['hl'], df2['hc'], df2['lc']], axis=1).max(axis=1)
        atr_pct = (tr.rolling(14).mean() / df2['close']) * 100
        spy_vol = float(atr_pct.tail(LOOKBACK).mean())

        self.spy_cache[cache_key] = (perf, spy_vol)
        return perf, spy_vol

    async def get_spy_bear_macro(self) -> bool:
        """
        Returns True if SPY is in a structural bear market (close < 200-day SMA).
        Cached per session. Used by V9-H regime gate to restrict to GOLD-only signals.
        """
        if 'bear_macro' in self.spy_cache:
            return self.spy_cache['bear_macro']

        df = None
        if self.ib_available:
            try:
                spy = Stock('SPY', 'ARCA', 'USD')
                bars = await self.ib.reqHistoricalDataAsync(
                    spy, '', '300 D', '1 day', 'TRADES', True, 1
                )
                if bars:
                    df = util.df(bars)
            except Exception:
                pass

        if df is None and self.yf_fallback:
            df = self.yf_adapter.get_historical_data('SPY', '1 day')

        if df is None or len(df) < 200:
            self.spy_cache['bear_macro'] = False
            return False

        sma200 = float(df['close'].iloc[-200:].mean())
        result = float(df['close'].iloc[-1]) < sma200
        self.spy_cache['bear_macro'] = result
        return result

    async def get_spy_consec_below_sma200(self) -> int:
        """
        Returns how many consecutive trading days SPY close has been below its
        200-day SMA (0 if currently above). Cached per session alongside bear_macro.
        Used by BOUNCE_BEAR_GATE to distinguish sustained bears from brief dips.
        """
        if 'consec_below_sma200' in self.spy_cache:
            return self.spy_cache['consec_below_sma200']

        df = None
        if self.ib_available:
            try:
                spy = Stock('SPY', 'ARCA', 'USD')
                bars = await self.ib.reqHistoricalDataAsync(
                    spy, '', '300 D', '1 day', 'TRADES', True, 1
                )
                if bars:
                    df = util.df(bars)
            except Exception:
                pass

        if df is None and self.yf_fallback:
            df = self.yf_adapter.get_historical_data('SPY', '1 day')

        if df is None or len(df) < 200:
            self.spy_cache['consec_below_sma200'] = 0
            return 0

        sma200 = df['close'].rolling(200).mean()
        count = 0
        for cl, sm in zip(reversed(df['close'].tolist()), reversed(sma200.tolist())):
            if pd.isna(sm) or cl >= sm:
                break
            count += 1

        self.spy_cache['consec_below_sma200'] = count
        return count

    async def get_vix_level(self) -> float:
        """
        Fetch the current VIX closing level. Cached per session.
        Returns 0.0 on failure (caller should treat as 'unknown').
        """
        if 'vix' in self.spy_cache:
            return self.spy_cache['vix']

        vix = 0.0
        try:
            if self.yf_fallback:
                df = self.yf_adapter.get_historical_data('^VIX', '1 day')
                if df is not None and len(df) >= 1:
                    vix = float(df['close'].iloc[-1])
        except Exception:
            pass

        self.spy_cache['vix'] = vix
        return vix

    def clear_cache(self):
        """Clear all per-session caches (SPY performance + Tension Index series)."""
        self.spy_cache.clear()
        self.series_cache.clear()


def check_liquidity(df: pd.DataFrame, min_dollar_volume: float = 5_000_000) -> bool:
    """Check if stock has sufficient liquidity"""
    avg_dollar_volume = (df['close'] * df['volume']).tail(20).mean()
    return avg_dollar_volume >= min_dollar_volume
