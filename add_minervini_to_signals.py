#!/usr/bin/env python3
"""
add_minervini_to_signals.py — Retroactively add MinerviniScore to signal CSVs.

For each CSV in scanner_output/signals/, downloads historical daily data via
yfinance for every symbol (ending on the signal date), evaluates the 8-condition
Minervini Stage 2 Trend Template, and writes a MinerviniScore column back to the
CSV in-place.

USAGE
-----
  # Dry-run — print what would change, do not write
  python add_minervini_to_signals.py --dry-run

  # Process all CSVs (adds MinerviniScore, skips already-processed files)
  python add_minervini_to_signals.py

  # Re-process even files that already have the column
  python add_minervini_to_signals.py --force

  # Only process a specific mode
  python add_minervini_to_signals.py --mode swing

  # Limit to newest N files
  python add_minervini_to_signals.py --max-files 10
"""

import argparse
import csv
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

SIGNALS_DIR = Path("scanner_output/signals")
MINERVINI_COL = "MinerviniScore"

try:
    import pandas as pd
    import yfinance as yf
except ImportError:
    print("ERROR: Missing dependencies. Run: pip install pandas yfinance")
    sys.exit(1)

# Import the shared function from indicators.py (same project)
sys.path.insert(0, str(Path(__file__).parent))
from indicators import calculate_minervini_template


# ── yfinance cache: avoid re-downloading the same ticker ─────────────────────
_history_cache: dict = {}

def _fetch_history(symbol: str, end_date: datetime) -> pd.DataFrame:
    """
    Download ~320 days of daily closes ending on end_date.
    Returns DataFrame with lowercase columns: open, high, low, close, volume.
    Cached per (symbol, end_date.date()).
    """
    key = (symbol, end_date.date())
    if key in _history_cache:
        return _history_cache[key]

    start = end_date - timedelta(days=460)   # ~320 trading days + buffer
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start.strftime("%Y-%m-%d"),
                            end=(end_date + timedelta(days=1)).strftime("%Y-%m-%d"))
        if df.empty:
            _history_cache[key] = pd.DataFrame()
            return pd.DataFrame()

        df = df.rename(columns=str.lower)
        # Keep only OHLCV columns
        df = df[['open', 'high', 'low', 'close', 'volume']].dropna()
        _history_cache[key] = df
    except Exception as e:
        print(f"    [yfinance error] {symbol}: {e}")
        _history_cache[key] = pd.DataFrame()
        return pd.DataFrame()

    return _history_cache[key]


def _parse_signal_date(filename: str) -> datetime:
    """Extract the scan date from filename: signals_swing_20260220_123019.csv"""
    m = re.search(r"(\d{8})_(\d{6})", filename)
    if m:
        return datetime.strptime(m.group(1), "%Y%m%d")
    return datetime.now()


def _score_symbol(symbol: str, signal_date: datetime) -> int:
    """Return MinerviniScore (0-8) for symbol on signal_date."""
    df = _fetch_history(symbol, signal_date)
    if df.empty or len(df) < 50:
        return -1   # insufficient data

    # Trim to bars up to and including signal_date
    # Use string comparison to avoid timezone edge-cases (yfinance may return
    # tz-aware or tz-naive index depending on the version/OS).
    cutoff = signal_date.strftime('%Y-%m-%d')
    df = df[df.index.strftime('%Y-%m-%d') <= cutoff]
    if len(df) < 50:
        return -1

    _, score = calculate_minervini_template(df)
    return score


def process_file(csv_path: Path, dry_run: bool = False, force: bool = False) -> int:
    """
    Add/update MinerviniScore column in a single signal CSV.
    Returns number of rows updated (0 if skipped).
    """
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for row in reader:
            if any(v.strip() for v in row.values()):
                rows.append(row)

    if not rows:
        print(f"  SKIP: empty file")
        return 0

    # Check if column already exists
    already_has_col = MINERVINI_COL in fieldnames
    if already_has_col and not force:
        print(f"  SKIP: {MINERVINI_COL} already present (use --force to recompute)")
        return 0

    signal_date = _parse_signal_date(csv_path.name)
    updated = 0
    rate_limit_delay = 0.3   # seconds between yfinance calls

    for row in rows:
        symbol = row.get("Symbol", "").strip()
        if not symbol:
            row[MINERVINI_COL] = ""
            continue

        score = _score_symbol(symbol, signal_date)
        row[MINERVINI_COL] = str(score) if score >= 0 else ""
        updated += 1
        time.sleep(rate_limit_delay)

    # Build output fieldnames: insert MinerviniScore after Quality (or at end)
    if not already_has_col:
        insert_after = "Quality"
        if insert_after in fieldnames:
            idx = fieldnames.index(insert_after) + 1
            fieldnames = fieldnames[:idx] + [MINERVINI_COL] + fieldnames[idx:]
        else:
            fieldnames = fieldnames + [MINERVINI_COL]

    if dry_run:
        print(f"  DRY-RUN: would add {MINERVINI_COL} to {updated} rows")
        for row in rows[:3]:
            print(f"    {row.get('Symbol'):6s}  score={row.get(MINERVINI_COL)}")
        return updated

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Written: {updated} rows with {MINERVINI_COL}")
    return updated


def main():
    parser = argparse.ArgumentParser(
        description="Retroactively add MinerviniScore to signal CSVs"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without writing files")
    parser.add_argument("--force", action="store_true",
                        help="Re-compute even if MinerviniScore column already exists")
    parser.add_argument("--mode", choices=["swing", "daytrade", "longterm", "scalping"],
                        help="Process only this trading mode")
    parser.add_argument("--max-files", type=int, default=0,
                        help="Process only the newest N files (0 = all)")
    parser.add_argument("--signals-dir", default=str(SIGNALS_DIR),
                        help=f"Signals directory (default: {SIGNALS_DIR})")
    args = parser.parse_args()

    signals_dir = Path(args.signals_dir)
    if not signals_dir.exists():
        print(f"ERROR: Signals directory not found: {signals_dir}")
        sys.exit(1)

    pattern = f"signals_{args.mode}_*.csv" if args.mode else "signals_*.csv"
    csv_files = list(signals_dir.glob(pattern))
    if not csv_files:
        print(f"No CSV files found in {signals_dir}")
        sys.exit(0)

    # Sort newest first
    def _sort_key(p):
        m = re.search(r"(\d{8}_\d{6})", p.name)
        return m.group(1) if m else p.name

    csv_files = sorted(csv_files, key=_sort_key, reverse=True)
    if args.max_files:
        csv_files = csv_files[:args.max_files]

    print(f"Processing {len(csv_files)} file(s)"
          + (" [DRY-RUN]" if args.dry_run else ""))

    total_rows = 0
    for csv_path in csv_files:
        print(f"\n{csv_path.name}")
        n = process_file(csv_path, dry_run=args.dry_run, force=args.force)
        total_rows += n

    print(f"\nDone. {total_rows} total rows updated across {len(csv_files)} file(s).")


if __name__ == "__main__":
    main()
