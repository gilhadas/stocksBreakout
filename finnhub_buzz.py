"""
finnhub_buzz.py — thin re-export shim
======================================
Canonical implementation lives in quantkit/sentiment/finnhub.py.
This module preserves backward-compatible imports for all project files
that do `from finnhub_buzz import ...`.
"""
from quantkit.sentiment.finnhub import (  # noqa: F401
    FinnhubBuzz,
    get_buzz,
    batch_buzz,
)
