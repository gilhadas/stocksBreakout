#!/usr/bin/env python3
"""
Build the LIVE signal panel: one row per (signal_date, mode, symbol) occurrence,
joined to what the stock ACTUALLY did afterwards.

This is measurement, not simulation. Every forward column is read off real daily
bars — no config sweep, no counterfactual, no look-ahead. "A stop at X% would have
exited this trade" is then arithmetic on an observed path.

Why this exists
---------------
The champion backtest and live production trade almost disjoint signal populations:
live runs TREND_CONFIRM Path A (config.py:226-228) while every documented champion
baseline was measured with --no-tc. The live archive is ~92% TREND_CONFIRM / ~5%
BOUNCE; the backtest stream is ~99.7% BOUNCE. The panel is the only place live's
real distribution can be studied.

Day-0 handling (important for stop research)
--------------------------------------------
Scans fire ~09:35-10:00 ET, but we only have DAILY bars, so the signal-day bar's
low/high include movement that happened BEFORE the signal existed. Counting that as
adverse excursion would attribute pre-signal movement to the trade. So:

  * mae_pct / mfe_pct / ret_* are measured from the NEXT bar onward (day+1 .. day+N)
  * day0_low_pct / day0_high_pct are recorded SEPARATELY so an analysis can opt in

Usage
-----
    python research/panel/build_panel.py                     # full rebuild
    python research/panel/build_panel.py --modes swing       # subset
    python research/panel/build_panel.py --limit-symbols 50  # quick smoke test
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PANEL_DIR = Path(__file__).resolve().parent
OUT_PARQUET = PANEL_DIR / 'panel.parquet'

# Forward horizons (trading days) measured from day+1
HORIZONS = (1, 3, 5, 10, 20, 30)
MAX_FORWARD_BARS = max(HORIZONS)

# Feature columns carried verbatim off the signal CSV when present.
# Missing ones are filled with NaN — the schema drifted over Apr-Jul as config changed.
FEATURE_COLS = [
    'Price', 'Vol', 'Dist', 'SMA_Dist%', 'Stop', 'Target', 'R:R', 'Gap%',
    'Quality', 'Type', 'RSI', 'GoldenCross', 'FreshCross', 'TC_Path', 'TC_Score',
    'Sector', 'MinerviniScore', 'WinProb', 'WinGrade',
    'FinBERT', 'FinBERT_Score', 'FinBERT_Net', 'FinBERT_Total',
    'Earnings_Date', 'Earnings_Timing', 'Earnings_This_Week',
]

_DATE_RE = re.compile(r'_(\d{8})_')


def _date_from_filename(fname: str) -> str | None:
    m = _DATE_RE.search(fname)
    if not m:
        return None
    d = m.group(1)
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def _mode_from_filename(fname: str) -> str:
    parts = fname.split('_')
    return parts[1] if len(parts) > 1 else 'unknown'


def load_signal_rows(modes: set[str], start: str, end: str) -> pd.DataFrame:
    """Flatten every archived signal CSV into one long frame."""
    from utils import list_files, load_data

    fnames = sorted(list_files('scanner_output/signals', 'signals_*.csv'))
    records = []
    skipped = 0

    for fname in fnames:
        date = _date_from_filename(fname)
        mode = _mode_from_filename(fname)
        if not date or mode not in modes or not (start <= date <= end):
            continue

        df = load_data(f"scanner_output/signals/{fname}")
        if df is None or df.empty:
            skipped += 1
            continue

        df.columns = [c.strip() for c in df.columns]
        if 'Symbol' not in df.columns and 'symbol' in df.columns:
            df = df.rename(columns={'symbol': 'Symbol'})
        if 'Symbol' not in df.columns:
            skipped += 1
            continue

        for _, row in df.iterrows():
            sym = str(row['Symbol']).strip().upper()
            if not sym or sym == 'NAN':
                continue
            rec = {'signal_date': date, 'mode': mode, 'symbol': sym,
                   'source_file': fname}
            for col in FEATURE_COLS:
                rec[col] = row[col] if col in df.columns else np.nan
            records.append(rec)

    print(f"  read {len(fnames)} files ({skipped} unusable) -> {len(records)} signal rows")
    if not records:
        return pd.DataFrame()

    panel = pd.DataFrame.from_records(records)
    panel['signal_date'] = pd.to_datetime(panel['signal_date'])
    return panel


def fetch_bars(symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """Daily bars per symbol. Reuses the project's cached yfinance adapter."""
    from yfinance_adapter import YFinanceAdapter

    adapter = YFinanceAdapter(use_disk_cache=True)
    bars: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(symbols, 1):
        try:
            df = adapter.get_historical_data(sym, '1 day', start_date=start, end_date=end)
        except Exception:
            df = None
        if df is not None and len(df) >= 2:
            df = df.copy()
            idx = pd.to_datetime(df.index)
            if getattr(idx, 'tz', None) is not None:
                idx = idx.tz_localize(None)
            df.index = idx.normalize()
            bars[sym] = df[~df.index.duplicated(keep='last')].sort_index()
        if i % 50 == 0 or i == len(symbols):
            print(f"    bars {i}/{len(symbols)} ({len(bars)} ok)", flush=True)
    return bars


