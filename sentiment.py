#!/usr/bin/env python3
"""
Sentiment & Sector Buzz Analysis
Ported from StocksAgent — standalone, no LangChain dependency.
Tavily API is optional: functions degrade gracefully without it.
"""

import os
import logging
import yfinance as yf

try:
    import requests
except ImportError:
    requests = None

from config import SENTIMENT

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def get_tavily_api_key():
    """Read Tavily API key from env var or config."""
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        key = SENTIMENT.get("tavily_api_key", "")
    return key


def _tavily_search(query, max_results=3):
    """Search via Tavily REST API. Returns list of result dicts or empty list."""
    api_key = get_tavily_api_key()
    if not api_key or requests is None:
        return []
    try:
        resp = requests.post(
            TAVILY_SEARCH_URL,
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        logger.debug(f"Tavily search failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Ticker → Sector Lookup
# ---------------------------------------------------------------------------

# Built once on first call: {ticker: sector_name}
_TICKER_SECTOR_MAP = None


def _build_ticker_sector_map():
    global _TICKER_SECTOR_MAP
    if _TICKER_SECTOR_MAP is not None:
        return
    _TICKER_SECTOR_MAP = {}
    for sector_name, sector_data in SENTIMENT.get("sector_etfs", {}).items():
        _TICKER_SECTOR_MAP[sector_data["etf"]] = sector_name
        for leader in sector_data.get("leaders", []):
            _TICKER_SECTOR_MAP[leader] = sector_name


def get_sector_for_ticker(ticker):
    """
    Return the sector name for a ticker.
    Checks config leaders first, falls back to yfinance info.
    Returns '' if sector cannot be determined.
    """
    _build_ticker_sector_map()
    sector = _TICKER_SECTOR_MAP.get(ticker.upper())
    if sector:
        return sector

    # yfinance fallback — map its sector strings to our names
    try:
        info = yf.Ticker(ticker).info
        yf_sector = info.get("sector", "")
        if not yf_sector:
            return ""
        # Map common yfinance sector names to our config names
        mapping = {
            "Technology": "Technology",
            "Energy": "Energy",
            "Financial Services": "Finance",
            "Healthcare": "Healthcare",
            "Consumer Cyclical": "Consumer",
            "Consumer Defensive": "Consumer",
            "Industrials": "Industrial",
            "Real Estate": "Real Estate",
            "Basic Materials": "Materials",
            "Communication Services": "Technology",
            "Utilities": "Industrial",
        }
        sector = mapping.get(yf_sector, yf_sector)
        # Cache for future lookups
        _TICKER_SECTOR_MAP[ticker.upper()] = sector
        return sector
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Sector Buzz Analysis
# ---------------------------------------------------------------------------

# Keywords used for news search per sector
_SECTOR_KEYWORDS = {
    "Technology": "AI stocks semiconductor tech rally",
    "Energy": "oil price crude oil energy stocks",
    "Finance": "bank stocks financial sector interest rates",
    "Healthcare": "biotech pharma healthcare",
    "Consumer": "retail consumer spending e-commerce",
    "Industrial": "manufacturing aerospace industrial",
    "Real Estate": "real estate REIT property",
    "Materials": "commodities materials mining",
}


def get_sector_buzz():
    """
    Analyze sector buzz using ETF performance data (yfinance) and optional
    Tavily news search.

    Returns:
        dict with keys:
            spy: dict with 5d/20d returns
            sectors: list of dicts sorted by buzz score descending
    """
    sectors_cfg = SENTIMENT.get("sector_etfs", {})
    if not sectors_cfg:
        return {"spy": {}, "sectors": []}

    try:
        # SPY baseline
        spy = yf.Ticker("SPY")
        spy_hist = spy.history(period="1mo")

        if spy_hist.empty:
            logger.warning("Unable to fetch SPY data")
            return {"spy": {}, "sectors": []}

        spy_current = spy_hist["Close"].iloc[-1]
        spy_5d = spy_hist["Close"].iloc[-5] if len(spy_hist) >= 5 else spy_current
        spy_20d = spy_hist["Close"].iloc[-20] if len(spy_hist) >= 20 else spy_current

        spy_5d_ret = ((spy_current - spy_5d) / spy_5d) * 100
        spy_20d_ret = ((spy_current - spy_20d) / spy_20d) * 100

        sector_scores = []

        for sector_name, sector_data in sectors_cfg.items():
            try:
                etf_ticker = sector_data["etf"]
                etf = yf.Ticker(etf_ticker)
                etf_hist = etf.history(period="1mo")

                if etf_hist.empty:
                    continue

                current_price = etf_hist["Close"].iloc[-1]
                price_5d = etf_hist["Close"].iloc[-5] if len(etf_hist) >= 5 else current_price
                price_20d = etf_hist["Close"].iloc[-20] if len(etf_hist) >= 20 else current_price

                return_5d = ((current_price - price_5d) / price_5d) * 100
                return_20d = ((current_price - price_20d) / price_20d) * 100

                rs_5d = return_5d - spy_5d_ret
                rs_20d = return_20d - spy_20d_ret

                avg_volume = etf_hist["Volume"].tail(20).mean()
                current_volume = etf_hist["Volume"].iloc[-1]
                volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

                # --- Scoring (0-10) ---
                score = 0

                # 1. Price Performance (0-4)
                price_score = 0
                if return_5d > 3:
                    price_score += 2
                elif return_5d > 1:
                    price_score += 1
                if return_20d > 5:
                    price_score += 2
                elif return_20d > 2:
                    price_score += 1
                score += min(4, price_score)

                # 2. Volume (0-3)
                if volume_ratio > 1.5:
                    score += 3
                elif volume_ratio > 1.2:
                    score += 2
                elif volume_ratio > 1.0:
                    score += 1

                # 3. Relative Strength vs SPY (0-2)
                if rs_5d > 1:
                    score += 1
                if rs_20d > 2:
                    score += 1

                # 4. News mentions (0-1) — requires Tavily
                keyword = _SECTOR_KEYWORDS.get(sector_name, sector_name)
                results = _tavily_search(f"{keyword} today", max_results=3)
                if len(results) >= 2:
                    score += 1

                # Sentiment label
                if return_5d > 1 and return_20d > 2:
                    sentiment = "bullish"
                elif return_5d < -1 or return_20d < -2:
                    sentiment = "bearish"
                else:
                    sentiment = "neutral"

                sector_scores.append({
                    "sector": sector_name,
                    "etf": etf_ticker,
                    "buzz": score,
                    "return_5d": round(return_5d, 2),
                    "return_20d": round(return_20d, 2),
                    "rs_5d": round(rs_5d, 2),
                    "rs_20d": round(rs_20d, 2),
                    "volume_ratio": round(volume_ratio, 2),
                    "sentiment": sentiment,
                })

            except Exception:
                continue

        sector_scores.sort(key=lambda x: x["buzz"], reverse=True)

        return {
            "spy": {
                "return_5d": round(spy_5d_ret, 2),
                "return_20d": round(spy_20d_ret, 2),
            },
            "sectors": sector_scores,
        }

    except Exception as e:
        logger.error(f"Sector buzz error: {e}")
        return {"spy": {}, "sectors": []}


def format_sector_buzz(data):
    """Pretty-print sector buzz data for CLI output."""
    spy = data.get("spy", {})
    sectors = data.get("sectors", [])

    lines = []
    lines.append("=" * 70)
    lines.append(" SECTOR BUZZ REPORT")
    lines.append("=" * 70)
    if spy:
        lines.append(
            f" Market (SPY):  5D: {spy.get('return_5d', 0):+.2f}%  |  "
            f"20D: {spy.get('return_20d', 0):+.2f}%"
        )
    lines.append("-" * 70)

    for s in sectors:
        bar = "#" * s["buzz"] + "." * (10 - s["buzz"])
        tag = "HOT" if s["buzz"] >= 7 else "ACT" if s["buzz"] >= 5 else "  -"
        sent = s["sentiment"][0].upper()  # B / N / b
        lines.append(
            f" [{tag}] {s['sector']:12s} ({s['etf']})  [{bar}] {s['buzz']:2d}/10  "
            f"5D:{s['return_5d']:+6.2f}%  20D:{s['return_20d']:+6.2f}%  "
            f"RS:{s['rs_5d']:+5.2f}%  Vol:{s['volume_ratio']:.2f}x  [{sent}]"
        )

    lines.append("-" * 70)
    lines.append(" HOT=7+ | ACT=5-6 | RS=Relative Strength vs SPY | Vol=vs 20d avg")
    lines.append(" Sentiment: [B]ullish [N]eutral [b]earish")
    lines.append("=" * 70)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Individual Ticker Sentiment
# ---------------------------------------------------------------------------

def check_sentiment(ticker):
    """
    Check web sentiment for a single ticker via Tavily search.

    Returns dict:
        ticker, mentions, sentiment, buzz_score, positive_signals,
        negative_signals, summary
    """
    neutral = {
        "ticker": ticker,
        "mentions": 0,
        "sentiment": "neutral",
        "buzz_score": 0,
        "positive_signals": 0,
        "negative_signals": 0,
        "summary": "No Tavily API key — sentiment unavailable",
    }

    api_key = get_tavily_api_key()
    if not api_key:
        return neutral

    try:
        results = _tavily_search(f"{ticker} stock price news today", max_results=3)

        if not results:
            neutral["summary"] = "No recent mentions found"
            return neutral

        mention_count = len(results)

        # Combine content for keyword analysis
        content = " ".join(
            r.get("content", "") + " " + r.get("title", "") for r in results
        ).lower()

        # Verify ticker is actually mentioned
        if ticker.lower() not in content:
            neutral["summary"] = "No specific mentions found"
            return neutral

        positive_kw = [
            "bullish", "buy", "moon", "rocket", "breakout", "surge",
            "rally", "strong", "upgrade", "gain", "up", "rise",
        ]
        negative_kw = [
            "bearish", "sell", "crash", "drop", "fall", "downgrade",
            "weak", "decline", "loss", "down",
        ]

        pos = sum(content.count(kw) for kw in positive_kw)
        neg = sum(content.count(kw) for kw in negative_kw)

        if pos > neg * 1.5:
            sentiment = "bullish"
        elif neg > pos * 1.5:
            sentiment = "bearish"
        else:
            sentiment = "neutral"

        # Buzz = sentiment strength (keyword hits) + mention coverage
        buzz_score = min(10, (pos + neg) * 2 + mention_count)

        summary = f"Found {mention_count} recent mentions. "
        if sentiment == "bullish":
            summary += "Positive sentiment detected."
        elif sentiment == "bearish":
            summary += "Negative sentiment detected."
        else:
            summary += "Mixed sentiment."

        return {
            "ticker": ticker,
            "mentions": mention_count,
            "sentiment": sentiment,
            "buzz_score": buzz_score,
            "positive_signals": pos,
            "negative_signals": neg,
            "summary": summary,
        }

    except Exception as e:
        logger.error(f"Sentiment check error for {ticker}: {e}")
        neutral["summary"] = f"Error: {e}"
        return neutral


if __name__ == "__main__":
    print("--- Sector Buzz ---")
    buzz = get_sector_buzz()
    print(format_sector_buzz(buzz))
    print()
    print("--- Ticker Sentiment (NVDA) ---")
    print(check_sentiment("NVDA"))
