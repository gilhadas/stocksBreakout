"""
feedback_backtest_hist.py
─────────────────────────
Replay the scan_feedback_agent alert logic on historical 1-minute intraday data
for the last N trading days (default 5).

Two modes
─────────
DEFAULT (decisions CSV mode)
  Uses today's scan_decisions CSV for symbols + resistance levels (prev_high).
  Tests whether those resistance levels held during the past N days.

WATCHLIST mode  (--watchlist <file>)
  Loads symbols from a TradingView watchlist file (EXCHANGE:SYMBOL lines).
  For each historical day D, computes prev_high dynamically as the rolling
  15-day high from daily bars — simulating what the scanner would have watched.
  This tests breakout accuracy on ACCEPTED/setup symbols, not just rejectees.

How it works (both modes)
────────────
1.  Fetch 1-min bars for each symbol for the last N trading days via yfinance.
2.  For every (symbol, day) pair:
      • "Scan snapshot" established at 09:45 ET:
            scan_price = actual price at 09:45
            prev_high  = fixed from CSV  OR  rolling 15-day high (watchlist mode)
      • Feedback-agent passes every INTERVAL minutes from 09:50 → 16:00 ET.
      • BREAKOUT  fired when price > prev_high  (first crossing only).
      • SURGE     fired when |Δ% since last pass| ≥ SURGE_THRESHOLD.
      • FLIP      fired when direction flips  AND |Δ%| ≥ SURGE_THRESHOLD.
3.  For every BREAKOUT alert the script measures forward returns:
        max_gain%   max close in [0, +60 min] relative to breakout price
        max_loss%   max drawdown in [0, +60 min]
        ret_15m     return at +15 min
        ret_30m     return at +30 min
        ret_60m     return at +60 min
4.  Writes two files:
        scanner_output/scan_decisions/feedback_backtest_<today>.csv
        scanner_output/scan_decisions/feedback_backtest_<today>_report.txt

Usage
─────
  python3 feedback_backtest_hist.py
  python3 feedback_backtest_hist.py --days 5 --interval 5
  python3 feedback_backtest_hist.py --watchlist input/1_26_Setups.txt
  python3 feedback_backtest_hist.py --watchlist input/1_26_Setups.txt --rolling-days 20
  python3 feedback_backtest_hist.py --date 20260309
  python3 feedback_backtest_hist.py --report
"""

import argparse
import csv
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np
import yfinance as yf
from zoneinfo import ZoneInfo

# ── Config ────────────────────────────────────────────────────────────────────
_NY_TZ          = ZoneInfo('America/New_York')
_SCAN_DIR       = Path('scanner_output/scan_decisions')
SURGE_THRESHOLD = 2.0    # % — same as feedback agent
FLAT_BAND       = 0.3    # % — same as feedback agent
CONFIRM_BAND    = 0.50   # % above prev_high to count as a "confirmed" breakout
CONFIRM_BARS    = 2      # must stay above CONFIRM_BAND for at least this many passes
FORWARD_MINS    = [15, 30, 60]
ROLLING_DAYS    = 15     # default rolling window for watchlist prev_high

# Exchanges to skip when parsing TradingView watchlists
_SKIP_EXCHANGES = {
    'BINANCE', 'BITSTAMP', 'COINBASE', 'CAPITALCOM',
    'BSE', 'XETR', 'MIL', 'SP', 'FX', 'CRYPTO',
    'COMEX', 'NYMEX',
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _today_str(date_str: Optional[str] = None) -> str:
    return date_str or datetime.now(_NY_TZ).strftime('%Y%m%d')


def _decisions_path(date_str: str) -> Path:
    return _SCAN_DIR / f'scan_decisions_{date_str}.csv'


def _last_n_trading_days(n: int, anchor: Optional[date] = None) -> list[date]:
    """Return the last N completed trading days (Mon–Fri) before anchor."""
    anchor = anchor or date.today()
    days: list[date] = []
    d = anchor - timedelta(days=1)
    while len(days) < n:
        if d.weekday() < 5:   # Mon=0 … Fri=4
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)   # ascending


