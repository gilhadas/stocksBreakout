"""
backtest_feedback_agent.py
──────────────────────────
Replay scan_decisions CSVs against historical intraday (5-min) bars to evaluate
how the feedback agent's breakout detection would have performed.

For each scan_decisions_YYYYMMDD.csv:
  1. Load symbols at pipeline stage >= --min-stage (default 5, matching the live agent)
  2. Fetch 5-min bars for that trading day via yfinance
  3. Simulate 5-min polling: find the FIRST bar where:
       close > prev_high  AND  vol_ratio >= VOL_MULT  AND  dist >= MIN_BREAKOUT_PCT%
  4. At breakout: compute ATR stop/target using daily bars PRIOR to entry date
  5. Track outcome over mode-specific hold period (daytrade=1d, swing=5d, longterm=20d)
  6. Compare entry price vs. day-open price (did we get a better fill than "buy at open"?)
  7. Print a per-date + aggregate report; optionally save to CSV.

Usage
─────
  python backtest_feedback_agent.py                          # all available scan_decisions CSVs
  python backtest_feedback_agent.py --date 20260324          # single date
  python backtest_feedback_agent.py --date 20260309,20260310 # specific dates
  python backtest_feedback_agent.py --min-stage 0            # include all pipeline stages
  python backtest_feedback_agent.py --no-vol-filter          # skip volume confirmation
  python backtest_feedback_agent.py --out results.csv        # save trade log to CSV
  python backtest_feedback_agent.py --verbose                # show every symbol checked
"""

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from config import MODES, OUTPUT_DIR

_NY_TZ    = ZoneInfo('America/New_York')
_SCAN_DIR = Path(OUTPUT_DIR) / 'scan_decisions'

# ── Live-agent constants (keep in sync with scan_feedback_agent.py) ───────────
VOL_MULT         = 2.0   # require bar volume >= Xx 20-bar trailing avg
MIN_BREAKOUT_PCT = 0.2   # min % above prev_high to fire BREAKOUT
MIN_STAGE_BREAKOUT = 5   # skip symbols rejected at stages below this (0 = off)

# Mode-specific hold periods (trading days)
MODE_HOLD_DAYS = {
    'daytrade': 1,
    'swing':    5,
    'longterm': 20,
}

# Pipeline stage depth (must match scan_feedback_agent.py STAGE_DEPTH)
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _trading_date(date_str: str) -> datetime:
    """Parse YYYYMMDD to a tz-aware datetime at market open."""
    d = datetime.strptime(date_str, '%Y%m%d')
    return d.replace(tzinfo=_NY_TZ)


def _next_trading_days(from_date: datetime, n: int) -> list[datetime]:
    """Return the next N trading days (Mon–Fri) after from_date."""
    days = []
    d = from_date + timedelta(days=1)
    while len(days) < n:
        if d.weekday() < 5:  # Mon–Fri
            days.append(d)
        d += timedelta(days=1)
    return days


def _next_trading_day(date_str: str) -> str:
    """Return the next Mon–Fri date string after date_str (YYYYMMDD)."""
    d = _trading_date(date_str)
    nxt = _next_trading_days(d, 1)
    return nxt[0].strftime('%Y%m%d')