def _forward_metrics(bar_df: pd.DataFrame, sig_ts: pd.Timestamp,
                     entry: float, stop: float, target: float) -> dict:
    """Everything measured off real bars, strictly AFTER the signal bar.

    Also validates the signal's own Price against the signal-day bar. Part of the
    archive records prices that never traded (e.g. ADBE flagged at 93.87 on a day
    it ranged 227.70-236.30) — those rows' Stop/Target are derived from the bogus
    Price too, so every downstream measurement on them is meaningless. They are
    measured anyway but flagged via price_in_bar_range so analyses can exclude them.
    """
    out: dict[str, float | int | bool | None] = {
        'bars_available': 0, 'entry_used': np.nan,
        'day0_low_pct': np.nan, 'day0_high_pct': np.nan,
        'price_in_bar_range': None, 'price_vs_bar_close_pct': np.nan,
        'mae_pct': np.nan, 'mfe_pct': np.nan, 'mae_pct_floored': np.nan,
        'bars_to_mae': np.nan,
        'hit_stop': None, 'hit_target': None,
        'bars_to_stop': np.nan, 'bars_to_target': np.nan,
    }
    for h in HORIZONS:
        out[f'ret_{h}d'] = np.nan

    sig_price = entry
    idx = bar_df.index
    pos = idx.searchsorted(sig_ts)

    # Day-0 bar. ENTRY IS THE BAR CLOSE, not the CSV's Price column — this mirrors
    # what live actually does (auto_portfolio fetches its own price; the §7 A/B
    # harness uses `avail['close'].iloc[0]`), and it makes the panel immune to the
    # archive's bad-price bug (HZ1: ~32% of rows record a price that never traded,
    # e.g. PLTR at 197.20 on a day it ranged 131.23-134.68). The CSV price is kept
    # only as a diagnostic via price_in_bar_range / price_vs_bar_close_pct.
    if pos < len(idx) and idx[pos] == sig_ts:
        d0 = bar_df.iloc[pos]
        lo0, hi0, cl0 = float(d0['low']), float(d0['high']), float(d0['close'])
        if np.isfinite(sig_price) and sig_price > 0:
            out['price_in_bar_range'] = bool(lo0 * 0.995 <= sig_price <= hi0 * 1.005)
            out['price_vs_bar_close_pct'] = (sig_price / cl0 - 1) * 100 if cl0 > 0 else np.nan
        entry = cl0
        out['day0_low_pct'] = (lo0 / entry - 1) * 100
        out['day0_high_pct'] = (hi0 / entry - 1) * 100
        fwd = bar_df.iloc[pos + 1: pos + 1 + MAX_FORWARD_BARS]
    else:
        # Signal date is not a bar (holiday / late file) — enter at next available
        fwd = bar_df.iloc[pos: pos + MAX_FORWARD_BARS]
        if len(fwd):
            entry = float(fwd['close'].iloc[0])

    if not np.isfinite(entry) or entry <= 0:
        return out
    out['entry_used'] = entry

    # Same production guard live applies: a stop at/above entry, or absurdly far,
    # is replaced by entry*0.95. Without this, HZ1's bogus stops would poison
    # hit_stop for a third of the panel.
    if not (np.isfinite(stop) and 0 < stop < entry and (entry - stop) / entry <= 0.30):
        stop = round(entry * 0.95, 4)
    if not (np.isfinite(target) and target > entry):
        target = np.nan

    n = len(fwd)
    out['bars_available'] = n
    if n == 0:
        return out

    lows = fwd['low'].to_numpy(dtype=float)
    highs = fwd['high'].to_numpy(dtype=float)
    closes = fwd['close'].to_numpy(dtype=float)

    # mae_pct is the LOWEST point relative to entry. It can be POSITIVE when a
    # stock gaps up and never trades back — that is "no adverse excursion", not a
    # bug. mae_pct_floored is the conventional MAE (clamped at 0) for the cases
    # where that convention is wanted. The real invariant is mae_pct <= mfe_pct.
    out['mae_pct'] = float((np.minimum.accumulate(lows)[-1] / entry - 1) * 100)
    out['mfe_pct'] = float((np.maximum.accumulate(highs)[-1] / entry - 1) * 100)
    out['mae_pct_floored'] = min(0.0, out['mae_pct'])
    out['bars_to_mae'] = int(np.argmin(lows) + 1)

    for h in HORIZONS:
        if n >= h:
            out[f'ret_{h}d'] = float((closes[h - 1] / entry - 1) * 100)

    if stop is not None and np.isfinite(stop) and stop > 0:
        hits = np.nonzero(lows <= stop)[0]
        out['hit_stop'] = bool(hits.size)
        if hits.size:
            out['bars_to_stop'] = int(hits[0] + 1)
    if target is not None and np.isfinite(target) and target > 0:
        hits = np.nonzero(highs >= target)[0]
        out['hit_target'] = bool(hits.size)
        if hits.size:
            out['bars_to_target'] = int(hits[0] + 1)

    return out