def _direction(pct: float) -> str:
    if pct > FLAT_BAND:
        return 'UP'
    if pct < -FLAT_BAND:
        return 'DOWN'
    return 'FLAT'


# ── Watchlist loader ──────────────────────────────────────────────────────────

def _load_tv_watchlist(filepath: str) -> list[str]:
    """
    Parse a TradingView watchlist file (EXCHANGE:SYMBOL per line).
    Skips ### headers, non-US exchanges, and converts '.' → '-' for yfinance.
    Returns deduplicated list of ticker symbols.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Watchlist file not found: {filepath}")

    symbols: list[str] = []
    seen: set[str] = set()
    # TradingView exports as a single comma-separated line; handle both formats
    raw_text = path.read_text().replace('\r', '')
    tokens = [t.strip() for part in raw_text.splitlines() for t in part.split(',')]
    for line in tokens:
        if not line or line.startswith('#'):
            continue
        if ':' in line:
            exchange, sym = line.split(':', 1)
            if exchange.upper() in _SKIP_EXCHANGES:
                continue
            sym = sym.replace('.', '-').upper()
        else:
            sym = line.replace('.', '-').upper()
        if sym and sym not in seen:
            seen.add(sym)
            symbols.append(sym)
    return symbols


# ── Rolling prev_high from daily bars ────────────────────────────────────────

def _fetch_daily_bars(symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    """Fetch daily OHLCV bars for rolling high computation."""
    if not symbols:
        return {}
    CHUNK = 100
    result: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        raw = yf.download(
            chunk,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval='1d',
            progress=False,
            auto_adjust=True,
            group_by='ticker' if len(chunk) > 1 else None,
        )
        if raw.empty:
            continue
        if len(chunk) == 1:
            sym = chunk[0]
            if not raw.empty:
                result[sym] = raw.copy()
        else:
            for sym in chunk:
                try:
                    df = raw[sym].dropna(how='all').copy()
                    if not df.empty:
                        result[sym] = df
                except Exception:
                    pass
    return result


def _build_rolling_prev_high(daily_bars: dict[str, pd.DataFrame],
                              trade_days: list[date],
                              rolling_days: int) -> dict[str, dict[date, float]]:
    """
    For each symbol and each trading day D, compute prev_high as the rolling
    max of 'High' over the `rolling_days` daily bars PRIOR TO D (exclusive).
    Returns {symbol: {day: prev_high}}.
    """
    result: dict[str, dict[date, float]] = {}
    for sym, df in daily_bars.items():
        # Normalise index to date objects
        df = df.copy()
        df.index = pd.to_datetime(df.index).date
        sym_phs: dict[date, float] = {}
        for d in trade_days:
            # Bars strictly before day D
            prior = df[df.index < d]
            if len(prior) < 3:          # not enough history
                continue
            window = prior['High'].iloc[-rolling_days:]
            sym_phs[d] = float(window.max())
        if sym_phs:
            result[sym] = sym_phs
    return result


# ── Data fetching ─────────────────────────────────────────────────────────────

def _fetch_1min_batch(symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    """
    Fetch 1-min bars for multiple symbols over [start, end).
    Returns {symbol: DataFrame(index=DatetimeTZAware, columns=[Open,High,Low,Close,Volume])}.
    """
    if not symbols:
        return {}

    print(f"  Fetching 1-min data for {len(symbols)} symbols "
          f"({start} → {end}) …", flush=True)

    # yfinance batch download
    raw = yf.download(
        symbols,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval='1m',
        progress=False,
        auto_adjust=True,
        group_by='ticker' if len(symbols) > 1 else None,
    )

    result: dict[str, pd.DataFrame] = {}
    if raw.empty:
        return result

    if len(symbols) == 1:
        sym = symbols[0]
        if not raw.empty:
            df = raw.copy()
            df.index = pd.to_datetime(df.index).tz_localize('UTC').tz_convert(_NY_TZ) \
                if df.index.tzinfo is None else df.index.tz_convert(_NY_TZ)
            result[sym] = df
    else:
        for sym in symbols:
            try:
                df = raw[sym].dropna(how='all').copy()
                if df.empty:
                    continue
                df.index = pd.to_datetime(df.index).tz_localize('UTC').tz_convert(_NY_TZ) \
                    if df.index.tzinfo is None else df.index.tz_convert(_NY_TZ)
                result[sym] = df
            except Exception:
                pass

    return result


# ── Outcome evaluation ────────────────────────────────────────────────────────

def _forward_returns(bars_1min: pd.DataFrame, breakout_ts: pd.Timestamp,
                     breakout_price: float) -> dict:
    """
    Compute forward returns from breakout_ts using 1-min bars.
    Returns dict with keys: max_gain_pct, max_loss_pct, ret_15m, ret_30m, ret_60m.
    """
    future = bars_1min.loc[breakout_ts:]
    if future.empty:
        return {f'ret_{m}m': float('nan') for m in FORWARD_MINS} | \
               {'max_gain_pct': float('nan'), 'max_loss_pct': float('nan')}

    fwd: dict = {}
    closes = future['Close'].values

    # max gain / max loss over 60-min window
    window = closes[:60]
    fwd['max_gain_pct'] = float((window.max() - breakout_price) / breakout_price * 100) \
        if len(window) else float('nan')
    fwd['max_loss_pct'] = float((window.min() - breakout_price) / breakout_price * 100) \
        if len(window) else float('nan')

    # fixed-interval returns
    for m in FORWARD_MINS:
        idx = m - 1   # 0-based: bar at minute m
        if idx < len(closes):
            fwd[f'ret_{m}m'] = float((closes[idx] - breakout_price) / breakout_price * 100)
        else:
            fwd[f'ret_{m}m'] = float('nan')

    return fwd


# ── Per-day simulation ────────────────────────────────────────────────────────

def _simulate_day(symbol: str, prev_high: float, day: date,
                  bars_1min: pd.DataFrame, interval_min: int) -> list[dict]:
    """
    Replay feedback-agent passes for one (symbol, day).
    Returns list of event dicts.
    """
    events: list[dict] = []

    # Filter to market hours on this day
    day_start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=_NY_TZ)
    day_end   = datetime(day.year, day.month, day.day, 16,  0, tzinfo=_NY_TZ)
    day_bars  = bars_1min[(bars_1min.index >= day_start) & (bars_1min.index < day_end)].copy()

    if day_bars.empty or len(day_bars) < 20:
        return events

    # Establish scan snapshot at 09:45
    snap_time = datetime(day.year, day.month, day.day, 9, 45, tzinfo=_NY_TZ)
    snap_rows = day_bars[day_bars.index <= snap_time]
    if snap_rows.empty:
        return events

    scan_price = float(snap_rows['Close'].iloc[-1])

    # Build list of check timestamps (every interval_min from 09:50 → 15:55)
    check_start = datetime(day.year, day.month, day.day, 9, 50, tzinfo=_NY_TZ)
    check_ts_list: list[pd.Timestamp] = []
    ts = check_start
    while ts <= day_end:
        check_ts_list.append(pd.Timestamp(ts))
        ts += timedelta(minutes=interval_min)

    # State
    last_price:    Optional[float] = None
    last_direction = 'FLAT'
    alerted_breakout = False
    alerted_surge_at: Optional[str] = None

    # Pre-flag: already above prev_high at scan time
    if scan_price >= prev_high:
        alerted_breakout = True   # started above level → ignore first cross

    for check_ts in check_ts_list:
        # Get closest bar at or before check_ts
        avail = day_bars[day_bars.index <= check_ts]
        if avail.empty:
            continue
        current_price = float(avail['Close'].iloc[-1])

        pct_since_last = (
            (current_price - last_price) / last_price * 100
            if last_price else float('nan')
        )
        cur_dir  = _direction(pct_since_last) if last_price else 'FLAT'
        prev_dir = last_direction

        event = 'OK'

        # BREAKOUT
        if (not alerted_breakout
                and current_price > prev_high
                and last_price is not None):
            event = 'BREAKOUT'

        # SURGE (before FLIP — higher priority)
        elif (last_price is not None
              and not np.isnan(pct_since_last)
              and abs(pct_since_last) >= SURGE_THRESHOLD
              and alerted_surge_at != f'{pct_since_last:.0f}'):
            event = 'SURGE'

        # FLIP (requires meaningful move — same as patched agent)
        elif (last_price is not None
              and cur_dir != prev_dir
              and prev_dir != 'FLAT'
              and not np.isnan(pct_since_last)
              and abs(pct_since_last) >= SURGE_THRESHOLD):
            event = 'FLIP'

        # Update state
        if event == 'BREAKOUT':
            alerted_breakout = True
        if event == 'SURGE':
            alerted_surge_at = f'{pct_since_last:.0f}'

        last_price     = current_price
        last_direction = cur_dir

        if event == 'OK':
            continue

        row: dict = {
            'day':           day.isoformat(),
            'symbol':        symbol,
            'event':         event,
            'check_time':    check_ts.strftime('%H:%M'),
            'scan_price':    round(scan_price, 4),
            'prev_high':     round(prev_high, 4),
            'current_price': round(current_price, 4),
            'pct_from_scan': round((current_price - scan_price) / scan_price * 100, 2)
                             if scan_price else float('nan'),
            'pct_since_last': round(pct_since_last, 2) if not np.isnan(pct_since_last) else '',
        }

        # Forward returns for BREAKOUT
        if event == 'BREAKOUT':
            fwd = _forward_returns(day_bars, check_ts, current_price)
            row.update(fwd)
            # Confirmed: price stayed ≥ CONFIRM_BAND above prev_high for CONFIRM_BARS passes
            future_passes = [t for t in check_ts_list if t > check_ts][:CONFIRM_BARS * 2]
            above_count = 0
            for fts in future_passes:
                favail = day_bars[day_bars.index <= fts]
                if not favail.empty:
                    fp = float(favail['Close'].iloc[-1])
                    if fp >= prev_high * (1 + CONFIRM_BAND / 100):
                        above_count += 1
            row['confirmed'] = above_count >= CONFIRM_BARS
        else:
            for m in FORWARD_MINS:
                row[f'ret_{m}m'] = ''
            row.update({'max_gain_pct': '', 'max_loss_pct': '', 'confirmed': ''})

        events.append(row)

    return events


# ── Main backtest ─────────────────────────────────────────────────────────────

_EVENT_COLS = [
    'day', 'symbol', 'event', 'check_time',
    'scan_price', 'prev_high', 'current_price',
    'pct_from_scan', 'pct_since_last',
    'confirmed', 'ret_15m', 'ret_30m', 'ret_60m',
    'max_gain_pct', 'max_loss_pct',
]


def run_backtest(date_str: Optional[str] = None,
                 n_days: int = 5,
                 interval_min: int = 5,
                 watchlist_path: Optional[str] = None,
                 rolling_days: int = ROLLING_DAYS) -> pd.DataFrame:
    """
    Full backtest run. Returns DataFrame of all events.

    watchlist_path : if given, load symbols from TradingView file and compute
                     prev_high dynamically per day (rolling_days-day high).
                     Otherwise use today's scan_decisions CSV.
    """
    today_str   = _today_str(date_str)
    anchor_date = datetime.strptime(today_str, '%Y%m%d').date()
    trade_days  = _last_n_trading_days(n_days, anchor=anchor_date)

    # ── Watchlist mode vs decisions-CSV mode ─────────────────────────────────
    watchlist_mode = watchlist_path is not None

    if watchlist_mode:
        symbols = _load_tv_watchlist(watchlist_path)
        # ph_map is built per-day after fetching daily bars (see below)
        ph_map_static: dict[str, float] = {}
        source_label = f"watchlist ({watchlist_path}), rolling {rolling_days}-day high"
    else:
        dec_path = _decisions_path(today_str)
        if not dec_path.exists():
            raise FileNotFoundError(f"Decisions CSV not found: {dec_path}")
        dec_df = pd.read_csv(dec_path, dtype=str).drop_duplicates(subset='symbol', keep='last')
        dec_df = dec_df[dec_df['symbol'].notna() & dec_df['prev_high'].notna()]
        dec_df = dec_df[dec_df['prev_high'].str.strip() != '']
        dec_df['prev_high_f'] = pd.to_numeric(dec_df['prev_high'], errors='coerce')
        dec_df = dec_df.dropna(subset=['prev_high_f'])
        dec_df = dec_df[dec_df['prev_high_f'] > 0]
        if dec_df.empty:
            print("No symbols with valid prev_high found in decisions CSV.")
            return pd.DataFrame()
        symbols        = dec_df['symbol'].str.strip().tolist()
        ph_map_static  = dict(zip(dec_df['symbol'].str.strip(), dec_df['prev_high_f']))
        source_label   = f"scan_decisions_{today_str}.csv  (fixed prev_high)"

    print(f"\n{'═'*62}")
    print(f"  FEEDBACK AGENT HISTORICAL BACKTEST")
    print(f"  Source         : {source_label}")
    print(f"  Symbols        : {len(symbols)}")
    print(f"  Trading days   : last {n_days}")
    print(f"  Pass interval  : {interval_min} min")
    print(f"{'═'*62}\n")
    print(f"  Days to simulate: {[str(d) for d in trade_days]}\n")

    fetch_start = trade_days[0]
    fetch_end   = trade_days[-1]

    # ── Fetch daily bars for rolling prev_high (watchlist mode only) ──────────
    rolling_ph: dict[str, dict[date, float]] = {}
    if watchlist_mode:
        # Need extra history before trade_days[0] for the rolling window
        daily_start = fetch_start - timedelta(days=rolling_days * 2)
        print(f"  Fetching daily bars for rolling {rolling_days}-day high …", flush=True)
        daily_bars = _fetch_daily_bars(symbols, daily_start, fetch_end)
        rolling_ph = _build_rolling_prev_high(daily_bars, trade_days, rolling_days)
        available  = sum(1 for sym in symbols if sym in rolling_ph)
        print(f"  Rolling prev_high computed for {available}/{len(symbols)} symbols.\n")

    # ── Fetch 1-min bars in chunks of 50 ─────────────────────────────────────
    CHUNK = 50
    all_bars: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        chunk_bars = _fetch_1min_batch(chunk, fetch_start, fetch_end)
        all_bars.update(chunk_bars)
    print(f"\n  Fetched 1-min data for {len(all_bars)}/{len(symbols)} symbols.\n")

    # ── Simulate ──────────────────────────────────────────────────────────────
    all_events: list[dict] = []

    for sym in symbols:
        bars = all_bars.get(sym)
        if bars is None or bars.empty:
            continue

        for day in trade_days:
            if watchlist_mode:
                ph = rolling_ph.get(sym, {}).get(day)
                if ph is None:
                    continue          # not enough history for this day
            else:
                ph = ph_map_static.get(sym)
                if ph is None:
                    continue

            events = _simulate_day(sym, ph, day, bars, interval_min)
            all_events.extend(events)

    if not all_events:
        print("  No events detected across all days.")
        return pd.DataFrame(columns=_EVENT_COLS)

    events_df = pd.DataFrame(all_events, columns=_EVENT_COLS)

    # ── Persist raw events CSV ────────────────────────────────────────────────
    _SCAN_DIR.mkdir(parents=True, exist_ok=True)
    suffix    = '_watchlist' if watchlist_mode else ''
    out_csv   = _SCAN_DIR / f'feedback_backtest_{today_str}{suffix}.csv'
    events_df.to_csv(out_csv, index=False)
    print(f"  Raw events saved → {out_csv}\n")

    return events_df


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(events_df: pd.DataFrame, today_str: str,
                 watchlist_mode: bool = False) -> str:
    lines: list[str] = []

    def p(s: str = '') -> None:
        lines.append(s)
        print(s)

    p(f"\n{'═'*62}")
    p(f"  SCAN FEEDBACK AGENT — HISTORICAL BACKTEST REPORT")
    p(f"  Generated : {datetime.now(_NY_TZ).strftime('%Y-%m-%d %H:%M ET')}")
    p(f"{'═'*62}")

    if events_df.empty:
        p("  No events to report.")
        return '\n'.join(lines)

    total   = len(events_df)
    by_evt  = events_df['event'].value_counts()
    p(f"\n  Total alert events : {total}")
    for ev, cnt in by_evt.items():
        p(f"    {ev:<12} {cnt:>5}")

    # ── BREAKOUT analysis ─────────────────────────────────────────────────────
    bo = events_df[events_df['event'] == 'BREAKOUT'].copy()
    if not bo.empty:
        for col in ['ret_15m', 'ret_30m', 'ret_60m', 'max_gain_pct', 'max_loss_pct']:
            bo[col] = pd.to_numeric(bo[col], errors='coerce')
        bo['confirmed'] = bo['confirmed'].apply(
            lambda x: True if str(x).lower() == 'true' else (False if str(x).lower() == 'false' else None)
        )

        confirmed = bo['confirmed'].sum()
        total_bo  = len(bo)
        conf_rate = confirmed / total_bo * 100 if total_bo else 0

        p(f"\n  {'─'*58}")
        p(f"  BREAKOUT ALERTS ({total_bo} total)")
        p(f"  {'─'*58}")
        p(f"  Confirmed (held ≥{CONFIRM_BAND}% above level × {CONFIRM_BARS} passes) : "
          f"{int(confirmed)}/{total_bo}  ({conf_rate:.0f}%)")

        # Forward returns stats
        p(f"\n  Forward returns (from breakout price)")
        p(f"  {'Metric':<18} {'Mean':>8} {'Median':>8} {'Win%':>7} {'N':>5}")
        p(f"  {'─'*48}")
        for col, label in [('ret_15m', '+15 min'), ('ret_30m', '+30 min'),
                            ('ret_60m', '+60 min'), ('max_gain_pct', 'Max gain 60m'),
                            ('max_loss_pct', 'Max loss 60m')]:
            s = bo[col].dropna()
            if s.empty:
                continue
            win_pct = (s > 0).sum() / len(s) * 100
            p(f"  {label:<18} {s.mean():>+8.2f}% {s.median():>+8.2f}% {win_pct:>6.0f}% {len(s):>5}")

        # Per-symbol breakdown
        p(f"\n  Per-symbol BREAKOUT summary")
        p(f"  {'Symbol':<8} {'Days':>5} {'Conf':>5} {'AvgRet30m':>10} {'MaxGain':>9} {'MaxLoss':>9}")
        p(f"  {'─'*52}")
        for sym, grp in bo.groupby('symbol'):
            conf_n = int(grp['confirmed'].sum())
            avg30  = grp['ret_30m'].mean()
            maxg   = grp['max_gain_pct'].max()
            maxl   = grp['max_loss_pct'].min()
            days_n = grp['day'].nunique()
            p(f"  {sym:<8} {days_n:>5} {conf_n:>5} "
              f"{avg30:>+9.2f}% {maxg:>+8.2f}% {maxl:>+8.2f}%")

    # ── SURGE analysis ────────────────────────────────────────────────────────
    surge = events_df[events_df['event'] == 'SURGE'].copy()
    if not surge.empty:
        surge['pct_since_last'] = pd.to_numeric(surge['pct_since_last'], errors='coerce')
        p(f"\n  {'─'*58}")
        p(f"  SURGE ALERTS ({len(surge)} total)")
        p(f"  {'─'*58}")
        up   = surge[surge['pct_since_last'] > 0]
        down = surge[surge['pct_since_last'] < 0]
        p(f"  Upward surges  : {len(up)}  (avg {up['pct_since_last'].mean():+.2f}%)")
        p(f"  Downward surges: {len(down)}  (avg {down['pct_since_last'].mean():+.2f}%)")

    # ── FLIP analysis ─────────────────────────────────────────────────────────
    flip = events_df[events_df['event'] == 'FLIP']
    if not flip.empty:
        p(f"\n  {'─'*58}")
        p(f"  FLIP ALERTS ({len(flip)} total)")
        p(f"  {'─'*58}")
        flip_by_sym = flip['symbol'].value_counts().head(10)
        for sym, cnt in flip_by_sym.items():
            p(f"  {sym:<8} {cnt} flip(s)")

    # ── Daily breakdown ───────────────────────────────────────────────────────
    p(f"\n  {'─'*58}")
    p(f"  DAILY SUMMARY")
    p(f"  {'─'*58}")
    p(f"  {'Day':<12} {'Events':>7} {'BREAKOUT':>9} {'SURGE':>7} {'FLIP':>6}")
    p(f"  {'─'*46}")
    for day, grp in events_df.groupby('day'):
        day_bo    = (grp['event'] == 'BREAKOUT').sum()
        day_surge = (grp['event'] == 'SURGE').sum()
        day_flip  = (grp['event'] == 'FLIP').sum()
        p(f"  {day:<12} {len(grp):>7} {day_bo:>9} {day_surge:>7} {day_flip:>6}")

    # ── Verdict ───────────────────────────────────────────────────────────────
    p(f"\n{'═'*62}")
    if not bo.empty:
        conf_rate = (bo['confirmed'].sum() / len(bo) * 100) if len(bo) else 0
        avg_30    = bo['ret_30m'].dropna().mean()
        verdict = (
            "✓ BREAKOUT alerts are RELIABLE"
            if conf_rate >= 50 and avg_30 > 0 else
            "△ BREAKOUT alerts are MIXED"
            if conf_rate >= 30 or avg_30 > 0 else
            "✗ BREAKOUT alerts are UNRELIABLE — review level calculation"
        )
        p(f"  VERDICT: {verdict}")
        p(f"           Confirmation rate {conf_rate:.0f}%,  avg +30m return {avg_30:+.2f}%")
    p(f"{'═'*62}\n")

    # ── Save report text ──────────────────────────────────────────────────────
    suffix      = '_watchlist' if watchlist_mode else ''
    report_path = _SCAN_DIR / f'feedback_backtest_{today_str}{suffix}_report.txt'
    report_path.write_text('\n'.join(lines))
    print(f"  Report saved → {report_path}")

    return '\n'.join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Replay scan_feedback_agent logic on historical 1-min intraday data'
    )
    parser.add_argument('--days',         type=int, default=5,
                        help='Number of past trading days to simulate (default 5)')
    parser.add_argument('--interval',     type=int, default=5,
                        help='Minutes between feedback-agent passes (default 5)')
    parser.add_argument('--date',         type=str, default=None,
                        help='Decisions CSV date YYYYMMDD (default today)')
    parser.add_argument('--watchlist',    type=str, default=None,
                        metavar='FILE',
                        help='TradingView watchlist file; uses rolling prev_high instead of decisions CSV')
    parser.add_argument('--rolling-days', type=int, default=ROLLING_DAYS,
                        help=f'Rolling window for prev_high in watchlist mode (default {ROLLING_DAYS})')
    parser.add_argument('--report',       action='store_true',
                        help='Load existing backtest CSV and just print report')
    args = parser.parse_args()

    today_str      = _today_str(args.date)
    watchlist_mode = args.watchlist is not None

    if args.report:
        suffix       = '_watchlist' if watchlist_mode else ''
        backtest_csv = _SCAN_DIR / f'feedback_backtest_{today_str}{suffix}.csv'
        if not backtest_csv.exists():
            print(f"No existing backtest found at {backtest_csv}")
            return
        events_df = pd.read_csv(backtest_csv, dtype=str)
        print_report(events_df, today_str, watchlist_mode=watchlist_mode)
        return

    events_df = run_backtest(
        date_str=args.date,
        n_days=args.days,
        interval_min=args.interval,
        watchlist_path=args.watchlist,
        rolling_days=args.rolling_days,
    )
    print_report(events_df, today_str, watchlist_mode=watchlist_mode)


if __name__ == '__main__':
    main()
