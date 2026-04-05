#!/usr/bin/env python3
"""
alphavantage_news.py
====================
Alpha Vantage News Sentiment integration for the breakout scanner.

Uses the Alpha Vantage NEWS_SENTIMENT endpoint to fetch pre-scored
news articles with per-ticker sentiment. This provides:
  1. Richer headline data for FinBERT (more sources than yfinance alone)
  2. Pre-computed sentiment scores as a second opinion alongside FinBERT
  3. Topic classification (technology, earnings, M&A, etc.)

API docs: https://www.alphavantage.co/documentation/#news-sentiment

Public API:
    get_news_sentiment(symbol, limit=10)   -> AVNewsSentiment dict
    batch_news_sentiment(symbols, limit=5) -> {symbol: AVNewsSentiment}  (populates headline cache)
    fetch_av_headlines(symbol, limit=10)   -> List[str]  (reads cache first — zero extra API calls)

AVNewsSentiment keys:
    label          : 'bullish' | 'somewhat_bullish' | 'neutral' | 'somewhat_bearish' | 'bearish'
    score          : float -1.0 to 1.0 (avg ticker_sentiment_score across articles)
    articles       : int (how many articles matched)
    top_headline   : str (most recent headline)
    headlines      : List[str] (title + summary snippets for FinBERT)
    topics         : List[str] (deduplicated topic tags)
    sources        : List[str] (deduplicated source names)

Environment:
    ALPHA_VANTAGE_API_KEY — required. Get a free key at https://www.alphavantage.co/support/#api-key
"""

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, TypedDict

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory headline cache — populated by batch_news_sentiment(), consumed
# by fetch_av_headlines() so FinBERT never triggers extra API calls.
# ---------------------------------------------------------------------------
_headline_cache: Dict[str, List[str]] = {}  # {SYMBOL: [headline, ...]}
_cache_lock = threading.Lock()
_cache_ts: Optional[datetime] = None        # when cache was last filled
_CACHE_TTL_MINUTES = 30

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class AVNewsSentiment(TypedDict):
    label: str           # 'bullish' | 'somewhat_bullish' | 'neutral' | 'somewhat_bearish' | 'bearish'
    score: float         # avg ticker_sentiment_score (-1 to +1)
    articles: int        # number of articles with this ticker
    top_headline: str    # most recent headline
    headlines: List[str] # title + summary for FinBERT consumption
    topics: List[str]    # deduplicated topics (e.g. 'technology', 'earnings')
    sources: List[str]   # deduplicated source names


# ---------------------------------------------------------------------------
# Alpha Vantage API
# ---------------------------------------------------------------------------

_AV_BASE_URL = "https://www.alphavantage.co/query"


def _get_api_key() -> str:
    """Read Alpha Vantage API key from environment."""
    return os.environ.get("ALPHA_VANTAGE_API_KEY", "")


def _fetch_news_raw(tickers: str = "", topics: str = "",
                    time_from: str = "", limit: int = 50,
                    sort: str = "LATEST") -> dict:
    """
    Raw call to Alpha Vantage NEWS_SENTIMENT endpoint.

    Returns parsed JSON response or empty dict on failure.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.debug("No ALPHA_VANTAGE_API_KEY set — skipping news fetch")
        return {}

    if requests is None:
        logger.debug("requests not installed — cannot fetch Alpha Vantage news")
        return {}

    params = {
        "function": "NEWS_SENTIMENT",
        "apikey": api_key,
        "sort": sort,
        "limit": min(limit, 1000),
    }
    if tickers:
        params["tickers"] = tickers
    if topics:
        params["topics"] = topics
    if time_from:
        params["time_from"] = time_from

    try:
        resp = requests.get(_AV_BASE_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # Check for API error messages
        if "Error Message" in data or "Note" in data:
            msg = data.get("Error Message") or data.get("Note", "")
            logger.warning(f"Alpha Vantage API: {msg}")
            return {}

        return data
    except Exception as e:
        logger.warning(f"Alpha Vantage news fetch failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# Sentiment label mapping
# ---------------------------------------------------------------------------

# AV labels → simplified labels matching our pipeline
_LABEL_MAP = {
    "Bullish": "bullish",
    "Somewhat-Bullish": "somewhat_bullish",
    "Neutral": "neutral",
    "Somewhat-Bearish": "somewhat_bearish",
    "Bearish": "bearish",
}


def _simplify_label(av_label: str) -> str:
    return _LABEL_MAP.get(av_label, "neutral")


def _score_to_simple_label(score: float) -> str:
    """Convert numeric score to label using AV's documented thresholds."""
    if score >= 0.35:
        return "bullish"
    elif score >= 0.15:
        return "somewhat_bullish"
    elif score > -0.15:
        return "neutral"
    elif score > -0.35:
        return "somewhat_bearish"
    else:
        return "bearish"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_news_sentiment(symbol: str, limit: int = 10,
                       hours_back: int = 48) -> AVNewsSentiment:
    """
    Fetch news sentiment for a single ticker from Alpha Vantage.

    Args:
        symbol:     Ticker (e.g. 'NVDA')
        limit:      Max articles to fetch (default 10)
        hours_back: Only consider articles from the last N hours (default 48)

    Returns:
        AVNewsSentiment dict. On failure returns neutral with empty fields.
    """
    neutral: AVNewsSentiment = {
        "label": "neutral",
        "score": 0.0,
        "articles": 0,
        "top_headline": "",
        "headlines": [],
        "topics": [],
        "sources": [],
    }

    time_from = ""
    if hours_back > 0:
        dt = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        time_from = dt.strftime("%Y%m%dT%H%M")

    data = _fetch_news_raw(tickers=symbol.upper(), limit=limit,
                           time_from=time_from)
    if not data:
        return neutral

    feed = data.get("feed", [])
    if not feed:
        return neutral

    return _extract_ticker_sentiment(symbol.upper(), feed)


