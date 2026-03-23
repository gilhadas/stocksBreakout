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
       BREAKOUT  – price crossed above prev_high
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

from config import NOTIFICATIONS, OUTPUT_DIR
from notifier import Notifier
import scalp_portfolio as sp
from zoneinfo import ZoneInfo

_NY_TZ       = ZoneInfo('America/New_York')
_SCAN_DIR    = Path(OUTPUT_DIR) / 'scan_decisions'
_DEFAULT_INT = 300   # seconds
_DEFAULT_RESCAN_INT = 900  # 15 min

# Alert thresholds
SURGE_THRESHOLD    = 2.0   # % move since last check that triggers SURGE alert
MISS_THRESHOLD     = 2.0   # % above scan_price considered a missed opportunity
FLAT_BAND          = 0.3   # % band for FLAT classification
TRAIL_STOP_PCT     = 1.0   # % trailing stop from post-breakout high (swing/daytrade)
EXIT_BELOW_PCT     = 0.3   # % below prev_high = failed breakout exit (swing/daytrade)
# Scalping: fixed-cent stops (overrides % stops when > 0)
SCALP_TRAIL_CENTS  = 3     # ¢ trailing stop from post-breakout high
SCALP_FAIL_CENTS   = 2     # ¢ below prev_high = failed breakout exit
CONFIRM_PASSES     = 1     # consecutive passes above prev_high (1 = immediate; backtest showed 2 adds no value)
VOL_MULT           = 2.0   # require bar volume ≥ Xx 20-bar avg at BREAKOUT (backtest: +6 pts WR)
MIN_BREAKOUT_PCT   = 0.2   # min % above prev_high before firing BREAKOUT
MIN_STAGE_BREAKOUT = 5     # only fire BREAKOUT for symbols rejected at stage ≥ this (0=off)

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
        prev_h   = alert.get('prev_high')

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
            if prev_h and event == 'BREAKOUT':
                vol_info = f"  vol={alert.get('vol_ratio', 0):.1f}x" if alert.get('vol_ratio') else ''
                lines.append(f"**Prev high:** ${prev_h:.2f} ← crossed ✓{vol_info}")

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
        prev_h = alert.get('prev_high')
        vol    = alert.get('vol_ratio', 0)

        lines = [
            f"SCALP BREAKOUT: {symbol}",
            f"Price: ${cur:.2f}",
        ]
        if prev_h:
            lines.append(f"Prev High: ${prev_h:.2f}")
        if vol:
            lines.append(f"Vol: {vol:.1f}x")
        lines.append(f"Stop: ${cur - SCALP_FAIL_CENTS / 100:.2f} | Target: ${cur + SCALP_TRAIL_CENTS * 2 / 100:.2f}")

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
        symbol = alert['symbol']
        cur    = alert['current_price']
        prev_h = alert.get('prev_high')
        vol    = alert.get('vol_ratio', 0)
        pct    = alert.get('pct_from_scan', 0)

        lines.append(f"• {symbol} @ ${cur:.2f}  ({pct:+.1f}% from scan)")
        if prev_h:
            lines.append(f"  Broke above prev high: ${prev_h:.2f}")
        if vol:
            lines.append(f"  Vol: {vol:.1f}x")
        lines.append('')

    body = f"BREAKOUT alert — {len(alerts)} symbol(s)\n\n" + '\n'.join(lines)
    notifier.send_email(subject, body)
    logger.info(f"Email alert sent: {subject}")


# ── Scanner runner ────────────────────────────────────────────────────────────

_SCANNER_CMD = [
    sys.executable, 'breakout_scanner.py',
    './input/1_26_Setups.txt',
    '--mock', '--bounce',
    '--mode', 'scalping', '--debug',
    # --notify removed: feedback agent handles its own BREAKOUT notifications.
    # Scanner --notify was causing duplicate/noisy alerts for every rescan.
]

