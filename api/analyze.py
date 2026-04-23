"""
On-demand single-symbol analysis for the mobile Analyze tab.
Reuses ScannerOrchestrator._scan_symbol with yfinance/Alpaca fallback (no IB).
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

# Python 3.14 compatibility: ib_insync/eventkit calls
# asyncio.get_event_loop_policy().get_event_loop() at import time, which raises
# when no loop is set on the current thread (even if a loop is running).
try:
    asyncio.get_event_loop_policy().get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import pandas as pd

from config import MODES, V9H_REGIME_GATE, BOUNCE_BEAR_GATE
from orchestrator import ScannerOrchestrator
from utils import classify_market_regime, get_smoothed_regime

logger = logging.getLogger(__name__)

_NY_TZ = ZoneInfo('America/New_York')

_VALID_MODES = set(MODES.keys())
_VALID_TIMEFRAMES = {'1 day', '1W', '15 mins', '5 mins', '1 min'}

_INDICATOR_COLS = [
    'RSI', 'MACD', 'MACD_Signal', 'MACD_Hist', 'ADX',
    'ATR', 'Vol_Ratio', 'BB_Position', 'BB_Width',
    'StochRSI_K', 'StochRSI_D', 'EMA_9', 'EMA_21',
    'SMA_50', 'SMA_150', 'SMA_200', 'Trend_Line',
    'Momentum_Score', 'Conviction_Score',
]


def _pick(row, col):
    if col not in row.index:
        return None
    v = row[col]
    try:
        if pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


async def analyze_symbol(symbol: str, mode: str = 'swing',
                         timeframe: str = '1 day') -> dict:
    """
    Run the scanner on a single symbol and return a structured report.

    Returns a dict with: symbol, mode, timeframe, regime, signal (or None),
    rejection_reason (if no signal), indicators snapshot, price, timestamp.
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"Invalid mode '{mode}'. Valid: {sorted(_VALID_MODES)}")
    if timeframe not in _VALID_TIMEFRAMES:
        raise ValueError(f"Invalid timeframe '{timeframe}'. Valid: {sorted(_VALID_TIMEFRAMES)}")

    symbol = symbol.upper().strip()

    orch = ScannerOrchestrator(ib_connection=None, yf_fallback=True)

    # Market context — mirror scan_watchlist setup
    if mode != 'scalping':
        lb = MODES[mode]['lookback']
        spy_perf, spy_vol = await orch.market_data.get_spy_performance(timeframe, lb)
        spy_perf_regime, spy_vol_regime = await orch.market_data.get_regime_spy_performance()
        raw_regime = classify_market_regime(spy_perf_regime, spy_vol_regime)
        regime, _ = get_smoothed_regime(raw_regime)
        bear_macro = False
        if V9H_REGIME_GATE.get('enabled'):
            bear_macro = await orch.market_data.get_spy_bear_macro()
        spy_consec_below = 0
        if BOUNCE_BEAR_GATE > 0:
            spy_consec_below = await orch.market_data.get_spy_consec_below_sma200()
    else:
        spy_perf, spy_vol = 0.0, 0.0
        regime = 'INTRADAY'
        bear_macro, spy_consec_below = False, 0

    # Try full cascade via orchestrator
    orch.detector.rejection_reasons.clear()
    signal = await orch._scan_symbol(
        symbol, mode, timeframe, spy_perf, regime,
        vol_thresh=None, atr_mult=None,
        detect_bounces=True,
        bear_macro=bear_macro,
        spy_consec_below=spy_consec_below,
    )

    # Fetch indicator snapshot directly (so we always have data, signal or not)
    df = await orch.market_data.get_historical_data(symbol, timeframe)
    indicators: dict = {}
    price: Optional[float] = None
    rejection_reason: Optional[str] = None

    if df is not None and len(df) > 0:
        # Ensure indicators are computed — orchestrator._scan_symbol already calls
        # detector.detect which computes them, but only when data is sufficient.
        # Run a fresh compute so we hand back values regardless.
        try:
            from indicators import calculate_all_indicators
            mcfg = MODES.get(mode, {})
            trend_type = mcfg.get('trend_type', 'SMA')
            trend_period = mcfg.get('trend_period', 20)
            df = calculate_all_indicators(df, trend_type, trend_period, timeframe)
        except Exception as e:
            logger.warning(f"Indicator compute failed for {symbol}: {e}")

        latest = df.iloc[-1]
        price = _pick(latest, 'close')
        for col in _INDICATOR_COLS:
            indicators[col] = _pick(latest, col)

    if signal is None:
        for r in orch.detector.rejection_reasons:
            if r.get('symbol') == symbol:
                rejection_reason = r.get('reasons')
                break

    return {
        'symbol': symbol,
        'mode': mode,
        'timeframe': timeframe,
        'regime': regime,
        'spy_perf': round(spy_perf * 100, 2) if isinstance(spy_perf, float) else None,
        'bear_macro': bool(bear_macro),
        'spy_consec_below_sma200': int(spy_consec_below),
        'price': round(price, 2) if isinstance(price, float) else None,
        'signal': signal,
        'rejection_reason': rejection_reason,
        'indicators': indicators,
        'portfolio_status': _portfolio_status(symbol, price),
        'timestamp': datetime.now(_NY_TZ).isoformat(),
    }


def _status_from_levels(price, stop, target):
    if not price:
        return 'UNKNOWN'
    if stop and price <= stop:
        return 'SELL'
    if target and price >= target:
        return 'TARGET'
    if stop and price <= stop * 1.05:
        return 'CAUTION'
    return 'HOLD'


def _portfolio_status(symbol: str, price):
    """Check both auto and manual portfolios for this symbol. Returns None if not held."""
    # Auto portfolio — load() is local JSON
    try:
        import auto_portfolio as ap
        data = ap.load()
        for p in data.get('positions', []):
            if p.get('symbol') == symbol:
                entry = p.get('entry_price')
                stop = p.get('stop')
                target = p.get('target')
                pnl_pct = ((price - entry) / entry * 100) if (price and entry) else None
                return {
                    'owned': True,
                    'source': 'auto',
                    'entry_price': entry,
                    'stop': stop,
                    'target': target,
                    'shares': p.get('shares'),
                    'status': _status_from_levels(price, stop, target),
                    'pnl_pct': round(pnl_pct, 2) if pnl_pct is not None else None,
                    'mode': p.get('mode'),
                    'date_added': p.get('date_added'),
                }
    except Exception as e:
        logger.debug(f"auto_portfolio lookup failed for {symbol}: {e}")

    # Manual portfolio — S3 via server helper
    try:
        from api.server import _load_portfolio_json
        mdata = _load_portfolio_json()
        raw = mdata.get('positions', {}) if mdata else {}
        positions = [{'symbol': k, **v} for k, v in raw.items()] if isinstance(raw, dict) else raw
        for p in positions:
            if p.get('symbol') == symbol:
                entry = p.get('entry_price')
                stop = p.get('stop')
                target = p.get('target')
                pnl_pct = ((price - entry) / entry * 100) if (price and entry) else None
                return {
                    'owned': True,
                    'source': 'manual',
                    'entry_price': entry,
                    'stop': stop,
                    'target': target,
                    'shares': p.get('shares'),
                    'status': _status_from_levels(price, stop, target),
                    'pnl_pct': round(pnl_pct, 2) if pnl_pct is not None else None,
                    'mode': p.get('mode'),
                    'date_added': p.get('date_added'),
                }
    except Exception as e:
        logger.debug(f"manual portfolio lookup failed for {symbol}: {e}")

    return None
