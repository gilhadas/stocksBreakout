#!/usr/bin/env python3
"""
congress_trades.py
==================
Congressional trade disclosure enrichment (STOCK Act filings).

Provides buys/sells counts and net direction for a symbol over a configurable
lookback window. Used as a *confirmation* signal alongside news catalysts —
the 45-day STOCK Act disclosure lag means this is not a leading indicator, but
clusters of buys from committee-relevant lawmakers can corroborate a setup.

Data sources (tried in order):
    1. Quiver Quantitative (paid, authoritative) — if QUIVER_API_KEY is set
    2. CapitolTrades BFF (free, unofficial) — otherwise

Public API:
    get_congress_summary(symbol, days=90)            -> CongressSummary | None
    batch_congress_summary(symbols, days=90)         -> {symbol: CongressSummary}

Environment:
    QUIVER_API_KEY      — optional paid override (https://www.quiverquant.com/)
    CONGRESS_CACHE_TTL  — seconds, default 86400 (24h)
"""

from __future__ import annotations

import json
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

_CAPITOL_TRADES_URL = "https://bff.capitoltrades.com/trades"
_QUIVER_URL = "https://api.quiverquant.com/beta/historical/congresstrading/{symbol}"
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "scanner_output", "cache", "congress")
_DEFAULT_TTL = int(os.environ.get("CONGRESS_CACHE_TTL", "86400"))
_UA = "stocksBreakout/congress_trades (github.com/stocksBreakout)"


class CongressSummary(TypedDict):
    symbol: str
    buys: int
    sells: int
    net: int                       # buys - sells
    top_politician: str            # lawmaker with the most trades in window
    recent_dates: List[str]        # up to 5 most-recent trade dates (ISO)
    source: str                    # 'quiver' | 'capitoltrades' | 'cache'
    window_days: int


def _cache_path(symbol: str, days: int) -> str:
    return os.path.join(_CACHE_DIR, f"{symbol.upper()}_{days}d.json")


def _load_cache(symbol: str, days: int, ttl: int) -> Optional[CongressSummary]:
    path = _cache_path(symbol, days)
    if not os.path.exists(path):
        return None
    try:
        age = time.time() - os.path.getmtime(path)
        if age > ttl:
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["source"] = "cache"
        return data  # type: ignore[return-value]
    except Exception as e:
        logger.debug(f"congress cache read failed for {symbol}: {e}")
        return None


def _save_cache(summary: CongressSummary) -> None:
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        path = _cache_path(summary["symbol"], summary["window_days"])
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f)
    except Exception as e:
        logger.debug(f"congress cache write failed: {e}")


def _classify(txn_type: str) -> str:
    """Normalize transaction type strings to 'buy' / 'sell' / 'other'."""
    t = (txn_type or "").lower()
    if "purchase" in t or "buy" in t:
        return "buy"
    if "sale" in t or "sell" in t:
        return "sell"
    return "other"


def _summarize(symbol: str, trades: List[Dict], days: int, source: str) -> CongressSummary:
    buys = sells = 0
    by_politician: Dict[str, int] = {}
    dates: List[str] = []
    for t in trades:
        side = _classify(t.get("type", ""))
        if side == "buy":
            buys += 1
        elif side == "sell":
            sells += 1
        pol = t.get("politician") or ""
        if pol:
            by_politician[pol] = by_politician.get(pol, 0) + 1
        d = t.get("date", "")
        if d:
            dates.append(d)
    top_pol = max(by_politician, key=by_politician.get) if by_politician else ""
    dates.sort(reverse=True)
    return CongressSummary(
        symbol=symbol.upper(),
        buys=buys,
        sells=sells,
        net=buys - sells,
        top_politician=top_pol,
        recent_dates=dates[:5],
        source=source,
        window_days=days,
    )


