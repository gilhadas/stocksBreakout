#!/usr/bin/env python3
"""
premarket_monitor.py
====================
Pre-market gap scanner and sector trigger detector.

Runs at 8:00 AM and 8:45 AM ET — 90 minutes before market open — so the
Phase 1 cron at 9:35 AM can focus on stocks already showing momentum.

What it does:
  1. Checks sector ETFs (IBIT, QQQ, XLE …) for pre-market gaps via yfinance prepost=True
  2. Triggers SECTOR_BASKETS (config.py) when ETF gap >= threshold
  3. Scans a curated PRIORITY_SYMBOLS list for individual gaps >= --threshold
  4. Writes scanner_output/lists/premarket_watch.txt
     → Phase 1 (breakout_scanner.py) reads this file and pre-seeds momentum_watch_daytrade.txt
  5. Sends Discord alert summarising all pre-market movers

Usage:
    python premarket_monitor.py                   # standard run (writes file + Discord)
    python premarket_monitor.py --dry-run         # print only — no file writes, no Discord
    python premarket_monitor.py --threshold 2.5   # custom gap %% threshold
    python premarket_monitor.py --symbols SMCI WIX # add extra symbols to check

Cron (add to crontab -e):
    0  8 * * 1-5 TZ=America/New_York cd $PROJECT_ROOT && $PYTHON_BIN premarket_monitor.py --notify >> scanner_output/logs/cron_premarket.log 2>&1
    45 8 * * 1-5 TZ=America/New_York cd $PROJECT_ROOT && $PYTHON_BIN premarket_monitor.py --notify >> scanner_output/logs/cron_premarket.log 2>&1
"""

import argparse
import logging
import sys
import warnings
from datetime import datetime, timedelta
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
from config import SECTOR_BASKETS
from notifier import Notifier

NY_TZ = ZoneInfo('America/New_York')
OUT_DIR = Path('scanner_output')
LISTS_DIR = OUT_DIR / 'lists'
LOG_DIR = OUT_DIR / 'logs'
PREMARKET_FILE = LISTS_DIR / 'premarket_watch.txt'

DEFAULT_GAP_THRESHOLD = 3.0  # individual symbol gap % to flag

# Sector ETFs monitored beyond SECTOR_BASKETS triggers
# (these only generate alerts, not basket additions)
WATCHONLY_ETFS: Dict[str, str] = {
    'QQQ':  'Tech/Nasdaq',
    'XLE':  'Energy',
    'XLF':  'Financials',
    'IBB':  'Biotech',
    'XME':  'Metals/Mining',
    'ARKK': 'Innovation/ARK',
    'GLD':  'Gold',
    'TLT':  'Bonds (inverse)',
}

# High-beta / catalyst-driven stocks always checked pre-market.
# These are often missed by the main ALL.txt scanner because they
# lack classic consolidation but move hard on sector catalysts.
PRIORITY_SYMBOLS: List[str] = [
    # Crypto-adjacent
    'COIN', 'MSTR', 'HOOD', 'MARA', 'RIOT', 'HUT', 'IREN', 'BITF', 'GLXY',
    'CORZ', 'BMNR', 'CIFR', 'APLD', 'CLSK', 'NBIS',
    # High-beta tech / AI
    'NVDA', 'AMD', 'SMCI', 'PLTR', 'CRWD', 'NET', 'SNOW', 'DDOG', 'MDB',
    # Biotech (often catalyst-driven pre-market)
    'MRNA', 'BNTX', 'NFLX',
    # High-momentum names that move with news
    'ASTS', 'RCAT', 'SOUN', 'RGTI', 'IONQ',
    # Other frequently moved
    'WIX', 'CRWV', 'ORBS', 'ETHA',
]


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _prev_close_and_vol(df_pm: pd.DataFrame, today_date) -> Tuple[Optional[float], float]:
    """Extract previous regular-session close and mean per-minute volume."""
    prev = df_pm[df_pm.index.date < today_date]
    if len(prev) == 0:
        return None, 0.0
    # Last regular-session bar (ignore after-hours noise past 16:00)
    reg = prev[(prev.index.hour < 16) | ((prev.index.hour == 16) & (prev.index.minute == 0))]
    if len(reg) == 0:
        reg = prev
    close = float(reg['Close'].iloc[-1])
    mean_vol = float(reg['Volume'].mean()) or 1.0
    return close, mean_vol


