"""
scan_feedback_agent.py
──────────────────────
Rejection watchdog: monitors symbols from today's scan_decisions CSV and sends
Discord alerts when their status changes (breakout, surge, direction flip,
or pipeline PROGRESS).

Runs every 5 min (or --interval). On each pass it:
  1. Loads all unique symbols from scan_decisions_YYYYMMDD.csv.
  2. Fetches their current price via yfinance.
  3. Compares against persisted state (scan_decisions_state_YYYYMMDD.json).
  4. Fires a Discord alert when:
       BREAKOUT  – scanner confirms breakout (price > prev_close + full indicator validation)
       PROGRESS  – symbol advanced deeper through the scanner pipeline
       SURGE     – price moved ≥ SURGE_THRESHOLD% since last check
       FLIP      – direction changed (UP→DOWN, etc.)
  5. Re-runs the scanner every --rescan-interval seconds to detect pipeline changes.
  6. Writes all observations to scan_feedback_YYYYMMDD.csv.

Usage:
  python3 scan_feedback_agent.py              # single run
  python3 scan_feedback_agent.py --loop       # every 5 min until Ctrl-C
  python3 scan_feedback_agent.py --interval 180 --loop
  python3 scan_feedback_agent.py --rescan-interval 0 --loop   # force re-scan every pass
  python3 scan_feedback_agent.py --summary    # print today's learning report
"""

import argparse
import csv
import fcntl
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import yfinance as yf

from config import NOTIFICATIONS, OUTPUT_DIR, MODES
from notifier import Notifier
import auto_portfolio as ap
from zoneinfo import ZoneInfo

_NY_TZ       = ZoneInfo('America/New_York')
_SCAN_DIR    = Path(OUTPUT_DIR) / 'scan_decisions'
_DEFAULT_INT = 300   # seconds
_DEFAULT_RESCAN_INT = 900  # 15 min — 3-mode rescan on 73 symbols takes ~3 min (validated)

# Alert thresholds
SURGE_THRESHOLD    = 2.0   # % move since last check that triggers SURGE alert
MISS_THRESHOLD     = 2.0   # % above scan_price considered a missed opportunity
FLAT_BAND          = 0.3   # % band for FLAT classification
# Mode-aware exit thresholds (fail = failed breakout %, trail = trailing stop %)
MODE_EXIT_PCT = {
    'daytrade': {'fail': 0.3,  'trail': 0.8},
    'swing':    {'fail': 1.0,  'trail': 2.5},
    'longterm': {'fail': 2.0,  'trail': 4.0},
}
CONFIRM_PASSES     = 1     # consecutive passes above prev_close (1 = immediate; backtest showed 2 adds no value)

# Pipeline stage depth — higher number = symbol got further through the scanner.
# Matches the actual gate order in orchestrator.py → scanner.py.
STAGE_DEPTH = {
    'no_data':           0,
    'insufficient_bars': 1,
    'price_range':       2,
    'spread':            3,
    'bearish_bb':        4,
    'no_breakout':       5,
    'low_liquidity':     6,
    'rr_grade_d':        7,
    'blow_off':          8,
    'low_score':         9,
    'failed_conditions': 10,
    'other':             5,
    'ACCEPTED':          11,
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
)
logger = logging.getLogger(__name__)

# ── CSV schema ────────────────────────────────────────────────────────────────
FB_COLUMNS = [
    'check_timestamp', 'symbol', 'first_seen_ts', 'reason_code',
    'scan_price', 'prev_high', 'current_price',
    'pct_from_scan', 'pct_since_last', 'direction', 'event',
    'prev_reason_code', 'score',
]

# ── Path helpers ──────────────────────────────────────────────────────────────

def _today(date_str: Optional[str] = None) -> str:
    return date_str or datetime.now(_NY_TZ).strftime('%Y%m%d')

def _decisions_path(date_str: str) -> Path:
    return _SCAN_DIR / f'scan_decisions_{date_str}.csv'

def _feedback_path(date_str: str) -> Path:
    return _SCAN_DIR / f'scan_feedback_{date_str}.csv'

def _state_path(date_str: str) -> Path:
    return _SCAN_DIR / f'scan_decisions_state_{date_str}.json'

def _init_feedback_csv(path: Path) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', newline='') as f:
            csv.DictWriter(f, fieldnames=FB_COLUMNS).writeheader()


# ── State persistence ─────────────────────────────────────────────────────────

def _load_state(date_str: str) -> dict:
    p = _state_path(date_str)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}

def _save_state(date_str: str, state: dict) -> None:
    _state_path(date_str).write_text(json.dumps(state, indent=2))


# ── Price fetching ────────────────────────────────────────────────────────────

def _fetch_prices(symbols: list[str]) -> tuple[dict[str, float], dict[str, float]]:
    """Batch-fetch latest prices and volume ratios via yfinance.

    Returns (prices, vol_ratios) where:
      prices     = {symbol: float}
      vol_ratios = {symbol: float}  — last bar volume / 20-bar avg volume
    """
    if not symbols:
        return {}, {}
    try:
        raw = yf.download(
            symbols, period='1d', interval='1m',
            progress=False, auto_adjust=True,
            group_by='ticker' if len(symbols) > 1 else None,
        )
        prices:     dict[str, float] = {}
        vol_ratios: dict[str, float] = {}
        if len(symbols) == 1:
            sym = symbols[0]
            if not raw.empty:
                closes = raw['Close'].dropna()
                if not closes.empty:
                    prices[sym] = float(closes.iloc[-1])
                vols = raw['Volume'].dropna()
                if len(vols) >= 5:
                    avg_v = float(vols.iloc[-20:].mean()) if len(vols) >= 20 else float(vols.mean())
                    vol_ratios[sym] = float(vols.iloc[-1]) / avg_v if avg_v > 0 else 0.0
        else:
            for sym in symbols:
                try:
                    col = raw[sym]['Close'].dropna()
                    if not col.empty:
                        prices[sym] = float(col.iloc[-1])
                    vols = raw[sym]['Volume'].dropna()
                    if len(vols) >= 5:
                        avg_v = float(vols.iloc[-20:].mean()) if len(vols) >= 20 else float(vols.mean())
                        vol_ratios[sym] = float(vols.iloc[-1]) / avg_v if avg_v > 0 else 0.0
                except Exception:
                    pass
        return prices, vol_ratios
    except Exception as e:
        logger.debug(f"Batch price fetch failed: {e}")
        return {}, {}


# ── Previous day close (daily-level reference for BREAKOUT detection) ─────────

_prev_close_cache: dict[str, float] = {}
_prev_close_date: str = ''


