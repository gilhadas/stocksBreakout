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

NY_TZ = ZoneInfo('America/New_York')
OUT_DIR = Path('scanner_output')
LISTS_DIR = OUT_DIR / 'lists'
OUTPUT_CSV = LISTS_DIR / 'bounce_candidates.csv'

# Fib levels (ratio → label)
FIB_RATIOS = [0.236, 0.382, 0.5, 0.618, 0.786]
FIB_LABELS = {
    0.236: '23.6%',
    0.382: '38.2%',
    0.5:   '50%',
    0.618: '61.8%',
    0.786: '78.6%',
}
CLASSIC_LEVELS = {0.382, 0.5, 0.618}  # primary bounce zone
GOLDEN_POCKET  = {0.5, 0.618}         # extra weight

DEFAULT_LOOKBACK_DAYS = 180  # daily bars fetched
DEFAULT_SWING_WINDOW  = 120  # bars over which we look for the swing high

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


def _rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


# ---------------------------------------------------------------------------
# Swing detection + Fib levels
# ---------------------------------------------------------------------------

def detect_swing(df: pd.DataFrame, window: int = DEFAULT_SWING_WINDOW) -> Optional[Dict]:
    """Find the most recent swing high in the last *window* bars and the
    swing low that preceded it.

    Returns: {swing_high, swing_high_idx, swing_low, swing_low_idx, range}
    """
    if df is None or len(df) < 30:
        return None

    tail = df.iloc[-window:] if len(df) >= window else df
    hi_idx = tail['high'].idxmax()
    swing_high = float(tail['high'].loc[hi_idx])

    # Swing low = lowest low between the start of the window and the swing high
    before_high = tail.loc[:hi_idx]
    if len(before_high) < 2:
        return None
    lo_idx = before_high['low'].idxmin()
    swing_low = float(before_high['low'].loc[lo_idx])

    if swing_high <= swing_low:
        return None

    return {
        'swing_high':      swing_high,
        'swing_high_date': str(hi_idx.date()) if hasattr(hi_idx, 'date') else str(hi_idx),
        'swing_low':       swing_low,
        'swing_low_date':  str(lo_idx.date()) if hasattr(lo_idx, 'date') else str(lo_idx),
        'range':           swing_high - swing_low,
    }


def fib_levels(swing: Dict) -> Dict[float, float]:
    """Return {ratio: price} for each FIB_RATIOS value (retraced from high)."""
    hi = swing['swing_high']
    rng = swing['range']
    return {r: hi - r * rng for r in FIB_RATIOS}


def nearest_fib_to_price(levels: Dict[float, float], price: float) -> Tuple[float, float, float]:
    """Return (ratio, level_price, distance_pct) of fib level closest to *price*."""
    ratios = list(levels.keys())
    prices = [levels[r] for r in ratios]
    i = int(np.argmin([abs(p - price) for p in prices]))
    ratio = ratios[i]
    lp = prices[i]
    dist_pct = (price - lp) / lp * 100 if lp else 0.0
    return ratio, lp, dist_pct


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_bounce(df: pd.DataFrame, swing: Dict) -> Dict:
    """Composite bounce score + component breakdown for one symbol."""
    close = float(df['close'].iloc[-1])
    levels = fib_levels(swing)
    ratio, fib_price, dist_pct = nearest_fib_to_price(levels, close)

    # Retracement progress from high toward low (0% = at high, 100% = at low)
    retraced_pct = (swing['swing_high'] - close) / swing['range'] * 100

    # SMAs
    sma50  = float(df['close'].rolling(50).mean().iloc[-1])  if len(df) >= 50  else np.nan
    sma150 = float(df['close'].rolling(150).mean().iloc[-1]) if len(df) >= 150 else np.nan
    sma200 = float(df['close'].rolling(200).mean().iloc[-1]) if len(df) >= 200 else np.nan

    def _within(a: float, b: float, pct: float) -> bool:
        if np.isnan(a) or np.isnan(b) or b == 0:
            return False
        return abs(a - b) / b * 100 <= pct

    # SMA confluence: does any SMA sit within 1.5% of the nearest fib level?
    sma_confluence = ''
    for tag, val in (('SMA50', sma50), ('SMA150', sma150), ('SMA200', sma200)):
        if _within(val, fib_price, 1.5):
            sma_confluence = tag
            break

    # Stage 2: trend alignment
    stage2 = (
        not np.isnan(sma50) and not np.isnan(sma150) and not np.isnan(sma200)
        and sma50 > sma150 > sma200
        and close > sma200
    )

    # RSI + reset zone
    rsi_series = _rsi_wilder(df['close'])
    rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty else np.nan
    rsi_reset = (not np.isnan(rsi)) and (35 <= rsi <= 50)

    # Volume expansion: last 3 days avg vs 20-day avg
    vol20 = float(df['volume'].rolling(20).mean().iloc[-1]) if len(df) >= 20 else 0.0
    vol3  = float(df['volume'].iloc[-3:].mean()) if len(df) >= 3 else 0.0
    vol_expansion = vol20 > 0 and (vol3 / vol20) >= 1.2

    # ── Scoring ──
    score = 0
    # +30 at a classic level (within 2%)
    at_classic = ratio in CLASSIC_LEVELS and abs(dist_pct) <= 2.0
    if at_classic:
        score += 30
    # +25 SMA confluence at that level
    if sma_confluence and at_classic:
        score += 25
    # +15 Stage 2
    if stage2:
        score += 15
    # +15 RSI reset
    if rsi_reset:
        score += 15
    # +10 Volume expansion
    if vol_expansion:
        score += 10
    # +5 golden pocket (50% / 61.8%)
    if at_classic and ratio in GOLDEN_POCKET:
        score += 5

    return {
        'current':         round(close, 2),
        'swing_low':       round(swing['swing_low'], 2),
        'swing_high':      round(swing['swing_high'], 2),
        'swing_high_date': swing['swing_high_date'],
        'retraced_pct':    round(retraced_pct, 1),
        'nearest_fib':     FIB_LABELS[ratio],
        'nearest_fib_ratio': ratio,
        'nearest_fib_price': round(fib_price, 2),
        'dist_to_fib_pct': round(dist_pct, 2),
        'sma_confluence':  sma_confluence,
        'stage2':          stage2,
        'rsi':             round(rsi, 1) if not np.isnan(rsi) else None,
        'rsi_reset':       rsi_reset,
        'vol_ratio_3d':    round(vol3 / vol20, 2) if vol20 else 0.0,
        'vol_expansion':   vol_expansion,
        'bounce_score':    score,
    }


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
