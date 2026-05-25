#!/usr/bin/env python3
"""
finnhub_buzz.py
===============
Finnhub "buzz" signal — is a ticker getting unusual news attention?

Note: Finnhub's /news-sentiment endpoint is premium-only (403 on free tier), so
we compute the same concept ourselves from /company-news (free, 60 req/min):

    articles_last_week  = count of articles in last 7 days
    weekly_baseline     = avg articles/week over the preceding 4 weeks
    buzz_ratio          = articles_last_week / weekly_baseline

A buzz_ratio >= 1.5 means "this name is getting ~50% more coverage than its
baseline" — a catalyst-present flag independent of sentiment tone.

Public API:
    get_buzz(symbol)                -> FinnhubBuzz | None
    batch_buzz(symbols, throttle=1.1) -> {symbol: FinnhubBuzz}

Environment:
    FINNHUB_API_KEY — free at https://finnhub.io/register
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, TypedDict

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

_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"


class FinnhubBuzz(TypedDict):
    articles_last_week: int
    weekly_average: float
    buzz_ratio: float          # articles_last_week / weekly_average (1.0 = baseline)


def _get_api_key() -> str:
    return os.environ.get("FINNHUB_API_KEY", "")


def get_buzz(symbol: str, baseline_weeks: int = 4) -> Optional[FinnhubBuzz]:
    """
    Return buzz metrics for one symbol, or None on failure.

    Fetches last-week article count from Finnhub /company-news and compares
    against the preceding `baseline_weeks`-week average. One HTTP call covers
    both windows (we just split the returned articles by publish date).
    """
    api_key = _get_api_key()
    if not api_key:
        logger.debug("FINNHUB_API_KEY not set — skipping buzz fetch")
        return None
    if requests is None:
        logger.debug("requests not installed — cannot fetch Finnhub buzz")
        return None

    today = datetime.now(timezone.utc).date()
    last_week_start = today - timedelta(days=7)
    baseline_start = today - timedelta(days=7 * (baseline_weeks + 1))
    baseline_end = last_week_start - timedelta(days=1)

    # Finnhub caps /company-news at 250 results per call, so query the two
    # windows separately — otherwise mega-caps (NVDA, TSLA) max out the last
    # window and the baseline comes back empty.
    def _count(start_iso: str, end_iso: str) -> Optional[int]:
        try:
            resp = requests.get(
                _COMPANY_NEWS_URL,
                params={
                    "symbol": symbol.upper(),
                    "from": start_iso,
                    "to": end_iso,
                    "token": api_key,
                },
                timeout=12,
            )
            resp.raise_for_status()
            arts = resp.json() or []
            return len(arts) if isinstance(arts, list) else None
        except Exception as e:
            logger.debug(f"Finnhub buzz fetch failed for {symbol} {start_iso}..{end_iso}: {e}")
            return None

    last_week = _count(last_week_start.isoformat(), today.isoformat())
    if last_week is None:
        return None

    # Pace between the two calls to stay well under 60 req/min when batching.
    time.sleep(0.2)
    baseline_total = _count(baseline_start.isoformat(), baseline_end.isoformat())
    if baseline_total is None:
        return None

    weekly_avg = (baseline_total / baseline_weeks) if baseline_weeks > 0 else 0.0
    buzz_ratio = (last_week / weekly_avg) if weekly_avg > 0 else 0.0

    return FinnhubBuzz(
        articles_last_week=last_week,
        weekly_average=round(weekly_avg, 2),
        buzz_ratio=round(buzz_ratio, 2),
    )


def batch_buzz(symbols: List[str], throttle_sec: float = 1.1) -> Dict[str, FinnhubBuzz]:
    """
    Fetch buzz for each symbol sequentially.

    Free tier is 60 req/min — we pace at ~55 req/min by default. If you have a
    paid plan, drop throttle_sec to 0 for parallel-safe bursts.
    """
    out: Dict[str, FinnhubBuzz] = {}
    for i, sym in enumerate(symbols):
        if i > 0 and throttle_sec > 0:
            time.sleep(throttle_sec)
        b = get_buzz(sym)
        if b is not None:
            out[sym.upper()] = b
    return out


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)
    syms = sys.argv[1:] if len(sys.argv) > 1 else ["NVDA", "TSLA", "SMCI"]
    print(f"Fetching Finnhub buzz for: {', '.join(syms)}")
    print("=" * 70)
    result = batch_buzz(syms)
    for s, b in result.items():
        tag = "HOT" if b["buzz_ratio"] >= 1.5 else ("warm" if b["buzz_ratio"] >= 1.0 else "quiet")
        print(
            f"  [{tag:<5}] {s:<6}  last_week={b['articles_last_week']:<3}  "
            f"baseline={b['weekly_average']:<6.1f}  ratio={b['buzz_ratio']:.2f}"
        )