def _fetch_prev_close(symbols: list[str]) -> dict[str, float]:
    """Batch-fetch previous trading day's close for each symbol.

    Cached per calendar day — the value doesn't change intraday.
    Returns {symbol: prev_day_close_price}.
    """
    global _prev_close_cache, _prev_close_date
    today = datetime.now(_NY_TZ).strftime('%Y%m%d')
    if _prev_close_date == today and _prev_close_cache:
        # Filter to requested symbols (may be subset)
        return {s: _prev_close_cache[s] for s in symbols if s in _prev_close_cache}

    if not symbols:
        return {}
    try:
        raw = yf.download(
            symbols, period='5d', interval='1d',
            progress=False, auto_adjust=True,
            group_by='ticker' if len(symbols) > 1 else None,
        )
        result: dict[str, float] = {}
        if len(symbols) == 1:
            sym = symbols[0]
            closes = raw['Close'].dropna()
            if len(closes) >= 2:
                # Last row may be today's partial bar; use second-to-last
                last_date = closes.index[-1]
                if hasattr(last_date, 'date') and last_date.date() == datetime.now(_NY_TZ).date():
                    result[sym] = float(closes.iloc[-2])
                else:
                    result[sym] = float(closes.iloc[-1])
        else:
            for sym in symbols:
                try:
                    closes = raw[sym]['Close'].dropna()
                    if len(closes) >= 2:
                        last_date = closes.index[-1]
                        if hasattr(last_date, 'date') and last_date.date() == datetime.now(_NY_TZ).date():
                            result[sym] = float(closes.iloc[-2])
                        else:
                            result[sym] = float(closes.iloc[-1])
                except Exception:
                    pass
        _prev_close_cache = result
        _prev_close_date = today
        logger.info(f"Fetched prev_close for {len(result)}/{len(symbols)} symbols")
        return result
    except Exception as e:
        logger.warning(f"prev_close batch fetch failed: {e}")
        return {}


# ── Scanner-based BREAKOUT validation ─────────────────────────────────────────

_spy_perf_cache: tuple[float, float] = (0.0, 0.0)  # (value, timestamp)


def _get_spy_perf() -> float:
    """Get SPY 15-day performance, cached for 15 minutes."""
    global _spy_perf_cache
    val, ts = _spy_perf_cache
    if time.time() - ts < 900:
        return val
    try:
        spy = yf.Ticker('SPY').history(period='1mo', interval='1d')
        if len(spy) >= 15:
            perf = (spy['Close'].iloc[-1] / spy['Close'].iloc[-15]) - 1
        else:
            perf = 0.0
        _spy_perf_cache = (float(perf), time.time())
        return float(perf)
    except Exception:
        return _spy_perf_cache[0]


def _mode_to_yf_params(mode: str) -> tuple[str, str]:
    """Map trading mode to yfinance (interval, period)."""
    return {
        'daytrade': ('15m', '5d'),
        'scalping': ('5m',  '2d'),
    }.get(mode, ('1d', '6mo'))


def _validate_breakout_with_scanner(
    symbol: str, mode: str, detector, regime: str = 'NORMAL'
) -> dict | None:
    """Run the full scanner pipeline to validate a potential breakout.

    Returns the signal dict if confirmed, None otherwise.
    """
    cfg = MODES.get(mode, MODES['swing'])
    yf_interval, yf_period = _mode_to_yf_params(mode)

    try:
        hist = yf.Ticker(symbol.replace(' ', '-')).history(
            period=yf_period, interval=yf_interval
        )
        if hist is None or hist.empty:
            return None
        # Scanner expects lowercase columns (same as yfinance_adapter.py:139)
        hist.columns = hist.columns.str.lower()

        min_bars = cfg.get('trend_period', 50)
        if len(hist) < min_bars:
            return None

        spy_perf = _get_spy_perf()

        # Map yf interval to IB-style timeframe the scanner expects
        tf_map = {'15m': '15 mins', '5m': '5 mins', '1d': '1 day'}
        timeframe = tf_map.get(yf_interval, '1 day')

        signal = detector.detect(
            hist, symbol, mode, timeframe,
            spy_perf=spy_perf,
            regime=regime,
            use_scoring=True,
        )
        return signal
    except Exception as e:
        logger.debug(f"Scanner validation failed for {symbol}: {e}")
        return None


# ── Direction ─────────────────────────────────────────────────────────────────

def _direction(pct: float) -> str:
    if pct > FLAT_BAND:
        return 'UP'
    if pct < -FLAT_BAND:
        return 'DOWN'
    return 'FLAT'


# ── Discord notification ──────────────────────────────────────────────────────

def _discord_color(event: str) -> int:
    return {
        'BREAKOUT': 0x00ff00,   # green
        'PROGRESS': 0x3498db,   # blue
        'ACCEPTED': 0x2ecc71,   # bright green
        'SURGE':    0xffaa00,   # orange
        'FLIP':     0xff4444,   # red-ish
        'EXIT':     0x9b59b6,   # purple
    }.get(event, 0x888888)