def get_premarket_gap(symbol: str, now_et: datetime) -> Optional[Dict]:
    """Return pre-market gap info for *symbol*, or None if data unavailable.

    Keys: symbol, prev_close, pm_price, gap_pct, pm_volume, pm_vol_ratio
    """
    try:
        t = yf.Ticker(symbol)
        df = t.history(period='3d', interval='1m', prepost=True)
        if df is None or len(df) < 2:
            return None

        df.index = df.index.tz_convert('America/New_York')
        today = now_et.date()

        prev_close, mean_min_vol = _prev_close_and_vol(df, today)
        if prev_close is None:
            return None

        # Pre-market = today, 04:00–09:29 ET
        pm = df[
            (df.index.date == today) &
            (df.index.hour >= 4) &
            ~((df.index.hour == 9) & (df.index.minute >= 30))
        ]
        if len(pm) == 0:
            return None

        pm_price = float(pm['Close'].iloc[-1])
        pm_volume = int(pm['Volume'].sum())
        gap_pct = (pm_price - prev_close) / prev_close * 100
        pm_vol_ratio = (pm_volume / (mean_min_vol * len(pm))) if mean_min_vol > 0 else 0.0

        return {
            'symbol': symbol,
            'prev_close': round(prev_close, 2),
            'pm_price': round(pm_price, 2),
            'gap_pct': round(gap_pct, 2),
            'pm_volume': pm_volume,
            'pm_vol_ratio': round(pm_vol_ratio, 2),
        }
    except Exception as e:
        logger.debug(f"{symbol}: {e}")
        return None


# ---------------------------------------------------------------------------
# Sector basket triggers
# ---------------------------------------------------------------------------

def check_sector_triggers(
    now_et: datetime,
    default_threshold: float,
) -> Tuple[List[Dict], List[str]]:
    """Check SECTOR_BASKETS ETFs and return triggered baskets + symbol list."""
    triggered: List[Dict] = []
    symbols: List[str] = []
    seen: set = set()

    for basket_name, basket in SECTOR_BASKETS.items():
        etf = basket['trigger_etf']
        threshold = basket.get('trigger_pct', default_threshold)
        result = get_premarket_gap(etf, now_et)
        if result is None:
            logger.debug(f"No pre-market data for basket ETF {etf}")
            continue

        gap = result['gap_pct']
        logger.info(f"  Basket ETF {etf:<6} {gap:+.1f}%  (threshold ±{threshold}%)")

        if abs(gap) >= threshold:
            new_syms = [s for s in basket['symbols'] if s not in seen]
            triggered.append({
                'basket_name': basket_name,
                'etf': etf,
                'gap_pct': gap,
                'symbols': new_syms,
                'threshold': threshold,
            })
            for s in new_syms:
                seen.add(s)
            symbols.extend(new_syms)

    return triggered, symbols


# ---------------------------------------------------------------------------
# Individual gapper scan
# ---------------------------------------------------------------------------

def scan_priority_symbols(
    now_et: datetime,
    gap_threshold: float,
    extra: Optional[List[str]] = None,
) -> List[Dict]:
    """Fetch pre-market gap for PRIORITY_SYMBOLS + any extras."""
    to_check = list(dict.fromkeys(PRIORITY_SYMBOLS + (extra or [])))
    gappers: List[Dict] = []

    for sym in to_check:
        r = get_premarket_gap(sym, now_et)
        if r and abs(r['gap_pct']) >= gap_threshold:
            gappers.append(r)

    gappers.sort(key=lambda x: abs(x['gap_pct']), reverse=True)
    return gappers


# ---------------------------------------------------------------------------
# ETF-only context (no basket, just market colour)
# ---------------------------------------------------------------------------

def scan_watchonly_etfs(now_et: datetime) -> List[Dict]:
    """Check WATCHONLY_ETFS and return gap data for context (no basket trigger)."""
    results = []
    for etf, sector_name in WATCHONLY_ETFS.items():
        r = get_premarket_gap(etf, now_et)
        if r:
            r['sector_name'] = sector_name
            results.append(r)
    results.sort(key=lambda x: abs(x['gap_pct']), reverse=True)
    return results


# ---------------------------------------------------------------------------
# Discord formatting
# ---------------------------------------------------------------------------

def _sign(v: float) -> str:
    return "↑" if v > 0 else "↓"


