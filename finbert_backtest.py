#!/usr/bin/env python3
"""
finbert_backtest.py
===================
Compare scanner performance with and without FinBERT quality promotion.

Methodology
-----------
1. Run a 30-day (configurable) breakout scan using the same infrastructure
   as enhanced_backtest.py (v5x variant — composite momentum + overextension filter)
2. For each signal, fetch CURRENT FinBERT sentiment via yfinance headlines
3. Apply FINBERT_PROMOTION rules  →  produce a "promoted" signal set
4. Run SimulationMode on 3 configurations side-by-side:
     A. Baseline HIGH+    — all HIGH/PREMIUM/GOLD, original quality + original sizing
     B. FinBERT HIGH+     — same signals, FinBERT-promoted quality (promoted signals
                            get larger positions: HIGH→PREMIUM = 2x → 3x capital)
     C. FinBERT PREMIUM+  — only PREMIUM/GOLD after promotion (stricter filter,
                            includes FinBERT-promoted HIGHs, excludes plain HIGHs)
5. Print:
     • Side-by-side performance table (return, Sharpe, drawdown, win rate)
     • Per-signal promotion attribution (which symbols were promoted + outcome)
     • SPY buy-and-hold benchmark

⚠️  DATA LIMITATION
  yfinance only provides the ~10 most recent news articles per ticker.
  Historical headlines from e.g. Feb 1 2026 are NOT available via this source.
  This backtest uses CURRENT FinBERT sentiment — testing the hypothesis:
    "Stocks persistently in a bullish news environment produce better breakout signals."
  This is a valid but biased proxy. A true historical news backtest would require a
  paid news API (Finnhub, NewsAPI, GDELT).

Usage
-----
  venv/bin/python finbert_backtest.py                              # last 30 trading days
  venv/bin/python finbert_backtest.py --start 2026-02-04 --end 2026-03-05
  venv/bin/python finbert_backtest.py --watchlist input/MAGS.txt --capital 50000
  venv/bin/python finbert_backtest.py --mode swing --headlines 12
  venv/bin/python finbert_backtest.py --no-sentiment               # baseline scan only (no FinBERT)
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import warnings
warnings.filterwarnings('ignore', message='.*unauthenticated.*')
warnings.filterwarnings('ignore', message='.*HF_TOKEN.*')

import numpy as np
import pandas as pd

# ── Ensure event loop before ib_insync-adjacent imports ──────────────────────
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

_FINBERT_OK = False
try:
    from finbert_sentiment import batch_sentiment, analyze_text
    _FINBERT_OK = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Finnhub historical news
# ---------------------------------------------------------------------------

_FINNHUB_CACHE_DIR = Path('scanner_output/cache/finnhub')
_FINNHUB_BASE_URL  = 'https://finnhub.io/api/v1/company-news'


def fetch_finnhub_headlines(
    symbol: str,
    date: 'pd.Timestamp',
    api_key: str,
    window_days: int = 2,
) -> List[str]:
    """Fetch news headlines from Finnhub for a given signal date.

    Uses a ±window_days window around `date` so pre-market / after-hours
    news on adjacent days is captured.  Results are cached forever on disk
    (Finnhub has historical data — re-fetching is wasteful).

    Args:
        symbol:      Stock ticker (e.g. 'AAPL')
        date:        Signal date (pd.Timestamp)
        api_key:     Finnhub API key (free at finnhub.io — 60 req/min)
        window_days: Days before signal date to include (default: 2)

    Returns:
        List of headline strings (title [+ first sentence of summary])
    """
    import json, time
    import requests as _req

    from_date = (date - pd.Timedelta(days=window_days)).strftime('%Y-%m-%d')
    to_date   = (date + pd.Timedelta(days=1)).strftime('%Y-%m-%d')

    _FINNHUB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _FINNHUB_CACHE_DIR / f"{symbol}_{from_date}_{to_date}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except Exception:
            pass

    articles: list = []
    for attempt in range(4):
        try:
            r = _req.get(
                _FINNHUB_BASE_URL,
                params={'symbol': symbol, 'from': from_date, 'to': to_date, 'token': api_key},
                timeout=10,
            )
            if r.status_code == 429:
                # Free tier is 60 req/min; back off exponentially and retry.
                backoff = 5 * (2 ** attempt)   # 5s, 10s, 20s, 40s
                print(f"    [Finnhub] {symbol} 429 — backing off {backoff}s (attempt {attempt + 1}/4)")
                time.sleep(backoff)
                continue
            r.raise_for_status()
            articles = r.json() or []
            break
        except Exception as exc:
            print(f"    [Finnhub] {symbol} on {from_date}→{to_date}: {exc}")
            time.sleep(1.2)
            return []

    headlines: List[str] = []
    for a in articles[:10]:
        title   = (a.get('headline') or '').strip()
        summary = (a.get('summary')  or '').strip()
        if not title:
            continue
        if summary:
            first_sent = summary.split('.')[0].strip()
            if first_sent and first_sent.lower() != title.lower()[:len(first_sent)]:
                text = f"{title}. {first_sent}"
            else:
                text = title
        else:
            text = title
        headlines.append(text)

    cache_file.write_text(json.dumps(headlines))
    # Pace at ~55 req/min — free tier ceiling is 60 req/min.
    time.sleep(1.2)
    return headlines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    today = datetime.today()
    # Default: last ~30 trading days (≈42 calendar days)
    default_end   = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    default_start = (today - timedelta(days=42)).strftime('%Y-%m-%d')

    p = argparse.ArgumentParser(
        description='FinBERT quality-promotion backtest vs baseline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument('--start',     default=default_start, help='Start date YYYY-MM-DD')
    p.add_argument('--end',       default=default_end,   help='End date   YYYY-MM-DD')
    p.add_argument('--capital',   type=int, default=100_000, help='Initial capital (default: 100 000)')
    p.add_argument('--watchlist', default=None, help='Watchlist file (default: standard fallbacks)')
    p.add_argument('--mode',      default='swing', choices=['swing', 'longterm', 'daytrade'],
                   help='Scan mode (default: swing)')
    p.add_argument('--headlines', type=int, default=8,
                   help='Max FinBERT headlines per ticker (default: 8)')
    p.add_argument('--age',       type=int, default=168,
                   help='Max headline age in hours for FinBERT (default: 168 = 7 days)')
    p.add_argument('--no-sentiment', action='store_true',
                   help='Skip FinBERT — run baseline scan only')
    p.add_argument('--limit', type=int, default=0,
                   help='Cap number of symbols loaded (0 = no cap)')
    p.add_argument('--finnhub-key', default=None, metavar='KEY',
                   help='Finnhub API key for TRUE historical news per signal date. '
                        'Free at finnhub.io. Overrides yfinance (current-only) headlines. '
                        'Also reads from FINNHUB_API_KEY env var.')
    return p.parse_args()


# ---------------------------------------------------------------------------
# FinBERT enrichment + promotion
# ---------------------------------------------------------------------------

def enrich_and_promote(
    signals: List[Dict],
    max_headlines: int,
    max_age_hours: int,
    finnhub_key: Optional[str] = None,
) -> Tuple[List[Dict], Dict]:
    """Fetch FinBERT sentiment per signal, apply FINBERT_PROMOTION rules.

    Two modes:
      • finnhub_key provided  → TRUE historical news per (symbol, signal_date)
                                 via Finnhub company-news API (cached to disk).
                                 Signals for the same symbol on different dates
                                 get independent news windows. ✓ No lookahead bias.
      • finnhub_key=None      → Current yfinance headlines (same for all dates).
                                 Fast, no API key required, but introduces
                                 lookahead bias (today's news, not signal-day news).

    Returns:
        promoted_signals : copy of signals with finbert_* keys + updated 'quality'
        sentiments       : {symbol: SentimentResult} — keyed by symbol (last seen
                           date wins for the display summary; per-signal data is in
                           the signal dict itself)
    """
    h2p     = FINBERT_PROMOTION['high_to_premium']
    p2g     = FINBERT_PROMOTION['premium_to_gold']
    enabled = FINBERT_PROMOTION.get('enabled', True)

    def _apply_promotion(s: dict, fb_label: str, fb_score: float, fb_net: float,
                         fb_headline: str) -> dict:
        s['finbert_label']    = fb_label
        s['finbert_score']    = fb_score
        s['finbert_net']      = fb_net
        s['finbert_headline'] = fb_headline[:80]
        if enabled and fb_label == 'bullish':
            q = s.get('quality', '')
            if q == 'HIGH' and fb_score >= h2p['min_score'] and fb_net >= h2p['min_net']:
                s['quality'] = 'PREMIUM'
                s['finbert_promoted'] = 'HIGH→PREMIUM'
            elif q == 'PREMIUM' and fb_score >= p2g['min_score'] and fb_net >= p2g['min_net']:
                s['quality'] = 'GOLD'
                s['finbert_promoted'] = 'PREMIUM→GOLD'
        return s

    sentiments: Dict = {}
    promoted: List[Dict] = []

    if finnhub_key:
        # ── Finnhub path: per-(symbol, date) historical news ─────────────────
        unique_pairs = list({(s['symbol'], s.get('date')) for s in signals})
        print(f"\n  [FinBERT+Finnhub] Historical news for {len(unique_pairs)} "
              f"(symbol, date) pair(s)…")

        # Cache per-pair FinBERT results in memory (avoid re-running same pair)
        pair_cache: Dict = {}
        for sym, sig_date in sorted(unique_pairs, key=lambda x: str(x[1])):
            headlines = fetch_finnhub_headlines(sym, sig_date, finnhub_key)
            if not headlines:
                pair_cache[(sym, sig_date)] = None
                continue
            try:
                fb = analyze_text(headlines)
                pair_cache[(sym, sig_date)] = fb
                sentiments[sym] = fb   # last date wins for summary table
                n_hl = len(headlines)
                print(f"    {fb['emoji']} {sym:<8} {str(sig_date)[:10]}  "
                      f"{fb['label']:<8} {fb['score']:.2f}  net={fb['net_score']:+.2f}  "
                      f"({n_hl} headlines)")
            except Exception as exc:
                print(f"    [FinBERT] {sym} {sig_date}: {exc}")
                pair_cache[(sym, sig_date)] = None

        for sig in signals:
            s = dict(sig)
            fb = pair_cache.get((s['symbol'], s.get('date')))
            if fb:
                s = _apply_promotion(s, fb['label'], fb['score'], fb['net_score'],
                                     fb['top_headline'])
            promoted.append(s)

    else:
        # ── yfinance path: current headlines for all unique symbols (batch) ───
        unique_syms = list({s['symbol'] for s in signals})
        print(f"\n  [FinBERT+yfinance] Current headlines for {len(unique_syms)} symbol(s)…")
        try:
            sentiments = batch_sentiment(unique_syms, max_headlines=max_headlines,
                                         max_age_hours=max_age_hours)
        except Exception as exc:
            print(f"  [FinBERT] Error: {exc}")
            return [dict(s) for s in signals], {}

        for sig in signals:
            s = dict(sig)
            fb = sentiments.get(s['symbol'])
            if fb:
                s = _apply_promotion(s, fb['label'], fb['score'], fb['net_score'],
                                     fb['top_headline'])
            promoted.append(s)

    return promoted, sentiments


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

_HIGH_PLUS    = {'HIGH', 'PREMIUM', 'GOLD'}
_PREMIUM_PLUS = {'PREMIUM', 'GOLD'}


def _filter(signals, tiers):
    return [s for s in signals if s.get('quality') in tiers]


def _perf_row(label, report, signal_count, trade_count=None):
    """Format one row of the comparison table."""
    if report is None:
        return f"  {label:<28} {'—':>8} {'—':>8} {'—':>10} {'—':>8}  (no trades)"
    tc   = report.get('total_trades', trade_count or 0)
    wr   = report.get('win_rate', 0)
    ret  = report.get('total_return', 0)
    sh   = report.get('sharpe_ratio', 0)
    dd   = report.get('max_drawdown', 0)
    return (
        f"  {label:<28} {ret:>+7.1f}%  {sh:>7.2f}  {dd:>+9.1f}%  {wr:>6.1f}%  [{tc} trades / {signal_count} signals]"
    )


def _print_promotion_table(baseline_sigs, promoted_sigs, historical, start_date, end_date):
    """Show per-symbol promotion decisions and actual price outcome."""
    promoted_map = {
        s['symbol'] + '|' + str(s.get('date', '')): s
        for s in promoted_sigs
        if s.get('finbert_promoted')
    }
    if not promoted_map:
        print("\n  No signals were promoted in this period.")
        return

    print(f"\n  {'Symbol':<8} {'Date':<12} {'Promotion':<18} {'FinBERT':<9} {'Score':>6} {'Net':>7}  {'Outcome':<16}  Headline")
    print("  " + "─" * 110)

    for _, sig in sorted(promoted_map.items()):
        sym       = sig['symbol']
        sig_date  = sig.get('date')
        promoted  = sig.get('finbert_promoted', '')
        fb_label  = sig.get('finbert_label', '?')
        fb_score  = sig.get('finbert_score', 0)
        fb_net    = sig.get('finbert_net', 0)
        headline  = sig.get('finbert_headline', '')[:55]
        entry     = sig.get('price', 0)
        tp        = sig.get('take_profit', 0)
        sl        = sig.get('stop_loss', 0)

        # Determine actual price outcome in the period after signal
        outcome = '?'
        try:
            df = historical.get(sym)
            if df is not None and sig_date is not None:
                future = df[df.index > sig_date]
                if len(future) >= 5:
                    peak  = future['high'].iloc[:20].max()
                    trough = future['low'].iloc[:20].min()
                    last   = future['close'].iloc[-1]
                    if entry > 0:
                        ret_pct = (last - entry) / entry * 100
                        hit_tp  = peak >= tp if tp > 0 else False
                        hit_sl  = trough <= sl if sl > 0 else False
                        if hit_tp:
                            outcome = f'+TP ({ret_pct:+.1f}%)'
                        elif hit_sl:
                            outcome = f'-SL ({ret_pct:+.1f}%)'
                        else:
                            outcome = f'open  ({ret_pct:+.1f}%)'
        except Exception:
            pass

        emoji = {'bullish': '🟢', 'bearish': '🔴', 'neutral': '⚪'}.get(fb_label, ' ')
        date_str = sig_date.strftime('%Y-%m-%d') if hasattr(sig_date, 'strftime') else str(sig_date)[:10]
        print(
            f"  {sym:<8} {date_str:<12} {promoted:<18} "
            f"{emoji}{fb_label:<8} {fb_score:>6.2f} {fb_net:>+7.2f}  "
            f"{outcome:<16}  {headline}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = _parse_args()
    cap  = args.capital

    print("\n" + "═" * 70)
    print("  FINBERT QUALITY-PROMOTION BACKTEST")
    print("═" * 70)
    print(f"  Period   : {args.start} → {args.end}")
    print(f"  Capital  : ${cap:,}")
    print(f"  Mode     : {args.mode}")
    # Resolve Finnhub key: CLI flag > env var
    import os
    finnhub_key = args.finnhub_key or os.environ.get('FINNHUB_API_KEY') or None

    if args.no_sentiment or not _FINBERT_OK:
        if not _FINBERT_OK:
            print("  FinBERT  : UNAVAILABLE (pip install transformers torch) — baseline only")
        else:
            print("  FinBERT  : disabled (--no-sentiment)")
        run_finbert = False
    elif finnhub_key:
        print(f"  FinBERT  : enabled | news source: Finnhub (TRUE historical per signal date)")
        print(f"             Cache  : scanner_output/cache/finnhub/")
        run_finbert = True
    else:
        print(f"  FinBERT  : enabled | headlines={args.headlines} | max_age={args.age}h")
        print("  ⚠️  NEWS  : Current yfinance headlines (lookahead bias).")
        print("  💡 TIP    : Add --finnhub-key KEY (free at finnhub.io) for true historical news.")
        run_finbert = True
    print("─" * 70)

    # 1 — Load symbols
    symbols = load_symbols(args.watchlist, limit=args.limit)
    print(f"\n  Symbols loaded: {len(symbols)}")

    # 2 — Fetch historical price data
    print("\n  Fetching historical price data…")
    historical, end_prices = fetch_all_data(symbols, args.start, args.end)

    # 3 — Run breakout scan (v5x: composite momentum + overextension filter)
    all_signals = run_all_scans(historical, args.start, args.end, modes=[args.mode])
    baseline_raw = all_signals.get('v5x', [])

    baseline_high_plus = _filter(baseline_raw, _HIGH_PLUS)
    baseline_prem_plus = _filter(baseline_raw, _PREMIUM_PLUS)

    print(f"\n  Baseline signals: {len(baseline_raw)} total  |  "
          f"{len(baseline_high_plus)} HIGH+  |  {len(baseline_prem_plus)} PREMIUM+")

    # Quality breakdown
    qual_counts = {}
    for s in baseline_raw:
        q = s.get('quality', 'UNKNOWN')
        qual_counts[q] = qual_counts.get(q, 0) + 1
    for q in ('GOLD', 'PREMIUM', 'HIGH', 'STANDARD'):
        if q in qual_counts:
            print(f"      {q:<10}: {qual_counts[q]}")

    # 4 — FinBERT enrichment + promotion
    if run_finbert:
        promoted_raw, sentiments = enrich_and_promote(
            baseline_high_plus, max_headlines=args.headlines, max_age_hours=args.age,
            finnhub_key=finnhub_key,
        )
        n_promoted = sum(1 for s in promoted_raw if s.get('finbert_promoted'))
        n_bullish  = sum(1 for s in promoted_raw if s.get('finbert_label') == 'bullish')
        n_bearish  = sum(1 for s in promoted_raw if s.get('finbert_label') == 'bearish')
        print(f"\n  FinBERT results: {n_bullish} bullish / {n_bearish} bearish "
              f"/ {len(promoted_raw) - n_bullish - n_bearish} neutral  →  "
              f"{n_promoted} signal(s) promoted")
        finbert_high_plus = _filter(promoted_raw, _HIGH_PLUS)
        finbert_prem_plus = _filter(promoted_raw, _PREMIUM_PLUS)
    else:
        promoted_raw = baseline_high_plus
        sentiments   = {}
        finbert_high_plus = baseline_high_plus
        finbert_prem_plus = baseline_prem_plus

    # 5 — SPY benchmark (filter SPY data to actual backtest period only)
    spy_historical_trimmed = dict(historical)
    if 'SPY' in historical:
        spy_full = historical['SPY']
        spy_trimmed = spy_full[spy_full.index >= pd.to_datetime(args.start)]
        spy_historical_trimmed = dict(historical)
        spy_historical_trimmed['SPY'] = spy_trimmed
    spy_result = calculate_spy_benchmark(spy_historical_trimmed, args.start, args.end, cap)

    # 6 — Run simulations
    print("\n  Running simulations…")

    result_a = run_simulation(baseline_high_plus, args.start, args.end, end_prices, historical, cap)
    result_b = run_simulation(baseline_prem_plus, args.start, args.end, end_prices, historical, cap) if run_finbert else None
    result_c = run_simulation(finbert_high_plus,  args.start, args.end, end_prices, historical, cap) if run_finbert else None
    result_d = run_simulation(finbert_prem_plus,  args.start, args.end, end_prices, historical, cap) if run_finbert else None

    # 7 — Print comparison table
    print("\n" + "═" * 70)
    print("  PERFORMANCE COMPARISON")
    print("─" * 70)
    print(f"  {'Config':<28} {'Return':>8}  {'Sharpe':>7}  {'Drawdown':>9}  {'WinRate':>7}  Trades")
    print("  " + "─" * 68)

    if spy_result:
        print(f"  {'SPY buy-and-hold':<28} {spy_result['total_return']:>+7.1f}%  "
              f"{spy_result['sharpe_ratio']:>7.2f}  {spy_result['max_drawdown']:>+9.1f}%  {'—':>7}")

    print("  " + "─" * 68)
    print(_perf_row('A: Baseline HIGH+', result_a, len(baseline_high_plus)))

    if run_finbert:
        print(_perf_row('B: Baseline PREMIUM+ (no FinBERT)', result_b, len(baseline_prem_plus)))
        print(_perf_row('C: FinBERT HIGH+  (sized by promo)', result_c, len(finbert_high_plus)))
        print(_perf_row('D: FinBERT PREMIUM+ (incl. promoted)', result_d, len(finbert_prem_plus)))

    print("  " + "─" * 68)

    # Delta summary
    if run_finbert and result_a and result_d:
        delta_ret = (result_d.get('total_return', 0) or 0) - (result_a.get('total_return', 0) or 0)
        delta_sh  = (result_d.get('sharpe_ratio', 0) or 0) - (result_a.get('sharpe_ratio', 0) or 0)
        delta_wr  = (result_d.get('win_rate', 0) or 0) - (result_a.get('win_rate', 0) or 0)
        arrow_ret = '⬆' if delta_ret > 0 else '⬇'
        print(f"\n  FinBERT PREMIUM+ vs Baseline HIGH+:")
        print(f"    Return: {delta_ret:>+.1f}%  {arrow_ret}   |   "
              f"Sharpe: {delta_sh:>+.2f}   |   Win rate: {delta_wr:>+.1f}%")

    # 8 — Promoted signal attribution
    if run_finbert and promoted_raw:
        print("\n" + "═" * 70)
        print("  PROMOTED SIGNAL OUTCOMES  (FinBERT-promoted signals only)")
        print("─" * 70)
        _print_promotion_table(baseline_high_plus, promoted_raw, historical, args.start, args.end)

    # 9 — Sentiment breakdown for all analysed symbols
    if sentiments:
        print("\n" + "═" * 70)
        print("  FINBERT SENTIMENT SUMMARY  (analysed symbols)")
        print("─" * 70)
        print(f"  {'Symbol':<8} {'Label':<10} {'Score':>6} {'Net':>7}  Headline")
        print("  " + "─" * 80)
        for sym, fb in sorted(sentiments.items(), key=lambda x: -x[1]['score']):
            emoji  = {'bullish': '🟢', 'bearish': '🔴', 'neutral': '⚪'}.get(fb['label'], ' ')
            hl     = fb['top_headline'][:55] + ('…' if len(fb['top_headline']) > 55 else '')
            promo  = next((s.get('finbert_promoted', '') for s in promoted_raw if s['symbol'] == sym and s.get('finbert_promoted')), '')
            promo_tag = f"  ⬆{promo}" if promo else ''
            print(f"  {sym:<8} {emoji}{fb['label']:<9} {fb['score']:>6.2f} {fb['net_score']:>+7.2f}  {hl}{promo_tag}")

    print("\n" + "═" * 70 + "\n")


if __name__ == '__main__':
    main()
