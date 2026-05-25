"""
quantkit.sentiment
==================
Financial sentiment analysis via FinBERT and Finnhub buzz signals.

Optional extras
---------------
FinBERT requires torch + transformers:
    pip install quantkit[sentiment]
    # or: pip install transformers torch

Finnhub requires requests:
    pip install quantkit[data]          # included by default
"""
from quantkit.sentiment.finbert import (
    SentimentResult,
    analyze_text,
    get_ticker_sentiment,
    batch_sentiment,
    fetch_headlines,
)
from quantkit.sentiment.finnhub import (
    FinnhubBuzz,
    get_buzz,
    batch_buzz,
)

__all__ = [
    # FinBERT
    "SentimentResult",
    "analyze_text",
    "get_ticker_sentiment",
    "batch_sentiment",
    "fetch_headlines",
    # Finnhub
    "FinnhubBuzz",
    "get_buzz",
    "batch_buzz",
]
