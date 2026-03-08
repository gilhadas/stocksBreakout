#!/usr/bin/env python3
"""
test_earnings_watchlist.py
==========================
Check which symbols in input/reporting_watchlist.txt have earnings
reports scheduled THIS WEEK (next 7 calendar days).

Outputs a console table + saves a CSV to scanner_output/signals/.

Usage:
  python test_earnings_watchlist.py
  python test_earnings_watchlist.py --days 14          # extend lookahead
  python test_earnings_watchlist.py --watchlist input/S&P_500.txt
  python test_earnings_watchlist.py --no-save          # console only
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

# ── path setup ───────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

_NY_TZ = ZoneInfo('America/New_York')
OUTPUT_DIR = 'scanner_output/signals'


# ── helpers ──────────────────────────────────────────────────────────────────

def load_symbols(filepath: str) -> list[str]:
    """
    Parse a watchlist file (comma-separated or one-per-line).
    Handles 'EXCHANGE:SYMBOL' format — takes the part after ':'.
    """
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(f"Watchlist not found: {filepath}")

    symbols: list[str] = []
    with open(p, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('###'):
                continue
            for tok in line.split(','):
                tok = tok.strip()
                if not tok:
                    continue
                # Strip exchange prefix (e.g. NASDAQ:AAPL → AAPL)
                sym = tok.split(':')[-1].strip()
                # Skip non-US exchange tokens
                _skip = {'BINANCE', 'BITSTAMP', 'COINBASE', 'CAPITALCOM',
                         'BSE', 'XETR', 'MIL', 'SP', 'FX', 'CRYPTO',
                         'COMEX', 'NYMEX'}
                if tok.split(':')[0].upper() in _skip:
                    continue
                # Convert dot-notation to dash (BRK.B → BRK-B)
                sym = sym.replace('.', '-')
                if sym:
                    symbols.append(sym.upper())

    return list(dict.fromkeys(symbols))  # deduplicate, preserve order


def fetch_earnings_date(symbol: str) -> dict:
    """
    Fetch next earnings date for a symbol via yfinance.
    Returns a dict with: symbol, earnings_date (date|None), timing (BMO/AMC/''),
    days_until (int|None).
    """
    import yfinance as yf

    result = {
        'Symbol': symbol,
        'Earnings_Date': None,
        'Earnings_Timing': '',
        'Days_Until': None,
        'This_Week': False,
        'Error': '',
    }

    try:
        cal = yf.Ticker(symbol).calendar
        if cal is None:
            result['Error'] = 'No calendar'
            return result

        ed_raw = None
        if isinstance(cal, dict):
            ed_val = cal.get('Earnings Date')
            if ed_val:
                ed_raw = ed_val[0] if isinstance(ed_val, list) else ed_val
        elif hasattr(cal, 'iloc'):
            try:
                ed_raw = cal.iloc[0, 0]
            except Exception:
                pass

        if ed_raw is None:
            result['Error'] = 'No date'
            return result

        # Normalise to date
        if isinstance(ed_raw, str):
            ed_raw = pd.Timestamp(ed_raw)
        if hasattr(ed_raw, 'date') and callable(ed_raw.date):
            ed_date = ed_raw.date()
        elif hasattr(ed_raw, 'date'):
            ed_date = ed_raw.date
        else:
            ed_date = pd.Timestamp(str(ed_raw)).date()

        result['Earnings_Date'] = ed_date

        # BMO / AMC
        if hasattr(ed_raw, 'hour') and ed_raw.hour > 0:
            result['Earnings_Timing'] = 'BMO' if ed_raw.hour < 12 else 'AMC'

        today = datetime.now(_NY_TZ).date()
        days_until = (ed_date - today).days
        result['Days_Until'] = days_until

    except Exception as e:
        result['Error'] = str(e)[:60]

    return result


def flag_this_week(rows: list[dict], days: int) -> list[dict]:
    """Mark rows where earnings fall within the next `days` calendar days."""
    today = datetime.now(_NY_TZ).date()
    for r in rows:
        du = r.get('Days_Until')
        if du is not None and 0 <= du <= days:
            r['This_Week'] = True
    return rows


def print_table(rows: list[dict], days: int) -> None:
    """Print a human-readable summary."""
    this_week = [r for r in rows if r['This_Week']]
    upcoming  = [r for r in rows if not r['This_Week'] and r['Earnings_Date']]
    no_data   = [r for r in rows if r['Earnings_Date'] is None]

    today = datetime.now(_NY_TZ).date()
    week_end = today + timedelta(days=days)

    print(f'\n{"="*65}')
    print(f'  EARNINGS CHECK — reporting_watchlist.txt')
    print(f'  Window: {today} → {week_end} ({days} days)')
    print(f'  Symbols checked: {len(rows)}')
    print(f'{"="*65}')

    if this_week:
        print(f'\n  ⚠️  THIS WEEK ({len(this_week)} symbol(s)):')
        print(f'  {"Symbol":<8} {"Earnings_Date":<15} {"Timing":<6} {"Days_Until":>10}')
        print(f'  {"-"*45}')
        for r in sorted(this_week, key=lambda x: x['Days_Until']):
            sym    = r['Symbol']
            ed     = str(r['Earnings_Date'])
            timing = r['Earnings_Timing'] or '—'
            du     = r['Days_Until']
            urgency = '🔴' if du <= 1 else ('🟠' if du <= 3 else '🟡')
            print(f'  {urgency} {sym:<7} {ed:<15} {timing:<6} {du:>9}d')
    else:
        print(f'\n  ✅ No earnings this week for watchlist symbols.')

    if upcoming:
        print(f'\n  📅 UPCOMING (next report after window):')
        upcoming_sorted = sorted(upcoming, key=lambda x: x['Days_Until'] if x['Days_Until'] is not None else 9999)
        for r in upcoming_sorted[:10]:
            ed = str(r['Earnings_Date'])
            du = r['Days_Until']
            timing = r['Earnings_Timing'] or '—'
            print(f'     {r["Symbol"]:<8} {ed:<15} {timing:<6} in {du}d')
        if len(upcoming) > 10:
            print(f'     ... and {len(upcoming) - 10} more')

    if no_data:
        syms = ', '.join(r['Symbol'] for r in no_data)
        print(f'\n  ❓ No earnings data: {syms}')

    print()


def save_csv(rows: list[dict]) -> str:
    """Save results to scanner_output/signals/ and return path."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now(_NY_TZ).strftime('%Y%m%d_%H%M%S')
    filepath = os.path.join(OUTPUT_DIR, f'earnings_watchlist_{ts}.csv')

    df = pd.DataFrame(rows)[
        ['Symbol', 'Earnings_Date', 'Earnings_Timing', 'Days_Until', 'This_Week', 'Error']
    ]
    df.to_csv(filepath, index=False)
    return filepath


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Check earnings dates for reporting watchlist')
    p.add_argument('--watchlist', default='input/reporting_watchlist.txt',
                   help='Path to watchlist file (default: input/reporting_watchlist.txt)')
    p.add_argument('--days', type=int, default=7,
                   help='Lookahead window in calendar days (default: 7 = this week)')
    p.add_argument('--no-save', action='store_true',
                   help='Skip saving CSV output')
    return p.parse_args()