def _to_float(v) -> float:
    try:
        f = float(v)
        return f if np.isfinite(f) else np.nan
    except (TypeError, ValueError):
        return np.nan


def build(modes: set[str], start: str, end: str, limit_symbols: int = 0) -> pd.DataFrame:
    print("[1/4] loading archived signal CSVs (local + S3)...")
    panel = load_signal_rows(modes, start, end)
    if panel.empty:
        return panel

    symbols = sorted(panel['symbol'].unique())
    if limit_symbols:
        symbols = symbols[:limit_symbols]
        panel = panel[panel['symbol'].isin(symbols)].copy()
    print(f"  {len(panel)} rows over {len(symbols)} symbols, "
          f"{panel['signal_date'].nunique()} dates")

    fetch_start = (panel['signal_date'].min() - timedelta(days=10)).strftime('%Y-%m-%d')
    # Clamp to today: yfinance returns NOTHING for a window ending in the future,
    # which silently zeroes out the whole fetch. Recent signals therefore carry
    # fewer than MAX_FORWARD_BARS — update_panel.py fills them in as days accrue.
    want_end = panel['signal_date'].max() + timedelta(days=75)
    fetch_end = min(want_end, pd.Timestamp.today().normalize()).strftime('%Y-%m-%d')
    print(f"[2/4] fetching daily bars {fetch_start} -> {fetch_end}...")
    bars = fetch_bars(symbols, fetch_start, fetch_end)
    print(f"  bars for {len(bars)}/{len(symbols)} symbols")

    print("[3/4] measuring forward outcomes...")
    metrics = []
    for row in panel.itertuples(index=False):
        bar_df = bars.get(row.symbol)
        if bar_df is None:
            metrics.append(_forward_metrics(pd.DataFrame(columns=['low', 'high', 'close']),
                                            row.signal_date, np.nan, np.nan, np.nan))
            continue
        metrics.append(_forward_metrics(
            bar_df, row.signal_date,
            _to_float(getattr(row, 'Price')),
            _to_float(getattr(row, 'Stop')),
            _to_float(getattr(row, 'Target')),
        ))
    panel = pd.concat([panel.reset_index(drop=True),
                       pd.DataFrame(metrics)], axis=1)

    print("[4/4] deriving episode structure...")
    # A symbol re-flagged on consecutive days is the SAME opportunity. Rather than
    # destroy that information by deduping, expose it and let analyses choose.
    panel = panel.sort_values(['symbol', 'mode', 'signal_date'], kind='mergesort')
    gap = panel.groupby(['symbol', 'mode'])['signal_date'].diff().dt.days
    panel['days_since_prev_signal'] = gap
    panel['is_episode_start'] = gap.isna() | (gap > 30)
    panel['episode_id'] = (panel.groupby(['symbol', 'mode'])['is_episode_start']
                           .cumsum().astype(int))

    panel = panel.sort_values(['signal_date', 'mode', 'symbol'],
                              kind='mergesort').reset_index(drop=True)
    return _normalize_dtypes(panel)


