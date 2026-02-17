"""
Signal Performance Tracker
--------------------------
Reads signal CSV files from scanner_output/signals/, fetches current prices,
computes gain% from entry, and saves performance reports to scanner_output/signal_reports/.

Each report CSV mirrors the original signal CSV with added columns:
  Current, Gain%, DaysSince, HitTarget, HitStop

Usage:
    python signal_tracker.py                  # Process all signal CSVs
    python signal_tracker.py --days 7         # Only signals from last 7 days
    python signal_tracker.py --file <path>    # Process a single CSV
"""

import argparse
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from config import OUTPUT_DIR
from yfinance_adapter import YFinanceAdapter

logger = logging.getLogger(__name__)

SIGNALS_DIR = Path(OUTPUT_DIR) / 'signals'
REPORTS_DIR = Path(OUTPUT_DIR) / 'signal_reports'


def _parse_date_from_filename(filename: str) -> str:
    """Extract date string (YYYYMMDD) from signal filename like signals_swing_20260217_093039.csv"""
    parts = filename.replace('.csv', '').split('_')
    for p in parts:
        if len(p) == 8 and p.isdigit():
            return p
    return ''


def track_signal_file(csv_path: Path, yf: YFinanceAdapter) -> pd.DataFrame:
    """
    Process a single signal CSV: fetch current prices, compute performance.

    Returns:
        DataFrame with original columns + Current, Gain%, DaysSince, HitTarget, HitStop
    """
    df = pd.read_csv(csv_path)
    if df.empty or 'Symbol' not in df.columns:
        return pd.DataFrame()

    # Parse signal date from filename
    date_str = _parse_date_from_filename(csv_path.name)
    if date_str:
        signal_date = datetime.strptime(date_str, '%Y%m%d')
    else:
        signal_date = datetime.fromtimestamp(csv_path.stat().st_mtime)

    days_since = (datetime.now() - signal_date).days

    # Fetch current prices for all symbols
    symbols = df['Symbol'].unique().tolist()
    price_map = {}
    five_days_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    today = datetime.now().strftime('%Y-%m-%d')

    for sym in symbols:
        try:
            hist = yf.get_historical_data(sym, '1 day',
                                          start_date=five_days_ago,
                                          end_date=today)
            if hist is not None and len(hist) > 0:
                price_map[sym] = float(hist['close'].iloc[-1])
        except Exception:
            pass

    # Compute performance columns
    currents = []
    gains = []
    hit_targets = []
    hit_stops = []

    for _, row in df.iterrows():
        sym = row['Symbol']
        entry = float(row['Price'])
        stop = float(row.get('Stop', 0))
        target = float(row.get('Target', 0))
        current = price_map.get(sym)

        if current is None:
            currents.append(None)
            gains.append(None)
            hit_targets.append(None)
            hit_stops.append(None)
            continue

        gain_pct = ((current - entry) / entry) * 100

        # Check if target or stop was hit by looking at price history since signal
        hit_target = False
        hit_stop = False
        if target > 0 or stop > 0:
            try:
                start = signal_date.strftime('%Y-%m-%d')
                hist = yf.get_historical_data(
                    sym, '1 day',
                    start_date=start,
                    end_date=datetime.now().strftime('%Y-%m-%d')
                )
                if hist is not None and len(hist) > 0:
                    if target > 0:
                        hit_target = bool(hist['high'].max() >= target)
                    if stop > 0:
                        hit_stop = bool(hist['low'].min() <= stop)
            except Exception:
                pass

        currents.append(round(current, 2))
        gains.append(round(gain_pct, 2))
        hit_targets.append(hit_target)
        hit_stops.append(hit_stop)

    df['Current'] = currents
    df['Gain%'] = gains
    df['DaysSince'] = days_since
    df['HitTarget'] = hit_targets
    df['HitStop'] = hit_stops

    return df


