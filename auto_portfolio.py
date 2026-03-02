"""
Auto Virtual Portfolio
======================
Automatically tracks all V9-C signals (GOLD/PREMIUM + MinerviniScore ≥ 7) from
scanner_output/signals/ CSV files.

Rules
-----
- Position size: 10% of initial capital ($10K per trade)
- Dedup: skip if symbol already in open positions
- Capital guard: skip if cash < cost; show warning
- Auto-close: when current_price <= stop → close at stop price
- Re-entry: a symbol that was closed CAN be re-added from a newer signal file
- File tracking: each signal file is processed only once (via 'processed_files' set)
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

_NY_TZ          = ZoneInfo('America/New_York')
_SIGNALS_DIR    = 'scanner_output/signals'
_PORTFOLIO_PATH = 'scanner_output/portfolio/auto_portfolio.json'

INITIAL_CAPITAL    = 100_000
POSITION_SIZE_PCT  = 0.10      # 10% of capital per trade
MIN_MINERVINI      = 7         # V9-C filter threshold
TRAIL_PCT          = 0.08      # 8% trailing stop from highest close since entry


# ── Persistence ──────────────────────────────────────────────────────────────

def _empty() -> dict:
    return {
        'capital':         INITIAL_CAPITAL,
        'positions':       [],          # list of open position dicts
        'closed':          [],          # list of closed position dicts
        'skipped_cash':    [],          # signals skipped due to insufficient cash
        'processed_files': [],          # filenames already scanned (avoid re-add)
        'last_updated':    None,
    }


def load() -> dict:
    from utils import load_json
    data = load_json(_PORTFOLIO_PATH)
    if data is not None:
        data.setdefault('skipped_cash', [])   # backfill for older saved files
        return data
    return _empty()


def _save(data: dict):
    from utils import save_json
    data['last_updated'] = datetime.now(_NY_TZ).isoformat()
    save_json(data, _PORTFOLIO_PATH)


# ── Helpers ───────────────────────────────────────────────────────────────────

def available_cash(data: dict) -> float:
    invested = sum(p['cost'] for p in data['positions'])
    return data['capital'] - invested


def open_symbols(data: dict) -> set:
    return {p['symbol'] for p in data['positions']}


def _date_from_filename(fname: str) -> str:
    """Extract YYYY-MM-DD from signals_swing_20240115_093500.csv"""
    m = re.search(r'(\d{8})', fname)
    if m:
        d = m.group(1)
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return datetime.now(_NY_TZ).strftime('%Y-%m-%d')


def _mode_from_filename(fname: str) -> str:
    """Extract mode (swing/daytrade/longterm) from filename."""
    for mode in ('daytrade', 'longterm', 'scalping', 'swing'):
        if mode in fname.lower():
            return mode
    return 'swing'


# ── Scan and add new V9-C signals ────────────────────────────────────────────

def scan_and_add(min_date: str | None = None,
                 position_pct: float | None = None) -> dict:
    """
    Scan signal CSV files and add new V9-C signals.

    Args:
        min_date: Optional 'YYYY-MM-DD' string.
            - Without min_date: only process files not yet in processed_files.
            - With min_date: process ALL files on or after min_date, regardless
              of whether they were previously processed. This lets you "re-scan
              from a date" and pick up signals you might have missed.
        position_pct: Position size as a fraction of initial capital (e.g. 0.05
            for 5%, 0.10 for 10%). Defaults to POSITION_SIZE_PCT (10%).
    Returns summary dict with counts.
    """
    import pandas as pd
    from utils import list_files, load_data

    pos_pct   = position_pct if position_pct is not None else POSITION_SIZE_PCT
    data      = load()
    processed = set(data.get('processed_files', []))
    open_syms = open_symbols(data)

    added_syms    = []
    skipped_dup   = []
    skipped_cash  = []
    skipped_no_v9c = 0
    files_scanned  = 0

    # Sort oldest-first so chronological order is respected.
    # list_files() returns newest-first; alphabetical sort gives oldest-first
    # for timestamped filenames and works for both local and S3.
    all_fnames = sorted(list_files(_SIGNALS_DIR, 'signals_*.csv'))
    if not all_fnames:
        return _build_result(added_syms, skipped_dup, skipped_cash, skipped_no_v9c, 0, data)

    for fname in all_fnames:
        date_str = _date_from_filename(fname)

        if min_date:
            # Date-filtered scan: re-process any file on or after min_date,
            # even if it was scanned before. Skip files before min_date entirely.
            if date_str < min_date:
                continue
        else:
            # Normal scan: skip already-processed files
            if fname in processed:
                continue

        files_scanned += 1
        file_mode = _mode_from_filename(fname)

        df = load_data(f"{_SIGNALS_DIR}/{fname}")
        if df is None or df.empty:
            processed.add(fname)
            continue

        # Normalise column names (strip whitespace, handle 'symbol' vs 'Symbol')
        df.columns = [c.strip() for c in df.columns]
        if 'Symbol' not in df.columns and 'symbol' in df.columns:
            df = df.rename(columns={'symbol': 'Symbol'})

        if 'Quality' not in df.columns or 'Symbol' not in df.columns:
            processed.add(fname)
            continue

        # V9-C filter: GOLD or PREMIUM + MinerviniScore >= 7
        mask = df['Quality'].isin(['GOLD', 'PREMIUM'])
        if 'MinerviniScore' in df.columns:
            mask = mask & (pd.to_numeric(df['MinerviniScore'], errors='coerce')
                           .fillna(0) >= MIN_MINERVINI)
        v9c = df[mask]

        if v9c.empty:
            skipped_no_v9c += len(df)
            processed.add(fname)
            continue

        for _, row in v9c.iterrows():
            sym = str(row.get('Symbol', '')).strip().upper()
            if not sym or sym == 'NAN':
                continue

            # Dedup: only block if CURRENTLY open
            if sym in open_syms:
                skipped_dup.append(sym)
                continue

            price = _safe_float(row.get('Price'))
            if not price:
                continue

            stop   = _safe_float(row.get('Stop'))   or round(price * 0.95, 2)
            target = _safe_float(row.get('Target')) or round(price * 1.10, 2)
            quality  = str(row.get('Quality', 'PREMIUM'))
            minervini = int(_safe_float(row.get('MinerviniScore')) or 0)
            # Mode: prefer from signal row, fall back to filename
            mode = str(row.get('Mode', file_mode)).lower().strip() or file_mode

            # Fetch actual entry price on date_added + today's current price
            entry_price, current_price = _fetch_entry_and_current(
                sym, date_str, price
            )

            # Guard: stop must be BELOW entry price (scanner uses CSV signal price
            # for stop calc, but yfinance may return a different actual close).
            if stop >= entry_price:
                stop = round(entry_price * 0.95, 4)

            # Position sizing: pos_pct of initial capital (use entry_price)
            position_value = data['capital'] * pos_pct
            shares = max(1, int(position_value / entry_price))
            cost   = round(shares * entry_price, 2)

            cash = available_cash(data)
            if cost > cash:
                skipped_cash.append(sym)
                data['skipped_cash'].append({
                    'symbol':          sym,
                    'date_added':      date_str,
                    'mode':            mode,
                    'quality':         quality,
                    'minervini_score': minervini,
                    'entry_price':     entry_price,
                    'current_price':   current_price,
                    'stop':            round(stop, 4),
                    'target':          round(target, 4),
                    'shares':          shares,
                    'cost':            cost,
                })
                continue

            data['positions'].append({
                'symbol':          sym,
                'date_added':      date_str,
                'mode':            mode,
                'quality':         quality,
                'minervini_score': minervini,
                'entry_price':     entry_price,
                'stop':            round(stop, 4),
                'target':          round(target, 4),
                'shares':          shares,
                'cost':            cost,
                'current_price':   current_price,
            })
            open_syms.add(sym)
            added_syms.append(sym)

        processed.add(fname)

    data['processed_files'] = sorted(processed)
    _save(data)
    return _build_result(added_syms, skipped_dup, skipped_cash,
                         skipped_no_v9c, files_scanned, data)


def _safe_float(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _fetch_entry_and_current(symbol: str, date_added: str,
                              csv_price: float) -> tuple[float, float]:
    """
    Fetch entry price (close on date_added) and current price (latest close).
    Falls back to csv_price if yfinance fails.
    Returns (entry_price, current_price).
    """
    import yfinance as yf
    import pandas as pd
    try:
        # Use start= only — specifying period= alongside start= is ambiguous
        hist = yf.Ticker(symbol.replace(' ', '-')).history(
            start=date_added, auto_adjust=True
        )
        if hist is None or hist.empty:
            return csv_price, csv_price

        # Strip timezone from index for safe date comparison
        idx = hist.index
        if hasattr(idx, 'tz') and idx.tz is not None:
            idx = idx.tz_localize(None)
        dates = idx.normalize()

        target_dt = pd.Timestamp(date_added)
        on_or_after = hist.loc[dates >= target_dt]
        entry = float(on_or_after['Close'].iloc[0]) if not on_or_after.empty else csv_price

        # Current price: last available close
        current = float(hist['Close'].dropna().iloc[-1])

        return round(entry, 4), round(current, 4)
    except Exception:
        return csv_price, csv_price


def _find_stop_hit_date(symbol: str, stop: float,
                        date_added: str, fallback: str) -> str:
    """
    Scan daily bars from date_added backwards to find the FIRST day whose
    intraday low was at or below `stop`.  Returns that date string or
    `fallback` (today) if history is unavailable.
    """
    import yfinance as yf
    try:
        hist = yf.Ticker(symbol.replace(' ', '-')).history(
            start=date_added, auto_adjust=True
        )
        if hist is None or hist.empty:
            return fallback
        idx = hist.index
        if hasattr(idx, 'tz') and idx.tz is not None:
            idx = idx.tz_localize(None)
        hist.index = idx
        for date, row in hist.iterrows():
            if float(row['Low']) <= stop:
                return date.strftime('%Y-%m-%d')
    except Exception:
        pass
    return fallback


def _build_result(added, dup, cash, no_v9c, files, data):
    return {
        'added':           len(added),
        'added_symbols':   added,
        'skipped_dup':     len(dup),
        'skipped_cash':    len(cash),
        'skipped_cash_syms': cash,
        'skipped_no_v9c':  no_v9c,
        'files_scanned':   files,
        'data':            data,
    }


# ── Refresh prices & auto-close stops ────────────────────────────────────────

def refresh_prices() -> dict:
    """
    Fetch current prices for all open positions.
    Auto-close any position where current_price <= stop.
    Returns {'closed': [symbols], 'data': data}
    """
    import yfinance as yf

    data = load()
    if not data['positions']:
        _save(data)
        return {'closed': [], 'updated': 0, 'data': data}

    symbols = [p['symbol'] for p in data['positions']]
    prices  = {}

    # Individual fetches — reliable for small portfolios
    for sym in symbols:
        try:
            hist = yf.Ticker(sym.replace(' ', '-')).history(period='2d')
            if hist is not None and not hist.empty:
                prices[sym] = float(hist['Close'].dropna().iloc[-1])
        except Exception:
            pass

    now_str    = datetime.now(_NY_TZ).strftime('%Y-%m-%d')
    closed_now = []
    still_open = []

    for p in data['positions']:
        sym     = p['symbol']
        current = prices.get(sym, p.get('current_price', p['entry_price']))
        p['current_price'] = round(current, 4)

        if current <= p['stop']:
            # Stop hit — find the ACTUAL day the low first crossed the stop
            exit_px   = p['stop']
            close_date = _find_stop_hit_date(
                sym, exit_px, p.get('date_added', now_str), now_str
            )
            pnl     = round((exit_px - p['entry_price']) * p['shares'], 2)
            pnl_pct = round((exit_px - p['entry_price']) / p['entry_price'] * 100, 2)
            data['closed'].append({
                **p,
                'date_closed':  close_date,
                'exit_price':   round(exit_px, 4),
                'pnl':          pnl,
                'pnl_pct':      pnl_pct,
                'close_reason': 'stop_hit',
            })
            closed_now.append(sym)
        else:
            still_open.append(p)

    data['positions'] = still_open
    _save(data)
    return {'closed': closed_now, 'updated': len(prices), 'data': data}


# ── Trailing stop simulation ──────────────────────────────────────────────────

def simulate_trailing_stops(trail_pct: float = TRAIL_PCT) -> dict:
    """
    Walk through every trading day from each position's date_added to today.
    Trailing stop = max(initial_stop, highest_close_since_entry * (1 - trail_pct)).
    Close the position on the first day its low touches or crosses the trailing stop.

    Returns {'closed': [symbols], 'checked': int, 'data': data}
    """
    import yfinance as yf
    import pandas as pd

    data = load()
    if not data['positions']:
        _save(data)
        return {'closed': [], 'checked': 0, 'data': data}

    closed_now = []
    still_open = []

    for p in data['positions']:
        sym          = p['symbol']
        date_added   = p['date_added']
        initial_stop = p['stop']
        entry_price  = p['entry_price']

        try:
            # Use start= only (no period=) to get the full history from entry
            hist = yf.Ticker(sym.replace(' ', '-')).history(
                start=date_added, auto_adjust=True
            )
            if hist is None or hist.empty:
                still_open.append(p)
                continue

            # Strip timezone so date string formatting is unambiguous
            idx = hist.index
            if hasattr(idx, 'tz') and idx.tz is not None:
                idx = idx.tz_localize(None)
            hist.index = idx

            # Walk each trading day chronologically
            highest_close = entry_price
            closed_this   = False
            first_bar     = True   # skip entry-day stop check (position just opened)

            for date, row in hist.iterrows():
                close = float(row['Close'])
                low   = float(row['Low'])

                if first_bar:
                    # On the entry day the position was just initiated; update
                    # high-water mark with the day's close, then move on.
                    if close > highest_close:
                        highest_close = close
                    first_bar = False
                    continue

                # Trailing stop is set BEFORE the current bar opens, based on
                # the highest close seen so far (previous day's data).
                trail_stop = max(initial_stop,
                                 round(highest_close * (1.0 - trail_pct), 4))

                # Position closed when intraday low touches the trailing stop
                if low <= trail_stop:
                    exit_px   = trail_stop
                    exit_date = date.strftime('%Y-%m-%d')
                    pnl       = round((exit_px - entry_price) * p['shares'], 2)
                    pnl_pct   = round((exit_px - entry_price) / entry_price * 100, 2)
                    data['closed'].append({
                        **p,
                        'date_closed':    exit_date,
                        'exit_price':     round(exit_px, 4),
                        'pnl':            pnl,
                        'pnl_pct':        pnl_pct,
                        'close_reason':   'trailing_stop',
                        'trail_pct':      trail_pct,
                        'highest_close':  round(highest_close, 4),
                    })
                    closed_now.append(sym)
                    closed_this = True
                    break

                # Update high-water mark AFTER stop check (stop was set before bar opened)
                if close > highest_close:
                    highest_close = close

            if not closed_this:
                # Still open — update current price to latest close
                p['current_price'] = round(float(hist['Close'].dropna().iloc[-1]), 4)
                # Store current trailing stop for UI display
                highest = max(entry_price,
                              float(hist['Close'].dropna().max()))
                p['trail_stop'] = max(initial_stop,
                                      round(highest * (1.0 - trail_pct), 4))
                still_open.append(p)

        except Exception:
            still_open.append(p)

    data['positions'] = still_open
    _save(data)
    return {'closed': closed_now, 'checked': len(data['positions']) + len(closed_now),
            'data': data}


# ── Missed-trade discovery ───────────────────────────────────────────────────

def rebuild_skipped_cash() -> dict:
    """
    Re-scan every signal file (including already-processed ones) to find
    V9-C signals that were NEVER taken (not open, not closed).

    These represent missed opportunities — either due to insufficient cash
    at the time, or signals that arrived while the portfolio was full.

    Rebuilds data['skipped_cash'] from scratch (de-duped by symbol,
    keeping the first occurrence).  Does NOT touch positions/closed/processed_files.

    Returns {'found': count, 'data': data}
    """
    import pandas as pd
    from utils import list_files, load_data

    data      = load()
    taken_syms = ({p['symbol'] for p in data['positions']} |
                  {t['symbol'] for t in data['closed']})

    data['skipped_cash'] = []   # rebuild from scratch
    seen_syms = set()

    all_fnames = sorted(list_files(_SIGNALS_DIR, 'signals_*.csv'))
    if not all_fnames:
        _save(data)
        return {'found': 0, 'data': data}

    for fname in all_fnames:
        date_str  = _date_from_filename(fname)
        file_mode = _mode_from_filename(fname)

        df = load_data(f"{_SIGNALS_DIR}/{fname}")
        if df is None or df.empty:
            continue

        df.columns = [c.strip() for c in df.columns]
        if 'Symbol' not in df.columns and 'symbol' in df.columns:
            df = df.rename(columns={'symbol': 'Symbol'})
        if 'Quality' not in df.columns or 'Symbol' not in df.columns:
            continue

        mask = df['Quality'].isin(['GOLD', 'PREMIUM'])
        if 'MinerviniScore' in df.columns:
            mask = mask & (pd.to_numeric(df['MinerviniScore'], errors='coerce')
                           .fillna(0) >= MIN_MINERVINI)
        v9c = df[mask]

        for _, row in v9c.iterrows():
            sym = str(row.get('Symbol', '')).strip().upper()
            if not sym or sym == 'NAN':
                continue
            if sym in taken_syms:
                continue        # was actually entered into a position
            if sym in seen_syms:
                continue        # already recorded from an earlier file

            price = _safe_float(row.get('Price'))
            if not price:
                continue

            stop     = _safe_float(row.get('Stop'))   or round(price * 0.95, 2)
            target   = _safe_float(row.get('Target')) or round(price * 1.10, 2)
            quality  = str(row.get('Quality', 'PREMIUM'))
            minervini = int(_safe_float(row.get('MinerviniScore')) or 0)
            mode = str(row.get('Mode', file_mode)).lower().strip() or file_mode

            entry_price, current_price = _fetch_entry_and_current(sym, date_str, price)

            position_value = data['capital'] * POSITION_SIZE_PCT
            shares = max(1, int(position_value / entry_price))
            cost   = round(shares * entry_price, 2)

            data['skipped_cash'].append({
                'symbol':          sym,
                'date_added':      date_str,
                'mode':            mode,
                'quality':         quality,
                'minervini_score': minervini,
                'entry_price':     entry_price,
                'current_price':   current_price,
                'stop':            round(stop, 4),
                'target':          round(target, 4),
                'shares':          shares,
                'cost':            cost,
            })
            seen_syms.add(sym)

    _save(data)
    return {'found': len(data['skipped_cash']), 'data': data}


# ── Manual close (UI) ─────────────────────────────────────────────────────────

def close_position(symbol: str, exit_price: float, reason: str = 'manual') -> dict:
    """Close a specific position at given price."""
    data = load()
    now_str    = datetime.now(_NY_TZ).strftime('%Y-%m-%d')
    still_open = []
    closed_rec = None

    for p in data['positions']:
        if p['symbol'].upper() == symbol.upper():
            pnl     = round((exit_price - p['entry_price']) * p['shares'], 2)
            pnl_pct = round((exit_price - p['entry_price']) / p['entry_price'] * 100, 2)
            closed_rec = {
                **p,
                'date_closed':  now_str,
                'exit_price':   round(exit_price, 4),
                'pnl':          pnl,
                'pnl_pct':      pnl_pct,
                'close_reason': reason,
            }
            data['closed'].append(closed_rec)
        else:
            still_open.append(p)

    data['positions'] = still_open
    _save(data)
    return closed_rec or {}


# ── Reset ─────────────────────────────────────────────────────────────────────

def reset() -> dict:
    data = _empty()
    _save(data)
    return data


def recalculate(position_pct: float = POSITION_SIZE_PCT,
                min_date: str | None = None) -> dict:
    """
    Reset the portfolio and rescan all signal files from scratch.

    Args:
        position_pct: Position size fraction (e.g. 0.05 for 5%, 0.10 for 10%).
        min_date: Optional start date 'YYYY-MM-DD'. If set, only process files
                  on or after this date.
    Returns the scan_and_add result dict.
    """
    reset()
    return scan_and_add(min_date=min_date, position_pct=position_pct)


# ── Summary helpers ───────────────────────────────────────────────────────────

def get_summary(data: dict) -> dict:
    cash       = available_cash(data)
    market_val = sum(p.get('current_price', p['entry_price']) * p['shares']
                     for p in data['positions'])
    cost_basis = sum(p['cost'] for p in data['positions'])
    unrealized = market_val - cost_basis
    realized   = sum(t.get('pnl', 0) for t in data['closed'])
    total_val  = cash + market_val
    total_pnl  = total_val - data['capital']

    return {
        'capital':      data['capital'],
        'cash':         round(cash, 2),
        'market_value': round(market_val, 2),
        'cost_basis':   round(cost_basis, 2),
        'unrealized':   round(unrealized, 2),
        'realized':     round(realized, 2),
        'total_value':  round(total_val, 2),
        'total_pnl':    round(total_pnl, 2),
        'open_count':   len(data['positions']),
        'closed_count': len(data['closed']),
        'win_count':    sum(1 for t in data['closed'] if t.get('pnl', 0) > 0),
    }