def batch_news_sentiment(symbols: List[str], limit_per_symbol: int = 5,
                         hours_back: int = 48) -> Dict[str, AVNewsSentiment]:
    """
    Fetch news sentiment for multiple tickers in a single API call.

    Alpha Vantage accepts comma-separated tickers, so this is efficient.
    Articles are then split by ticker based on ticker_sentiment entries.

    Args:
        symbols:          List of tickers
        limit_per_symbol: Approximate articles per symbol (total = limit_per_symbol * len(symbols))
        hours_back:       Only consider articles from the last N hours

    Returns:
        {symbol: AVNewsSentiment} dict
    """
    if not symbols:
        return {}

    tickers_str = ",".join(s.upper() for s in symbols)
    total_limit = min(limit_per_symbol * len(symbols), 1000)

    time_from = ""
    if hours_back > 0:
        dt = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        time_from = dt.strftime("%Y%m%dT%H%M")

    data = _fetch_news_raw(tickers=tickers_str, limit=total_limit,
                           time_from=time_from)

    results: Dict[str, AVNewsSentiment] = {}
    feed = data.get("feed", [])

    for sym in symbols:
        sym_upper = sym.upper()
        results[sym_upper] = _extract_ticker_sentiment(sym_upper, feed)

    # Populate headline cache so FinBERT can reuse without extra API calls
    with _cache_lock:
        global _cache_ts
        for sym_upper, av in results.items():
            _headline_cache[sym_upper] = av["headlines"]
        _cache_ts = datetime.now(timezone.utc)

    return results


def fetch_av_headlines(symbol: str, limit: int = 10,
                       hours_back: int = 48) -> List[str]:
    """
    Return headlines for FinBERT consumption.

    Reads from the in-memory cache populated by batch_news_sentiment().
    Only falls back to a live API call if the cache is empty/stale AND
    the caller explicitly needs headlines. This ensures FinBERT never
    silently burns extra API quota.
    """
    sym = symbol.upper()

    # Try cache first
    with _cache_lock:
        if (sym in _headline_cache and _cache_ts
                and (datetime.now(timezone.utc) - _cache_ts).total_seconds()
                < _CACHE_TTL_MINUTES * 60):
            cached = _headline_cache[sym][:limit]
            if cached:
                return cached

    # Cache miss — make a single live call (only path that costs quota)
    result = get_news_sentiment(symbol, limit=limit, hours_back=hours_back)
    return result["headlines"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_ticker_sentiment(symbol: str, feed: list) -> AVNewsSentiment:
    """
    Extract sentiment for a specific ticker from the feed articles.

    Only considers articles where the ticker appears in ticker_sentiment
    with relevance_score > 0.5.
    """
    neutral: AVNewsSentiment = {
        "label": "neutral",
        "score": 0.0,
        "articles": 0,
        "top_headline": "",
        "headlines": [],
        "topics": [],
        "sources": [],
    }

    scores: List[float] = []
    headlines: List[str] = []
    topics_set: set = set()
    sources_set: set = set()
    top_headline = ""

    for article in feed:
        # Check if this ticker is mentioned with sufficient relevance
        ticker_sentiments = article.get("ticker_sentiment", [])
        ticker_match = None
        for ts in ticker_sentiments:
            if ts.get("ticker", "").upper() == symbol:
                relevance = float(ts.get("relevance_score", 0))
                if relevance > 0.5:
                    ticker_match = ts
                break

        if not ticker_match:
            continue

        # Collect score
        score = float(ticker_match.get("ticker_sentiment_score", 0))
        scores.append(score)

        # Build headline text for FinBERT
        title = article.get("title", "").strip()
        summary = article.get("summary", "").strip()
        if title:
            # Combine title + first sentence of summary (like finbert_sentiment does)
            snippet = title
            if summary:
                first_sentence = summary.split(". ")[0]
                if first_sentence and first_sentence != title:
                    snippet = f"{title}. {first_sentence}"
            headlines.append(snippet)

            if not top_headline:
                top_headline = title

        # Collect topics
        for topic in article.get("topics", []):
            topic_name = topic.get("topic", "")
            if topic_name:
                topics_set.add(topic_name)

        # Collect sources
        source = article.get("source", "")
        if source:
            sources_set.add(source)

    if not scores:
        return neutral

    import numpy as np
    avg_score = float(np.mean(scores))

    return AVNewsSentiment(
        label=_score_to_simple_label(avg_score),
        score=round(avg_score, 4),
        articles=len(scores),
        top_headline=top_headline,
        headlines=headlines,
        topics=sorted(topics_set),
        sources=sorted(sources_set),
    )


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG)
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["NVDA", "AAPL"]

    print(f"Fetching Alpha Vantage news sentiment for: {', '.join(symbols)}")
    print("=" * 70)

    results = batch_news_sentiment(symbols, limit_per_symbol=5)
    for sym, r in results.items():
        label_emoji = {
            "bullish": "+", "somewhat_bullish": "~+",
            "neutral": " =", "somewhat_bearish": "~-", "bearish": "-",
        }.get(r["label"], "?")
        print(
            f"  [{label_emoji}] {sym:<8} {r['label']:<18} "
            f"score={r['score']:+.3f}  articles={r['articles']}  "
            f"topics={r['topics']}"
        )
        if r["top_headline"]:
            print(f"         headline: \"{r['top_headline'][:80]}\"")
        print()
