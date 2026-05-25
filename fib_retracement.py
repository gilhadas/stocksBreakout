#!/usr/bin/env python3
r"""
fib_retracement.py
==================
Daily-chart Fibonacci retracement scanner for bounce candidates.

Finds stocks that have pulled back from a recent swing high to a classic
Fibonacci level (38.2% / 50% / 61.8%) where SMA confluence and an RSI reset
make a bounce statistically more likely.

Output columns (bounce_candidates.csv):
    Symbol, Current, SwingLow, SwingHigh, RetracedPct, NearestFibLabel,
    NearestFibPrice, DistToFibPct, SMAConfluence, Stage2, RSI,
    VolExpansion, BounceScore

Bounce score (0-100):
    +30  within 2% of 38.2 / 50 / 61.8 level       (the classic zone)
    +25  SMA 50 / 150 / 200 within 1.5% of that level (confluence)
    +15  Stage 2 context (SMA50 > SMA150 > SMA200 AND price > SMA200)
    +15  RSI in 35-50 reset zone (not overbought, not capitulating)
    +10  Volume expansion last 3 days (>= 1.2× 20-day avg)
    + 5  level is 50% or 61.8% (the "golden pocket")

Usage:
    python fib_retracement.py input/ALL.txt --min-score 60 --notify
    python fib_retracement.py input/ALL.txt --top 20 --dry-run
    python fib_retracement.py --held-only    # scan currently-held auto-portfolio positions

Cron:
    30 16 * * 1-5 TZ=America/New_York ... fib_retracement.py input/ALL.txt \
        --min-score 60 --notify >> scanner_output/logs/cron_fib_$(date +\%Y\%m\%d).log 2>&1

Math (LaTeX):
    $$\text{fib\_price}(r) = H - r \cdot (H - L)$$
    $$\text{retraced\_pct}  = \frac{H - C}{H - L} \cdot 100$$
    where H = swing high, L = prior swing low, C = current close, r ∈ {0.236, 0.382, 0.5, 0.618, 0.786}.
"""

import argparse
import csv
import logging
import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
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

# Core scoring logic lives in quantkit.fib (canonical implementation)
from quantkit.fib import (
    FIB_RATIOS, FIB_LABELS, CLASSIC_LEVELS, GOLDEN_POCKET,
    detect_swing, fib_levels, nearest_fib_to_price, score_bounce,
    DEFAULT_SWING_WINDOW,
)

NY_TZ = ZoneInfo('America/New_York')
OUT_DIR = Path('scanner_output')
LISTS_DIR = OUT_DIR / 'lists'
OUTPUT_CSV = LISTS_DIR / 'bounce_candidates.csv'

DEFAULT_LOOKBACK_DAYS = 180  # daily bars fetched

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data + indicator helpers
# ---------------------------------------------------------------------------

def _fetch_daily(symbol: str, days: int = DEFAULT_LOOKBACK_DAYS) -> Optional[pd.DataFrame]:
    """Fetch daily bars from yfinance. Returns lowercase-column DataFrame or None."""
    try:
        df = yf.Ticker(symbol).history(period=f'{days}d', interval='1d', auto_adjust=False)
        if df is None or df.empty or len(df) < 40:
            return None
        df = df.rename(columns={
            'Open': 'open', 'High': 'high', 'Low': 'low',
            'Close': 'close', 'Volume': 'volume',
        })
        return df[['open', 'high', 'low', 'close', 'volume']]
    except Exception as exc:
        logger.debug(f"{symbol}: fetch failed — {exc}")
        return None


# ---------------------------------------------------------------------------
# Watchlist scan
# ---------------------------------------------------------------------------

def load_symbols(path: str) -> List[str]:
    """Parse comma/newline-separated watchlist file (supports EXCHANGE:SYM)."""
    p = Path(path)
    if not p.exists():
        logger.error(f"Watchlist not found: {path}")
        return []
    text = p.read_text(encoding='utf-8')
    seen: set = set()
    out: List[str] = []
    for tok in text.replace('\n', ',').split(','):
        t = tok.strip()
        if not t or t.startswith('#') or t.startswith('###'):
            continue
        s = t.split(':')[-1].strip().upper().replace('.', '-')
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def load_held_symbols() -> List[str]:
    try:
        import auto_portfolio as ap
        data = ap.load()
        return [p['symbol'].upper() for p in data.get('positions', [])]
    except Exception:
        return []


def scan_watchlist(symbols: List[str], min_score: int) -> List[Dict]:
    """Score every symbol; return only those with bounce_score >= min_score."""
    results: List[Dict] = []
    for i, sym in enumerate(symbols, 1):
        if i % 25 == 0:
            logger.info(f"  … scanned {i}/{len(symbols)}")
        df = _fetch_daily(sym)
        if df is None:
            continue
        swing = detect_swing(df)
        if swing is None:
            continue
        info = score_bounce(df, swing)
        if info['bounce_score'] < min_score:
            continue
        info['symbol'] = sym
        results.append(info)

    results.sort(key=lambda r: r['bounce_score'], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    'Symbol', 'BounceScore', 'Current', 'SwingLow', 'SwingHigh', 'SwingHighDate',
    'RetracedPct', 'NearestFib', 'NearestFibPrice', 'DistToFibPct',
    'SMAConfluence', 'Stage2', 'RSI', 'VolRatio3d',
]


