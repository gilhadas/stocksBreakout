"""
finbert_sentiment.py
====================
Professional-grade financial sentiment analysis using ProsusAI/FinBERT.

FinBERT is a BERT model fine-tuned on ~70k financial news sentences from Reuters
and Bloomberg. It consistently outperforms general-purpose BERT and keyword-matching
on financial text because it understands domain-specific phrases:
  "beat earnings estimates"  → positive  (general BERT: ambiguous)
  "guidance cut"             → negative
  "raised full-year outlook" → positive

Model: ProsusAI/finbert (~420 MB, downloaded once to ~/.cache/huggingface/)
Output classes: positive / negative / neutral → mapped to bullish / bearish / neutral

Public API:
    get_ticker_sentiment(symbol)          → SentimentResult dict
    batch_sentiment(symbols)              → {symbol: SentimentResult}
    analyze_text(text_or_list)            → SentimentResult
    fetch_headlines(symbol, max_count=8)  → List[str]

SentimentResult keys:
    label        : 'bullish' | 'bearish' | 'neutral'
    score        : float 0.0-1.0  (FinBERT confidence for the dominant label)
    net_score    : float -1.0–1.0 (bullish_pct - bearish_pct; sign = direction, magnitude = conviction)
    breakdown    : {'bullish': int, 'bearish': int, 'neutral': int}
    headlines    : int  (how many headlines were analysed)
    top_headline : str  (most recent headline)
    emoji        : str  (🟢 / 🔴 / ⚪)

Usage:
    from finbert_sentiment import get_ticker_sentiment, batch_sentiment

    r = get_ticker_sentiment('COIN')
    print(r['label'], r['score'], r['top_headline'])

    results = batch_sentiment(['COIN', 'MSTR', 'IBIT'])
    for sym, r in results.items():
        print(f"{sym}: {r['emoji']} {r['label']} ({r['score']:.2f}) — {r['top_headline'][:60]}")
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Dict, List, Optional, TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class SentimentResult(TypedDict):
    label: str          # 'bullish' | 'bearish' | 'neutral'
    score: float        # FinBERT avg confidence for dominant label (0–1)
    net_score: float    # bullish_pct − bearish_pct (−1 to +1)
    breakdown: dict     # {'bullish': int, 'bearish': int, 'neutral': int}
    headlines: int      # number of headlines analysed
    top_headline: str   # most recent headline text
    emoji: str          # 🟢 / 🔴 / ⚪


_NEUTRAL_RESULT: SentimentResult = {
    'label': 'neutral',
    'score': 0.5,
    'net_score': 0.0,
    'breakdown': {'bullish': 0, 'bearish': 0, 'neutral': 0},
    'headlines': 0,
    'top_headline': '',
    'emoji': '⚪',
}


# ---------------------------------------------------------------------------
# Model loading (lazy, thread-safe singleton)
# ---------------------------------------------------------------------------

_model_lock = threading.Lock()
_finbert_pipeline = None
_model_available: Optional[bool] = None   # None = not yet checked


def _load_finbert():
    """Load FinBERT pipeline once, return it (or None if unavailable)."""
    global _finbert_pipeline, _model_available
    if _model_available is False:
        return None
    if _finbert_pipeline is not None:
        return _finbert_pipeline

    with _model_lock:
        if _finbert_pipeline is not None:
            return _finbert_pipeline
        try:
            import logging as _logging
            _logging.getLogger('transformers').setLevel(_logging.ERROR)
            _logging.getLogger('huggingface_hub').setLevel(_logging.ERROR)
            from transformers import pipeline, logging as _hf_logging
            _hf_logging.set_verbosity_error()   # suppresses LOAD REPORT table
            logger.info("Loading FinBERT (ProsusAI/finbert) — first run downloads ~420 MB …")
            _finbert_pipeline = pipeline(
                task='text-classification',
                model='ProsusAI/finbert',
                tokenizer='ProsusAI/finbert',
                device=-1,          # CPU; set to 0 for GPU
                truncation=True,
                max_length=512,
            )
            _model_available = True
            logger.info("FinBERT ready.")
        except Exception as exc:
            logger.warning(f"FinBERT unavailable (install transformers + torch): {exc}")
            _model_available = False
        return _finbert_pipeline


# ---------------------------------------------------------------------------
# News headline fetching
# ---------------------------------------------------------------------------

def fetch_headlines(symbol: str, max_count: int = 8,
                    max_age_hours: int = 48) -> List[str]:
    """Fetch recent news headlines for *symbol* via yfinance.

    Combines ``title`` + first sentence of ``summary`` for each article to give
    FinBERT more context without overflowing the 512-token window.

    Args:
        symbol:        Ticker (e.g. 'COIN')
        max_count:     Max number of articles to return (default 8)
        max_age_hours: Skip articles older than this (default 48 h)

    Returns:
        List of headline strings (title [+ summary snippet])
    """
    texts: List[str] = []

    # Primary source: Alpaca (Benzinga) — highest-quality headline feed.
    # Pulled from cache first so we don't make extra API calls per FinBERT run.
    try:
        from alpaca_news import fetch_alpaca_headlines_cached
        alp = fetch_alpaca_headlines_cached(
            symbol, limit=max_count, hours_back=max_age_hours,
        )
        if alp:
            texts.extend(alp[:max_count])
    except Exception as exc:
        logger.debug(f"Alpaca headline fetch skipped for {symbol}: {exc}")

    if len(texts) >= max_count:
        return texts[:max_count]

    try:
        import yfinance as yf
        news = yf.Ticker(symbol).news or []
    except Exception as exc:
        logger.debug(f"yfinance news fetch failed for {symbol}: {exc}")
        news = []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    for item in news:
        try:
            # yfinance ≥0.2.50 wraps data under 'content'
            content = item.get('content') or item
            title = content.get('title', '').strip()
            if not title:
                continue

            # Age filter
            pub_raw = content.get('pubDate') or content.get('providerPublishTime', '')
            if pub_raw:
                try:
                    if isinstance(pub_raw, (int, float)):
                        pub_dt = datetime.fromtimestamp(pub_raw, tz=timezone.utc)
                    else:
                        pub_dt = datetime.fromisoformat(pub_raw.replace('Z', '+00:00'))
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pass  # Can't parse — include anyway

            # Append a snippet of the summary for extra context (FinBERT benefit)
            summary = content.get('summary', '').strip()
            if summary:
                # Take the first sentence only (avoid blowing the token budget)
                first_sent = summary.split('.')[0].strip()
                if first_sent and first_sent.lower() != title.lower()[:len(first_sent)]:
                    text = f"{title}. {first_sent}"
                else:
                    text = title
            else:
                text = title

            texts.append(text)
            if len(texts) >= max_count:
                break

        except Exception:
            continue

    # Supplement with Alpha Vantage headlines if yfinance returned fewer than max_count
    if len(texts) < max_count:
        try:
            from alphavantage_news import fetch_av_headlines
            av_headlines = fetch_av_headlines(
                symbol, limit=max_count - len(texts),
                hours_back=max_age_hours
            )
            # Deduplicate by checking if title prefix already exists
            existing_titles = {t.split('.')[0].lower().strip() for t in texts}
            for h in av_headlines:
                if len(texts) >= max_count:
                    break
                h_prefix = h.split('.')[0].lower().strip()
                if h_prefix not in existing_titles:
                    texts.append(h)
                    existing_titles.add(h_prefix)
        except Exception as exc:
            logger.debug(f"Alpha Vantage headline supplement skipped: {exc}")

    return texts


# ---------------------------------------------------------------------------
# Core inference
# ---------------------------------------------------------------------------

_LABEL_MAP = {'positive': 'bullish', 'negative': 'bearish', 'neutral': 'neutral'}
_EMOJI_MAP = {'bullish': '🟢', 'bearish': '🔴', 'neutral': '⚪'}


def analyze_text(text_or_list) -> SentimentResult:
    """Run FinBERT on a string or list of strings and return aggregated sentiment.

    Each input string is treated as one inference unit (headline + summary snippet).
    The aggregate is:
      - dominant label = label with highest total weighted score
      - score          = mean FinBERT confidence for the dominant label's items
      - net_score      = (bullish_count − bearish_count) / total
    """
    if isinstance(text_or_list, str):
        texts = [text_or_list]
    else:
        texts = list(text_or_list)

    texts = [t for t in texts if t and len(t.strip()) > 5]
    if not texts:
        return dict(_NEUTRAL_RESULT)

    finbert = _load_finbert()
    if finbert is None:
        return dict(_NEUTRAL_RESULT)

    try:
        outputs = finbert(texts)
    except Exception as exc:
        logger.warning(f"FinBERT inference error: {exc}")
        return dict(_NEUTRAL_RESULT)

    scores_sum = {'bullish': 0.0, 'bearish': 0.0, 'neutral': 0.0}
    counts     = {'bullish': 0,   'bearish': 0,   'neutral': 0}

    for out in outputs:
        lbl = _LABEL_MAP.get(out['label'].lower(), 'neutral')
        scores_sum[lbl] += out['score']
        counts[lbl] += 1

    total = len(outputs)
    dominant = max(counts, key=lambda k: (counts[k], scores_sum[k]))
    avg_score = scores_sum[dominant] / counts[dominant] if counts[dominant] else 0.5

    n_bull, n_bear = counts['bullish'], counts['bearish']
    net = round((n_bull - n_bear) / total, 3) if total else 0.0

    return SentimentResult(
        label=dominant,
        score=round(avg_score, 3),
        net_score=net,
        breakdown=dict(counts),
        headlines=total,
        top_headline=texts[0],
        emoji=_EMOJI_MAP[dominant],
    )


# ---------------------------------------------------------------------------
# Public convenience functions
# ---------------------------------------------------------------------------

def get_ticker_sentiment(
    symbol: str,
    max_headlines: int = 8,
    max_age_hours: int = 48,
) -> SentimentResult:
    """Fetch headlines for *symbol* and return FinBERT sentiment.

    Example:
        r = get_ticker_sentiment('COIN')
        # {'label': 'bullish', 'score': 0.84, 'net_score': 0.5, ...}
    """
    headlines = fetch_headlines(symbol, max_headlines, max_age_hours)
    result = analyze_text(headlines)
    # Guarantee top_headline is the actual first headline (not snippet)
    if headlines and not result['top_headline']:
        result['top_headline'] = headlines[0]
    return result


def batch_sentiment(
    symbols: List[str],
    max_headlines: int = 8,
    max_age_hours: int = 48,
) -> Dict[str, SentimentResult]:
    """Run FinBERT on multiple symbols and return a dict of results.

    Headlines are fetched per-symbol (yfinance) and all batched into a single
    FinBERT call for efficiency, then results are split back per-symbol.

    Example:
        results = batch_sentiment(['COIN', 'MSTR', 'IBIT'])
    """
    # Collect headlines per symbol
    symbol_headlines: Dict[str, List[str]] = {}
    for sym in symbols:
        symbol_headlines[sym] = fetch_headlines(sym, max_headlines, max_age_hours)

    # Flatten into one list for a single batched inference
    finbert = _load_finbert()
    if finbert is None:
        return {sym: dict(_NEUTRAL_RESULT) for sym in symbols}

    # Build flat list with symbol tags for re-association
    flat_texts: List[str] = []
    sym_spans: Dict[str, tuple] = {}   # sym → (start_idx, end_idx)
    for sym, headlines in symbol_headlines.items():
        start = len(flat_texts)
        flat_texts.extend(headlines)
        sym_spans[sym] = (start, len(flat_texts))

    if not flat_texts:
        return {sym: dict(_NEUTRAL_RESULT) for sym in symbols}

    try:
        all_outputs = finbert(flat_texts)
    except Exception as exc:
        logger.warning(f"FinBERT batch inference error: {exc}")
        return {sym: dict(_NEUTRAL_RESULT) for sym in symbols}

    # Split results back per symbol
    results: Dict[str, SentimentResult] = {}
    for sym in symbols:
        start, end = sym_spans[sym]
        chunk = all_outputs[start:end]
        headlines = symbol_headlines[sym]

        if not chunk:
            results[sym] = dict(_NEUTRAL_RESULT)
            continue

        scores_sum = {'bullish': 0.0, 'bearish': 0.0, 'neutral': 0.0}
        counts     = {'bullish': 0,   'bearish': 0,   'neutral': 0}
        for out in chunk:
            lbl = _LABEL_MAP.get(out['label'].lower(), 'neutral')
            scores_sum[lbl] += out['score']
            counts[lbl] += 1

        total = len(chunk)
        dominant = max(counts, key=lambda k: (counts[k], scores_sum[k]))
        avg_score = scores_sum[dominant] / counts[dominant] if counts[dominant] else 0.5
        n_bull, n_bear = counts['bullish'], counts['bearish']
        net = round((n_bull - n_bear) / total, 3) if total else 0.0

        results[sym] = SentimentResult(
            label=dominant,
            score=round(avg_score, 3),
            net_score=net,
            breakdown=dict(counts),
            headlines=total,
            top_headline=headlines[0] if headlines else '',
            emoji=_EMOJI_MAP[dominant],
        )

    return results


# ---------------------------------------------------------------------------
# CLI for quick testing
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse, sys
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

    parser = argparse.ArgumentParser(description='FinBERT sentiment for stock tickers')
    parser.add_argument('symbols', nargs='+', help='Ticker symbols (e.g. COIN MSTR IBIT)')
    parser.add_argument('--headlines', type=int, default=8, help='Max headlines per ticker')
    parser.add_argument('--age', type=int, default=48, help='Max headline age in hours')
    parser.add_argument('--verbose', action='store_true', help='Show each headline and its label')
    args = parser.parse_args()

    results = batch_sentiment(args.symbols, args.headlines, args.age)
    print(f"\n{'Symbol':<8} {'Sentiment':<10} {'Score':>6} {'Net':>7} {'Bull':>5} {'Bear':>5} {'Neu':>5}  Top Headline")
    print('─' * 110)
    for sym, r in results.items():
        bk = r['breakdown']
        hl = r['top_headline'][:55] + ('…' if len(r['top_headline']) > 55 else '')
        print(f"{r['emoji']} {sym:<6} {r['label']:<10} {r['score']:>6.3f} {r['net_score']:>+7.3f} "
              f"{bk['bullish']:>5} {bk['bearish']:>5} {bk['neutral']:>5}  {hl}")

        if args.verbose:
            for headline in fetch_headlines(sym, args.headlines, args.age):
                out = _load_finbert()([headline])[0]
                lbl = _LABEL_MAP.get(out['label'].lower(), 'neutral')
                print(f"         {_EMOJI_MAP[lbl]} [{out['score']:.2f}] {headline[:80]}")
            print()
    print()
