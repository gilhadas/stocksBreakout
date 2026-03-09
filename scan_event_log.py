"""
scan_event_log.py
-----------------
Custom logging.Handler that intercepts SCAN-level messages and writes
structured rows to a daily CSV: scanner_output/scan_decisions_YYYYMMDD.csv

Each row captures:
  timestamp, symbol, reason_code, reason_detail,
  price, prev_high, bars_have, bars_need, score, score_max, rr, raw

Hook into the scanner at startup:
  from scan_event_log import attach_scan_csv_handler
  attach_scan_csv_handler()

The feedback agent (scan_feedback_agent.py) reads this CSV and enriches
each row with a subsequent price check to build a learning dataset.
"""

import csv
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from config import OUTPUT_DIR

_NY_TZ = ZoneInfo('America/New_York')
SCAN_LEVEL = 15  # same as utils.py

# ── CSV schema ────────────────────────────────────────────────────────────────
COLUMNS = [
    'timestamp', 'symbol',
    'reason_code', 'reason_detail',
    'price', 'prev_high',
    'bars_have', 'bars_need',
    'score', 'score_max',
    'rr',
    'raw',
]

# ── Message parsers ───────────────────────────────────────────────────────────
# Every SCAN message follows: "{SYMBOL}: skip — {detail}"
_MSG_RE = re.compile(r'^([A-Z.\-]+): skip — (.+)$')

_REASON_CODES = {
    'insufficient bars':       'insufficient_bars',
    'no data returned':        'no_data',
    'no price break':          'no_breakout',
    'low liquidity':           'low_liquidity',
    'bearish BB trend':        'bearish_bb',
    'blow-off':                'blow_off',
    'score too low':           'low_score',
    'R:R grade D':             'rr_grade_d',
    'failed:':                 'failed_conditions',
    'spread':                  'spread',
    'price $':                 'price_range',
}

def _reason_code(detail: str) -> str:
    for key, code in _REASON_CODES.items():
        if key in detail:
            return code
    return 'other'

def _extract(detail: str) -> dict:
    """Pull numeric fields from the reason detail string."""
    out = {}

    # close=178.20  or  $178.20
    m = re.search(r'close=([\d.]+)', detail)
    if not m:
        m = re.search(r'\$([\d.]+)', detail)
    if m:
        out['price'] = m.group(1)

    # prev_high=180.50
    m = re.search(r'prev_high=([\d.]+)', detail)
    if m:
        out['prev_high'] = m.group(1)

    # bars: "(20 < 100)"
    m = re.search(r'\((\d+)\s*<\s*(\d+)\)', detail)
    if m:
        out['bars_have'] = m.group(1)
        out['bars_need'] = m.group(2)

    # score: "(45/100)"
    m = re.search(r'\((\d+)/(\d+)\)', detail)
    if m:
        out['score']     = m.group(1)
        out['score_max'] = m.group(2)

    # rr=1.23
    m = re.search(r'rr=([\d.]+)', detail)
    if m:
        out['rr'] = m.group(1)

    return out


# ── Handler ───────────────────────────────────────────────────────────────────
class ScanCSVHandler(logging.Handler):
    """
    Logging handler that writes SCAN-level (level=15) records to a daily CSV.
    Attach to the root logger once at startup via attach_scan_csv_handler().
    """

    def __init__(self, output_dir: str = OUTPUT_DIR):
        super().__init__(level=SCAN_LEVEL)
        self.csv_path = (
            Path(output_dir)
            / 'scan_decisions'
            / f'scan_decisions_{datetime.now(_NY_TZ):%Y%m%d}.csv'
        )
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.csv_path.exists():
            with open(self.csv_path, 'w', newline='') as f:
                csv.DictWriter(f, fieldnames=COLUMNS).writeheader()

    # emit is called by the logging framework — must never raise
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno != SCAN_LEVEL:
            return
        try:
            msg = record.getMessage()
            m = _MSG_RE.match(msg)
            if not m:
                return  # not a per-symbol skip message

            symbol, detail = m.group(1), m.group(2)
            nums = _extract(detail)

            row = {
                'timestamp':     datetime.fromtimestamp(record.created, tz=_NY_TZ).isoformat(),
                'symbol':        symbol,
                'reason_code':   _reason_code(detail),
                'reason_detail': detail,
                'price':         nums.get('price', ''),
                'prev_high':     nums.get('prev_high', ''),
                'bars_have':     nums.get('bars_have', ''),
                'bars_need':     nums.get('bars_need', ''),
                'score':         nums.get('score', ''),
                'score_max':     nums.get('score_max', ''),
                'rr':            nums.get('rr', ''),
                'raw':           msg,
            }
            with open(self.csv_path, 'a', newline='') as f:
                csv.DictWriter(f, fieldnames=COLUMNS).writerow(row)
        except Exception:
            pass  # never crash the scanner

    @property
    def decisions_path(self) -> Path:
        return self.csv_path


# ── Public API ────────────────────────────────────────────────────────────────
_handler: Optional[ScanCSVHandler] = None


def attach_scan_csv_handler(output_dir: str = OUTPUT_DIR) -> ScanCSVHandler:
    """
    Attach the CSV handler to the root logger.
    Safe to call multiple times — only installs once per process.
    Returns the handler so callers can inspect .decisions_path.
    """
    global _handler
    if _handler is None:
        _handler = ScanCSVHandler(output_dir)
        logging.getLogger().addHandler(_handler)
    return _handler


def get_decisions_path(output_dir: str = OUTPUT_DIR) -> Path:
    """Return today's scan_decisions CSV path (may not exist yet)."""
    return (
        Path(output_dir)
        / 'scan_decisions'
        / f'scan_decisions_{datetime.now(_NY_TZ):%Y%m%d}.csv'
    )