def write_csv(results: List[Dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for r in results:
            w.writerow([
                r['symbol'], r['bounce_score'], r['current'], r['swing_low'],
                r['swing_high'], r['swing_high_date'], r['retraced_pct'],
                r['nearest_fib'], r['nearest_fib_price'], r['dist_to_fib_pct'],
                r['sma_confluence'] or '-',
                'Y' if r['stage2'] else 'N',
                r['rsi'] if r['rsi'] is not None else '',
                r['vol_ratio_3d'],
            ])


def format_discord(results: List[Dict], watchlist_name: str, now_et: datetime,
                   top_n: int = 10) -> Tuple[str, str]:
    subject = (
        f"🎯 Fib Bounce Candidates {now_et.strftime('%Y-%m-%d %H:%M ET')} — "
        f"{len(results)} found ({watchlist_name})"
    )
    lines = [
        f"**Fibonacci bounce scan — {watchlist_name}** "
        f"({now_et.strftime('%Y-%m-%d %H:%M ET')})\n",
        f"Top {min(top_n, len(results))} by bounce_score (of {len(results)} ≥ threshold):\n",
    ]
    for r in results[:top_n]:
        conf = f" · {r['sma_confluence']}" if r['sma_confluence'] else ''
        stage = ' · Stage2' if r['stage2'] else ''
        rsi_str = f" · RSI {r['rsi']}" if r['rsi'] is not None else ''
        lines.append(
            f"  **{r['symbol']:<6}** score **{r['bounce_score']}**  "
            f"${r['current']} @ {r['nearest_fib']} (${r['nearest_fib_price']}, "
            f"{r['dist_to_fib_pct']:+.1f}%)  "
            f"retraced {r['retraced_pct']:.0f}%{conf}{stage}{rsi_str}"
        )
    lines.append("")
    lines.append(f"→ Full table in `{OUTPUT_CSV.name}`.")
    return subject, '\n'.join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Daily-chart Fibonacci retracement scanner for bounce candidates'
    )
    parser.add_argument('watchlist', nargs='?', default='input/ALL.txt',
                        help='Watchlist file (default: input/ALL.txt). '
                             'Ignored if --held-only is set.')
    parser.add_argument('--held-only', action='store_true',
                        help='Scan only currently-open auto-portfolio symbols')
    parser.add_argument('--min-score', type=int, default=60,
                        help='Minimum bounce_score to include (default: 60)')
    parser.add_argument('--top', type=int, default=10,
                        help='Top N to include in Discord alert (default: 10)')
    parser.add_argument('--notify', action='store_true',
                        help='Send Discord alert')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print only — no file writes, no Discord')
    parser.add_argument('--output', default=str(OUTPUT_CSV),
                        help=f'Output CSV path (default: {OUTPUT_CSV})')
    args = parser.parse_args()

    now_et = datetime.now(NY_TZ)
    logger.info(f"Fib retracement scanner | {now_et.strftime('%Y-%m-%d %H:%M %Z')}")

    if args.held_only:
        symbols = load_held_symbols()
        watchlist_name = 'held positions'
    else:
        symbols = load_symbols(args.watchlist)
        watchlist_name = Path(args.watchlist).name

    if not symbols:
        logger.warning("No symbols to scan.")
        return 1

    logger.info(f"Scanning {len(symbols)} symbols from {watchlist_name} "
                f"(min_score={args.min_score})")
    results = scan_watchlist(symbols, args.min_score)

    # ── Summary ──
    logger.info("")
    logger.info("=" * 70)
    logger.info(f"  {len(results)} candidates with bounce_score >= {args.min_score}")
    logger.info("=" * 70)
    for r in results[:args.top]:
        conf = f" [{r['sma_confluence']}]" if r['sma_confluence'] else ''
        logger.info(
            f"  {r['symbol']:<6}  score={r['bounce_score']:>3}  "
            f"${r['current']:<7} @ {r['nearest_fib']:<6} "
            f"(${r['nearest_fib_price']}, {r['dist_to_fib_pct']:+.1f}%)  "
            f"retraced {r['retraced_pct']:.0f}%{conf}  "
            f"RSI {r['rsi']}"
        )
    if len(results) > args.top:
        logger.info(f"  … and {len(results) - args.top} more")

    # ── Write CSV ──
    if not args.dry_run and results:
        out_path = Path(args.output)
        write_csv(results, out_path)
        logger.info(f"Wrote {len(results)} rows → {out_path}")
    elif args.dry_run:
        logger.info("Dry-run — no CSV written.")

    # ── Discord alert ──
    if (not args.dry_run) and results and args.notify:
        subject, message = format_discord(results, watchlist_name, now_et, top_n=args.top)
        try:
            notifier = Notifier()
            notifier.send_discord(subject=subject, message=message,
                                  notification_type='alerts')
            logger.info("Discord alert sent.")
        except Exception as exc:
            logger.warning(f"Discord send failed: {exc}")

    return 0 if results else 1


if __name__ == '__main__':
    sys.exit(main())