def _send_discord_alerts(alerts: list[dict]) -> None:
    """Post one Discord embed per alert event to the 'alerts' webhook."""
    try:
        cfg = NOTIFICATIONS.get('discord', {})
        if not cfg.get('enabled'):
            logger.debug("Discord not enabled — skipping alert")
            return
        
        # Get 'alerts' webhook from new webhooks dict, fallback to legacy webhook_url
        webhooks = cfg.get('webhooks', {})
        webhook_url = webhooks.get('alerts') or cfg.get('webhook_url')
        
        if not webhook_url:
            logger.debug("No Discord alerts webhook configured — skipping")
            return
    except Exception:
        return

    for alert in alerts:
        event    = alert['event']
        symbol   = alert['symbol']
        cur      = alert['current_price']
        pct_scan = alert['pct_from_scan']
        pct_last = alert['pct_since_last']
        reason   = alert['reason_code']
        prev_cl  = alert.get('prev_close')

        title = f"[FEEDBACK] {symbol} — {event}"

        if event == 'PROGRESS':
            prev_reason = alert.get('prev_reason_code', '?')
            prev_depth  = STAGE_DEPTH.get(prev_reason, -1)
            new_depth   = STAGE_DEPTH.get(reason, -1)
            gates       = new_depth - prev_depth
            lines = [
                f"**Pipeline advancement**",
                f"{prev_reason} (stage {prev_depth}) → {reason} (stage {new_depth})",
                f"Passed {gates} additional gate(s)",
                f"**Current:** ${cur:.2f}",
            ]
            score_val = alert.get('score')
            if score_val is not None and reason == 'low_score':
                lines.append(f"**Score:** {score_val}")
            detail = alert.get('progress_detail', '')
            if 'score improved' in detail:
                lines.append(f"**{detail}**")

        elif event == 'ACCEPTED':
            prev_reason = alert.get('prev_reason_code', '?')
            prev_depth  = STAGE_DEPTH.get(prev_reason, -1)
            lines = [
                f"**Signal now passes all checks!**",
                f"Previously rejected: {prev_reason} (stage {prev_depth})",
                f"**Current:** ${cur:.2f}",
            ]

        elif event == 'EXIT':
            exit_reason  = alert.get('exit_reason', '?')
            entry_price  = alert.get('entry_price', 0)
            trade_ret    = alert.get('trade_ret_pct', 0)
            post_bo_high = alert.get('post_bo_high', 0)
            result_emoji = '✓ WIN' if trade_ret > 0 else '✗ LOSS'
            lines = [
                f"**{result_emoji}** — {exit_reason}",
                f"**Entry:** ${entry_price:.2f}  →  **Exit:** ${cur:.2f}",
                f"**Return:** {trade_ret:+.2f}%",
            ]
            if post_bo_high and post_bo_high > 0:
                lines.append(f"**Peak:** ${post_bo_high:.2f}  (unrealized {((post_bo_high - entry_price) / entry_price * 100):+.1f}%)")

        else:
            lines = [
                f"**Current:** ${cur:.2f}",
                f"**vs scan price:** {pct_scan:+.2f}%",
                f"**vs last check:** {pct_last:+.2f}%",
                f"**Rejected for:** {reason}",
            ]
            if event == 'BREAKOUT':
                quality = alert.get('quality', '')
                rr_val  = alert.get('rr', 0)
                vol_r   = alert.get('vol_ratio', 0)
                rsi_val = alert.get('rsi', '')
                patterns = alert.get('patterns', '')
                lines = [
                    f"**Scanner confirmed: {quality}**",
                    f"**Price:** ${cur:.2f}",
                ]
                if prev_cl:
                    lines.append(f"**Prev close:** ${prev_cl:.2f} ← crossed ✓")
                if rr_val:
                    lines.append(f"**R:R:** {rr_val:.1f}")
                if vol_r:
                    lines.append(f"**Volume:** {vol_r:.1f}x")
                if rsi_val:
                    lines.append(f"**RSI:** {rsi_val}")
                if patterns:
                    lines.append(f"**Patterns:** {patterns}")
                stop_val = alert.get('stop', 0)
                tgt_val  = alert.get('target', 0)
                if stop_val and tgt_val:
                    lines.append(f"**Stop:** ${stop_val:.2f}  |  **Target:** ${tgt_val:.2f}")

        embed = {
            'title':       title,
            'description': '\n'.join(lines),
            'color':       _discord_color(event),
        }
        try:
            r = requests.post(
                webhook_url,
                json={'embeds': [embed]},
                timeout=8,
            )
            if r.status_code == 204:
                logger.info(f"Discord alert sent to 'alerts' webhook: {title}")
            else:
                logger.warning(f"Discord returned {r.status_code} for {symbol}")
        except Exception as e:
            logger.error(f"Discord post failed for {symbol}: {e}")


# ── Telegram notification ────────────────────────────────────────────────────

