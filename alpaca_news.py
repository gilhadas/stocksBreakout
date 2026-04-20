#!/usr/bin/env python3
"""
alpaca_news.py
==============
Alpaca (Benzinga-backed) news headlines for the breakout scanner.

Provides high-quality headline text used as the primary source for FinBERT.
No built-in sentiment score — that's handled by FinBERT downstream.

API docs: https://docs.alpaca.markets/reference/news-3
Free tier: 200 req/min, historical supported (back years), paper account works.

Public API:
    get_alpaca_headlines(symbol, limit=10, hours_back=48)   -> List[str]
    batch_alpaca_headlines(symbols, limit_per_symbol=8, ...) -> {symbol: List[str]}

Environment:
    ALPACA_API_KEY    — same var as alpaca_adapter.py
    ALPACA_SECRET_KEY — same var as alpaca_adapter.py
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger(__name__)

_ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"

# Shared cache consumed by finbert_sentiment.fetch_headlines() so FinBERT
# never triggers extra HTTP calls after a batch prefetch.
_headline_cache: Dict[str, List[str]] = {}
_cache_lock = threading.Lock()
_cache_ts: Optional[datetime] = None
_CACHE_TTL_MINUTES = 30


def _get_keys() -> tuple[str, str]:
    return (
        os.environ.get("ALPACA_API_KEY", ""),
        os.environ.get("ALPACA_SECRET_KEY", ""),
    )


def _fetch_raw(symbols: str, start: str, end: str, limit: int = 50) -> list:
    """Raw GET against Alpaca's /v1beta1/news. Returns the news array."""
    key_id, secret = _get_keys()
    if not key_id or not secret:
        logger.debug("ALPACA_API_KEY/ALPACA_SECRET_KEY not set — skipping Alpaca news")
        return []
    if requests is None:
        logger.debug("requests not installed — cannot fetch Alpaca news")
        return []

    headers = {
        "APCA-API-KEY-ID": key_id,
        "APCA-API-SECRET-KEY": secret,
    }
    params = {
        "symbols": symbols,
        "start": start,
        "end": end,
        "limit": min(limit, 50),
        "sort": "desc",
        "include_content": "false",
        "exclude_contentless": "true",
    }

    try:
        resp = requests.get(_ALPACA_NEWS_URL, headers=headers,
                            params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("news", []) or []
    except Exception as e:
        logger.warning(f"Alpaca news fetch failed: {e}")
        return []


def _to_text(article: dict) -> str:
    """Format one article as headline + first summary sentence for FinBERT."""
    title = (article.get("headline") or "").strip()
    if not title:
        return ""
    summary = (article.get("summary") or "").strip()
    if summary:
        first = summary.split(". ")[0].strip()
        if first and first.lower() != title.lower()[:len(first)]:
            return f"{title}. {first}"
    return title


def get_alpaca_headlines(symbol: str, limit: int = 10,
                        hours_back: int = 48) -> List[str]:
    """Fetch recent headlines for a single ticker from Alpaca/Benzinga."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    articles = _fetch_raw(symbols=symbol.upper(), start=start, end=end, limit=limit)

    # Filter: only articles where THIS ticker is in the symbols array.
    sym_upper = symbol.upper()
    texts: List[str] = []
    for art in articles:
        syms = [s.upper() for s in (art.get("symbols") or [])]
        if sym_upper not in syms:
            continue
        t = _to_text(art)
        if t:
            texts.append(t)
        if len(texts) >= limit:
            break
    return texts


def batch_alpaca_headlines(symbols: List[str], limit_per_symbol: int = 8,
                          hours_back: int = 48) -> Dict[str, List[str]]:
    """
    Fetch headlines for many symbols in a single Alpaca call and split by symbol.

    Populates the module-level cache so finbert_sentiment.fetch_headlines()
    picks Alpaca text without any extra API calls.
    """
    if not symbols:
        return {}

    syms_upper = [s.upper() for s in symbols]
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Alpaca allows comma-separated symbols. Use a healthy total limit so we get
    # headlines for each name.
    total_limit = min(max(limit_per_symbol * len(syms_upper), 20), 50)
    articles = _fetch_raw(
        symbols=",".join(syms_upper), start=start, end=end, limit=total_limit,
    )

    by_symbol: Dict[str, List[str]] = {s: [] for s in syms_upper}
    for art in articles:
        text = _to_text(art)
        if not text:
            continue
        for s in (art.get("symbols") or []):
            su = s.upper()
            if su in by_symbol and len(by_symbol[su]) < limit_per_symbol:
                by_symbol[su].append(text)

    with _cache_lock:
        global _cache_ts
        for sym_u, heads in by_symbol.items():
            if heads:
                _headline_cache[sym_u] = heads
        _cache_ts = datetime.now(timezone.utc)

    return by_symbol


def fetch_alpaca_headlines_cached(symbol: str, limit: int = 8,
                                  hours_back: int = 48) -> List[str]:
    """Read from cache first; only hit the API if nothing is cached yet."""
    sym = symbol.upper()
    with _cache_lock:
        if (sym in _headline_cache and _cache_ts
                and (datetime.now(timezone.utc) - _cache_ts).total_seconds()
                < _CACHE_TTL_MINUTES * 60):
            cached = _headline_cache[sym][:limit]
            if cached:
                return cached
    return get_alpaca_headlines(symbol, limit=limit, hours_back=hours_back)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)
    syms = sys.argv[1:] if len(sys.argv) > 1 else ["NVDA", "AAPL", "TSLA"]
    print(f"Fetching Alpaca/Benzinga headlines for: {', '.join(syms)}")
    print("=" * 70)
    result = batch_alpaca_headlines(syms, limit_per_symbol=5)
    for s, heads in result.items():
        print(f"\n{s}: {len(heads)} headline(s)")
        for h in heads:
            print(f"  - {h[:100]}")