def _fetch_quiver(symbol: str, days: int, api_key: str) -> Optional[List[Dict]]:
    if requests is None:
        return None
    try:
        resp = requests.get(
            _QUIVER_URL.format(symbol=symbol.upper()),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": _UA,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json() or []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
        trades = []
        for r in data:
            d_str = r.get("TransactionDate") or r.get("Date") or ""
            try:
                d = datetime.strptime(d_str[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            if d < cutoff:
                continue
            trades.append({
                "date": d.isoformat(),
                "type": r.get("Transaction") or r.get("Type") or "",
                "politician": r.get("Representative") or r.get("Senator") or r.get("Name") or "",
            })
        return trades
    except Exception as e:
        logger.debug(f"Quiver fetch failed for {symbol}: {e}")
        return None


def _fetch_capitoltrades(symbol: str, days: int) -> Optional[List[Dict]]:
    """
    Fetch trades from CapitolTrades' unofficial BFF endpoint.

    Schema observed (Apr 2026):
        { "data": [ { "txDate": "YYYY-MM-DD", "txType": "Purchase"|"Sale",
                      "politician": {"fullName": "..."}, "asset": {...} }, ... ] }
    """
    if requests is None:
        return None
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    try:
        resp = requests.get(
            _CAPITOL_TRADES_URL,
            params={
                "assetTicker": symbol.upper(),
                "pageSize": 100,
                "sortBy": "-txDate",
            },
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        payload = resp.json() or {}
    except Exception as e:
        logger.debug(f"CapitolTrades fetch failed for {symbol}: {e}")
        return None

    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []

    trades: List[Dict] = []
    for r in rows:
        d_str = r.get("txDate") or r.get("transactionDate") or ""
        try:
            d = datetime.strptime(d_str[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if d < cutoff:
            continue
        pol = ""
        p = r.get("politician")
        if isinstance(p, dict):
            pol = p.get("fullName") or (
                f"{p.get('firstName', '')} {p.get('lastName', '')}".strip()
            )
        trades.append({
            "date": d.isoformat(),
            "type": r.get("txType") or r.get("transactionType") or "",
            "politician": pol,
        })
    return trades


def get_congress_summary(
    symbol: str,
    days: int = 90,
    use_cache: bool = True,
    ttl: int = _DEFAULT_TTL,
) -> Optional[CongressSummary]:
    """Return Congressional trade summary for one symbol, or None on failure."""
    if use_cache:
        cached = _load_cache(symbol, days, ttl)
        if cached is not None:
            return cached

    api_key = os.environ.get("QUIVER_API_KEY", "").strip()
    trades: Optional[List[Dict]] = None
    source = ""
    if api_key:
        trades = _fetch_quiver(symbol, days, api_key)
        source = "quiver"
    if trades is None:
        trades = _fetch_capitoltrades(symbol, days)
        source = "capitoltrades"
    if trades is None:
        return None

    summary = _summarize(symbol, trades, days, source)
    _save_cache(summary)
    return summary


def batch_congress_summary(
    symbols: List[str],
    days: int = 90,
    throttle_sec: float = 0.5,
    use_cache: bool = True,
) -> Dict[str, CongressSummary]:
    """Fetch summaries sequentially. Paces to be polite to the unofficial BFF."""
    out: Dict[str, CongressSummary] = {}
    for i, sym in enumerate(symbols):
        s = get_congress_summary(sym, days=days, use_cache=use_cache)
        if s is not None:
            out[sym.upper()] = s
        if throttle_sec > 0 and i < len(symbols) - 1 and (s is None or s.get("source") != "cache"):
            time.sleep(throttle_sec)
    return out


def _parse_watchlist(path: str) -> List[str]:
    syms: List[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            for tok in line.split(","):
                s = tok.strip().split(":")[-1].strip().upper()
                if s and not s.startswith("#"):
                    syms.append(s)
    return syms


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="*", help="Tickers to look up")
    ap.add_argument("--watchlist", help="Path to watchlist file (comma/line separated)")
    ap.add_argument("--days", type=int, default=90, help="Lookback window (default 90)")
    ap.add_argument("--rebuild-cache", action="store_true", help="Ignore cache and refetch")
    ap.add_argument("--min-net", type=int, default=0,
                    help="Only display symbols with net buys >= this (default 0)")
    args = ap.parse_args()

    syms: List[str] = []
    if args.symbols:
        syms.extend(s.upper() for s in args.symbols)
    if args.watchlist:
        syms.extend(_parse_watchlist(args.watchlist))
    if not syms:
        syms = ["NVDA", "TSLA", "PLTR", "AAPL"]

    print(f"Fetching Congress trades ({args.days}d) for {len(syms)} symbol(s)…")
    print("=" * 78)
    results = batch_congress_summary(syms, days=args.days, use_cache=not args.rebuild_cache)
    for sym in syms:
        s = results.get(sym)
        if not s:
            print(f"  {sym:<6}  (no data)")
            continue
        if s["net"] < args.min_net and s["buys"] == 0:
            continue
        tag = "BUY" if s["net"] >= 2 else ("buy" if s["net"] >= 1 else ("neutral" if s["net"] == 0 else "sell"))
        print(
            f"  [{tag:<7}] {sym:<6}  buys={s['buys']:<2}  sells={s['sells']:<2}  "
            f"net={s['net']:+d}  top={s['top_politician'][:24]:<24}  src={s['source']}"
        )
