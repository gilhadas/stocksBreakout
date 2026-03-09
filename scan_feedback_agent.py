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
from zoneinfo import ZoneInfo

_NY_TZ       = ZoneInfo('America/New_York')
_SCAN_DIR    = Path(OUTPUT_DIR) / 'scan_decisions'
_DEFAULT_INT = 300   # seconds
_DEFAULT_RESCAN_INT = 900  # 15 min

# Alert thresholds
SURGE_THRESHOLD = 2.0   # % move since last check that triggers SURGE alert
MISS_THRESHOLD  = 2.0   # % above scan_price considered a missed opportunity
FLAT_BAND       = 0.3   # % band for FLAT classification

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

def _fetch_prices(symbols: list[str]) -> dict[str, float]:
    """Batch-fetch latest prices via yfinance. Returns {symbol: price}."""
    if not symbols:
        return {}
    try:
        raw = yf.download(
            symbols, period='1d', interval='1m',
            progress=False, auto_adjust=True,
            group_by='ticker' if len(symbols) > 1 else None,
        )
        prices: dict[str, float] = {}
        if len(symbols) == 1:
            sym = symbols[0]
            if not raw.empty:
                prices[sym] = float(raw['Close'].dropna().iloc[-1])
        else:
            for sym in symbols:
                try:
                    col = raw[sym]['Close'].dropna()
                    if not col.empty:
                        prices[sym] = float(col.iloc[-1])
                except Exception:
                    pass
        return prices
    except Exception as e:
        logger.debug(f"Batch price fetch failed: {e}")
        return {}


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

        else:
            lines = [
                f"**Current:** ${cur:.2f}",
                f"**vs scan price:** {pct_scan:+.2f}%",
                f"**vs last check:** {pct_last:+.2f}%",
                f"**Rejected for:** {reason}",
            ]
            if prev_h and event == 'BREAKOUT':
                lines.append(f"**Prev high:** ${prev_h:.2f} ← crossed ✓")

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


# ── Scanner runner ────────────────────────────────────────────────────────────

_SCANNER_CMD = [
    sys.executable, 'breakout_scanner.py',
    './input/1_26_Setups.txt',
    '--mock', '--notify', '--bounce',
    '--mode', 'scalping', '--debug',
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
        # Record rescan time so the next run doesn't immediately re-scan
        state.setdefault('_meta', {})['last_rescan_ts'] = time.time()
    else:
        # ── Periodic re-scan to detect pipeline progress ──────────────────────
        meta = state.get('_meta', {})
        last_rescan = meta.get('last_rescan_ts', 0)
        if time.time() - last_rescan >= rescan_interval:
            did_rescan = _run_scanner(dec_path, label="re-scan")
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

    # ── Fetch current prices (batch) ──────────────────────────────────────────
    prices = _fetch_prices(symbols)

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

        # prev_high from CSV is only valid when it's in the same ballpark as
        # the real current price (within 30%).
        prev_high_raw = prev_high
        if not pd.isna(prev_high_raw) and abs(prev_high_raw - current_price) / current_price > 0.30:
            prev_high_raw = float('nan')

        live_prev_high = prev_high_raw if not pd.isna(prev_high_raw) else prev.get('prev_high')

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

        # ── Detect events ─────────────────────────────────────────────────────
        event = 'OK'

        breakout_crossed = (
            not is_first_check
            and live_prev_high is not None
            and current_price > live_prev_high
            and not prev.get('alerted_breakout', False)
        )
        if breakout_crossed:
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
            alerts.append(alert)
            if event == 'PROGRESS':
                logger.info(f"  ★ PROGRESS   {symbol:<8} {progress_detail}")
            else:
                logger.info(
                    f"  ★ {event:<10} {symbol:<8} "
                    f"${current_price:.2f}  "
                    f"pct_scan={pct_from_scan:+.2f}%  "
                    f"pct_last={pct_since_last:+.2f}%  "
                    f"[{reason_code}]"
                )
        else:
            logger.debug(
                f"  OK         {symbol:<8} ${current_price:.2f}  "
                f"pct_scan={pct_from_scan:+.2f}%  [{reason_code}]"
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

    # ── Send Discord ──────────────────────────────────────────────────────────
    if alerts:
        _send_discord_alerts(alerts)

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