def _normalize_dtypes(panel: pd.DataFrame) -> pd.DataFrame:
    """Cast tri-state flags to nullable boolean.

    Left as object dtype these hold Python bool/None, and `~df['flag']` silently
    does *bitwise* negation on ints (True -> -2) instead of logical negation, so a
    filter like `df[~df['price_in_bar_range']]` fails or, worse, selects nonsense.
    Nullable 'boolean' makes ~ behave and keeps NA distinct from False.
    """
    for col in ('price_in_bar_range', 'hit_stop', 'hit_target', 'is_episode_start'):
        if col in panel.columns:
            panel[col] = panel[col].astype('boolean')
    return panel


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--modes', default='swing,longterm',
                    help='comma-separated signal modes (default: swing,longterm; '
                         'daytrade excluded — not in live use)')
    ap.add_argument('--start', default='2000-01-01')
    ap.add_argument('--end', default='2100-01-01')
    ap.add_argument('--limit-symbols', type=int, default=0, help='smoke-test cap')
    ap.add_argument('--out', default=str(OUT_PARQUET))
    args = ap.parse_args()

    panel = build(set(args.modes.split(',')), args.start, args.end, args.limit_symbols)
    if panel.empty:
        print("No signal rows found — nothing written.")
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(out, index=False)
    print(f"\nwrote {len(panel)} rows -> {out}")

    # Verification gate. NOTE: the naive "mae<=0<=mfe" is NOT a valid invariant —
    # a gap-up that never retraces has mae>0, a pure decliner has mfe<0. Both are
    # legitimate. The real invariant is that the low never exceeds the high.
    m = panel.dropna(subset=['mae_pct', 'mfe_pct'])
    bad = m[m['mae_pct'] > m['mfe_pct']]
    if len(bad):
        print(f"  !! INVARIANT VIOLATED on {len(bad)} rows (mae_pct <= mfe_pct)")
        return 2
    print(f"  invariant ok (mae_pct <= mfe_pct) on {len(m)} measured rows")

    checked = panel['price_in_bar_range'].notna()
    if checked.any():
        bad_px = (panel['price_in_bar_range'] == False).sum()  # noqa: E712
        pct = bad_px / checked.sum() * 100
        print(f"  price sanity: {bad_px}/{checked.sum()} rows ({pct:.1f}%) have a signal "
              f"Price outside the signal-day bar range -> price_in_bar_range=False")
    dup = panel.duplicated(subset=['signal_date', 'mode', 'symbol'], keep=False).sum()
    if dup:
        print(f"  note: {dup} rows share a (date, mode, symbol) key — multiple scans "
              f"per day; distinguished by source_file")
    return 0


if __name__ == '__main__':
    sys.exit(main())
