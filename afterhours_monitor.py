#!/usr/bin/env python3
"""
afterhours_monitor.py
=====================
After-hours gap scanner focused on the earnings `reporting_watchlist.txt`.

Runs after the regular close to catch moves driven by AMC earnings releases,
analyst actions, or late-day news flow. Complements premarket_monitor.py:

    premarket_monitor.py → 08:00 / 08:45 ET  (before open)
    afterhours_monitor.py → 16:15 / 20:00 ET (after close)

What it does:
  1. Loads input/reporting_watchlist.txt (symbols reporting earnings this week)
  2. For each, fetches today's regular close and post-16:00 ET after-hours bars
  3. Flags any symbol with an after-hours move ≥ --threshold (default 3%)
  4. Tags names that are currently held in the auto-portfolio (⚠ HELD)
  5. Tags names with earnings today/tomorrow (from yfinance calendar)
  6. Writes scanner_output/lists/afterhours_watch.txt (seeds next morning's premarket scan)
  7. Sends a Discord alert summarising movers

Usage:
    python afterhours_monitor.py                  # standard run
    python afterhours_monitor.py --dry-run        # print only — no file writes, no Discord
    python afterhours_monitor.py --threshold 2.0  # lower sensitivity
    python afterhours_monitor.py --symbols NVDA COIN  # add extra symbols

Cron:
    15 16 * * 1-5 TZ=America/New_York ... afterhours_monitor.py --notify      # post-close
    0  20 * * 1-5 TZ=America/New_York ... afterhours_monitor.py --notify      # after AMC earnings
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
from notifier import Notifier

NY_TZ = ZoneInfo('America/New_York')
OUT_DIR = Path('scanner_output')
LISTS_DIR = OUT_DIR / 'lists'
AFTERHOURS_FILE = LISTS_DIR / 'afterhours_watch.txt'

DEFAULT_GAP_THRESHOLD = 3.0  # after-hours % move threshold to flag

REPORTING_FILE = Path('input/reporting_watchlist.txt')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------

def load_reporting_watchlist() -> List[str]:
    """Parse input/reporting_watchlist.txt (comma/newline-separated tickers)."""
    if not REPORTING_FILE.exists():
        logger.warning(f"{REPORTING_FILE} not found — no symbols to monitor.")
        return []
    text = REPORTING_FILE.read_text(encoding='utf-8')
    seen: set = set()
    syms: List[str] = []
    for tok in text.replace('\n', ',').split(','):
        s = tok.strip().upper()
        if s and s not in seen:
            seen.add(s)
            syms.append(s)
    return syms


def load_held_symbols() -> set:
    """Return the set of currently-open auto_portfolio symbols (upper-cased)."""
    try:
        import auto_portfolio as ap
        data = ap.load()
        return {p['symbol'].upper() for p in data.get('positions', [])}
    except Exception as exc:
        logger.debug(f"auto_portfolio not loadable: {exc}")
        return set()


# ---------------------------------------------------------------------------
# After-hours bar extraction
# ---------------------------------------------------------------------------

def _reg_close_and_ah_bars(
    df: pd.DataFrame,
    today: "datetime.date",
) -> Tuple[Optional[float], Optional[pd.DataFrame]]:
    """Split today's 1-min history into (regular close price, after-hours bars).

    Regular close  = last bar where hour < 16 OR (hour == 16 AND minute == 0).
    After-hours    = bars where hour >= 16 AND minute > 0, or hour in 17..19.
    """
    today_df = df[df.index.date == today]
    if len(today_df) == 0:
        return None, None

    reg = today_df[
        (today_df.index.hour < 16)
        | ((today_df.index.hour == 16) & (today_df.index.minute == 0))
    ]
    if len(reg) == 0:
        return None, None
    reg_close = float(reg['Close'].iloc[-1])

    ah = today_df[
        ((today_df.index.hour == 16) & (today_df.index.minute > 0))
        | ((today_df.index.hour >= 17) & (today_df.index.hour < 20))
    ]
    return reg_close, (ah if len(ah) > 0 else None)


def get_afterhours_move(symbol: str, now_et: datetime) -> Optional[Dict]:
    """Return after-hours move info for *symbol*, or None if no AH activity yet.

    Keys: symbol, reg_close, ah_price, ah_high, ah_low, move_pct, ah_volume
    """
    try:
        t = yf.Ticker(symbol)
        df = t.history(period='2d', interval='1m', prepost=True)
        if df is None or len(df) < 2:
            return None
        df.index = df.index.tz_convert('America/New_York')
        today = now_et.date()

        reg_close, ah = _reg_close_and_ah_bars(df, today)
        if reg_close is None or ah is None:
            return None

        ah_price = float(ah['Close'].iloc[-1])
        ah_high  = float(ah['High'].max())
        ah_low   = float(ah['Low'].min())
        ah_vol   = int(ah['Volume'].sum())
        move_pct = (ah_price - reg_close) / reg_close * 100 if reg_close else 0.0

        return {
            'symbol':    symbol,
            'reg_close': round(reg_close, 2),
            'ah_price':  round(ah_price, 2),
            'ah_high':   round(ah_high, 2),
            'ah_low':    round(ah_low, 2),
            'move_pct':  round(move_pct, 2),
            'ah_volume': ah_vol,
        }
    except Exception as exc:
        logger.debug(f"{symbol}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Earnings tagging
# ---------------------------------------------------------------------------

def fetch_earnings_tag(symbol: str, today) -> Optional[str]:
    """Return 'today AMC', 'tomorrow BMO', 'in 3d (BMO)', etc. or None."""
    try:
        cal = yf.Ticker(symbol).calendar
        if cal is None:
            return None
        raw = None
        if isinstance(cal, dict):
            ed = cal.get('Earnings Date')
            if ed:
                raw = ed[0] if isinstance(ed, list) else ed
        elif hasattr(cal, 'iloc'):
            try:
                raw = cal.iloc[0, 0]
            except Exception:
                return None
        if raw is None:
            return None

        if isinstance(raw, str):
            raw = pd.Timestamp(raw)
        if hasattr(raw, 'date'):
            ed_date = raw.date() if callable(raw.date) else raw.date
        else:
            ed_date = pd.Timestamp(str(raw)).date()

        days = (ed_date - today).days
        if days < 0 or days > 7:
            return None

        timing = ''
        if hasattr(raw, 'hour') and raw.hour > 0:
            timing = ' AMC' if raw.hour >= 12 else ' BMO'

        if days == 0:
            return f"today{timing}"
        if days == 1:
            return f"tomorrow{timing}"
        return f"in {days}d{timing}"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def scan_afterhours_movers(
    symbols: List[str],
    now_et: datetime,
    threshold: float,
) -> List[Dict]:
    """Scan *symbols* for after-hours moves ≥ threshold (absolute %)."""
    movers: List[Dict] = []
    today = now_et.date()
    held = load_held_symbols()

    for sym in symbols:
        r = get_afterhours_move(sym, now_et)
        if not r:
            continue
        if abs(r['move_pct']) < threshold:
            continue
        r['is_held'] = sym.upper() in held
        r['earnings_tag'] = fetch_earnings_tag(sym, today)
        movers.append(r)

    movers.sort(key=lambda x: abs(x['move_pct']), reverse=True)
    return movers


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _sign(pct: float) -> str:
    return '▲' if pct >= 0 else '▼'


def format_discord_message(
    movers: List[Dict],
    now_et: datetime,
    total_watched: int,
    threshold: float,
) -> Tuple[str, str]:
    held_movers = [m for m in movers if m['is_held']]
    earnings_movers = [m for m in movers if m['earnings_tag']]

    subject = (
        f"🌙 After-Hours {now_et.strftime('%H:%M ET')} — "
        f"{len(movers)} mover(s) on reporting watchlist"
    )
    if held_movers:
        subject += f" · {len(held_movers)} HELD"

    lines = [
        f"**After-hours scan {now_et.strftime('%Y-%m-%d %H:%M ET')}**",
        f"Watched {total_watched} symbols, threshold ±{threshold}%.\n",
    ]

    if held_movers:
        lines.append(f"**⚠ HELD positions moving after-hours ({len(held_movers)}):**")
        for m in held_movers:
            tag = f"  · earnings {m['earnings_tag']}" if m['earnings_tag'] else ''
            lines.append(
                f"  {_sign(m['move_pct'])} `{m['symbol']:<6}` **{m['move_pct']:+.1f}%**  "
                f"${m['ah_price']} (close ${m['reg_close']}){tag}"
            )
        lines.append("")

    if earnings_movers:
        lines.append(f"**📅 Earnings-window movers ({len(earnings_movers)}):**")
        for m in earnings_movers[:15]:
            held_flag = '  ⚠ HELD' if m['is_held'] else ''
            lines.append(
                f"  {_sign(m['move_pct'])} `{m['symbol']:<6}` **{m['move_pct']:+.1f}%**  "
                f"${m['ah_price']} (close ${m['reg_close']})  "
                f"— earnings {m['earnings_tag']}{held_flag}"
            )
        if len(earnings_movers) > 15:
            lines.append(f"  … and {len(earnings_movers) - 15} more")
        lines.append("")

    other = [m for m in movers if not m['is_held'] and not m['earnings_tag']]
    if other:
        lines.append(f"**Other watchlist movers ({len(other)}):**")
        for m in other[:15]:
            lines.append(
                f"  {_sign(m['move_pct'])} `{m['symbol']:<6}` {m['move_pct']:+.1f}%  "
                f"${m['ah_price']} (close ${m['reg_close']})"
            )
        if len(other) > 15:
            lines.append(f"  … and {len(other) - 15} more")
        lines.append("")

    lines.append(f"→ Written to `{AFTERHOURS_FILE.name}` — premarket scan picks it up tomorrow.")
    return subject, '\n'.join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description='After-hours scanner for earnings reporting watchlist'
    )
    parser.add_argument('--threshold', type=float, default=DEFAULT_GAP_THRESHOLD,
                        metavar='PCT',
                        help=f'Absolute %% move to flag (default: {DEFAULT_GAP_THRESHOLD})')
    parser.add_argument('--symbols', nargs='+', metavar='SYM',
                        help='Extra symbols to check beyond the reporting watchlist')
    parser.add_argument('--notify', action='store_true',
                        help='Force enable Discord notifications')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print only — no file writes, no Discord')
    parser.add_argument('--output', default=str(AFTERHOURS_FILE),
                        help=f'Output file (default: {AFTERHOURS_FILE})')
    args = parser.parse_args()

    now_et = datetime.now(NY_TZ)
    logger.info(f"After-hours monitor | {now_et.strftime('%Y-%m-%d %H:%M %Z')}")

    if now_et.hour < 16:
        logger.warning(
            "Market not yet closed — after-hours data will be empty. "
            "Run between 16:15 and 20:00 ET."
        )

    symbols = load_reporting_watchlist()
    if args.symbols:
        symbols = list(dict.fromkeys(symbols + [s.upper() for s in args.symbols]))
    if not symbols:
        logger.warning("No symbols to scan. Populate input/reporting_watchlist.txt.")
        return 1

    logger.info(f"── Scanning {len(symbols)} symbols for after-hours moves ──")
    movers = scan_afterhours_movers(symbols, now_et, args.threshold)

    # ── Summary ──
    logger.info("")
    logger.info("=" * 60)
    if movers:
        for m in movers:
            flags = []
            if m['is_held']:
                flags.append('HELD')
            if m['earnings_tag']:
                flags.append(f"earnings {m['earnings_tag']}")
            tag = f"  ({', '.join(flags)})" if flags else ''
            logger.info(
                f"  {_sign(m['move_pct'])} {m['symbol']:<6} {m['move_pct']:+.1f}%  "
                f"(${m['ah_price']} vs close ${m['reg_close']}){tag}"
            )
    else:
        logger.info(f"  No after-hours moves ≥ {args.threshold}%.")
    logger.info("=" * 60)
    logger.info("")

    # ── Write output ──
    if not args.dry_run and movers:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('\n'.join(m['symbol'] for m in movers))
        logger.info(f"Written {len(movers)} symbols → {out}")
    elif args.dry_run:
        logger.info("Dry-run — no file written.")

    # ── Discord alert ──
    should_notify = (not args.dry_run) and movers and args.notify
    if should_notify:
        subject, message = format_discord_message(
            movers, now_et, len(symbols), args.threshold,
        )
        try:
            notifier = Notifier()
            held_present = any(m['is_held'] for m in movers)
            notification_type = 'exits' if held_present else 'alerts'
            notifier.send_discord(
                subject=subject, message=message,
                notification_type=notification_type,
            )
            logger.info(f"Discord alert sent ({notification_type}).")
        except Exception as exc:
            logger.warning(f"Discord send failed: {exc}")

    return 0 if movers else 1


if __name__ == '__main__':
    sys.exit(main())
