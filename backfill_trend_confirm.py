"""
backfill_trend_confirm.py — Backfill TREND_CONFIRM signal CSVs over a date range.

Why:
  When detect_trend_confirm() is added or its thresholds are tuned, the existing
  signal CSVs in scanner_output/signals/ do not contain TREND_CONFIRM rows.
  auto_portfolio.recalculate() only reads CSVs from disk — running it would
  not benefit from the new detector.  This script regenerates the CSVs by
  walking each business day in the requested range, calling the detector
  against the symbol universe with truncated history, and writing one CSV
  per (day, mode) using the same schema the live scanner emits.

Usage:
  python backfill_trend_confirm.py                    # default: 2026-04-01 → today
  python backfill_trend_confirm.py 2026-04-01         # explicit start
  python backfill_trend_confirm.py 2026-04-01 2026-04-25  # explicit range

Filename convention (matches the live scanner):
  scanner_output/signals/signals_<mode>_<YYYYMMDD>_160000.csv
                                                    ^ 160000 = 16:00 ET = market close
"""
import asyncio; asyncio.set_event_loop(asyncio.new_event_loop())
import sys
import pandas as pd
import yfinance as yf
from pathlib import Path
from datetime import date as _date

from indicators import calculate_all_indicators
from scanner import BreakoutDetector
from sentiment import get_sector_for_ticker
from utils import save_data

ROOT = Path(__file__).parent
SIGNALS_DIR = ROOT / 'scanner_output' / 'signals'

# Schema matches the existing scanner output (superset of swing/longterm columns).
# Extra TC_* columns at the end are TREND_CONFIRM-specific diagnostic fields.
COLUMNS = [
    'Symbol','Price','Vol','Dist','SMA_Dist%','Stop','Target','R:R','Gap%',
    'Mode','Quality','Type','RSI','Streak','SR_Resistance','SR_Res_Strength',
    'VPOC','HVN_Ceiling','Sector','FinBERT','FinBERT_Score','FinBERT_Net',
    'FinBERT_Headline','FinBERT_Total','Earnings_Date','Earnings_Timing',
    'Earnings_Warning','Earnings_This_Week','Reporting_Watchlist',
    'GoldenCross','FreshCross','TC_Path','TC_Score',
]


def load_universe() -> list[str]:
    with open(ROOT / 'input' / 'all.txt') as f:
        return [s.strip() for s in f if s.strip() and not s.startswith('#')]


def fetch_symbol(sym: str, history_start: str, history_end: str):
    """One yfinance call per symbol; returns indicator-enriched DataFrame or None."""
    try:
        h = yf.Ticker(sym).history(start=history_start, end=history_end, auto_adjust=True)
        if h is None or len(h) < 160:
            return None
        h.columns = [c.lower() for c in h.columns]
        h = h[['open', 'high', 'low', 'close', 'volume']].dropna()
        h.index = pd.DatetimeIndex(h.index).tz_localize(None)
        return calculate_all_indicators(h, 'SMA', 150, '1 day')
    except Exception:
        return None


def build_row(sig: dict) -> dict:
    """Map a detect_trend_confirm() result to the full CSV schema."""
    try:
        sector = get_sector_for_ticker(sig['Symbol']) or ''
    except Exception:
        sector = ''
    return {
        'Symbol':           sig['Symbol'],
        'Price':            sig['Price'],
        'Vol':              sig['Vol'],
        'Dist':             sig['Dist'],
        'SMA_Dist%':        sig['SMA_Dist%'],
        'Stop':             sig['Stop'],
        'Target':           sig['Target'],
        'R:R':              sig['R:R'],
        'Gap%':             sig['Gap%'],
        'Mode':             sig['Mode'],
        'Quality':          sig['Quality'],
        'Type':             sig['Type'],
        'RSI':              sig['RSI'],
        'Streak':           '',
        'SR_Resistance':    '',
        'SR_Res_Strength':  '',
        'VPOC':             '',
        'HVN_Ceiling':      '',
        'Sector':           sector,
        'FinBERT':          '',
        'FinBERT_Score':    '',
        'FinBERT_Net':      '',
        'FinBERT_Headline': '',
        'FinBERT_Total':    '',
        'Earnings_Date':    '',
        'Earnings_Timing':  '',
        'Earnings_Warning': '',
        'Earnings_This_Week': False,
        'Reporting_Watchlist': False,
        'GoldenCross':      sig.get('GoldenCross', False),
        'FreshCross':       sig.get('FreshCross', False),
        'TC_Path':          sig.get('TC_Path', ''),
        'TC_Score':         sig.get('TC_Score', ''),
    }


def main(start: str, end: str):
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    det = BreakoutDetector()
    universe = load_universe()
    print(f'Universe: {len(universe)} symbols. Date range: {start} → {end}')
    print('Fetching daily history (one yfinance call per symbol)…')

    # Pre-fetch all data — keeps yfinance calls to one per ticker for the whole range
    cache: dict[str, pd.DataFrame] = {}
    history_start = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime('%Y-%m-%d')
    history_end   = (pd.Timestamp(end)   + pd.Timedelta(days=2)).strftime('%Y-%m-%d')
    for n, sym in enumerate(universe, 1):
        df = fetch_symbol(sym, history_start, history_end)
        if df is not None:
            cache[sym] = df
        if n % 50 == 0:
            print(f'  fetched {n}/{len(universe)} (kept {len(cache)})', flush=True)
    print(f'Cached {len(cache)} symbols with sufficient history\n', flush=True)

    business_days = pd.date_range(start, end, freq='B')
    total_written = 0
    for d in business_days:
        for mode in ('swing', 'longterm'):
            rows = []
            for sym, df in cache.items():
                if d not in df.index:
                    continue
                i = df.index.get_loc(d)
                sub = df.iloc[:i + 1]
                sig = det.detect_trend_confirm(sub, sym, mode, '1 day', spy_perf=0.0)
                if sig:
                    rows.append(build_row(sig))
            if not rows:
                continue
            fname = SIGNALS_DIR / f'signals_{mode}_{d.strftime("%Y%m%d")}_160000.csv'
            df_out = pd.DataFrame(rows, columns=COLUMNS)
            # save_data writes to S3 (via _is_cloud()) AND local — required for
            # auto_portfolio.recalculate to see the file (list_files reads S3 first).
            save_data(df_out, str(fname))
            total_written += len(rows)
            print(f'  {d.date()} {mode:8s}: {len(rows):3d} signals → {fname.name}', flush=True)
    print(f'\nDone. {total_written} TREND_CONFIRM rows written across {len(business_days)} business days.')


if __name__ == '__main__':
    start = sys.argv[1] if len(sys.argv) > 1 else '2026-04-01'
    end   = sys.argv[2] if len(sys.argv) > 2 else _date.today().strftime('%Y-%m-%d')
    main(start, end)