def main() -> None:
    args = parse_args()

    print(f'\n📋 Loading: {args.watchlist}')
    symbols = load_symbols(args.watchlist)
    print(f'   {len(symbols)} symbols: {", ".join(symbols[:15])}{"..." if len(symbols) > 15 else ""}')

    print(f'\n⏳ Fetching earnings calendars...')
    rows: list[dict] = []
    for i, sym in enumerate(symbols, 1):
        print(f'  [{i:3}/{len(symbols)}] {sym:<8}', end='\r', flush=True)
        rows.append(fetch_earnings_date(sym))
    print(' ' * 50, end='\r')

    rows = flag_this_week(rows, args.days)
    print_table(rows, args.days)

    # ── Assert: at least 1 symbol has a date (sanity) ─────────────────────
    has_date = [r for r in rows if r['Earnings_Date'] is not None]
    if not has_date:
        print('  ⚠️  WARNING: No earnings dates fetched — yfinance may be returning empty calendars.')
    else:
        print(f'  ✓ Fetched dates for {len(has_date)}/{len(rows)} symbols.')

    this_week_syms = [r['Symbol'] for r in rows if r['This_Week']]
    if this_week_syms:
        print(f'  ✓ This-week earnings: {", ".join(this_week_syms)}')

    if not args.no_save:
        csv_path = save_csv(rows)
        print(f'\n  💾 Saved: {csv_path}')

    print()


if __name__ == '__main__':
    main()
