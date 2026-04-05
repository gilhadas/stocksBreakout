#!/usr/bin/env python3
"""
alphavantage_backtest.py
========================
Compare scanner performance WITH and WITHOUT Alpha Vantage news sentiment.

Methodology
-----------
1. Run a 30-day (configurable) breakout scan → collect HIGH+ signals
2. For each (symbol, signal_date) fetch Alpha Vantage NEWS_SENTIMENT with
   a ±2 day window → cache results to disk forever (no re-fetching)
3. Use AV sentiment as:
   a) Extra headlines for FinBERT (more data → better FinBERT accuracy)
   b) AV pre-scored sentiment for signal filtering / promotion
4. Run SimulationMode on 4 configurations side-by-side:
     A. Baseline HIGH+              — no sentiment, original quality
     B. Baseline PREMIUM+           — no sentiment, stricter filter
     C. AV-enriched HIGH+           — AV headlines fed to FinBERT → promotion
     D. AV-filtered PREMIUM+        — drop signals with AV bearish sentiment
5. Print comparison table + per-signal AV attribution

DATA CACHING
  Alpha Vantage free tier = 25 calls/day, so we cache aggressively to disk.
  First run for N symbols will need N calls (1 per unique symbol in the
  signal date window). Subsequent runs hit cache — 0 API calls.
  Cache dir: scanner_output/cache/alphavantage/

Usage
-----
  venv/bin/python alphavantage_backtest.py                             # last 30 trading days
  venv/bin/python alphavantage_backtest.py --start 2026-03-05 --end 2026-04-04
  venv/bin/python alphavantage_backtest.py --watchlist input/MAGS.txt --capital 50000
  venv/bin/python alphavantage_backtest.py --no-av                     # baseline only (no AV)
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import warnings
warnings.filterwarnings('ignore', message='.*unauthenticated.*')

# ── Event loop for ib_insync-adjacent imports ────────────────────────────────
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

sys.path.insert(0, str(Path(__file__).parent))

from enhanced_backtest import (
    fetch_all_data,
    load_symbols,
    run_all_scans,
    run_simulation,
    calculate_spy_benchmark,
)
from config import FINBERT_PROMOTION


# ---------------------------------------------------------------------------
# Alpha Vantage historical news (cached to disk)
# ---------------------------------------------------------------------------

_AV_CACHE_DIR = Path('scanner_output/cache/alphavantage')
_AV_BASE_URL = 'https://www.alphavantage.co/query'


def _get_av_api_key() -> str:
    return os.environ.get('ALPHA_VANTAGE_API_KEY', '')


def fetch_av_news_for_date(
    symbol: str,
    date: 'pd.Timestamp',
    api_key: str,
    window_days: int = 2,
    limit: int = 20,
) -> dict:
    """Fetch Alpha Vantage NEWS_SENTIMENT for a symbol around a signal date.

    Returns raw AV response dict (with 'feed' list). Cached to disk
    to avoid burning API quota on repeat runs.

    Args:
        symbol:      Ticker (e.g. 'NVDA')
        date:        Signal date (pd.Timestamp)
        api_key:     Alpha Vantage API key
        window_days: Days before signal date to include (default: 2)
        limit:       Max articles to fetch (default: 20)

    Returns:
        dict with 'feed' key → list of article dicts, or empty dict on error
    """
    import requests

    from_date = (date - pd.Timedelta(days=window_days)).strftime('%Y%m%dT0000')
    to_date = (date + pd.Timedelta(days=1)).strftime('%Y%m%dT2359')

    _AV_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _AV_CACHE_DIR / f"{symbol}_{from_date}_{to_date}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except Exception:
            pass

    try:
        r = requests.get(
            _AV_BASE_URL,
            params={
                'function': 'NEWS_SENTIMENT',
                'tickers': symbol,
                'time_from': from_date,
                'time_to': to_date,
                'limit': limit,
                'sort': 'RELEVANCE',
                'apikey': api_key,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        print(f"    [AV] {symbol} on {from_date}→{to_date}: {exc}")
        return {}

    # Check for rate limit or error
    if 'Error Message' in data or 'Note' in data or 'Information' in data:
        msg = data.get('Error Message') or data.get('Note') or data.get('Information', '')
        print(f"    [AV] API note: {msg[:80]}")
        return {}

    # Cache to disk
    cache_file.write_text(json.dumps(data, indent=2))
    time.sleep(3.5)  # 25 calls/day → ~3.5s between calls is safe pacing
    return data


def extract_av_sentiment(symbol: str, av_data: dict) -> dict:
    """Extract per-ticker sentiment from an AV response.

    Returns:
        dict with keys: label, score, articles, headlines, top_headline, topics
    """
    from alphavantage_news import _extract_ticker_sentiment
    feed = av_data.get('feed', [])
    return _extract_ticker_sentiment(symbol.upper(), feed)


# ---------------------------------------------------------------------------
# FinBERT integration (optional — uses AV headlines as input)
# ---------------------------------------------------------------------------

_FINBERT_OK = False
try:
    from finbert_sentiment import analyze_text
    _FINBERT_OK = True
except ImportError:
    pass


def _run_finbert_on_headlines(headlines: List[str]) -> Optional[dict]:
    """Run FinBERT on a list of headline strings. Returns SentimentResult or None."""
    if not _FINBERT_OK or not headlines:
        return None
    try:
        return analyze_text(headlines)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# AV enrichment + promotion pipeline
# ---------------------------------------------------------------------------

def enrich_with_av(
    signals: List[Dict],
    api_key: str,
    window_days: int = 2,
    use_finbert: bool = True,
) -> Tuple[List[Dict], Dict]:
    """Fetch AV news for each signal, apply FinBERT promotion.

    For each unique (symbol, date) pair:
      1. Fetch AV NEWS_SENTIMENT (cached)
      2. Extract AV sentiment + headlines
      3. Run FinBERT on AV headlines (if available)
      4. Apply FINBERT_PROMOTION rules

    Returns:
        enriched_signals: copy of signals with av_* and finbert_* keys
        av_sentiments:    {symbol: av_sentiment_dict}
    """
    h2p = FINBERT_PROMOTION['high_to_premium']
    p2g = FINBERT_PROMOTION['premium_to_gold']
    enabled = FINBERT_PROMOTION.get('enabled', True)

    unique_pairs = sorted(
        {(s['symbol'], s.get('date')) for s in signals},
        key=lambda x: str(x[1]),
    )
    print(f"\n  [AV] Fetching news for {len(unique_pairs)} (symbol, date) pairs…")

    # Cache per-pair results in memory
    pair_cache: Dict = {}
    av_sentiments: Dict = {}

    for sym, sig_date in unique_pairs:
        av_data = fetch_av_news_for_date(sym, sig_date, api_key, window_days)
        av_sent = extract_av_sentiment(sym, av_data)
        pair_cache[(sym, sig_date)] = {
            'av': av_sent,
            'finbert': None,
        }
        av_sentiments[sym] = av_sent

        # Run FinBERT on AV headlines if available
        if use_finbert and av_sent['articles'] > 0 and av_sent['headlines']:
            fb = _run_finbert_on_headlines(av_sent['headlines'])
            pair_cache[(sym, sig_date)]['finbert'] = fb

        # Progress log
        fb_info = pair_cache[(sym, sig_date)]['finbert']
        av_label = av_sent['label'] if av_sent['articles'] > 0 else 'no-news'
        fb_label = fb_info['label'] if fb_info else '—'
        date_str = sig_date.strftime('%Y-%m-%d') if hasattr(sig_date, 'strftime') else str(sig_date)[:10]
        print(f"    {sym:<8} {date_str}  AV={av_label:<18} "
              f"score={av_sent['score']:+.3f}  arts={av_sent['articles']}  "
              f"FinBERT={fb_label}")

    # Enrich and promote signals
    enriched: List[Dict] = []
    for sig in signals:
        s = dict(sig)
        cache_entry = pair_cache.get((s['symbol'], s.get('date')))
        if not cache_entry:
            enriched.append(s)
            continue

        av = cache_entry['av']
        fb = cache_entry['finbert']

        # Add AV fields
        s['av_label'] = av['label']
        s['av_score'] = av['score']
        s['av_articles'] = av['articles']
        s['av_top_headline'] = av['top_headline'][:80]
        s['av_topics'] = ','.join(av['topics'][:5])

        # Add FinBERT fields + promotion
        if fb:
            s['finbert_label'] = fb['label']
            s['finbert_score'] = fb['score']
            s['finbert_net'] = fb['net_score']
            s['finbert_headline'] = fb['top_headline'][:80]
            n_headlines = sum(fb['breakdown'].values())

            if enabled and fb['label'] == 'bullish':
                q = s.get('quality', '')
                h2p_min_hl = h2p.get('min_headlines', 1)
                p2g_min_hl = p2g.get('min_headlines', 1)
                if (q == 'HIGH' and fb['score'] >= h2p['min_score']
                        and fb['net_score'] >= h2p['min_net']
                        and n_headlines >= h2p_min_hl):
                    s['quality'] = 'PREMIUM'
                    s['finbert_promoted'] = 'HIGH→PREMIUM'
                elif (q == 'PREMIUM' and fb['score'] >= p2g['min_score']
                      and fb['net_score'] >= p2g['min_net']
                      and n_headlines >= p2g_min_hl):
                    s['quality'] = 'GOLD'
                    s['finbert_promoted'] = 'PREMIUM→GOLD'

        enriched.append(s)

    return enriched, av_sentiments


# ---------------------------------------------------------------------------
# Filtering strategies
# ---------------------------------------------------------------------------

_HIGH_PLUS = {'HIGH', 'PREMIUM', 'GOLD'}
_PREMIUM_PLUS = {'PREMIUM', 'GOLD'}


def _filter(signals, tiers):
    return [s for s in signals if s.get('quality') in tiers]


def _filter_av_bearish(signals, tiers):
    """Like _filter but also drops signals with bearish AV sentiment."""
    return [
        s for s in signals
        if s.get('quality') in tiers
        and s.get('av_label') not in ('bearish', 'somewhat_bearish')
    ]


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def _perf_row(label, report, signal_count):
    if report is None:
        return f"  {label:<38} {'—':>8} {'—':>8} {'—':>10} {'—':>8}  (no trades)"
    tc = report.get('total_trades', 0)
    wr = report.get('win_rate', 0)
    ret = report.get('total_return', 0)
    sh = report.get('sharpe_ratio', 0)
    dd = report.get('max_drawdown', 0)
    return (
        f"  {label:<38} {ret:>+7.1f}%  {sh:>7.2f}  "
        f"{dd:>+9.1f}%  {wr:>6.1f}%  [{tc} trades / {signal_count} sigs]"
    )


def _print_av_attribution(enriched_sigs, historical):
    """Show per-signal AV sentiment vs actual outcome."""
    sigs_with_av = [s for s in enriched_sigs if s.get('av_articles', 0) > 0]
    if not sigs_with_av:
        print("\n  No signals had Alpha Vantage news data.")
        return

    print(f"\n  {'Symbol':<8} {'Date':<12} {'Quality':<10} "
          f"{'AV Label':<18} {'AV Score':>9} {'Arts':>4}  "
          f"{'Promoted':<18} {'Outcome':<14}  Headline")
    print("  " + "─" * 130)

    for sig in sorted(sigs_with_av, key=lambda s: str(s.get('date', ''))):
        sym = sig['symbol']
        sig_date = sig.get('date')
        quality = sig.get('quality', '?')
        av_label = sig.get('av_label', '?')
        av_score = sig.get('av_score', 0)
        av_arts = sig.get('av_articles', 0)
        promoted = sig.get('finbert_promoted', '—')
        headline = sig.get('av_top_headline', '')[:50]
        entry = sig.get('price', 0)

        # Actual price outcome
        outcome = '?'
        try:
            df = historical.get(sym)
            if df is not None and sig_date is not None:
                future = df[df.index > sig_date]
                if len(future) >= 5 and entry > 0:
                    last = future['close'].iloc[-1]
                    ret_pct = (last - entry) / entry * 100
                    tp = sig.get('take_profit', 0)
                    sl = sig.get('stop_loss', 0)
                    peak = future['high'].iloc[:20].max()
                    trough = future['low'].iloc[:20].min()
                    if tp > 0 and peak >= tp:
                        outcome = f'+TP ({ret_pct:+.1f}%)'
                    elif sl > 0 and trough <= sl:
                        outcome = f'-SL ({ret_pct:+.1f}%)'
                    else:
                        outcome = f'open ({ret_pct:+.1f}%)'
        except Exception:
            pass

        date_str = sig_date.strftime('%Y-%m-%d') if hasattr(sig_date, 'strftime') else str(sig_date)[:10]
        print(
            f"  {sym:<8} {date_str:<12} {quality:<10} "
            f"{av_label:<18} {av_score:>+9.3f} {av_arts:>4}  "
            f"{promoted:<18} {outcome:<14}  {headline}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    today = datetime.today()
    default_end = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    default_start = (today - timedelta(days=42)).strftime('%Y-%m-%d')

    p = argparse.ArgumentParser(
        description='Alpha Vantage news sentiment backtest vs baseline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument('--start', default=default_start, help='Start date YYYY-MM-DD')
    p.add_argument('--end', default=default_end, help='End date YYYY-MM-DD')
    p.add_argument('--capital', type=int, default=100_000, help='Initial capital')
    p.add_argument('--watchlist', default=None, help='Watchlist file')
    p.add_argument('--mode', default='swing', choices=['swing', 'longterm', 'daytrade'])
    p.add_argument('--no-av', action='store_true', help='Skip Alpha Vantage — baseline only')
    p.add_argument('--no-finbert', action='store_true',
                   help='Skip FinBERT — use raw AV scores only (no promotion)')
    p.add_argument('--limit', type=int, default=0, help='Cap number of symbols (0 = no cap)')
    p.add_argument('--window', type=int, default=2,
                   help='News window days before signal date (default: 2)')
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = _parse_args()
    cap = args.capital

    av_key = _get_av_api_key()

    print("\n" + "═" * 75)
    print("  ALPHA VANTAGE NEWS SENTIMENT BACKTEST")
    print("═" * 75)
    print(f"  Period   : {args.start} → {args.end}")
    print(f"  Capital  : ${cap:,}")
    print(f"  Mode     : {args.mode}")

    if args.no_av or not av_key:
        if not av_key:
            print("  AV News  : UNAVAILABLE (set ALPHA_VANTAGE_API_KEY) — baseline only")
        else:
            print("  AV News  : disabled (--no-av)")
        run_av = False
    else:
        cached_count = len(list(_AV_CACHE_DIR.glob('*.json'))) if _AV_CACHE_DIR.exists() else 0
        print(f"  AV News  : enabled | window=±{args.window}d | cache={cached_count} files")
        if not args.no_finbert and _FINBERT_OK:
            print(f"  FinBERT  : enabled (AV headlines → FinBERT → promotion)")
        elif args.no_finbert:
            print(f"  FinBERT  : disabled (--no-finbert) — raw AV scores only")
        else:
            print(f"  FinBERT  : UNAVAILABLE (pip install transformers torch)")
        run_av = True
    print("─" * 75)

    # 1 — Load symbols
    symbols = load_symbols(args.watchlist, limit=args.limit)
    print(f"\n  Symbols loaded: {len(symbols)}")

    # 2 — Fetch historical price data
    print("\n  Fetching historical price data…")
    historical, end_prices = fetch_all_data(symbols, args.start, args.end)

    # 3 — Run breakout scan
    all_signals = run_all_scans(historical, args.start, args.end, modes=[args.mode])
    baseline_raw = all_signals.get('v5x', [])

    baseline_high = _filter(baseline_raw, _HIGH_PLUS)
    baseline_prem = _filter(baseline_raw, _PREMIUM_PLUS)

    print(f"\n  Baseline signals: {len(baseline_raw)} total  |  "
          f"{len(baseline_high)} HIGH+  |  {len(baseline_prem)} PREMIUM+")

    # Quality breakdown
    qual_counts = {}
    for s in baseline_raw:
        q = s.get('quality', 'UNKNOWN')
        qual_counts[q] = qual_counts.get(q, 0) + 1
    for q in ('GOLD', 'PREMIUM', 'HIGH', 'STANDARD'):
        if q in qual_counts:
            print(f"      {q:<10}: {qual_counts[q]}")

    # 4 — AV enrichment
    if run_av:
        enriched_sigs, av_sentiments = enrich_with_av(
            baseline_high,
            api_key=av_key,
            window_days=args.window,
            use_finbert=not args.no_finbert and _FINBERT_OK,
        )
        n_with_news = sum(1 for s in enriched_sigs if s.get('av_articles', 0) > 0)
        n_bullish = sum(1 for s in enriched_sigs
                        if s.get('av_label') in ('bullish', 'somewhat_bullish'))
        n_bearish = sum(1 for s in enriched_sigs
                        if s.get('av_label') in ('bearish', 'somewhat_bearish'))
        n_promoted = sum(1 for s in enriched_sigs if s.get('finbert_promoted'))

        print(f"\n  AV results: {n_with_news}/{len(enriched_sigs)} signals with news")
        print(f"  AV sentiment: {n_bullish} bullish / {n_bearish} bearish "
              f"/ {len(enriched_sigs) - n_bullish - n_bearish} neutral/no-news")
        if not args.no_finbert and _FINBERT_OK:
            print(f"  FinBERT promotions: {n_promoted}")

        av_high = _filter(enriched_sigs, _HIGH_PLUS)
        av_prem = _filter(enriched_sigs, _PREMIUM_PLUS)
        av_filtered_prem = _filter_av_bearish(enriched_sigs, _PREMIUM_PLUS)
    else:
        enriched_sigs = baseline_high
        av_high = baseline_high
        av_prem = baseline_prem
        av_filtered_prem = baseline_prem

    # 5 — SPY benchmark
    spy_historical = dict(historical)
    if 'SPY' in historical:
        spy_full = historical['SPY']
        spy_trimmed = spy_full[spy_full.index >= pd.to_datetime(args.start)]
        spy_historical['SPY'] = spy_trimmed
    spy_result = calculate_spy_benchmark(spy_historical, args.start, args.end, cap)

    # 6 — Run simulations
    print("\n  Running simulations…")

    result_a = run_simulation(baseline_high, args.start, args.end, end_prices, historical, cap)
    result_b = run_simulation(baseline_prem, args.start, args.end, end_prices, historical, cap)

    if run_av:
        result_c = run_simulation(av_high, args.start, args.end, end_prices, historical, cap)
        result_d = run_simulation(av_prem, args.start, args.end, end_prices, historical, cap)
        result_e = run_simulation(av_filtered_prem, args.start, args.end, end_prices, historical, cap)
    else:
        result_c = result_d = result_e = None

    # 7 — Comparison table
    print("\n" + "═" * 75)
    print("  PERFORMANCE COMPARISON")
    print("─" * 75)
    print(f"  {'Config':<38} {'Return':>8}  {'Sharpe':>7}  "
          f"{'Drawdown':>9}  {'WinRate':>7}  Trades")
    print("  " + "─" * 73)

    if spy_result:
        print(f"  {'SPY buy-and-hold':<38} {spy_result['total_return']:>+7.1f}%  "
              f"{spy_result['sharpe_ratio']:>7.2f}  "
              f"{spy_result['max_drawdown']:>+9.1f}%  {'—':>7}")

    print("  " + "─" * 73)
    print(_perf_row('A: Baseline HIGH+ (no news)', result_a, len(baseline_high)))
    print(_perf_row('B: Baseline PREMIUM+ (no news)', result_b, len(baseline_prem)))

    if run_av:
        print("  " + "─" * 73)
        print(_perf_row('C: AV-enriched HIGH+ (w/ promotion)', result_c, len(av_high)))
        print(_perf_row('D: AV-enriched PREMIUM+', result_d, len(av_prem)))
        print(_perf_row('E: AV-filtered PREMIUM+ (drop bearish)', result_e, len(av_filtered_prem)))

    print("  " + "─" * 73)

    # Delta summary
    if run_av and result_a and result_e:
        delta_ret = (result_e.get('total_return', 0) or 0) - (result_a.get('total_return', 0) or 0)
        delta_sh = (result_e.get('sharpe_ratio', 0) or 0) - (result_a.get('sharpe_ratio', 0) or 0)
        delta_wr = (result_e.get('win_rate', 0) or 0) - (result_a.get('win_rate', 0) or 0)
        arrow = '↑' if delta_ret > 0 else '↓'
        print(f"\n  AV-filtered PREMIUM+ vs Baseline HIGH+:")
        print(f"    Return: {delta_ret:>+.1f}%  {arrow}   |   "
              f"Sharpe: {delta_sh:>+.2f}   |   Win rate: {delta_wr:>+.1f}%")

    if run_av and result_a and result_c:
        delta_ret = (result_c.get('total_return', 0) or 0) - (result_a.get('total_return', 0) or 0)
        arrow = '↑' if delta_ret > 0 else '↓'
        print(f"\n  AV-enriched HIGH+ vs Baseline HIGH+:")
        print(f"    Return: {delta_ret:>+.1f}%  {arrow}")

    # 8 — Per-signal AV attribution
    if run_av:
        print("\n" + "═" * 75)
        print("  PER-SIGNAL ALPHA VANTAGE ATTRIBUTION")
        print("─" * 75)
        _print_av_attribution(enriched_sigs, historical)

    # 9 — AV sentiment by outcome analysis
    if run_av:
        _print_sentiment_vs_outcome(enriched_sigs, historical)

    print("\n" + "═" * 75 + "\n")


def _print_sentiment_vs_outcome(enriched_sigs, historical):
    """Analyze: do AV-bullish signals outperform AV-bearish/neutral?"""
    buckets = {
        'AV Bullish': [],
        'AV Neutral/No-news': [],
        'AV Bearish': [],
    }

    for sig in enriched_sigs:
        av_label = sig.get('av_label', 'neutral')
        entry = sig.get('price', 0)
        sig_date = sig.get('date')
        sym = sig['symbol']

        if entry <= 0 or sig_date is None:
            continue

        # Calculate 20-day forward return
        try:
            df = historical.get(sym)
            if df is None:
                continue
            future = df[df.index > sig_date]
            if len(future) < 5:
                continue
            lookforward = min(20, len(future))
            last = future['close'].iloc[lookforward - 1]
            ret = (last - entry) / entry * 100
        except Exception:
            continue

        if av_label in ('bullish', 'somewhat_bullish'):
            buckets['AV Bullish'].append(ret)
        elif av_label in ('bearish', 'somewhat_bearish'):
            buckets['AV Bearish'].append(ret)
        else:
            buckets['AV Neutral/No-news'].append(ret)

    print("\n" + "─" * 75)
    print("  AV SENTIMENT vs FORWARD RETURNS (20-day)")
    print("─" * 75)
    print(f"  {'Bucket':<24} {'Count':>6} {'Avg Ret':>9} {'Win Rate':>9} {'Median':>8}")
    print("  " + "─" * 58)

    for label, returns in buckets.items():
        if not returns:
            print(f"  {label:<24} {'0':>6} {'—':>9} {'—':>9} {'—':>8}")
            continue
        arr = np.array(returns)
        avg = arr.mean()
        wr = (arr > 0).sum() / len(arr) * 100
        med = np.median(arr)
        print(f"  {label:<24} {len(arr):>6} {avg:>+8.2f}% {wr:>8.1f}% {med:>+7.2f}%")


if __name__ == '__main__':
    main()
