#!/usr/bin/env python3
"""
early_premarket_scan.py
=======================
4:00 AM ET scanner for stocks moving on earnings releases or news before the
main premarket check at 8:00 AM.

What it does:
  1. Loads scanner_output/lists/afterhours_watch.txt — ER candidates from prior
     evening's afterhours_monitor.py run
  2. Merges with news movers from Alpha Vantage (last 8 h) and PRIORITY_SYMBOLS
  3. Fetches premarket 1-min bars via yfinance (prepost=True) for each symbol
  4. Flags symbols with a premarket move >= --threshold (default 3%)
  5. For each gapper: checks Alpaca news + Finnhub buzz to confirm catalyst
  6. Ranks by: abs(gap_pct) × (1 + catalyst_boost)
  7. Writes scanner_output/lists/early_premarket_watch.txt (one symbol per line)
  8. Sends Discord/Telegram alert

Output file is picked up by:
  - signal_surge_monitor.py (SIGNAL_FILES list) for real-time surge monitoring
  - premarket_monitor.py at 8:00 AM — can be merged into momentum scan

Usage:
    python early_premarket_scan.py                   # standard run
    python early_premarket_scan.py --notify          # Telegram + Discord alerts
    python early_premarket_scan.py --threshold 2.0   # lower sensitivity
    python early_premarket_scan.py --dry-run         # print only, no file writes

Cron:
    0 4 * * 1-5 TZ=America/New_York ... early_premarket_scan.py --notify
"""

import argparse
import logging
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)

try:
    import yfinance as yf
except ImportError:
    print("ERROR: yfinance not installed. Run: pip install yfinance")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))
from notifier import Notifier

NY_TZ = ZoneInfo('America/New_York')
OUT_DIR = Path('scanner_output')
LISTS_DIR = OUT_DIR / 'lists'
EARLY_WATCH_FILE = LISTS_DIR / 'early_premarket_watch.txt'
AFTERHOURS_FILE  = LISTS_DIR / 'afterhours_watch.txt'

DEFAULT_GAP_THRESHOLD = 3.0
MAX_WORKERS = 10          # parallel yfinance calls
MAX_SCAN_SYMBOLS = 300    # cap to keep runtime under ~2 min
NEWS_HOURS_BACK = 10      # Alpha Vantage window: overnight + pre-dawn

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Symbol universe builders
# ---------------------------------------------------------------------------

def load_afterhours_candidates() -> List[str]:
    """Return symbols from last evening's afterhours_watch.txt."""
    if not AFTERHOURS_FILE.exists():
        logger.info("afterhours_watch.txt not found — no ER candidates to seed")
        return []
    symbols = []
    for line in AFTERHOURS_FILE.read_text().splitlines():
        sym = line.strip().upper()
        if sym and not sym.startswith('#'):
            symbols.append(sym)
    logger.info(f"Loaded {len(symbols)} ER candidates from afterhours_watch.txt")
    return symbols


def load_priority_symbols() -> List[str]:
    """Return the high-beta / catalyst-driven priority list from premarket_monitor."""
    # Re-use the same list premarket_monitor.py uses so we stay in sync.
    try:
        import premarket_monitor as pm
        return list(pm.PRIORITY_SYMBOLS)
    except Exception as exc:
        logger.debug(f"premarket_monitor import failed ({exc}) — using fallback list")
        return [
            'COIN', 'MSTR', 'HOOD', 'MARA', 'RIOT', 'NVDA', 'AMD', 'SMCI',
            'PLTR', 'CRWD', 'TSLA', 'META', 'AVGO', 'APP', 'CVNA', 'UPST',
            'AFRM', 'SOFI', 'MRNA', 'BNTX', 'NFLX', 'ASTS', 'SOUN', 'IONQ',
        ]


def discover_news_symbols(hours_back: int = NEWS_HOURS_BACK) -> List[str]:
    """Return tickers with strong overnight news via Alpha Vantage."""
    try:
        from news_scanner import discover_news_movers
        movers = discover_news_movers(
            hours_back=hours_back,
            min_articles=1,         # lower bar — any catalyst counts at 4 AM
            min_avg_score=0.15,
            min_relevance=0.50,
            top_k=40,
        )
        syms = [m['symbol'] for m in movers]
        logger.info(f"Alpha Vantage news movers: {len(syms)} symbols")
        return syms
    except Exception as exc:
        logger.debug(f"news_scanner unavailable: {exc}")
        return []


def build_scan_universe(er_candidates: List[str]) -> List[str]:
    """Merge ER candidates + news movers + priority symbols, capped at MAX_SCAN_SYMBOLS."""
    news_syms    = discover_news_symbols()
    priority     = load_priority_symbols()

    # Combine: ER candidates first (highest priority), then news, then priority
    seen: set = set()
    merged: List[str] = []
    for sym in er_candidates + news_syms + priority:
        s = sym.strip().upper()
        if s and s not in seen:
            seen.add(s)
            merged.append(s)
        if len(merged) >= MAX_SCAN_SYMBOLS:
            break

    logger.info(f"Scan universe: {len(merged)} symbols "
                f"(ER={len(er_candidates)}, news={len(news_syms)}, priority={len(priority)})")
    return merged