def _send_telegram_alerts(alerts: list[dict]) -> None:
    """Send scalp BREAKOUT alerts to Telegram."""
    try:
        cfg = NOTIFICATIONS.get('telegram', {})
        if not cfg.get('enabled'):
            return
        bot_token = cfg.get('bot_token', '')
        chat_id   = cfg.get('chat_id', '')
        if not bot_token or not chat_id:
            return
    except Exception:
        return

    for alert in alerts:
        symbol = alert['symbol']
        cur    = alert['current_price']
        prev_cl = alert.get('prev_close')
        vol    = alert.get('vol_ratio', 0)

        mode     = alert.get('mode', 'swing')
        quality  = alert.get('quality', '')
        stop_val = alert.get('stop', 0)
        tgt_val  = alert.get('target', 0)
        rr_val   = alert.get('rr', 0)
        patterns = alert.get('patterns', '')
        lines = [
            f"BREAKOUT [{mode.upper()}] {quality}: {symbol}",
            f"Price: ${cur:.2f}",
        ]
        if prev_cl:
            lines.append(f"Prev Close: ${prev_cl:.2f}")
        if vol:
            lines.append(f"Vol: {vol:.1f}x")
        if rr_val:
            lines.append(f"R:R: {rr_val:.1f}")
        if stop_val:
            lines.append(f"Stop: ${stop_val:.2f} | Target: ${tgt_val:.2f}")
        if patterns:
            lines.append(f"Patterns: {patterns}")

        text = '\n'.join(lines)
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            r = requests.post(url, data={
                'chat_id': chat_id,
                'text': text,
            }, timeout=8)
            if r.status_code == 200:
                logger.info(f"Telegram alert sent: {symbol}")
            else:
                logger.warning(f"Telegram returned {r.status_code} for {symbol}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"Telegram post failed for {symbol}: {e}")


# ── Email notification ────────────────────────────────────────────────────────

def _send_email_alerts(alerts: list[dict]) -> None:
    """Send BREAKOUT alerts via email."""
    notifier = Notifier()
    if not notifier.email_enabled:
        return

    subject = f"BREAKOUT Alert: {', '.join(a['symbol'] for a in alerts)}"

    lines = []
    for alert in alerts:
        symbol  = alert['symbol']
        cur     = alert['current_price']
        prev_cl = alert.get('prev_close')
        vol     = alert.get('vol_ratio', 0)
        pct     = alert.get('pct_from_scan', 0)
        quality = alert.get('quality', '')
        rr_val  = alert.get('rr', 0)

        lines.append(f"• {symbol} @ ${cur:.2f}  ({pct:+.1f}% from scan) — {quality}")
        if prev_cl:
            lines.append(f"  Broke above prev close: ${prev_cl:.2f}")
        if vol:
            lines.append(f"  Vol: {vol:.1f}x")
        if rr_val:
            lines.append(f"  R:R: {rr_val:.1f}")
        lines.append('')

    body = f"BREAKOUT alert — {len(alerts)} symbol(s)\n\n" + '\n'.join(lines)
    notifier.send_email(subject, body)
    logger.info(f"Email alert sent: {subject}")


# ── Scanner runner ────────────────────────────────────────────────────────────

# Rescan merges momentum_watch_daytrade.txt (73) + 1_26_Setups.txt (147) = ~203 unique symbols
_MOMENTUM_WATCHLIST = Path(OUTPUT_DIR) / 'lists' / 'momentum_watch_daytrade.txt'
_CURATED_WATCHLIST  = Path('input') / '1_26_Setups.txt'
_RESCAN_MODES       = ['daytrade', 'swing']   # longterm excluded: ~60 min/mode, handled by weekly cron


def _build_rescan_watchlist() -> Path | None:
    """
    Merge momentum_watch_daytrade.txt + 1_26_Setups.txt into a temp file.
    Returns path to merged file, or None if neither source exists.
    Validated timing: ~203 symbols × 3 modes ≈ 8 min total.
    """
    symbols: set[str] = set()

    if _MOMENTUM_WATCHLIST.exists():
        for line in _MOMENTUM_WATCHLIST.read_text().splitlines():
            sym = line.strip().upper()
            if sym and not sym.startswith('#'):
                symbols.add(sym)

    _SKIP_EXCHANGES = {
        'BINANCE', 'BITSTAMP', 'COINBASE', 'CAPITALCOM', 'BSE',
        'XETR', 'MIL', 'SP', 'FX', 'CRYPTO', 'COMEX', 'NYMEX',
    }
    if _CURATED_WATCHLIST.exists():
        for token in _CURATED_WATCHLIST.read_text().replace(',', '\n').split('\n'):
            t = token.strip().upper()
            if t and not t.startswith('#'):
                if ':' in t:
                    exchange, sym = t.split(':', 1)
                    if exchange in _SKIP_EXCHANGES:
                        continue
                else:
                    sym = t
                if sym and sym.replace('-', '').isalpha():
                    symbols.add(sym)

    if not symbols:
        return None

    merged = Path(OUTPUT_DIR) / 'scan_decisions' / '.feedback_rescan_watchlist.txt'
    merged.parent.mkdir(parents=True, exist_ok=True)
    merged.write_text('\n'.join(sorted(symbols)) + '\n')
    logger.info(f"Rescan watchlist: {len(symbols)} symbols (momentum={_MOMENTUM_WATCHLIST.exists()}, curated={_CURATED_WATCHLIST.exists()})")
    return merged


def _run_rescan(dec_path: Path, label: str = "re-scan") -> bool:
    """
    Run the breakout scanner on the merged watchlist (momentum + curated) in all 3 modes.
    Each mode appends mode-tagged rows to the decisions CSV via scan_event_log.py.
    Returns True if at least one mode completed successfully.
    Full ALL.txt scans are handled by the regular cron jobs.
    Validated: ~203 symbols × 3 modes ≈ 8 min total (fits 15-min rescan window).
    """
    watchlist = _build_rescan_watchlist()
    if watchlist is None:
        logger.info("No rescan watchlist available — skipping rescan")
        return False

    success_any = False
    for mode in _RESCAN_MODES:
        cmd = [
            sys.executable, 'breakout_scanner.py',
            str(watchlist),
            '--mock', '--bounce',
            '--mode', mode, '--debug',
        ]
        logger.info(f"Running {label} [{mode}] on {watchlist.name} …")
        try:
            result = subprocess.run(
                cmd,
                cwd=Path(__file__).parent,
                timeout=600,
                check=False,
            )
            if result.returncode == 0:
                success_any = True
                logger.info(f"{label.capitalize()} [{mode}] complete")
            else:
                logger.warning(f"Scanner [{mode}] exited with code {result.returncode}")
        except subprocess.TimeoutExpired:
            logger.error(f"Scanner [{mode}] timed out after 600 s")
        except Exception as e:
            logger.error(f"Failed to run scanner [{mode}]: {e}", exc_info=True)

    return success_any


def _load_decisions_with_mode(dec_path: Path) -> dict[str, dict]:
    """
    Read today's scan_decisions CSV and return {symbol: row} using highest-priority
    mode per symbol. Priority: longterm(3) > swing(2) > daytrade(1).
    Rows without a mode column default to 'daytrade'.
    """
    _PRIORITY = {'longterm': 3, 'swing': 2, 'daytrade': 1}
    best: dict[str, dict] = {}
    try:
        with open(dec_path, newline='') as f:
            for row in csv.DictReader(f):
                sym  = str(row.get('symbol', '')).strip().upper()
                mode = str(row.get('mode', '') or 'daytrade').strip()
                if not sym:
                    continue
                cur_pri = _PRIORITY.get(best[sym].get('mode', ''), 0) if sym in best else -1
                if _PRIORITY.get(mode, 0) > cur_pri:
                    best[sym] = dict(row)
                    best[sym]['mode'] = mode
    except Exception as e:
        logger.warning(f"Could not load decisions with mode from {dec_path}: {e}")
    return best


def _compute_stops(symbol: str, entry: float, mode: str) -> tuple[float, float]:
    """
    Compute stop and target using ATR × MODES[mode] sl_mult / tp_mult.
    Falls back to a heuristic % if ATR data is unavailable.
    """
    cfg = MODES.get(mode, MODES['swing'])
    # Normalize timeframe: config may store "1 day" or "1d" — always use yfinance format
    tf_raw = cfg.get('default_timeframe', '1d')
    is_daily = 'day' in tf_raw.lower() or tf_raw.strip() == '1d'
    tf = '1d' if is_daily else tf_raw.replace(' ', '')
    try:
        period = '3mo' if is_daily else '5d'
        hist = yf.Ticker(symbol).history(period=period, interval=tf)
        if len(hist) >= 5:
            tr = pd.concat([
                hist['High'] - hist['Low'],
                (hist['High'] - hist['Close'].shift()).abs(),
                (hist['Low']  - hist['Close'].shift()).abs(),
            ], axis=1).max(axis=1)
            atr    = tr.rolling(14).mean().iloc[-1]
            stop   = round(entry - atr * cfg['sl_mult'], 4)
            target = round(entry + atr * cfg['tp_mult'], 4)
            if stop < entry:   # sanity check: stop must be below entry for a long
                return stop, target
            logger.warning(f"_compute_stops({symbol}): ATR stop {stop} >= entry {entry}, using fallback")
    except Exception as exc:
        logger.warning(f"_compute_stops({symbol}): ATR fetch failed ({exc}), using fallback")
    # Fallback: sensible fixed % by mode (atr_mult in config is a scanner param, not a % stop)
    _FALLBACK_STOP_PCT = {'daytrade': 0.015, 'swing': 0.03, 'longterm': 0.05}
    sl_pct = _FALLBACK_STOP_PCT.get(mode, 0.03)
    tp_pct = sl_pct * cfg.get('tp_mult', 6.0) / max(cfg.get('sl_mult', 2.0), 0.001)
    stop   = round(entry * (1 - sl_pct), 4)
    target = round(entry * (1 + tp_pct), 4)
    if stop >= entry:
        logger.error(f"_compute_stops({symbol}): fallback also produced stop {stop} >= entry {entry}; skipping trade")
        return None, None
    return stop, target


# ── Signal file cleanup ──────────────────────────────────────────────────────

_SIGNALS_DIR = Path(OUTPUT_DIR) / 'signals'
_KEEP_DAYS   = 7  # keep last N days of signal files

def _cleanup_signal_files():
    """
    Remove duplicate signal CSV files: keep only the LATEST file per mode per
    day, and delete all files older than _KEEP_DAYS.
    Pattern: signals_{mode}_{YYYYMMDD}_{HHMMSS}.csv
    """
    import re
    if not _SIGNALS_DIR.exists():
        return

    # Group files by (mode, date)
    groups: dict[tuple[str, str], list[Path]] = {}
    pattern = re.compile(r'signals_(\w+)_(\d{8})_\d{6}\.csv$')

    for f in _SIGNALS_DIR.glob('signals_*.csv'):
        m = pattern.match(f.name)
        if not m:
            continue
        mode, date_str = m.group(1), m.group(2)
        groups.setdefault((mode, date_str), []).append(f)

    # Cutoff date for age-based cleanup
    cutoff = datetime.now(_NY_TZ).strftime('%Y%m%d')
    try:
        from datetime import timedelta
        cutoff_dt = datetime.now(_NY_TZ) - timedelta(days=_KEEP_DAYS)
        cutoff = cutoff_dt.strftime('%Y%m%d')
    except Exception:
        pass

    removed = 0
    for (mode, date_str), files in groups.items():
        if date_str < cutoff:
            # Old files: delete all
            for f in files:
                f.unlink(missing_ok=True)
                removed += 1
        elif len(files) > 1:
            # Same day: keep only the newest, delete the rest
            files.sort(key=lambda p: p.name)
            for f in files[:-1]:  # delete all but last (newest timestamp)
                f.unlink(missing_ok=True)
                removed += 1

    if removed:
        logger.info(f"Cleaned up {removed} old/duplicate signal file(s)")


# ── Core run ──────────────────────────────────────────────────────────────────

def run_once(decisions_date: Optional[str] = None,
             rescan_interval: int = _DEFAULT_RESCAN_INT) -> list[dict]:
    """
    One feedback pass over today's decisions CSV.

    Returns list of alert dicts (one per status change detected).
    """
    date_str  = _today(decisions_date)
    dec_path  = _decisions_path(date_str)
    fb_path   = _feedback_path(date_str)

    _init_feedback_csv(fb_path)
    state = _load_state(date_str)

    # ── Periodic re-scan: momentum_watch_daytrade.txt × 3 modes ──────────────
    # Full ALL.txt scans are handled by cron (9:35 daytrade/swing, Monday longterm).
    # Feedback agent only rescans the small 73-symbol momentum watchlist.
    did_rescan = False
    meta = state.get('_meta', {})
    last_rescan = meta.get('last_rescan_ts', 0)
    _now_et = datetime.now(_NY_TZ)
    _market_open  = _now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    _market_close = _now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    _in_market_hours = _market_open <= _now_et <= _market_close
    if _in_market_hours and time.time() - last_rescan >= rescan_interval:
        # Stamp BEFORE rescan starts so concurrent cron instances skip it
        state.setdefault('_meta', {})['last_rescan_ts'] = time.time()
        _save_state(date_str, state)
        did_rescan = _run_rescan(dec_path)
        _cleanup_signal_files()

    check_ts = datetime.now(_NY_TZ)

    # ── Load decisions: one canonical entry per symbol (latest scan wins) ──────
    if not dec_path.exists():
        logger.warning(f"Decisions file not found: {dec_path} — waiting for cron scan")
        return []

    dec_df = pd.read_csv(dec_path, dtype=str, index_col=False)
    if dec_df.empty:
        logger.info("Decisions CSV is empty.")
        return []

    # Deduplicate: keep the last row per symbol (most recent rejection)
    dec_df = dec_df.drop_duplicates(subset='symbol', keep='last')

    # Build mode-priority map: {symbol: row_with_best_mode}
    decisions_with_mode = _load_decisions_with_mode(dec_path)
    mode_counts = {}
    for r in decisions_with_mode.values():
        m = r.get('mode', 'daytrade')
        mode_counts[m] = mode_counts.get(m, 0) + 1
    logger.info(f"Loaded {len(decisions_with_mode)} symbols from scan_decisions "
                f"(modes: {', '.join(f'{m}={c}' for m, c in sorted(mode_counts.items()))})")

    symbols = dec_df['symbol'].dropna().str.strip().tolist()
    symbols = [s for s in symbols if s]

    if not symbols:
        logger.info("No symbols to check.")
        return []

    # ── Detect ACCEPTED: symbols in state but gone from CSV after re-scan ─────
    alerts   : list[dict] = []
    new_rows : list[dict] = []
    current_symbols = set(symbols)

    if did_rescan:
        for sym, sym_state in list(state.items()):
            if sym == '_meta':
                continue
            if sym not in current_symbols and sym_state.get('reason_code') and sym_state['reason_code'] != 'ACCEPTED':
                prev_reason = sym_state['reason_code']
                cur_price   = sym_state.get('last_price', 0)
                base        = sym_state.get('base_price', 0)
                pct_scan    = ((cur_price - base) / base * 100) if base else 0.0

                alert = {
                    'event':            'ACCEPTED',
                    'symbol':           sym,
                    'reason_code':      'ACCEPTED',
                    'prev_reason_code': prev_reason,
                    'current_price':    cur_price,
                    'pct_from_scan':    pct_scan,
                    'pct_since_last':   0.0,
                }
                alerts.append(alert)
                logger.info(
                    f"  ★ ACCEPTED   {sym:<8} "
                    f"was {prev_reason} (stage {STAGE_DEPTH.get(prev_reason, '?')}) → now ACCEPTED"
                )
                # Update state
                state[sym]['prev_reason_code'] = prev_reason
                state[sym]['reason_code'] = 'ACCEPTED'

    # ── Fetch current prices + volume ratios (batch) ────────────────────────
    prices, vol_ratios = _fetch_prices(symbols)

    # ── Fetch previous day close for all symbols (cached per day) ─────────
    prev_closes = _fetch_prev_close(symbols)

    # ── BreakoutDetector instance: reused for all scanner validations ─────
    from scanner import BreakoutDetector
    _detector = BreakoutDetector()

    for _, row in dec_df.iterrows():
        symbol      = str(row.get('symbol', '')).strip()
        reason_code = str(row.get('reason_code', '')).strip()
        first_seen  = str(row.get('timestamp', '')).strip()

        if not symbol:
            continue

        # Parse scan_price
        try:
            scan_price = float(row.get('price', '') or 'nan')
        except ValueError:
            scan_price = float('nan')

        # Parse score (for low_score sub-progress)
        try:
            row_score = int(row.get('score', '') or '0')
        except ValueError:
            row_score = 0
        try:
            row_score_max = int(row.get('score_max', '') or '0')
        except ValueError:
            row_score_max = 0

        current_price = prices.get(symbol)
        if current_price is None or current_price <= 0:
            continue

        # ── Load previous state for this symbol ───────────────────────────────
        prev           = state.get(symbol, {})
        is_first_check = not prev
        last_price     = prev.get('last_price')

        # base_price: always the real market price at FIRST observation.
        base_price = prev.get('base_price') if not is_first_check else current_price

        # prev_close: previous trading day's close (daily-level resistance).
        # Replaces the old intraday consolidation prev_high which was meaningless.
        prev_close = prev_closes.get(symbol)
        live_prev_high = prev_close  # backward-compat alias used in EXIT tracking

        pct_from_scan = (
            (current_price - base_price) / base_price * 100
            if base_price and base_price > 0 else float('nan')
        )
        pct_since_last = (
            (current_price - last_price) / last_price * 100
            if last_price else float('nan')
        )

        cur_dir  = _direction(pct_since_last) if last_price else 'FLAT'
        prev_dir = prev.get('last_direction', 'FLAT')

        # ── Detect PROGRESS ──────────────────────────────────────────────────
        prev_reason     = prev.get('reason_code', '')
        prev_depth      = STAGE_DEPTH.get(prev_reason, -1)
        new_depth       = STAGE_DEPTH.get(reason_code, -1)
        prev_best_score = prev.get('best_score')
        progress_detected = False
        progress_detail   = ''

        if not is_first_check and did_rescan and prev_depth >= 0 and new_depth > prev_depth:
            gates = new_depth - prev_depth
            transition_key = f"{prev_reason}->{reason_code}"
            already_alerted = prev.get('progress_alerted') or []
            if transition_key not in already_alerted and gates >= 2:
                progress_detected = True
                progress_detail = (
                    f"{prev_reason} (stage {prev_depth}) -> "
                    f"{reason_code} (stage {new_depth}) -- passed {gates} more gate(s)"
                )

        elif (not is_first_check and did_rescan
              and reason_code == 'low_score' and prev_reason == 'low_score'
              and row_score > 0 and prev_best_score is not None
              and row_score > prev_best_score):
            progress_detected = True
            score_str = f"{row_score}/{row_score_max}" if row_score_max else str(row_score)
            progress_detail = f"score improved {prev_best_score} -> {score_str}"

        # ── Compute confirmation pass count ──────────────────────────────────
        # Tracks how many consecutive 5-min passes price has been above prev_close
        # (only relevant before first BREAKOUT alert)
        prev_confirm_count = prev.get('confirm_count', 0)
        if (not is_first_check
                and live_prev_high is not None
                and not prev.get('alerted_breakout', False)):
            if current_price > live_prev_high:
                confirm_count = prev_confirm_count + 1
            else:
                confirm_count = 0   # dropped back below — reset
        else:
            confirm_count = prev_confirm_count  # already alerted or first check

        # ── Detect EXIT (post-breakout trade tracking) ─────────────────────
        exit_reason   = ''
        exit_trade_ret = 0.0
        prev_in_trade   = prev.get('in_trade', False)
        prev_entry      = prev.get('entry_price', 0)
        prev_bo_high    = prev.get('post_bo_high', 0)

        if prev_in_trade and prev_entry and prev_entry > 0:
            cur_bo_high  = max(prev_bo_high, current_price)
            sym_mode     = decisions_with_mode.get(symbol, {}).get('mode', 'swing')
            exit_pcts    = MODE_EXIT_PCT.get(sym_mode, MODE_EXIT_PCT['swing'])
            trail_stop   = cur_bo_high * (1 - exit_pcts['trail'] / 100)
            fail_level   = (live_prev_high or prev_entry) * (1 - exit_pcts['fail'] / 100)

            if current_price < fail_level:
                exit_reason = 'FAILED'
            elif current_price < trail_stop and cur_bo_high > prev_entry * 1.003:
                exit_reason = 'TRAIL_STOP'

            if exit_reason:
                exit_trade_ret = (current_price - prev_entry) / prev_entry * 100

        # ── Detect events ─────────────────────────────────────────────────────
        event = 'OK'
        _scanner_signal = None  # populated only on confirmed BREAKOUT

        if exit_reason:
            event = 'EXIT'
        elif (
            not is_first_check
            and prev_close is not None
            and current_price > prev_close
            and not prev.get('alerted_breakout', False)
            and confirm_count >= CONFIRM_PASSES
        ):
            # Stage 1 passed: price above previous day's close.
            # Stage 2: run full scanner pipeline to validate the breakout.
            sym_mode = decisions_with_mode.get(symbol, {}).get('mode', 'swing')
            _scanner_signal = _validate_breakout_with_scanner(
                symbol, sym_mode, _detector,
            )
            if _scanner_signal is not None:
                event = 'BREAKOUT'
                logger.info(
                    f"  ✓ Scanner confirmed {symbol} [{sym_mode}] "
                    f"Quality={_scanner_signal.get('Quality')} "
                    f"R:R={_scanner_signal.get('R:R')}"
                )
        elif progress_detected:
            event = 'PROGRESS'
        elif (
            not is_first_check
            and not pd.isna(pct_since_last)
            and abs(pct_since_last) >= SURGE_THRESHOLD
            and prev.get('alerted_surge_at') != f'{pct_since_last:.0f}'
        ):
            event = 'SURGE'
        elif (
            not is_first_check
            and cur_dir != prev_dir
            and prev_dir != 'FLAT'
            and not pd.isna(pct_since_last)
            and abs(pct_since_last) >= SURGE_THRESHOLD
        ):
            event = 'FLIP'

        # ── Update state ──────────────────────────────────────────────────────
        already_above = (
            is_first_check
            and live_prev_high is not None
            and current_price > live_prev_high
        )

        new_best_score = prev_best_score
        if reason_code == 'low_score' and row_score > 0:
            new_best_score = max(row_score, prev_best_score or 0)

        progress_alerted_list = list(prev.get('progress_alerted') or [])
        if progress_detected and prev_reason and reason_code:
            progress_alerted_list.append(f"{prev_reason}->{reason_code}")

        # ── Trade tracking state + Auto Portfolio auto-trade ─────────────
        _bo_stop_px = _bo_target_px = 0.0
        _bo_quality = _bo_rr = _bo_patterns = ''
        if event == 'BREAKOUT' and _scanner_signal:
            sym_mode     = decisions_with_mode.get(symbol, {}).get('mode', 'swing')
            _bo_stop_px  = _scanner_signal.get('Stop', 0)
            _bo_target_px = _scanner_signal.get('Target', 0)
            _bo_quality  = _scanner_signal.get('Quality', '')
            _bo_rr       = _scanner_signal.get('R:R', 0)
            _bo_patterns = _scanner_signal.get('Patterns', '')
            # Sanity check: stop must be below entry and not absurdly far
            # (catches stale/split-adjusted yfinance data returning wrong price scale)
            _max_stop_dist = {'daytrade': 0.10, 'swing': 0.25, 'longterm': 0.40}
            _stop_too_far = (
                current_price > 0 and _bo_stop_px and _bo_stop_px < current_price
                and (current_price - _bo_stop_px) / current_price > _max_stop_dist.get(sym_mode, 0.25)
            )
            if not _bo_stop_px or _bo_stop_px >= current_price or _stop_too_far:
                # Fallback to ATR-based stops if scanner signal has bad data
                _bo_stop_px, _bo_target_px = _compute_stops(symbol, current_price, sym_mode)
            # Post-entry confirmation: reject if price moved >5% from scanner signal price
            _sig_price = _scanner_signal.get('Price', 0)
            if _sig_price and current_price > 0:
                _drift_pct = abs(current_price - _sig_price) / _sig_price
                if _drift_pct > 0.05:
                    logger.warning(
                        f"  BREAKOUT {symbol}: price drifted {_drift_pct:.1%} from signal "
                        f"(signal=${_sig_price:.2f} vs current=${current_price:.2f}) — skipping"
                    )
                    event = 'OK'
            if event != 'BREAKOUT':
                pass  # drift or earlier check already rejected
            elif _bo_stop_px is None or _bo_stop_px >= current_price:
                logger.warning(f"  BREAKOUT {symbol}: skipping portfolio add — invalid stop/target")
                event = 'OK'   # suppress alert; don't add to portfolio
            else:
                in_trade     = True
                entry_price  = current_price
                post_bo_high = current_price
            result = ap.add_position_direct(
                symbol, current_price, _bo_stop_px or 0.0, _bo_target_px or 0.0,
                mode=sym_mode, vol_ratio=vol_ratios.get(symbol, 0),
            ) if event == 'BREAKOUT' else {'reason': 'skipped_invalid_stop'}
            logger.info(
                f"  auto_portfolio add [{sym_mode}] {symbol}: "
                f"stop={_bo_stop_px or 0:.2f} target={_bo_target_px or 0:.2f} "
                f"quality={_bo_quality} R:R={_bo_rr} → {result['reason']}"
            )
        elif event == 'EXIT':
            in_trade     = False
            entry_price  = 0
            post_bo_high = 0
            # Auto-close from auto_portfolio
            ap.close_position(symbol, current_price, reason=exit_reason.lower())
        else:
            in_trade     = prev_in_trade
            entry_price  = prev_entry
            post_bo_high = max(prev_bo_high, current_price) if prev_in_trade else 0

        state[symbol] = {
            'base_price':        base_price,
            'prev_close':        prev_close,
            'reason_code':       reason_code,
            'prev_reason_code':  prev_reason if did_rescan and prev_reason else prev.get('prev_reason_code', ''),
            'first_seen_ts':     first_seen,
            'last_price':        current_price,
            'last_direction':    cur_dir,
            'last_check_ts':     check_ts.isoformat(),
            'alerted_breakout':  prev.get('alerted_breakout', False) or (event == 'BREAKOUT') or already_above,
            'alerted_surge_at':  f'{pct_since_last:.0f}' if event == 'SURGE' else prev.get('alerted_surge_at', ''),
            'best_score':        new_best_score,
            'progress_alerted':  progress_alerted_list,
            'in_trade':          in_trade,
            'entry_price':       entry_price,
            'post_bo_high':      post_bo_high,
            'confirm_count':     0 if (event == 'BREAKOUT' or already_above) else confirm_count,
        }

        # ── Build CSV row ─────────────────────────────────────────────────────
        fb_row = {
            'check_timestamp': check_ts.isoformat(),
            'symbol':          symbol,
            'first_seen_ts':   first_seen,
            'reason_code':     reason_code,
            'scan_price':      f'{base_price:.4f}' if base_price else '',
            'prev_high':       f'{prev_close:.4f}' if prev_close else '',
            'current_price':   f'{current_price:.4f}',
            'pct_from_scan':   f'{pct_from_scan:.2f}' if not pd.isna(pct_from_scan) else '',
            'pct_since_last':  f'{pct_since_last:.2f}' if not pd.isna(pct_since_last) else '',
            'direction':       cur_dir,
            'event':           event,
            'prev_reason_code': prev_reason if event == 'PROGRESS' else '',
            'score':           f'{row_score}/{row_score_max}' if reason_code == 'low_score' and row_score else '',
        }
        new_rows.append(fb_row)

        if event != 'OK':
            alert = {
                'event':            event,
                'symbol':           symbol,
                'reason_code':      reason_code,
                'prev_reason_code': prev_reason,
                'current_price':    current_price,
                'pct_from_scan':    pct_from_scan if not pd.isna(pct_from_scan) else 0.0,
                'pct_since_last':   pct_since_last if not pd.isna(pct_since_last) else 0.0,
                'prev_close':       prev_close,
                'progress_detail':  progress_detail,
                'score':            f'{row_score}/{row_score_max}' if row_score else None,
            }
            # Add EXIT-specific fields
            if event == 'EXIT':
                alert['exit_reason']   = exit_reason
                alert['trade_ret_pct'] = exit_trade_ret
                alert['entry_price']   = prev_entry
                alert['post_bo_high']  = prev_bo_high
            if event == 'BREAKOUT':
                alert['vol_ratio'] = vol_ratios.get(symbol, 0)
                alert['mode']      = decisions_with_mode.get(symbol, {}).get('mode', 'swing')
                alert['stop']      = _bo_stop_px
                alert['target']    = _bo_target_px
                alert['quality']   = _bo_quality
                alert['rr']        = _bo_rr
                alert['patterns']  = _bo_patterns
                alert['win_prob']  = _scanner_signal.get('WinProb', '') if _scanner_signal else ''
                alert['rsi']       = _scanner_signal.get('RSI', '') if _scanner_signal else ''
            alerts.append(alert)
            if event == 'PROGRESS':
                logger.info(f"  ★ PROGRESS   {symbol:<8} {progress_detail}")
            elif event == 'EXIT':
                logger.info(
                    f"  ★ EXIT       {symbol:<8} "
                    f"${current_price:.2f}  ret={exit_trade_ret:+.2f}%  "
                    f"reason={exit_reason}"
                )
            else:
                _ps = f'{pct_from_scan:+.2f}' if not pd.isna(pct_from_scan) else 'n/a'
                _pl = f'{pct_since_last:+.2f}' if not pd.isna(pct_since_last) else 'n/a'
                logger.info(
                    f"  ★ {event:<10} {symbol:<8} "
                    f"${current_price:.2f}  "
                    f"pct_scan={_ps}%  "
                    f"pct_last={_pl}%  "
                    f"[{reason_code}]"
                )
        else:
            _ps = f'{pct_from_scan:+.2f}' if not pd.isna(pct_from_scan) else 'n/a'
            logger.debug(
                f"  OK         {symbol:<8} ${current_price:.2f}  "
                f"pct_scan={_ps}%  [{reason_code}]"
            )

    # ── Persist state ─────────────────────────────────────────────────────────
    _save_state(date_str, state)

    # ── Append to feedback CSV ────────────────────────────────────────────────
    if new_rows:
        with open(fb_path, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=FB_COLUMNS)
            writer.writerows(new_rows)

    logger.info(
        f"Checked {len(new_rows)} symbols — "
        f"{len(alerts)} event(s): "
        f"{', '.join(a['event'] + ':' + a['symbol'] for a in alerts) or 'none'}"
    )

    # ── Send Discord + Telegram + Email (only actionable BREAKOUT alerts) ──
    notify_alerts = [a for a in alerts if a['event'] == 'BREAKOUT']
    if notify_alerts:
        _send_discord_alerts(notify_alerts)
        _send_telegram_alerts(notify_alerts)
        _send_email_alerts(notify_alerts)

    # ── Portfolio health check (advisory, respects cooldown) ──────────────
    try:
        from position_health import run_health_check
        run_health_check()
    except Exception as _e:
        logger.debug(f"Health check skipped: {_e}")

    return alerts


# ── Summary / learning report ─────────────────────────────────────────────────

def print_summary(date_str: Optional[str] = None) -> None:
    today_str = _today(date_str)
    fb_path   = _feedback_path(today_str)

    if not fb_path.exists() or fb_path.stat().st_size < 50:
        logger.info("No feedback data yet.")
        return

    df = pd.read_csv(fb_path, dtype=str)
    if df.empty:
        return

    df['pct_from_scan'] = pd.to_numeric(df['pct_from_scan'], errors='coerce')
    df['missed'] = df['pct_from_scan'] > MISS_THRESHOLD

    print('\n' + '═' * 62)
    print(f'  SCAN FEEDBACK SUMMARY — {today_str}')
    print('═' * 62)
    print(f'  Total checks : {len(df)}')
    print(f'  Events       : {(df["event"] != "OK").sum()}')
    print(f'  BREAKOUTs    : {(df["event"] == "BREAKOUT").sum()}')
    print(f'  EXITs        : {(df["event"] == "EXIT").sum()}')
    print(f'  PROGRESSes   : {(df["event"] == "PROGRESS").sum()}')
    print(f'  ACCEPTEDs    : {(df["event"] == "ACCEPTED").sum()}')
    print(f'  SURGEs       : {(df["event"] == "SURGE").sum()}')
    print(f'  FLIPs        : {(df["event"] == "FLIP").sum()}')
    print(f'  Missed (>{MISS_THRESHOLD}%+): {df["missed"].sum()}')
    print()

    # Pipeline progressions
    progress_df = df[df['event'].isin(['PROGRESS', 'ACCEPTED'])].copy()
    if not progress_df.empty:
        print('  PIPELINE PROGRESSIONS')
        print('  ' + '─' * 48)
        for _, r in progress_df.iterrows():
            prev_r = r.get('prev_reason_code', '?')
            new_r  = r.get('reason_code', '?')
            sc     = r.get('score', '')
            score_str = f"  score={sc}" if sc and str(sc) not in ('', 'nan') else ''
            print(f"  {r['symbol']:<8} {prev_r} → {new_r}{score_str}")
        print()

    # Per-reason breakdown (latest check per symbol)
    latest = df.sort_values('check_timestamp').drop_duplicates('symbol', keep='last')
    grp = latest.groupby('reason_code')['pct_from_scan'].agg(['mean', 'count'])
    grp.columns = ['avg_pct', 'n']
    grp['missed_n'] = latest.groupby('reason_code')['missed'].sum()
    grp['missed_%'] = (grp['missed_n'] / grp['n'] * 100).round(1)
    grp = grp.sort_values('avg_pct', ascending=False)

    print(f"  {'Reason':<22} {'N':>5} {'AvgMove%':>9} {'Missed%':>8}")
    print('  ' + '─' * 48)
    for code, r in grp.iterrows():
        flag = ' ← review' if r['missed_%'] > 20 else ''
        print(f"  {code:<22} {int(r['n']):>5} {r['avg_pct']:>+8.2f}% {r['missed_%']:>7.1f}%{flag}")

    # Top missed
    missed_df = (
        latest[latest['missed']]
        .sort_values('pct_from_scan', ascending=False)
        .head(10)
    )
    if not missed_df.empty:
        print()
        print(f'  TOP MISSED OPPORTUNITIES (rejected but >{MISS_THRESHOLD}% up from scan)')
        print('  ' + '─' * 48)
        for _, r in missed_df.iterrows():
            print(
                f"  {r['symbol']:<8} +{float(r['pct_from_scan']):.1f}%"
                f"  [{r['reason_code']}]"
                f"  first seen {str(r['first_seen_ts'])[11:16]}"
            )

    print('═' * 62 + '\n')


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Scan feedback agent — monitors rejected symbols for status changes and pipeline progress'
    )
    parser.add_argument('--loop', action='store_true',
                        help='Run continuously on --interval (default 5 min)')
    parser.add_argument('--interval', type=int, default=_DEFAULT_INT,
                        help='Seconds between runs in loop mode (default 300)')
    parser.add_argument('--rescan-interval', type=int, default=_DEFAULT_RESCAN_INT,
                        help='Seconds between full scanner re-runs for progress detection (default 900)')
    parser.add_argument('--date', type=str, default=None,
                        help='Process decisions from this date YYYYMMDD (default today)')
    parser.add_argument('--summary', action='store_true',
                        help='Print learning summary and exit')
    args = parser.parse_args()

    if args.summary:
        print_summary(args.date)
        return

    # Single-instance guard: skip if a previous cron run is still active.
    # Uses a non-blocking exclusive lock; --loop mode waits (blocks) instead.
    _lock_file = open('/tmp/feedback_agent.lock', 'w')
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | (0 if args.loop else fcntl.LOCK_NB))
    except BlockingIOError:
        logger.info("Feedback agent already running — skipping this invocation")
        _lock_file.close()
        return

    try:
        logger.info(
            f"Scan feedback agent started "
            f"{'(loop every %ds, rescan every %ds)' % (args.interval, args.rescan_interval) if args.loop else '(single run)'}"
        )

        while True:
            try:
                run_once(args.date, rescan_interval=args.rescan_interval)
            except Exception as e:
                logger.error(f"Feedback agent error: {e}", exc_info=True)

            if not args.loop:
                break

            logger.info(f"Next check in {args.interval}s …")
            time.sleep(args.interval)

        print_summary(args.date)
    finally:
        fcntl.flock(_lock_file, fcntl.LOCK_UN)
        _lock_file.close()


if __name__ == '__main__':
    main()