def _run_scanner(dec_path: Path, label: str = "initial") -> bool:
    """
    Run the breakout scanner once. Returns True if it completed successfully.
    The scanner appends rows to the decisions CSV via scan_event_log.py.
    """
    logger.info(f"Running {label} scan to seed/update decisions CSV …")
    logger.info(f"CMD: {' '.join(_SCANNER_CMD)}")
    try:
        result = subprocess.run(
            _SCANNER_CMD,
            cwd=Path(__file__).parent,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            logger.warning(f"Scanner exited with code {result.returncode}")
            return False
        logger.info(f"{label.capitalize()} scan complete — decisions CSV: {dec_path}")
        return True
    except subprocess.TimeoutExpired:
        logger.error("Scanner timed out after 600 s")
        return False
    except Exception as e:
        logger.error(f"Failed to run scanner: {e}", exc_info=True)
        return False


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

    # ── First run of the day: seed via scanner ────────────────────────────────
    did_rescan = False
    if not state:
        did_rescan = _run_scanner(dec_path, label="initial")
        _cleanup_signal_files()
        # Record rescan time so the next run doesn't immediately re-scan
        state.setdefault('_meta', {})['last_rescan_ts'] = time.time()
    else:
        # ── Periodic re-scan to detect pipeline progress ──────────────────────
        meta = state.get('_meta', {})
        last_rescan = meta.get('last_rescan_ts', 0)
        if time.time() - last_rescan >= rescan_interval:
            did_rescan = _run_scanner(dec_path, label="re-scan")
            _cleanup_signal_files()
            state.setdefault('_meta', {})['last_rescan_ts'] = time.time()

    check_ts = datetime.now(_NY_TZ)

    # ── Load decisions: one canonical entry per symbol (latest scan wins) ──────
    if not dec_path.exists():
        logger.warning(f"Decisions file still missing after scan: {dec_path}")
        return []

    dec_df = pd.read_csv(dec_path, dtype=str)
    if dec_df.empty:
        logger.info("Decisions CSV is empty.")
        return []

    # Deduplicate: keep the last row per symbol (most recent rejection)
    dec_df = dec_df.drop_duplicates(subset='symbol', keep='last')

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

    for _, row in dec_df.iterrows():
        symbol      = str(row.get('symbol', '')).strip()
        reason_code = str(row.get('reason_code', '')).strip()
        first_seen  = str(row.get('timestamp', '')).strip()

        if not symbol:
            continue

        # Parse scan_price and prev_high
        try:
            scan_price = float(row.get('price', '') or 'nan')
        except ValueError:
            scan_price = float('nan')
        try:
            prev_high = float(row.get('prev_high', '') or 'nan')
        except ValueError:
            prev_high = float('nan')

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

        # prev_high from CSV: reject if >20% away from current price (bad data)
        prev_high_raw = prev_high
        if not pd.isna(prev_high_raw) and abs(prev_high_raw - current_price) / current_price > 0.20:
            prev_high_raw = float('nan')

        # Lock prev_high: once set, never let a rescan lower it (prevents
        # false breakouts from yfinance data instability — see MPC 2026-03-10).
        saved_prev_high = prev.get('prev_high')
        if not pd.isna(prev_high_raw):
            if saved_prev_high and saved_prev_high > 0:
                live_prev_high = max(saved_prev_high, prev_high_raw)
            else:
                live_prev_high = prev_high_raw
        else:
            live_prev_high = saved_prev_high

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
        # Tracks how many consecutive 5-min passes price has been above prev_high
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
            # Scalping: fixed-cent stops; otherwise percentage-based
            if SCALP_TRAIL_CENTS > 0:
                trail_stop = cur_bo_high - (SCALP_TRAIL_CENTS / 100.0)
                fail_level = (live_prev_high or prev_entry) - (SCALP_FAIL_CENTS / 100.0)
            else:
                trail_stop = cur_bo_high * (1 - TRAIL_STOP_PCT / 100)
                fail_level = (live_prev_high or prev_entry) * (1 - EXIT_BELOW_PCT / 100)

            if current_price < fail_level:
                exit_reason = 'FAILED'
            elif current_price < trail_stop and cur_bo_high > prev_entry * 1.003:
                exit_reason = 'TRAIL_STOP'

            if exit_reason:
                exit_trade_ret = (current_price - prev_entry) / prev_entry * 100

        # ── Detect events ─────────────────────────────────────────────────────
        event = 'OK'

        if exit_reason:
            event = 'EXIT'
        elif (
            not is_first_check
            and live_prev_high is not None
            and not prev.get('alerted_breakout', False)
            and confirm_count >= CONFIRM_PASSES
        ):
            # ── Quality filters (data-driven from backtest comparison) ────
            bo_ok = True

            # Pipeline-stage filter: skip early-stage rejections (no setup context)
            if MIN_STAGE_BREAKOUT > 0:
                sym_stage = STAGE_DEPTH.get(reason_code, -1)
                if sym_stage < MIN_STAGE_BREAKOUT:
                    bo_ok = False

            # Min distance above level
            if bo_ok and MIN_BREAKOUT_PCT > 0 and live_prev_high > 0:
                dist_pct = (current_price - live_prev_high) / live_prev_high * 100
                if dist_pct < MIN_BREAKOUT_PCT:
                    bo_ok = False

            # Volume confirmation: last bar vol vs 20-bar trailing avg
            if bo_ok and VOL_MULT > 1.0:
                vr = vol_ratios.get(symbol, 0)
                if vr < VOL_MULT:
                    bo_ok = False

            if bo_ok:
                event = 'BREAKOUT'
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

        # ── Trade tracking state + Scalp Portfolio auto-trade ────────────
        if event == 'BREAKOUT':
            in_trade     = True
            entry_price  = current_price
            post_bo_high = current_price
            # Auto-buy into scalp portfolio
            stop_px   = current_price - (SCALP_FAIL_CENTS / 100.0)
            target_px = current_price + (SCALP_TRAIL_CENTS * 2 / 100.0)
            sp.add_position(
                symbol, current_price, stop_px, target_px,
                vol_ratio=vol_ratios.get(symbol, 0),
            )
        elif event == 'EXIT':
            in_trade     = False
            entry_price  = 0
            post_bo_high = 0
            # Auto-sell from scalp portfolio
            sp.close_position(symbol, current_price, reason=exit_reason.lower())
        else:
            in_trade     = prev_in_trade
            entry_price  = prev_entry
            post_bo_high = max(prev_bo_high, current_price) if prev_in_trade else 0

        state[symbol] = {
            'base_price':        base_price,
            'prev_high':         live_prev_high,
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
            'prev_high':       f'{live_prev_high:.4f}' if live_prev_high else '',
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
                'prev_high':        live_prev_high,
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


if __name__ == '__main__':
    main()