# ---------------------------------------------------------------------------
# Price data
# ---------------------------------------------------------------------------

def _get_premarket_move(symbol: str) -> Optional[Dict]:
    """Return premarket gap info for *symbol*, or None on failure / no data.

    Keys: symbol, prev_close, pm_price, pm_high, pm_low, gap_pct, pm_volume
    """
    try:
        t = yf.Ticker(symbol)
        df = t.history(period='2d', interval='1m', prepost=True)
        if df is None or len(df) < 2:
            return None
        df.index = pd.DatetimeIndex(df.index).tz_convert('America/New_York')

        now_et = datetime.now(NY_TZ)
        today  = now_et.date()
        yest   = (now_et - timedelta(days=1)).date()

        # Prior regular-session close: last bar on or before 16:00 ET yesterday
        prior_bars = df[
            (df.index.date == yest)
            & (df.index.hour <= 16)
        ]
        if len(prior_bars) == 0:
            # Weekend gap: go back further
            prior_bars = df[df.index.hour <= 16]
        if len(prior_bars) == 0:
            return None
        prev_close = float(prior_bars['Close'].iloc[-1])

        # Premarket bars today: 04:00-09:29 ET
        pm_bars = df[
            (df.index.date == today)
            & (df.index.hour >= 4)
            & ~((df.index.hour == 9) & (df.index.minute >= 30))
        ]
        if len(pm_bars) == 0:
            return None

        pm_price  = float(pm_bars['Close'].iloc[-1])
        pm_high   = float(pm_bars['High'].max())
        pm_low    = float(pm_bars['Low'].min())
        pm_vol    = int(pm_bars['Volume'].sum())
        gap_pct   = (pm_price - prev_close) / prev_close * 100 if prev_close else 0.0

        return {
            'symbol':     symbol,
            'prev_close': round(prev_close, 2),
            'pm_price':   round(pm_price, 2),
            'pm_high':    round(pm_high, 2),
            'pm_low':     round(pm_low, 2),
            'gap_pct':    round(gap_pct, 2),
            'pm_volume':  pm_vol,
        }
    except Exception as exc:
        logger.debug(f"{symbol}: yfinance error — {exc}")
        return None


