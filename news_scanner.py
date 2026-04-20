#!/usr/bin/env python3
"""
news_scanner.py
===============
Discover tickers with active bullish news and write them to a watchlist
file consumable by breakout_scanner.py.

Pulls the Alpha Vantage NEWS_SENTIMENT feed (last N hours, curated topics),
scores each ticker by (article_count × avg_sentiment × avg_relevance), and
exports the top-K bullish names. Pairs with `--require-catalyst` on the
scanner so we only act on names with both a technical setup AND fresh news.

Usage:
    python news_scanner.py --out scanner_output/lists/news_watch.txt --top 30
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('news_scanner')

try:
    from alphavantage_news import _fetch_news_raw  # type: ignore
except ImportError:
    _fetch_news_raw = None


_DEFAULT_TOPICS = (
    'earnings,technology,ipo,mergers_and_acquisitions,'
    'financial_markets,blockchain,energy_transportation,life_sciences'
)

# Tickers we never want surfaced as "news movers" — indices, broad ETFs,
# mega-caps that dominate every feed regardless of idiosyncratic news.
_EXCLUDE = {
    'SPY', 'QQQ', 'DIA', 'IWM', 'VOO', 'VTI', 'IVV', 'VEA', 'EFA', 'EEM',
    'TLT', 'HYG', 'LQD', 'GLD', 'SLV', 'USO', 'UNG',
}


def discover_news_movers(
    hours_back: int = 18,
    min_articles: int = 2,
    min_avg_score: float = 0.20,
    min_relevance: float = 0.55,
    top_k: int = 30,
    topics: str = _DEFAULT_TOPICS,
) -> List[Dict]:
    """Return a list of tickers with recent bullish news, sorted by strength.

    Each entry: {symbol, articles, avg_score, avg_relevance, rank_score, top_headline}
    """
    if _fetch_news_raw is None:
        logger.error('alphavantage_news not importable — cannot discover news movers')
        return []

    if not os.environ.get('ALPHA_VANTAGE_API_KEY'):
        logger.error('ALPHA_VANTAGE_API_KEY not set — aborting')
        return []

    time_from = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime('%Y%m%dT%H%M')
    data = _fetch_news_raw(topics=topics, time_from=time_from, limit=1000, sort='LATEST')
    feed = data.get('feed', []) if isinstance(data, dict) else []
    if not feed:
        logger.warning('Alpha Vantage returned no articles')
        return []

    logger.info(f'Fetched {len(feed)} articles from last {hours_back}h')

    # Aggregate per ticker
    agg: Dict[str, Dict] = defaultdict(lambda: {
        'scores': [], 'relevances': [], 'headlines': [],
    })
    for article in feed:
        title = (article.get('title') or '').strip()
        for ts in article.get('ticker_sentiment', []) or []:
            sym = (ts.get('ticker') or '').upper().strip()
            if not sym or sym in _EXCLUDE:
                continue
            # Skip crypto (e.g., CRYPTO:BTC) and forex (FOREX:USD) — we only trade equities
            if ':' in sym:
                continue
            try:
                score = float(ts.get('ticker_sentiment_score', 0) or 0)
                relevance = float(ts.get('relevance_score', 0) or 0)
            except (TypeError, ValueError):
                continue
            if relevance < min_relevance:
                continue
            agg[sym]['scores'].append(score)
            agg[sym]['relevances'].append(relevance)
            if title:
                agg[sym]['headlines'].append(title)

    movers: List[Dict] = []
    for sym, d in agg.items():
        n = len(d['scores'])
        if n < min_articles:
            continue
        avg_score = sum(d['scores']) / n
        if avg_score < min_avg_score:
            continue
        avg_rel = sum(d['relevances']) / n
        # Rank = magnitude × breadth × relevance. Log-damps article count so
        # one-off mega-news stories don't get drowned by universally-covered names.
        import math
        rank = avg_score * math.log1p(n) * avg_rel
        movers.append({
            'symbol': sym,
            'articles': n,
            'avg_score': round(avg_score, 3),
            'avg_relevance': round(avg_rel, 3),
            'rank_score': round(rank, 3),
            'top_headline': d['headlines'][0][:120] if d['headlines'] else '',
        })

    movers.sort(key=lambda m: m['rank_score'], reverse=True)
    return movers[:top_k]


def write_watchlist(movers: List[Dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open('w', encoding='utf-8') as f:
        for m in movers:
            f.write(m['symbol'] + '\n')


def main() -> int:
    ap = argparse.ArgumentParser(description='Discover tickers with bullish news and export a watchlist.')
    ap.add_argument('--out', default='scanner_output/lists/news_watch.txt',
                    help='Output watchlist path (default: scanner_output/lists/news_watch.txt)')
    ap.add_argument('--top', type=int, default=30, help='Max tickers to export')
    ap.add_argument('--hours', type=int, default=18, help='Look back this many hours')
    ap.add_argument('--min-articles', type=int, default=2, help='Minimum articles per ticker')
    ap.add_argument('--min-score', type=float, default=0.20,
                    help='Minimum average ticker_sentiment_score (0.15 = somewhat-bullish, 0.35 = bullish)')
    ap.add_argument('--min-relevance', type=float, default=0.55,
                    help='Minimum per-article relevance_score to count')
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s | %(levelname)s | %(message)s',
    )

    movers = discover_news_movers(
        hours_back=args.hours,
        min_articles=args.min_articles,
        min_avg_score=args.min_score,
        min_relevance=args.min_relevance,
        top_k=args.top,
    )

    if not movers:
        logger.warning('No bullish news movers found — writing empty watchlist')
        write_watchlist([], Path(args.out))
        return 0

    out_path = Path(args.out)
    write_watchlist(movers, out_path)

    logger.info(f'Wrote {len(movers)} tickers to {out_path}')
    logger.info('Top movers:')
    for m in movers[:10]:
        logger.info(
            f"  {m['symbol']:<6}  articles={m['articles']:<2}  "
            f"score={m['avg_score']:+.2f}  rel={m['avg_relevance']:.2f}  "
            f"rank={m['rank_score']:+.2f}  \"{m['top_headline'][:70]}\""
        )
    return 0


if __name__ == '__main__':
    sys.exit(main())
