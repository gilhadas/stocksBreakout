"""
quantkit.sentiment.finbert
==========================
FinBERT financial sentiment — ProsusAI/finbert model (~420 MB download on first use).

Requires: pip install transformers torch
The package degrades gracefully if transformers/torch are absent — all functions
return a neutral result with a warning rather than raising ImportError.

Quick start
-----------
    from quantkit.sentiment.finbert import analyze_text, get_ticker_sentiment

    # From pre-fetched headlines:
    result = analyze_text(["NVDA beats earnings estimates", "guidance raised"])
    print(result['label'], result['score'])          # 'bullish'  0.91

    # Full pipeline (fetches via yfinance by default):
    result = get_ticker_sentiment('COIN', headlines=['COIN token surges on ETF news'])

SentimentResult keys
--------------------
    label        : 'bullish' | 'bearish' | 'neutral'
    score        : float 0.0–1.0  (FinBERT avg confidence for dominant label)
    net_score    : float −1.0–+1.0  (bullish_pct − bearish_pct)
    breakdown    : {'bullish': int, 'bearish': int, 'neutral': int}
    headlines    : int  (how many texts were analysed)
    top_headline : str  (first headline in the list)
    emoji        : '🟢' / '🔴' / '⚪'
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, TypedDict

logger = logging.getLogger(__name__)


# ── Types ─────────────────────────────────────────────────────────────────────

class SentimentResult(TypedDict):
    label:        str
    score:        float
    net_score:    float
    breakdown:    dict
    headlines:    int
    top_headline: str
    emoji:        str


_NEUTRAL_RESULT: SentimentResult = {
    'label':        'neutral',
    'score':        0.5,
    'net_score':    0.0,
    'breakdown':    {'bullish': 0, 'bearish': 0, 'neutral': 0},
    'headlines':    0,
    'top_headline': '',
    'emoji':        '⚪',
}

_LABEL_MAP = {'positive': 'bullish', 'negative': 'bearish', 'neutral': 'neutral'}
_EMOJI_MAP = {'bullish': '🟢', 'bearish': '🔴', 'neutral': '⚪'}


# ── Lazy model singleton ──────────────────────────────────────────────────────

_model_lock = threading.Lock()
_finbert_pipeline = None
_model_available: Optional[bool] = None


def _load_finbert():
    """Load FinBERT pipeline once, thread-safe.  Returns None if unavailable."""
    global _finbert_pipeline, _model_available
    if _model_available is False:
        return None
    if _finbert_pipeline is not None:
        return _finbert_pipeline

    with _model_lock:
        if _finbert_pipeline is not None:
            return _finbert_pipeline
        try:
            import logging as _log
            _log.getLogger('transformers').setLevel(_log.ERROR)
            _log.getLogger('huggingface_hub').setLevel(_log.ERROR)
            from transformers import pipeline, logging as _hf
            _hf.set_verbosity_error()
            logger.info("Loading FinBERT (ProsusAI/finbert) — first run downloads ~420 MB …")
            _finbert_pipeline = pipeline(
                task='text-classification',
                model='ProsusAI/finbert',
                tokenizer='ProsusAI/finbert',
                device=-1,          # CPU; set to 0 for CUDA GPU
                truncation=True,
                max_length=512,
            )
            _model_available = True
            logger.info("FinBERT ready.")
        except Exception as exc:
            logger.warning("FinBERT unavailable (install transformers + torch): %s", exc)
            _model_available = False
        return _finbert_pipeline


# ── Core inference ────────────────────────────────────────────────────────────

def analyze_text(text_or_list) -> SentimentResult:
    """Run FinBERT on a string or list of strings; return aggregated sentiment.

    Parameters
    ----------
    text_or_list : str or List[str]
        One or more financial news headlines / snippets.

    Returns
    -------
    SentimentResult dict.  Neutral if no valid input or model unavailable.
    """
    texts = [text_or_list] if isinstance(text_or_list, str) else list(text_or_list)
    texts = [t for t in texts if t and len(t.strip()) > 5]
    if not texts:
        return dict(_NEUTRAL_RESULT)

    finbert = _load_finbert()
    if finbert is None:
        return dict(_NEUTRAL_RESULT)

    try:
        outputs = finbert(texts)
    except Exception as exc:
        logger.warning("FinBERT inference error: %s", exc)
        return dict(_NEUTRAL_RESULT)

    scores_sum = {'bullish': 0.0, 'bearish': 0.0, 'neutral': 0.0}
    counts     = {'bullish': 0,   'bearish': 0,   'neutral': 0}

    for out in outputs:
        lbl = _LABEL_MAP.get(out['label'].lower(), 'neutral')
        scores_sum[lbl] += out['score']
        counts[lbl] += 1

    total    = len(outputs)
    dominant = max(counts, key=lambda k: (counts[k], scores_sum[k]))
    avg_score = scores_sum[dominant] / counts[dominant] if counts[dominant] else 0.5
    n_bull, n_bear = counts['bullish'], counts['bearish']
    net = round((n_bull - n_bear) / total, 3) if total else 0.0

    return SentimentResult(
        label        = dominant,
        score        = round(avg_score, 3),
        net_score    = net,
        breakdown    = dict(counts),
        headlines    = total,
        top_headline = texts[0],
        emoji        = _EMOJI_MAP[dominant],
    )


# ── Headline fetching (yfinance fallback, no project-specific imports) ────────

def fetch_headlines(
    symbol: str, max_count: int = 8, max_age_hours: int = 48
) -> List[str]:
    """Fetch recent news headlines for *symbol* via yfinance.

    Uses title + first sentence of summary for each article to give FinBERT
    more context without exceeding the 512-token window.

    To use a different news source, pass headlines directly to :func:`analyze_text`.

    Parameters
    ----------
    symbol        : Ticker symbol (e.g. 'COIN').
    max_count     : Max articles to return (default 8).
    max_age_hours : Skip articles older than this (default 48 h).

    Returns
    -------
    List[str] of headline strings.
    """
    texts: List[str] = []
    try:
        import yfinance as yf
        news = yf.Ticker(symbol).news or []
    except Exception as exc:
        logger.debug("yfinance news fetch failed for %s: %s", symbol, exc)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    for item in news:
        try:
            content = item.get('content') or item
            title   = content.get('title', '').strip()
            if not title:
                continue

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
                    pass

            summary    = content.get('summary', '').strip()
            first_sent = summary.split('.')[0].strip() if summary else ''
            if first_sent and first_sent.lower() != title.lower()[:len(first_sent)]:
                text = f"{title}. {first_sent}"
            else:
                text = title

            texts.append(text)
            if len(texts) >= max_count:
                break
        except Exception:
            continue

    return texts


# ── Public convenience functions ──────────────────────────────────────────────

def get_ticker_sentiment(
    symbol: str,
    headlines: Optional[List[str]] = None,
    max_headlines: int = 8,
    max_age_hours: int = 48,
) -> SentimentResult:
    """Return FinBERT sentiment for a ticker.

    Parameters
    ----------
    symbol        : Ticker (e.g. 'COIN').
    headlines     : Pre-fetched headlines list.  If None, fetches via yfinance.
    max_headlines : Max articles to fetch (ignored if *headlines* provided).
    max_age_hours : Max headline age in hours (ignored if *headlines* provided).

    Returns
    -------
    SentimentResult dict.
    """
    if headlines is None:
        headlines = fetch_headlines(symbol, max_headlines, max_age_hours)
    result = analyze_text(headlines)
    if headlines and not result['top_headline']:
        result['top_headline'] = headlines[0]
    return result


def batch_sentiment(
    symbols: List[str],
    headlines_map: Optional[Dict[str, List[str]]] = None,
    max_headlines: int = 8,
    max_age_hours: int = 48,
) -> Dict[str, SentimentResult]:
    """Run FinBERT on multiple symbols in a single batched inference call.

    Parameters
    ----------
    symbols       : List of tickers.
    headlines_map : Pre-fetched dict {symbol: [headlines]}.
                    If None, fetches via yfinance for each symbol.
    max_headlines : Max articles per symbol (ignored if headlines_map provided).
    max_age_hours : Max age in hours (ignored if headlines_map provided).

    Returns
    -------
    Dict {symbol: SentimentResult}.
    """
    if headlines_map is None:
        headlines_map = {sym: fetch_headlines(sym, max_headlines, max_age_hours)
                         for sym in symbols}

    finbert = _load_finbert()
    if finbert is None:
        return {sym: dict(_NEUTRAL_RESULT) for sym in symbols}

    # Flatten for single batched call
    flat_texts: List[str] = []
    sym_spans: Dict[str, tuple] = {}
    for sym in symbols:
        start = len(flat_texts)
        flat_texts.extend(headlines_map.get(sym, []))
        sym_spans[sym] = (start, len(flat_texts))

    if not flat_texts:
        return {sym: dict(_NEUTRAL_RESULT) for sym in symbols}

    try:
        all_outputs = finbert(flat_texts)
    except Exception as exc:
        logger.warning("FinBERT batch inference error: %s", exc)
        return {sym: dict(_NEUTRAL_RESULT) for sym in symbols}

    results: Dict[str, SentimentResult] = {}
    for sym in symbols:
        start, end = sym_spans[sym]
        chunk      = all_outputs[start:end]
        hl_list    = headlines_map.get(sym, [])

        if not chunk:
            results[sym] = dict(_NEUTRAL_RESULT)
            continue

        scores_sum = {'bullish': 0.0, 'bearish': 0.0, 'neutral': 0.0}
        counts     = {'bullish': 0,   'bearish': 0,   'neutral': 0}
        for out in chunk:
            lbl = _LABEL_MAP.get(out['label'].lower(), 'neutral')
            scores_sum[lbl] += out['score']
            counts[lbl] += 1

        total     = len(chunk)
        dominant  = max(counts, key=lambda k: (counts[k], scores_sum[k]))
        avg_score = scores_sum[dominant] / counts[dominant] if counts[dominant] else 0.5
        n_bull, n_bear = counts['bullish'], counts['bearish']
        net       = round((n_bull - n_bear) / total, 3) if total else 0.0

        results[sym] = SentimentResult(
            label        = dominant,
            score        = round(avg_score, 3),
            net_score    = net,
            breakdown    = dict(counts),
            headlines    = total,
            top_headline = hl_list[0] if hl_list else '',
            emoji        = _EMOJI_MAP[dominant],
        )

    return results