def scan_universe(symbols: List[str], threshold: float) -> List[Dict]:
    """Parallel-fetch premarket moves; return movers above threshold."""
    movers: List[Dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_get_premarket_move, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            result = fut.result()
            if result and abs(result['gap_pct']) >= threshold:
                movers.append(result)
    movers.sort(key=lambda x: abs(x['gap_pct']), reverse=True)
    return movers


# ---------------------------------------------------------------------------
# Catalyst scoring
# ---------------------------------------------------------------------------

def _alpaca_article_count(symbol: str, hours_back: int = NEWS_HOURS_BACK) -> int:
    """Count recent Alpaca news articles for *symbol*."""
    try:
        from alpaca_news import get_alpaca_headlines
        headlines = get_alpaca_headlines(symbol, limit=20, hours_back=hours_back)
        return len(headlines)
    except Exception:
        return 0


def _finnhub_buzz_ratio(symbol: str) -> float:
    """Return Finnhub buzz ratio (1.0 = baseline). 0 on failure."""
    try:
        from finnhub_buzz import get_buzz
        result = get_buzz(symbol)
        return result['buzz_ratio'] if result else 0.0
    except Exception:
        return 0.0


def enrich_with_catalysts(
    movers: List[Dict],
    er_set: set,
) -> List[Dict]:
    """Add catalyst fields and rank_score to each mover in-place."""
    for m in movers:
        sym = m['symbol']
        has_er     = sym in er_set
        articles   = _alpaca_article_count(sym)
        buzz       = _finnhub_buzz_ratio(sym)

        # catalyst_boost: 0..~2.0
        catalyst_boost = (
            (1.0 if has_er else 0.0)
            + min(articles / 8.0, 0.75)
            + min(max(buzz - 1.0, 0) / 2.0, 0.5)
        )
        rank_score = abs(m['gap_pct']) * (1.0 + catalyst_boost * 0.5)

        m['has_er']         = has_er
        m['articles']       = articles
        m['buzz_ratio']     = round(buzz, 2)
        m['catalyst_boost'] = round(catalyst_boost, 2)
        m['rank_score']     = round(rank_score, 2)

    movers.sort(key=lambda x: x['rank_score'], reverse=True)
    return movers


def filter_movers(movers: List[Dict], threshold: float) -> List[Dict]:
    """Keep symbols with a confirmed catalyst OR a strong gap."""
    STRONG_GAP = threshold * 2.0   # e.g. 6% — accept without catalyst
    return [
        m for m in movers
        if m.get('has_er')
        or m.get('articles', 0) >= 1
        or m.get('buzz_ratio', 0) >= 1.3
        or abs(m['gap_pct']) >= STRONG_GAP
    ]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_watch_file(symbols: List[str], dry_run: bool) -> None:
    if dry_run:
        logger.info(f"[dry-run] Would write {len(symbols)} symbols to {EARLY_WATCH_FILE}")
        return
    LISTS_DIR.mkdir(parents=True, exist_ok=True)
    EARLY_WATCH_FILE.write_text('\n'.join(symbols) + '\n')
    logger.info(f"Wrote {len(symbols)} symbols to {EARLY_WATCH_FILE}")


def build_notification(movers: List[Dict], threshold: float) -> Tuple[str, str]:
    """Return (subject, body) for Discord/Telegram."""
    now_str = datetime.now(NY_TZ).strftime('%H:%M ET')
    subject = f"⏰ Early Premarket ({now_str}) — {len(movers)} gapper{'s' if len(movers) != 1 else ''}"

    lines = [f"**Early Premarket Movers** — {now_str}", ""]
    for m in movers[:20]:
        arrow  = "🔺" if m['gap_pct'] > 0 else "🔻"
        tags   = []
        if m.get('has_er'):
            tags.append("ER")
        if m.get('articles', 0) >= 2:
            tags.append(f"news×{m['articles']}")
        if m.get('buzz_ratio', 0) >= 1.5:
            tags.append(f"buzz×{m['buzz_ratio']:.1f}")
        tag_str = f"  [{', '.join(tags)}]" if tags else ""
        lines.append(
            f"{arrow} **{m['symbol']}** {m['gap_pct']:+.1f}%  "
            f"${m['pm_price']:.2f} (prev ${m['prev_close']:.2f})"
            f"{tag_str}"
        )

    if len(movers) > 20:
        lines.append(f"… +{len(movers) - 20} more")

    lines += [
        "",
        f"Threshold: ±{threshold}% | Watch file: `{EARLY_WATCH_FILE.name}`",
    ]
    return subject, '\n'.join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(threshold: float, dry_run: bool, notify: bool) -> int:
    logger.info(f"=== Early Premarket Scan @ {datetime.now(NY_TZ).strftime('%H:%M ET')} ===")
    logger.info(f"Threshold: ±{threshold}%  dry_run={dry_run}  notify={notify}")

    er_candidates = load_afterhours_candidates()
    er_set        = set(er_candidates)
    universe      = build_scan_universe(er_candidates)

    logger.info(f"Fetching premarket data for {len(universe)} symbols ...")
    raw_movers = scan_universe(universe, threshold)
    logger.info(f"Gap movers (>= {threshold}%): {len(raw_movers)}")

    if not raw_movers:
        logger.info("No premarket movers found — exiting")
        write_watch_file([], dry_run)
        return 0

    logger.info("Enriching with catalyst data ...")
    enrich_with_catalysts(raw_movers, er_set)
    qualified = filter_movers(raw_movers, threshold)
    logger.info(f"Qualified (catalyst confirmed): {len(qualified)}")

    if not qualified:
        logger.info("No catalyst-confirmed movers — exiting")
        write_watch_file([], dry_run)
        return 0

    symbols_out = [m['symbol'] for m in qualified]
    write_watch_file(symbols_out, dry_run)

    # Log summary table
    logger.info(
        f"\n{'Symbol':<8} {'Gap%':>6}  {'PM $':>7}  {'Prev $':>7}  {'ER':<4} "
        f"{'Art':>4}  {'Buzz':>5}  {'Score':>6}"
    )
    for m in qualified[:30]:
        logger.info(
            f"{m['symbol']:<8} {m['gap_pct']:>+6.1f}%  "
            f"${m['pm_price']:>6.2f}  ${m['prev_close']:>6.2f}  "
            f"{'YES' if m['has_er'] else '-':<4} "
            f"{m['articles']:>4}  {m['buzz_ratio']:>5.2f}  {m['rank_score']:>6.2f}"
        )

    if notify and not dry_run:
        subject, body = build_notification(qualified, threshold)
        notifier = Notifier()
        notifier.send_all(subject, body)
        logger.info("Notifications sent")

    return len(qualified)


def main() -> None:
    parser = argparse.ArgumentParser(description='4 AM early premarket scanner')
    parser.add_argument('--threshold', type=float, default=DEFAULT_GAP_THRESHOLD,
                        help=f'Premarket gap %% threshold (default {DEFAULT_GAP_THRESHOLD})')
    parser.add_argument('--notify',   action='store_true', help='Send Telegram + Discord alerts')
    parser.add_argument('--dry-run',  action='store_true', help='Print only, no file writes')
    args = parser.parse_args()

    n = run(threshold=args.threshold, dry_run=args.dry_run, notify=args.notify)
    sys.exit(0 if n >= 0 else 1)


if __name__ == '__main__':
    main()