def format_discord_message(
    triggered_baskets: List[Dict],
    gappers: List[Dict],
    etf_context: List[Dict],
    now_et: datetime,
    total_watch: int,
    dry_run: bool,
) -> Tuple[str, str]:

    basket_summary = ', '.join(
        f"{b['etf']} {b['gap_pct']:+.1f}%" for b in triggered_baskets
    )
    subject = (
        f"⚡ Pre-Market {now_et.strftime('%H:%M ET')} — {total_watch} symbols flagged"
        + (f" | {basket_summary}" if basket_summary else "")
    )

    lines: List[str] = [
        f"**Pre-market scan {now_et.strftime('%H:%M ET')} ({now_et.strftime('%Y-%m-%d')})**\n"
    ]

    # Sector ETF context
    if etf_context:
        lines.append("**Market Context (ETF gaps):**")
        for e in etf_context:
            bar = "🟢" if e['gap_pct'] > 0 else "🔴"
            lines.append(f"  {bar} `{e['symbol']:<5}` {e['gap_pct']:+.1f}%  ({e['sector_name']})")
        lines.append("")

    # Triggered baskets
    if triggered_baskets:
        lines.append("**🚨 Sector Basket Triggers:**")
        for b in triggered_baskets:
            syms_str = ', '.join(b['symbols'][:10])
            if len(b['symbols']) > 10:
                syms_str += f" +{len(b['symbols'])-10} more"
            lines.append(
                f"  {_sign(b['gap_pct'])} `{b['etf']}` **{b['gap_pct']:+.1f}%** "
                f"→ **{b['basket_name'].upper()}** basket added: {syms_str}"
            )
        lines.append("")

    # Individual gappers
    if gappers:
        lines.append("**📈 Individual Pre-Market Gappers:**")
        for g in gappers[:15]:
            lines.append(
                f"  {_sign(g['gap_pct'])} `{g['symbol']:<8}` **{g['gap_pct']:+.1f}%**  "
                f"(${g['pm_price']} vs ${g['prev_close']})"
            )
        if len(gappers) > 15:
            lines.append(f"  … and {len(gappers)-15} more")
        lines.append("")

    lines.append(f"→ {total_watch} symbols written to `premarket_watch.txt`")
    lines.append("→ Phase 1 at 9:35 AM will inject these into `momentum_watch_daytrade.txt`")
    if dry_run:
        lines.append("*(dry-run — no files written)*")

    return subject, '\n'.join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Pre-market gap scanner and sector basket trigger'
    )
    parser.add_argument('--threshold', type=float, default=DEFAULT_GAP_THRESHOLD,
                        metavar='PCT',
                        help=f'Gap %% threshold for individual symbols (default: {DEFAULT_GAP_THRESHOLD})')
    parser.add_argument('--symbols', nargs='+', metavar='SYM',
                        help='Extra symbols to check beyond the priority list')
    parser.add_argument('--notify', action='store_true',
                        help='Force enable Discord notifications')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print results only — no file writes, no Discord')
    parser.add_argument('--output', default=str(PREMARKET_FILE),
                        help=f'Output file (default: {PREMARKET_FILE})')
    args = parser.parse_args()

    now_et = datetime.now(NY_TZ)
    logger.info(f"Pre-market monitor | {now_et.strftime('%Y-%m-%d %H:%M %Z')}")

    if now_et.hour >= 9 and now_et.minute >= 35:
        logger.warning("Market already open — pre-market data may be stale, proceeding anyway.")

    # 1 — Sector ETF triggers (basket additions)
    logger.info("── Sector basket ETF check ──────────────────────")
    triggered_baskets, basket_syms = check_sector_triggers(now_et, args.threshold)

    # 2 — Priority individual gappers
    logger.info(f"── Priority symbols ({len(PRIORITY_SYMBOLS)}) ──────────────────────")
    gappers = scan_priority_symbols(now_et, args.threshold, extra=args.symbols)

    # 3 — Watch-only ETFs for market context
    logger.info("── Market context ETFs ──────────────────────────")
    etf_context = scan_watchonly_etfs(now_et)

    # Merge, deduplicate
    all_watch = list(dict.fromkeys(basket_syms + [g['symbol'] for g in gappers]))

    # ── Summary ──────────────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    if triggered_baskets:
        for b in triggered_baskets:
            logger.info(
                f"  {_sign(b['gap_pct'])} BASKET '{b['basket_name'].upper()}': "
                f"{b['etf']} {b['gap_pct']:+.1f}% → {len(b['symbols'])} symbols added"
            )
    if gappers:
        logger.info("  Individual gappers:")
        for g in gappers:
            logger.info(
                f"    {_sign(g['gap_pct'])} {g['symbol']:<8} {g['gap_pct']:+.1f}%  "
                f"(${g['pm_price']} vs ${g['prev_close']})"
            )
    if not triggered_baskets and not gappers:
        logger.info("  No pre-market gaps above threshold — nothing to flag.")
        logger.info("  (Run with --threshold 1.5 to lower sensitivity)")
    logger.info(f"  Total watch symbols: {len(all_watch)}")
    logger.info("=" * 60)
    logger.info("")

    # ── Write output ─────────────────────────────────────────────────────────
    if not args.dry_run:
        if all_watch:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text('\n'.join(all_watch))
            logger.info(f"Written {len(all_watch)} symbols → {out}")
        else:
            logger.info("No symbols to write — premarket_watch.txt not updated.")
    else:
        logger.info("Dry-run — no file written.")

    # ── Discord alert ─────────────────────────────────────────────────────────
    should_notify = (not args.dry_run) and all_watch and (args.notify or triggered_baskets)
    if should_notify:
        subject, message = format_discord_message(
            triggered_baskets, gappers, etf_context, now_et,
            len(all_watch), dry_run=False
        )
        notifier = Notifier()
        sent = notifier.send_discord(subject=subject, message=message)
        logger.info("Discord alert sent" if sent else "Discord alert failed")

    return 0 if all_watch else 1


if __name__ == '__main__':
    sys.exit(main())
