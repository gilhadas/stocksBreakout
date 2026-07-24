#!/usr/bin/env python3
"""
Daily incremental panel update. Two jobs, both idempotent:

  1. APPEND rows for signal CSVs not already in the panel (identified by
     source_file, so a re-run of the same scan is not double-counted).
  2. ADVANCE forward measurements on existing rows that are still "open" —
     a row signalled 3 days ago has only 3 of 30 forward bars, so its
     mae/mfe/ret_30d are provisional and must be recomputed as bars accrue.

A row is FROZEN once bars_available == MAX_FORWARD_BARS; frozen rows are never
recomputed, which is what makes repeated runs cheap and makes "run it twice ->
no change" true.

Usage:
    python research/panel/update_panel.py            # normal daily run
    python research/panel/update_panel.py --dry-run  # report, write nothing
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research.panel.build_panel import (  # noqa: E402
    MAX_FORWARD_BARS, OUT_PARQUET, _forward_metrics, _to_float,
    fetch_bars, load_signal_rows,
)

METRIC_COLS = [
    'bars_available', 'entry_used', 'day0_low_pct', 'day0_high_pct',
    'price_in_bar_range', 'price_vs_bar_close_pct',
    'mae_pct', 'mfe_pct', 'mae_pct_floored', 'bars_to_mae',
    'hit_stop', 'hit_target', 'bars_to_stop', 'bars_to_target',
    'ret_1d', 'ret_3d', 'ret_5d', 'ret_10d', 'ret_20d', 'ret_30d',
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--panel', default=str(OUT_PARQUET))
    ap.add_argument('--modes', default='swing,longterm')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    path = Path(args.panel)
    if not path.exists():
        print(f"No panel at {path} — run build_panel.py first.")
        return 1

    panel = pd.read_parquet(path)
    known_files = set(panel['source_file'].unique())
    print(f"existing panel: {len(panel)} rows, {len(known_files)} source files, "
          f"latest {panel['signal_date'].max().date()}")

    # ── 1. new signal files ────────────────────────────────────────────────────
    fresh = load_signal_rows(set(args.modes.split(',')), '2000-01-01', '2100-01-01')
    new_rows = fresh[~fresh['source_file'].isin(known_files)].copy() if not fresh.empty \
        else pd.DataFrame()
    print(f"new signal rows: {len(new_rows)}"
          f" from {new_rows['source_file'].nunique() if len(new_rows) else 0} new files")

    # ── 2. rows still accruing forward bars ────────────────────────────────────
    open_mask = panel['bars_available'].fillna(0) < MAX_FORWARD_BARS
    open_rows = panel[open_mask]
    print(f"open rows needing re-measurement: {len(open_rows)}"
          f" (frozen: {len(panel) - len(open_rows)})")

    if not len(new_rows) and not len(open_rows):
        print("nothing to do.")
        return 0
    if args.dry_run:
        print("--dry-run: no write.")
        return 0

    combined = pd.concat([panel[~open_mask], open_rows, new_rows],
                         ignore_index=True, sort=False)

    need = pd.concat([open_rows, new_rows], ignore_index=True, sort=False)
    symbols = sorted(need['symbol'].unique())
    start = (need['signal_date'].min() - timedelta(days=10)).strftime('%Y-%m-%d')
    # Never ask for a future window — yfinance returns nothing for one (see build_panel).
    end = pd.Timestamp.today().normalize().strftime('%Y-%m-%d')
    print(f"fetching bars for {len(symbols)} symbols {start} -> {end}...")
    bars = fetch_bars(symbols, start, end)

    remeasure_idx = combined.index[combined['bars_available'].fillna(0) < MAX_FORWARD_BARS]
    print(f"re-measuring {len(remeasure_idx)} rows...")
    for i in remeasure_idx:
        row = combined.loc[i]
        bar_df = bars.get(row['symbol'])
        if bar_df is None:
            continue
        m = _forward_metrics(bar_df, row['signal_date'],
                             _to_float(row.get('Price')),
                             _to_float(row.get('Stop')),
                             _to_float(row.get('Target')))
        for k, v in m.items():
            combined.at[i, k] = v

    # Rebuild episode structure over the full set (new rows change the gaps).
    combined = combined.sort_values(['symbol', 'mode', 'signal_date'], kind='mergesort')
    gap = combined.groupby(['symbol', 'mode'])['signal_date'].diff().dt.days
    combined['days_since_prev_signal'] = gap
    combined['is_episode_start'] = gap.isna() | (gap > 30)
    combined['episode_id'] = (combined.groupby(['symbol', 'mode'])['is_episode_start']
                              .cumsum().astype(int))
    combined = combined.sort_values(['signal_date', 'mode', 'symbol'],
                                    kind='mergesort').reset_index(drop=True)

    combined.to_parquet(path, index=False)
    print(f"wrote {len(combined)} rows -> {path}")

    m = combined.dropna(subset=['mae_pct', 'mfe_pct'])
    bad = m[m['mae_pct'] > m['mfe_pct']]
    if len(bad):
        print(f"  !! INVARIANT VIOLATED on {len(bad)} rows")
        return 2
    print(f"  invariant ok on {len(m)} measured rows")
    return 0


if __name__ == '__main__':
    sys.exit(main())
