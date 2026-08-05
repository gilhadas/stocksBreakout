"""
MarketDataHandler.clear_cache() must clear every per-session cache, not just one.

WHY THIS EXISTS
---------------
The handler holds two per-session dicts: `spy_cache` (SPY performance/regime) and
`series_cache` ((symbol, timeframe) -> OHLCV df, populated by get_market_series() for
Tension Index context). clear_cache() only ever cleared spy_cache — series_cache was
never touched by anything.

Currently low-risk in practice: every call site that constructs a MarketDataHandler
(breakout_scanner.py, pages/scan_page.py, api/analyze.py) creates a fresh instance per
scan/request, and TENSION_CONFIG['enabled'] is False, so series_cache never actually
grows today. But the method's own name promises to clear "the cache" — a caller relying
on clear_cache() to reset state before reusing a handler would silently keep every
series_cache entry from the prior session. Fixed at the source rather than left for the
day Tension Index (or a future long-lived handler) makes it a real leak.
"""
import pytest

from market_data import MarketDataHandler


@pytest.fixture
def handler() -> MarketDataHandler:
    return MarketDataHandler(ib_connection=None, yf_fallback=False)


def test_clear_cache_empties_series_cache(handler):
    handler.series_cache[('SPY', '1 day')] = object()
    handler.series_cache[('XLK', '1 day')] = object()

    handler.clear_cache()

    assert handler.series_cache == {}, "series_cache survived clear_cache()"


def test_clear_cache_still_empties_spy_cache(handler):
    """Guard the original behavior — don't trade one cache for the other."""
    handler.spy_cache['vix'] = 20.0

    handler.clear_cache()

    assert handler.spy_cache == {}


def test_clear_cache_with_both_populated(handler):
    handler.spy_cache['vix'] = 20.0
    handler.series_cache[('SPY', '1 day')] = object()

    handler.clear_cache()

    assert handler.spy_cache == {}
    assert handler.series_cache == {}