def track_all_signals(days: int = None, single_file: str = None) -> list:
    """
    Process signal CSVs and save performance reports.

    Args:
        days: Only process signals from last N days (None = all)
        single_file: Process only this specific file

    Returns:
        List of (source_csv, report_csv, summary_dict) tuples
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    yf = YFinanceAdapter()

    if single_file:
        csv_files = [Path(single_file)]
    else:
        csv_files = sorted(SIGNALS_DIR.glob('signals_*.csv'))

    if days is not None:
        cutoff = (datetime.now() - timedelta(days=days)).date()
        filtered = []
        for f in csv_files:
            date_str = _parse_date_from_filename(f.name)
            if date_str:
                try:
                    fdate = datetime.strptime(date_str, '%Y%m%d').date()
                    if fdate >= cutoff:
                        filtered.append(f)
                except ValueError:
                    pass
        csv_files = filtered

    results = []
    for csv_path in csv_files:
        report_name = csv_path.name.replace('signals_', 'report_')
        report_path = REPORTS_DIR / report_name

        # Skip if report already exists and is newer than source
        if report_path.exists() and report_path.stat().st_mtime > csv_path.stat().st_mtime:
            # Still load for summary
            try:
                df = pd.read_csv(report_path)
                summary = _build_summary(df, csv_path.name)
                results.append((str(csv_path), str(report_path), summary))
            except Exception:
                pass
            continue

        print(f"  Processing {csv_path.name}...", end='\r')
        df = track_signal_file(csv_path, yf)
        if df.empty:
            continue

        df.to_csv(report_path, index=False)

        summary = _build_summary(df, csv_path.name)
        results.append((str(csv_path), str(report_path), summary))
        print(f"  {csv_path.name} -> {len(df)} signals, avg gain: {summary['avg_gain']:+.2f}%")

    # Save combined summary
    if results:
        summary_df = pd.DataFrame([r[2] for r in results])
        summary_path = REPORTS_DIR / 'summary.csv'
        summary_df.to_csv(summary_path, index=False)
        print(f"\nSummary saved to {summary_path}")

    return results


def _build_summary(df: pd.DataFrame, source_name: str) -> dict:
    """Build summary stats for a single signal report."""
    valid = df.dropna(subset=['Gain%'])
    n = len(valid)
    if n == 0:
        return {'source': source_name, 'signals': 0, 'avg_gain': 0, 'winners': 0,
                'losers': 0, 'win_rate': 0, 'best': 0, 'worst': 0,
                'hit_target': 0, 'hit_stop': 0}

    winners = (valid['Gain%'] > 0).sum()
    losers = (valid['Gain%'] < 0).sum()

    return {
        'source': source_name,
        'signals': n,
        'avg_gain': round(valid['Gain%'].mean(), 2),
        'median_gain': round(valid['Gain%'].median(), 2),
        'winners': int(winners),
        'losers': int(losers),
        'win_rate': round((winners / n) * 100, 1) if n > 0 else 0,
        'best': round(valid['Gain%'].max(), 2),
        'worst': round(valid['Gain%'].min(), 2),
        'hit_target': int(valid['HitTarget'].sum()) if 'HitTarget' in valid else 0,
        'hit_stop': int(valid['HitStop'].sum()) if 'HitStop' in valid else 0,
        'days_since': int(valid['DaysSince'].iloc[0]) if 'DaysSince' in valid else 0,
    }


def get_all_reports() -> pd.DataFrame:
    """Load all report CSVs and return combined DataFrame for Streamlit."""
    reports = sorted(REPORTS_DIR.glob('report_*.csv'))
    if not reports:
        return pd.DataFrame()

    frames = []
    for rp in reports:
        try:
            df = pd.read_csv(rp)
            df['_source'] = rp.stem
            frames.append(df)
        except Exception:
            continue

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def get_summary_table() -> pd.DataFrame:
    """Load the summary.csv for Streamlit display."""
    summary_path = REPORTS_DIR / 'summary.csv'
    if summary_path.exists():
        return pd.read_csv(summary_path)
    return pd.DataFrame()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Signal Performance Tracker')
    parser.add_argument('--days', type=int, help='Only process signals from last N days')
    parser.add_argument('--file', type=str, help='Process a single signal CSV file')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    print("Signal Performance Tracker")
    print("=" * 50)

    results = track_all_signals(days=args.days, single_file=args.file)

    if not results:
        print("No signal files to process")
    else:
        print(f"\nProcessed {len(results)} signal files")

        # Print summary table
        total_signals = sum(r[2]['signals'] for r in results)
        total_winners = sum(r[2]['winners'] for r in results)
        total_losers = sum(r[2]['losers'] for r in results)
        all_gains = [r[2]['avg_gain'] for r in results if r[2]['signals'] > 0]
        overall_avg = sum(all_gains) / len(all_gains) if all_gains else 0

        print(f"\nOverall: {total_signals} signals | "
              f"{total_winners}W / {total_losers}L | "
              f"Avg gain: {overall_avg:+.2f}%")