def _infer_sim_date(csv_path: Path, date_str: str) -> str:
    """
    Determine which trading day to simulate against by reading the first scan timestamp.

    Rules:
      - Weekend timestamp (Sat/Sun)  → next Monday
      - Weekday, time >= 16:00 ET    → next trading day (post-market scan)
      - Weekday, time <  16:00 ET    → same trading day (intraday or pre-market scan)
      - Fallback                     → next trading day
    """
    try:
        with open(csv_path, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                ts_str = row.get('timestamp', '').strip()
                if not ts_str:
                    continue
                ts    = datetime.fromisoformat(ts_str).astimezone(_NY_TZ)
                wday  = ts.weekday()   # Mon=0 … Fri=4 Sat=5 Sun=6
                if wday >= 5:
                    # Weekend: next Monday
                    return _next_trading_day(date_str)
                if ts.hour >= 16:
                    # After market close: next trading day
                    return _next_trading_day(date_str)
                # During or before market hours: simulate same day
                return date_str
    except Exception:
        pass
    return _next_trading_day(date_str)


def _load_decisions(csv_path: Path) -> list[dict]:
    """Load scan_decisions CSV; return rows with a valid prev_high."""
    rows = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            sym  = row.get('symbol', '').strip().upper()
            ph   = row.get('prev_high', '').strip()
            mode = row.get('mode', '').strip() or 'swing'   # old CSVs have no mode col
            if not sym or not ph:
                continue
            try:
                prev_high = float(ph)
            except ValueError:
                continue
            if prev_high <= 0:
                continue
            # Parse scan price (may be empty for early-stage rejections)
            try:
                scan_price = float(row.get('price', '') or 'nan')
            except ValueError:
                scan_price = float('nan')
            row['symbol']     = sym
            row['prev_high']  = prev_high
            row['mode']       = mode
            row['scan_price'] = scan_price
            rows.append(row)
    return rows


def _highest_priority(rows: list[dict]) -> dict[str, dict]:
    """Return {symbol: row} keeping the highest-priority mode per symbol."""
    _PRI = {'longterm': 3, 'swing': 2, 'daytrade': 1}
    best: dict[str, dict] = {}
    for row in rows:
        sym = row['symbol']
        cur_pri = _PRI.get(best[sym]['mode'], 0) if sym in best else -1
        if _PRI.get(row['mode'], 0) > cur_pri:
            best[sym] = row
    return best


def _fetch_5min_bars(symbol: str, date_str: str) -> pd.DataFrame:
    """
    Fetch 5-min bars for the given trading date.
    Returns DataFrame with columns: Open, High, Low, Close, Volume
    Rows are tz-aware (America/New_York), filtered to market hours 9:30–16:00.
    """
    date_dt = _trading_date(date_str)
    market_open  = date_dt.replace(hour=9,  minute=30)
    market_close = date_dt.replace(hour=16, minute=0)

    today_str = datetime.now(_NY_TZ).strftime('%Y%m%d')

    try:
        if date_str >= today_str:
            # For today / very recent: use period='1d' (start/end unreliable)
            hist = yf.Ticker(symbol).history(period='1d', interval='5m', prepost=False)
        else:
            start  = date_dt.strftime('%Y-%m-%d')
            end    = (date_dt + timedelta(days=1)).strftime('%Y-%m-%d')
            hist   = yf.Ticker(symbol).history(
                start=start, end=end, interval='5m', prepost=False
            )
    except Exception:
        return pd.DataFrame()

    if hist.empty:
        return pd.DataFrame()

    # Filter to regular market hours only
    hist.index = hist.index.tz_convert(_NY_TZ)
    hist = hist[(hist.index >= market_open) & (hist.index < market_close)]
    return hist


def _compute_atr_stops(
    symbol: str,
    entry_price: float,
    mode: str,
    as_of_date: str,
) -> tuple[float, float]:
    """
    Compute stop and target from ATR × config sl_mult / tp_mult.
    Uses daily bars from the 3 months PRIOR to as_of_date so we don't peek forward.
    Falls back to heuristic % if data is unavailable.
    """
    cfg    = MODES.get(mode, MODES['swing'])
    end_dt = _trading_date(as_of_date)
    start  = (end_dt - timedelta(days=100)).strftime('%Y-%m-%d')
    end    = end_dt.strftime('%Y-%m-%d')

    try:
        hist = yf.Ticker(symbol).history(start=start, end=end, interval='1d')
        if len(hist) >= 10:
            tr = pd.concat([
                hist['High'] - hist['Low'],
                (hist['High'] - hist['Close'].shift()).abs(),
                (hist['Low']  - hist['Close'].shift()).abs(),
            ], axis=1).max(axis=1)
            atr    = tr.rolling(14).mean().iloc[-1]
            stop   = round(entry_price - atr * cfg['sl_mult'], 4)
            target = round(entry_price + atr * cfg['tp_mult'], 4)
            return stop, target
    except Exception:
        pass

    # Fallback: heuristic %
    sl_pct = cfg.get('atr_mult', 0.5) * cfg.get('sl_mult', 2.0) / 100
    tp_pct = cfg.get('atr_mult', 0.5) * cfg.get('tp_mult', 6.0) / 100
    return round(entry_price * (1 - sl_pct), 4), round(entry_price * (1 + tp_pct), 4)


def _track_outcome(
    symbol: str,
    entry_price: float,
    stop: float,
    target: float,
    entry_date: str,
    mode: str,
) -> dict:
    """
    Simulate trade outcome over MODE_HOLD_DAYS[mode] trading days.
    Returns dict with: exit_price, exit_reason, pnl_pct, hold_days.

    Exit logic:
      - If any daily High >= target → WIN (exit at target)
      - If any daily Low <= stop   → LOSS (exit at stop)
      - First to occur wins; tie on same day → conservative (loss wins)
      - If neither: exit at close of last day
    """
    hold_days = MODE_HOLD_DAYS.get(mode, 5)
    end_dt    = _trading_date(entry_date)
    hold_end  = _next_trading_days(end_dt, hold_days)

    if not hold_end:
        return {'exit_price': entry_price, 'exit_reason': 'NO_DATA', 'pnl_pct': 0.0, 'hold_days': 0}

    start = (end_dt + timedelta(days=1)).strftime('%Y-%m-%d')
    end   = (hold_end[-1] + timedelta(days=2)).strftime('%Y-%m-%d')

    try:
        hist = yf.Ticker(symbol).history(start=start, end=end, interval='1d')
    except Exception:
        hist = pd.DataFrame()

    if hist.empty:
        return {'exit_price': entry_price, 'exit_reason': 'NO_DATA', 'pnl_pct': 0.0, 'hold_days': 0}

    hist = hist.iloc[:hold_days]  # cap at hold period

    for i, (ts, bar) in enumerate(hist.iterrows()):
        hit_stop   = bar['Low']  <= stop
        hit_target = bar['High'] >= target
        if hit_stop and hit_target:
            # Same day: assume worst case (stop hit first — conservative)
            return {
                'exit_price':  stop,
                'exit_reason': 'STOP',
                'pnl_pct':     (stop - entry_price) / entry_price * 100,
                'hold_days':   i + 1,
            }
        if hit_stop:
            return {
                'exit_price':  stop,
                'exit_reason': 'STOP',
                'pnl_pct':     (stop - entry_price) / entry_price * 100,
                'hold_days':   i + 1,
            }
        if hit_target:
            return {
                'exit_price':  target,
                'exit_reason': 'TARGET',
                'pnl_pct':     (target - entry_price) / entry_price * 100,
                'hold_days':   i + 1,
            }

    # Expired: close at last bar
    exit_px = float(hist['Close'].iloc[-1])
    return {
        'exit_price':  exit_px,
        'exit_reason': 'EXPIRED',
        'pnl_pct':     (exit_px - entry_price) / entry_price * 100,
        'hold_days':   len(hist),
    }


def _vol_ratio_at(bars: pd.DataFrame, idx: int, window: int = 20) -> float:
    """Volume ratio: bars.iloc[idx].Volume / mean of prior `window` bars."""
    if idx == 0:
        return 0.0
    start = max(0, idx - window)
    avg   = bars['Volume'].iloc[start:idx].mean()
    if avg <= 0:
        return 0.0
    return float(bars['Volume'].iloc[idx]) / avg


# ── Per-symbol simulation ─────────────────────────────────────────────────────

def _simulate_symbol(
    symbol: str,
    prev_high: float,
    scan_price: float,
    mode: str,
    date_str: str,
    reason_code: str,
    no_vol_filter: bool,
    verbose: bool,
) -> dict | None:
    """
    Simulate the feedback agent's breakout detection for one symbol on one day.
    Returns a trade dict if a breakout was detected, else None.
    """
    bars = _fetch_5min_bars(symbol, date_str)
    if bars.empty:
        if verbose:
            print(f"    {symbol:<8} no 5-min data")
        return None

    # Day open price (for entry quality comparison)
    day_open = float(bars['Open'].iloc[0])

    # Mirror the live agent's "already_above" check:
    # If scan_price was already above prev_high at scan time, no fresh breakout
    # is possible — the live agent would have set already_above=True and skipped.
    # Use a 0.5% tolerance to catch floating-point edge cases.
    if not pd.isna(scan_price) and scan_price > prev_high * 1.005:
        if verbose:
            print(
                f"    {symbol:<8} already above prev_high at scan "
                f"(scan=${scan_price:.2f} > ph={prev_high:.2f}) — skip"
            )
        return None

    # Simulate pass-by-pass
    for idx in range(1, len(bars)):  # skip bar 0 (same as "first check = no alert")
        bar   = bars.iloc[idx]
        close = float(bar['Close'])

        # Filter 1: must close above prev_high
        if close <= prev_high:
            continue

        # Filter 2: min distance above prev_high
        if MIN_BREAKOUT_PCT > 0:
            dist_pct = (close - prev_high) / prev_high * 100
            if dist_pct < MIN_BREAKOUT_PCT:
                continue

        # Filter 3: volume confirmation
        if not no_vol_filter and VOL_MULT > 1.0:
            vr = _vol_ratio_at(bars, idx)
            if vr < VOL_MULT:
                continue

        # Breakout detected at this bar
        entry_ts    = bar.name
        entry_price = close
        vr_at_entry = _vol_ratio_at(bars, idx)

        stop, target = _compute_atr_stops(symbol, entry_price, mode, date_str)

        # How far into the day did we enter? (bar index as fraction of trading day)
        day_progress_pct = idx / len(bars) * 100

        outcome = _track_outcome(symbol, entry_price, stop, target, date_str, mode)

        if verbose:
            print(
                f"    {symbol:<8} BREAKOUT @ ${entry_price:.2f} "
                f"(prev_high={prev_high:.2f}, bar {idx}/{len(bars)}, "
                f"vol={vr_at_entry:.1f}x, mode={mode}) "
                f"→ {outcome['exit_reason']} {outcome['pnl_pct']:+.1f}%"
            )

        return {
            'date':            date_str,
            'symbol':          symbol,
            'mode':            mode,
            'reason_code':     reason_code,
            'prev_high':       round(prev_high, 4),
            'day_open':        round(day_open, 4),
            'entry_price':     round(entry_price, 4),
            'entry_bar_idx':   idx,
            'entry_bar_count': len(bars),
            'entry_day_pct':   round(day_progress_pct, 1),
            'vol_ratio':       round(vr_at_entry, 2),
            'stop':            stop,
            'target':          target,
            'exit_price':      round(outcome['exit_price'], 4),
            'exit_reason':     outcome['exit_reason'],
            'pnl_pct':         round(outcome['pnl_pct'], 3),
            'hold_days':       outcome['hold_days'],
            # Entry quality: negative = we paid LESS than open (better fill)
            'vs_open_pct':     round((entry_price - day_open) / day_open * 100, 3),
        }

    if verbose:
        print(f"    {symbol:<8} no breakout fired (prev_high={prev_high:.2f})")
    return None


# ── Per-date processing ───────────────────────────────────────────────────────

def _process_date(
    date_str: str,
    min_stage: int,
    no_vol_filter: bool,
    verbose: bool,
    sim_date: str | None = None,
    extra_csv_dates: list[str] | None = None,
) -> tuple[list[dict], dict]:
    """
    Process one or more scan_decisions_YYYYMMDD.csv files mapped to the same sim_date.
    extra_csv_dates: additional (earlier) CSV dates to merge into the decision set.
    sim_date: trading day to simulate against (default: auto-inferred from scan timestamp).
    Returns (trade_list, stats_dict).
    """
    csv_path = _SCAN_DIR / f'scan_decisions_{date_str}.csv'
    if not csv_path.exists():
        print(f"  [SKIP] {csv_path.name} not found")
        return [], {}

    # Determine which trading day to simulate
    if sim_date is None:
        sim_date = _infer_sim_date(csv_path, date_str)

    # Skip if sim_date is today or in the future (market may not have traded yet)
    today_str = datetime.now(_NY_TZ).strftime('%Y%m%d')
    if sim_date >= today_str:
        print(f"\n── {date_str}  (sim day: {sim_date})  [SKIP — market data not yet available]")
        return [], {}

    # Load rows from primary CSV; then merge earlier CSVs (later ones take precedence)
    all_rows: list[dict] = []
    for extra_ds in (extra_csv_dates or []):
        extra_path = _SCAN_DIR / f'scan_decisions_{extra_ds}.csv'
        if extra_path.exists():
            all_rows.extend(_load_decisions(extra_path))
    all_rows.extend(_load_decisions(csv_path))   # primary CSV last → wins on dedup

    rows   = all_rows
    by_sym = _highest_priority(rows)
    eligible = {
        sym: row for sym, row in by_sym.items()
        if STAGE_DEPTH.get(row.get('reason_code', ''), 0) >= min_stage
    }

    mode_counts = {}
    for row in eligible.values():
        m = row['mode']
        mode_counts[m] = mode_counts.get(m, 0) + 1

    total_eligible = len(eligible)
    print(
        f"\n── {date_str}  (sim day: {sim_date})  "
        f"({total_eligible} eligible: "
        + ', '.join(f"{m}={c}" for m, c in sorted(mode_counts.items()))
        + f")  from {len(rows)} rows"
    )

    trades  = []
    no_fire = 0

    for sym, row in sorted(eligible.items()):
        result = _simulate_symbol(
            symbol       = sym,
            prev_high    = row['prev_high'],
            scan_price   = row.get('scan_price', float('nan')),
            mode         = row['mode'],
            date_str     = sim_date,
            reason_code  = row.get('reason_code', ''),
            no_vol_filter= no_vol_filter,
            verbose      = verbose,
        )
        if result:
            trades.append(result)
        else:
            no_fire += 1

    # Compute per-date stats
    detection_rate = len(trades) / total_eligible * 100 if total_eligible else 0
    wins  = [t for t in trades if t['pnl_pct'] > 0]
    stats = {
        'date':           date_str,
        'eligible':       total_eligible,
        'detected':       len(trades),
        'no_fire':        no_fire,
        'detection_rate': round(detection_rate, 1),
        'win_rate':       round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        'avg_pnl_pct':    round(np.mean([t['pnl_pct'] for t in trades]), 3) if trades else 0.0,
        'med_pnl_pct':    round(np.median([t['pnl_pct'] for t in trades]), 3) if trades else 0.0,
        'avg_vs_open':    round(np.mean([t['vs_open_pct'] for t in trades]), 3) if trades else 0.0,
        'targets_hit':    sum(1 for t in trades if t['exit_reason'] == 'TARGET'),
        'stops_hit':      sum(1 for t in trades if t['exit_reason'] == 'STOP'),
        'expired':        sum(1 for t in trades if t['exit_reason'] == 'EXPIRED'),
    }

    print(
        f"  Detected {stats['detected']}/{stats['eligible']} "
        f"({stats['detection_rate']}%)  |  "
        f"WR {stats['win_rate']}%  |  "
        f"Avg P&L {stats['avg_pnl_pct']:+.2f}%  |  "
        f"Avg vs open {stats['avg_vs_open']:+.2f}%  |  "
        f"T/S/E {stats['targets_hit']}/{stats['stops_hit']}/{stats['expired']}"
    )

    return trades, stats


# ── Report ────────────────────────────────────────────────────────────────────

def _print_aggregate(all_trades: list[dict], all_stats: list[dict]) -> None:
    if not all_trades:
        print("\nNo trades detected across all dates.")
        return

    wins  = [t for t in all_trades if t['pnl_pct'] > 0]
    total = len(all_trades)

    # Mode breakdown
    by_mode: dict[str, list] = {}
    for t in all_trades:
        by_mode.setdefault(t['mode'], []).append(t)

    print("\n" + "═" * 65)
    print("  AGGREGATE RESULTS")
    print("═" * 65)
    print(f"  Dates processed:    {len(all_stats)}")
    print(f"  Total eligible:     {sum(s['eligible'] for s in all_stats)}")
    print(f"  Total detected:     {total}")
    det_rate = total / sum(s['eligible'] for s in all_stats) * 100 if all_stats else 0
    print(f"  Detection rate:     {det_rate:.1f}%")
    print(f"  Win rate:           {len(wins)/total*100:.1f}%  ({len(wins)}/{total})")
    print(f"  Avg P&L:            {np.mean([t['pnl_pct'] for t in all_trades]):+.2f}%")
    print(f"  Median P&L:         {np.median([t['pnl_pct'] for t in all_trades]):+.2f}%")
    pnl_arr = np.array([t['pnl_pct'] for t in all_trades])
    print(f"  Std dev P&L:        {np.std(pnl_arr):.2f}%")
    if np.std(pnl_arr) > 0:
        sharpe = np.mean(pnl_arr) / np.std(pnl_arr) * (252 ** 0.5)
        print(f"  Sharpe (annualised):{sharpe:.2f}  (rough estimate)")
    print(f"  Avg entry vs open:  {np.mean([t['vs_open_pct'] for t in all_trades]):+.2f}%  "
          f"(negative = better than open)")
    print(f"  Targets hit:        {sum(1 for t in all_trades if t['exit_reason']=='TARGET')}")
    print(f"  Stops hit:          {sum(1 for t in all_trades if t['exit_reason']=='STOP')}")
    print(f"  Expired:            {sum(1 for t in all_trades if t['exit_reason']=='EXPIRED')}")

    print()
    print("  By mode:")
    for mode in ('daytrade', 'swing', 'longterm'):
        mt = by_mode.get(mode, [])
        if not mt:
            continue
        mw = [t for t in mt if t['pnl_pct'] > 0]
        print(
            f"    {mode:<10} {len(mt):>3} trades  |  "
            f"WR {len(mw)/len(mt)*100:.0f}%  |  "
            f"Avg {np.mean([t['pnl_pct'] for t in mt]):+.2f}%  |  "
            f"vs open {np.mean([t['vs_open_pct'] for t in mt]):+.2f}%"
        )

    print()
    print("  Top 5 winners:")
    for t in sorted(all_trades, key=lambda x: x['pnl_pct'], reverse=True)[:5]:
        print(f"    {t['symbol']:<8} {t['date']}  [{t['mode']}]  "
              f"{t['pnl_pct']:+.1f}%  entry=${t['entry_price']:.2f}  "
              f"exit={t['exit_reason']}")

    print()
    print("  Top 5 losers:")
    for t in sorted(all_trades, key=lambda x: x['pnl_pct'])[:5]:
        print(f"    {t['symbol']:<8} {t['date']}  [{t['mode']}]  "
              f"{t['pnl_pct']:+.1f}%  entry=${t['entry_price']:.2f}  "
              f"exit={t['exit_reason']}")

    print("═" * 65)


def _save_csv(trades: list[dict], path: Path) -> None:
    if not trades:
        return
    fieldnames = list(trades[0].keys())
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(trades)
    print(f"\n  Trade log saved → {path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _discover_dates() -> list[str]:
    """Return sorted list of YYYYMMDD strings from available scan_decisions_*.csv files."""
    dates = []
    for p in sorted(_SCAN_DIR.glob('scan_decisions_????????.csv')):
        ds = p.stem.replace('scan_decisions_', '')
        if len(ds) == 8 and ds.isdigit():
            dates.append(ds)
    return dates


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description='Backtest scan_feedback_agent against historical scan_decisions CSVs'
    )
    p.add_argument(
        '--date', default=None,
        help='Comma-separated YYYYMMDD dates to backtest (default: all available)'
    )
    p.add_argument(
        '--min-stage', type=int, default=MIN_STAGE_BREAKOUT,
        help=f'Minimum pipeline stage to include (default: {MIN_STAGE_BREAKOUT})'
    )
    p.add_argument(
        '--no-vol-filter', action='store_true',
        help='Skip volume confirmation (backtest without VOL_MULT filter)'
    )
    p.add_argument(
        '--out', default=None,
        help='Save trade log to this CSV path'
    )
    p.add_argument(
        '--verbose', '-v', action='store_true',
        help='Show every symbol checked'
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if args.date:
        dates = [d.strip() for d in args.date.split(',') if d.strip()]
    else:
        dates = _discover_dates()

    if not dates:
        print(f"No scan_decisions CSVs found in {_SCAN_DIR}")
        sys.exit(1)

    print(f"backtest_feedback_agent.py")
    print(f"  Dates:       {', '.join(dates)}")
    print(f"  Min stage:   {args.min_stage}")
    print(f"  Vol filter:  {'OFF' if args.no_vol_filter else f'ON (>={VOL_MULT}x)'}")
    print(f"  Breakout %:  {MIN_BREAKOUT_PCT}%")

    # Group CSVs by their sim_date to merge decisions before simulating.
    # Multiple CSVs can map to the same sim_date (e.g. post-market Sunday scan +
    # intraday Monday scan both map to Monday). We merge decisions per sim_date,
    # keeping the highest-priority mode per symbol — preferring later CSV timestamps.
    sim_date_map: dict[str, list[str]] = {}  # sim_date → [csv_date, ...]
    csv_path_by_date: dict[str, Path] = {}
    for ds in dates:
        csv_path = _SCAN_DIR / f'scan_decisions_{ds}.csv'
        if not csv_path.exists():
            continue
        csv_path_by_date[ds] = csv_path
        sd = _infer_sim_date(csv_path, ds)
        sim_date_map.setdefault(sd, []).append(ds)

    all_trades: list[dict] = []
    all_stats:  list[dict] = []

    for sd in sorted(sim_date_map):
        csv_dates = sorted(sim_date_map[sd])   # process in chronological order
        trades, stats = _process_date(
            date_str     = csv_dates[-1],       # use label of most recent CSV
            min_stage    = args.min_stage,
            no_vol_filter= args.no_vol_filter,
            verbose      = args.verbose,
            sim_date     = sd,
            extra_csv_dates = csv_dates[:-1],   # merge earlier CSVs into decisions
        )
        all_trades.extend(trades)
        if stats:
            all_stats.append(stats)

    _print_aggregate(all_trades, all_stats)

    if args.out:
        _save_csv(all_trades, Path(args.out))


if __name__ == '__main__':
    main()
