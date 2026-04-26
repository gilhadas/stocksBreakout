"""
Auto Virtual Portfolio
======================
Automatically tracks all V9-H signals (GOLD/PREMIUM; breakouts require MinerviniScore ≥ 7) from
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
import json
import re
import time
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

_NY_TZ          = ZoneInfo('America/New_York')
_SIGNALS_DIR    = 'scanner_output/signals'
_PORTFOLIO_PATH = 'scanner_output/portfolio/auto_portfolio.json'


def _portfolio_path_for(user_id: str | None = None) -> str:
    """Return the portfolio file path for the given user.

    Background agents call load()/save() with no user_id and always get the
    flat legacy path so they are unaffected by the multi-user change.
    The API layer passes the logged-in user's UUID.
    """
    import os as _os
    default_id = _os.environ.get('DEFAULT_USER_ID', '')
    if not user_id or user_id == default_id:
        return _PORTFOLIO_PATH
    return f'scanner_output/portfolio/{user_id}/auto_portfolio.json'

INITIAL_CAPITAL    = 100_000
POSITION_SIZE_PCT  = 0.05      # 5% of capital per trade
MIN_MINERVINI      = 7         # V9-H filter: breakout signals only
TRAIL_PCT          = 0.08      # 8% trailing stop from highest close since entry
MAX_ADDS_PER_SCAN  = 10        # Max new positions admitted per trading day (across all files for that day)
MAX_PORTFOLIO_ATR_RISK = 0.12  # Max total portfolio risk as ATR% (12% = aggressive)

# ── Price cache (two-level) ───────────────────────────────────────────────────
# Entry price is immutable historical data → disk-backed, survives server restarts.
# Current price changes daily → in-memory only, 1-hour TTL.

_ENTRY_CACHE_PATH = 'scanner_output/portfolio/entry_price_cache.json'
# { "SYMBOL|YYYY-MM-DD": entry_price_float }
_ENTRY_PRICE_CACHE: dict[str, float] = {}
# { "SYMBOL": (current_price_float, timestamp) }
_CURRENT_PRICE_CACHE: dict[str, tuple[float, float]] = {}
_CURRENT_PRICE_TTL = 3600  # seconds (1 hour)
# { "SYMBOL|YYYY-MM-DD": cumulative_split_factor }  — session-only, no disk backing
_SPLIT_CACHE: dict[str, float] = {}


def _load_entry_price_cache() -> None:
    global _ENTRY_PRICE_CACHE
    try:
        with open(_ENTRY_CACHE_PATH) as f:
            _ENTRY_PRICE_CACHE = json.load(f)
    except Exception:
        _ENTRY_PRICE_CACHE = {}


def _save_entry_price_cache() -> None:
    try:
        import os
        os.makedirs(os.path.dirname(_ENTRY_CACHE_PATH), exist_ok=True)
        with open(_ENTRY_CACHE_PATH, 'w') as f:
            json.dump(_ENTRY_PRICE_CACHE, f)
    except Exception:
        pass


_load_entry_price_cache()


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


def load(user_id: str | None = None) -> dict:
    from utils import load_json
    data = load_json(_portfolio_path_for(user_id))
    if data is not None:
        data.setdefault('skipped_cash', [])   # backfill for older saved files
        return data
    return _empty()


def _save(data: dict, user_id: str | None = None):
    from utils import save_json, _is_cloud, _to_local_abs
    path = _portfolio_path_for(user_id)
    data['last_updated'] = datetime.now(_NY_TZ).isoformat()
    if _is_cloud():
        # On cloud (Streamlit Cloud / S3), skip fcntl — S3 is the source of truth
        # and the local filesystem may be read-only or the directory may not exist.
        save_json(data, path)
    else:
        import fcntl, os
        abs_path = _to_local_abs(path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        lock_path = abs_path + '.lock'
        with open(lock_path, 'w') as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                save_json(data, path)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)


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


# ── Scan and add new V9-H signals ────────────────────────────────────────────

def scan_and_add(min_date: str | None = None,
                 position_pct: float | None = None,
                 user_id: str | None = None,
                 notify: bool = True) -> dict:
    """
    Scan signal CSV files and add new V9-H signals.

    V9-H admission:
      - Breakout signals: GOLD/PREMIUM + MinerviniScore >= 7
      - BOUNCE / CONTINUATION / SMA20_CROSS: GOLD/PREMIUM (no MinerviniScore
        requirement — these signal types don't compute it, and the orchestrator's
        V9-H regime gate already filtered regime-inappropriate signals)

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
    data      = load(user_id=user_id)
    processed = set(data.get('processed_files', []))
    open_syms = open_symbols(data)

    added_syms    = []
    skipped_dup   = []
    skipped_cash  = []
    skipped_risk  = []
    skipped_cap   = []
    skipped_no_v9c = 0
    files_scanned  = 0

    # Sort oldest-first so chronological order is respected.
    # list_files() returns newest-first; alphabetical sort gives oldest-first
    # for timestamped filenames and works for both local and S3.
    all_fnames = sorted(list_files(_SIGNALS_DIR, 'signals_*.csv'))
    if not all_fnames:
        return _build_result(added_syms, skipped_dup, skipped_cash, skipped_no_v9c, 0, data)

    # Group files by trading date so we can pool signals across modes (and
    # across multiple scans of the same day) before applying the per-day cap.
    # Without pooling, MAX_ADDS_PER_SCAN was a *per-file* cap — when many files
    # for the same date arrived (swing + longterm + daytrade backfill), the top
    # 3 from each file got admitted ahead of higher-conviction signals from
    # sibling files.  Pooling lets the date's globally-best signals win.
    files_by_date: dict[str, list[str]] = {}
    for fname in all_fnames:
        date_str = _date_from_filename(fname)
        if min_date:
            if date_str < min_date:
                continue
        else:
            if fname in processed:
                continue
        files_by_date.setdefault(date_str, []).append(fname)

    for date_str in sorted(files_by_date.keys()):
        adds_this_scan = 0                                  # per-day cap
        date_files = files_by_date[date_str]
        # Load + filter every file for this date, then concatenate
        per_file_dfs = []
        for fname in date_files:
            files_scanned += 1
            df_raw = load_data(f"{_SIGNALS_DIR}/{fname}")
            if df_raw is None or df_raw.empty:
                processed.add(fname)
                continue
            df_raw.columns = [c.strip() for c in df_raw.columns]
            if 'Symbol' not in df_raw.columns and 'symbol' in df_raw.columns:
                df_raw = df_raw.rename(columns={'symbol': 'Symbol'})
            if 'Quality' not in df_raw.columns or 'Symbol' not in df_raw.columns:
                processed.add(fname)
                continue
            # V9-H filter: GOLD or PREMIUM (Minervini bypass for non-breakout types)
            mask = df_raw['Quality'].isin(['GOLD', 'PREMIUM'])
            if 'MinerviniScore' in df_raw.columns and 'Type' in df_raw.columns:
                is_breakout = ~df_raw['Type'].isin(
                    ['BOUNCE', 'CONTINUATION', 'SMA20_CROSS', 'TREND_CONFIRM']
                )
                minervini_ok = (pd.to_numeric(df_raw['MinerviniScore'], errors='coerce')
                                .fillna(0) >= MIN_MINERVINI)
                mask = mask & (minervini_ok | ~is_breakout)
            elif 'MinerviniScore' in df_raw.columns:
                mask = mask & (pd.to_numeric(df_raw['MinerviniScore'], errors='coerce')
                               .fillna(0) >= MIN_MINERVINI)
            df_filtered = df_raw[mask].copy()
            if df_filtered.empty:
                skipped_no_v9c += len(df_raw)
                processed.add(fname)
                continue
            df_filtered['_source_file'] = fname
            df_filtered['_file_mode']   = _mode_from_filename(fname)
            per_file_dfs.append(df_filtered)
            processed.add(fname)

        if not per_file_dfs:
            continue
        v9h = pd.concat(per_file_dfs, ignore_index=True)

        # Pooled priority sort across all files for this date:
        # Quality (GOLD first) → WinProb desc → R:R desc → Dist desc (momentum tiebreak).
        # Without the Dist tiebreak, ties at PREMIUM+R:R=2.5 fell to alphabetical sort,
        # so AA/ADM/AMX won 4 longterm slots over INTC even though INTC had a stronger
        # 20-day move.  Dist is computed by every detector (% move from base/streak/20-day).
        sort_cols, sort_asc = [], []
        if 'Quality' in v9h.columns:
            v9h['_q_rank'] = v9h['Quality'].map({'GOLD': 0, 'PREMIUM': 1, 'HIGH': 2}).fillna(3)
            sort_cols.append('_q_rank'); sort_asc.append(True)
        if 'WinProb' in v9h.columns:
            v9h['_wp'] = pd.to_numeric(v9h['WinProb'], errors='coerce').fillna(0)
            sort_cols.append('_wp'); sort_asc.append(False)
        if 'R:R' in v9h.columns:
            v9h['_rr'] = pd.to_numeric(v9h['R:R'], errors='coerce').fillna(0)
            sort_cols.append('_rr'); sort_asc.append(False)
        if 'Dist' in v9h.columns:
            v9h['_dist'] = pd.to_numeric(v9h['Dist'], errors='coerce').fillna(-1e9)
            sort_cols.append('_dist'); sort_asc.append(False)
        if sort_cols:
            v9h = v9h.sort_values(sort_cols, ascending=sort_asc).drop(
                columns=[c for c in ['_q_rank', '_wp', '_rr', '_dist'] if c in v9h.columns]
            )

        # Dedup within the date — if the same symbol appears in both swing and
        # longterm pools, prefer the higher-priority row (already sorted).
        v9h = v9h.drop_duplicates(subset=['Symbol'], keep='first')

        for _, row in v9h.iterrows():
            file_mode = row.get('_file_mode', 'swing')
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
            _ms = _safe_float(row.get('MinerviniScore'))
            minervini = int(_ms) if (_ms is not None and _ms == _ms) else 0
            # Mode: prefer from signal row, fall back to filename
            mode = str(row.get('Mode', file_mode)).lower().strip() or file_mode

            # Daily deployment cap — prevent all capital spent in one scan.
            # Signals are now sorted by priority above, so we skip the lowest-ranked ones.
            if adds_this_scan >= MAX_ADDS_PER_SCAN:
                entry_price, current_price = _fetch_entry_and_current(sym, date_str, price)
                if price and entry_price:
                    _sf = _detect_split_factor(sym, date_str)
                    if _sf != 1.0:
                        stop   = round(stop   / _sf, 4)
                        target = round(target / _sf, 4)
                        price  = round(price  / _sf, 4)
                if stop >= entry_price or (entry_price > 0 and (entry_price - stop) / entry_price > 0.30):
                    stop = round(entry_price * 0.95, 4)
                # Skip price-scale-mismatched signals even from the cap path
                if (target <= entry_price or
                        (price and entry_price and
                         abs(entry_price - price) / max(price, entry_price) > 0.50)):
                    continue
                skipped_cap.append(sym)
                data['skipped_cash'].append(_build_skipped_entry(
                    row, sym, date_str, mode, quality, minervini,
                    entry_price, current_price, stop, target, skip_reason='cap',
                ))
                continue

            # Fetch actual entry price on date_added + today's current price
            entry_price, current_price = _fetch_entry_and_current(
                sym, date_str, price
            )

            # Split adjustment: if a stock split occurred since the signal date,
            # yfinance auto_adjust=True will have already divided the historical close
            # by the split factor.  We rescale csv stop/target to match the new price
            # scale so the signal remains valid.
            # Example: 2:1 split after signal at $100 → entry_price = $50 (adjusted);
            # stop $95 → $47.50, target $120 → $60.
            if price and entry_price:
                split_factor = _detect_split_factor(sym, date_str)
                if split_factor != 1.0:
                    import logging
                    logging.getLogger(__name__).info(
                        f"SPLIT {sym}: {split_factor}x since {date_str} — "
                        f"rescaling stop {stop}→{round(stop/split_factor,4)}, "
                        f"target {target}→{round(target/split_factor,4)}"
                    )
                    stop   = round(stop   / split_factor, 4)
                    target = round(target / split_factor, 4)
                    price  = round(price  / split_factor, 4)

            # Guard: stop must be below entry and not based on wrong price scale
            # (catches stale/split-adjusted data returning wrong close in scanner)
            if stop >= entry_price or (entry_price > 0 and (entry_price - stop) / entry_price > 0.30):
                stop = round(entry_price * 0.95, 4)

            # Guard: skip if target is below entry — signal is from wrong timeframe or
            # price scale (e.g. BOUNCE detected on a historical weekly bar at $66 when
            # the real daily price is $149; TP of $75 ends up below entry).
            if target <= entry_price:
                import logging
                logging.getLogger(__name__).warning(
                    f"SKIP {sym}: target {target} <= entry {entry_price} "
                    f"(csv_price={price}) — price scale mismatch after split adjust"
                )
                skipped_risk.append(sym)
                continue

            # Guard: csv_price vs real entry divergence >50% after split adjustment
            # → signal is from a completely different price era (stale historical bar).
            if price and entry_price and abs(entry_price - price) / max(price, entry_price) > 0.50:
                import logging
                logging.getLogger(__name__).warning(
                    f"SKIP {sym}: csv_price={price} vs entry_price={entry_price} "
                    f"divergence >50% after split adjust — signal skipped"
                )
                skipped_risk.append(sym)
                continue

            # ── Portfolio balance guards (includes ATR checks) ────────────
            balance_result = _check_portfolio_balance(data, sym, mode, pos_pct)
            if balance_result['blocked']:
                import logging
                logging.getLogger(__name__).info(
                    f"BALANCE: skipped {sym} — {balance_result['reason']}"
                )
                skipped_risk.append(sym)
                continue
            balance_sizing_mult = balance_result.get('sizing_mult', 1.0)

            # Position sizing: pos_pct × quality_mult × ATR adjustment × event mult × balance
            from config import QUALITY_SIZING, ATR_SIZING
            quality_mult = QUALITY_SIZING.get(quality, 1.0)
            atr_adj = _compute_atr_adjustment(sym)
            event_mult = _safe_float(row.get('Event_Sizing_Mult')) or 1.0
            position_value = data['capital'] * pos_pct * quality_mult * atr_adj * event_mult * balance_sizing_mult

            # Hard cap: no position > max_single_position_pct of capital
            hard_cap = data['capital'] * ATR_SIZING.get('max_single_position_pct', 0.20)
            position_value = min(position_value, hard_cap)

            shares = max(1, int(position_value / entry_price))
            cost   = round(shares * entry_price, 2)

            cash = available_cash(data)
            if cost > cash:
                skipped_cash.append(sym)
                data['skipped_cash'].append(_build_skipped_entry(
                    row, sym, date_str, mode, quality, minervini,
                    entry_price, current_price, stop, target,
                    shares=shares, cost=cost, skip_reason='cash',
                ))
                continue

            # Resolve sector for diversity tracking
            try:
                from sentiment import get_sector_for_ticker
                _sector = get_sector_for_ticker(sym)
            except Exception:
                _sector = ''

            pos_dict = {
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
                'sector':          _sector,
                'type':            str(row.get('Type', '')),
            }
            data['positions'].append(pos_dict)
            open_syms.add(sym)
            added_syms.append(sym)
            adds_this_scan += 1

            # IB execution hook — place bracket order if enabled
            try:
                from ib_executor import IB_EXEC_CONFIG
                if IB_EXEC_CONFIG.get('enabled'):
                    _queue_ib_order(pos_dict)
            except Exception:
                pass

        processed.add(fname)

    data['processed_files'] = sorted(processed)

    # Sort skipped list: best priority first so the UI surfaces top missed trades
    data['skipped_cash'].sort(key=lambda e: e.get('priority_score', 0), reverse=True)

    _save(data, user_id=user_id)

    # ── Notify on newly added positions ──────────────────────────────────────
    if added_syms and notify:
        try:
            from notifier import Notifier
            _notifier = Notifier()
            _signals = [
                {
                    'Symbol':  p['symbol'],
                    'Price':   p['entry_price'],
                    'Stop':    p['stop'],
                    'Target':  p['target'],
                    'R:R':     round((p['target'] - p['entry_price']) / max(p['entry_price'] - p['stop'], 1e-6), 2),
                    'Vol':     '',
                    'Quality': p['quality'],
                    'Mode':    p['mode'],
                    'Sector':  p.get('sector', ''),
                    'Type':    'AUTO_PORTFOLIO',
                    'Shares':  p['shares'],
                    'Cost':    p['cost'],
                }
                for p in data['positions']
                if p['symbol'] in added_syms
            ]
            # Write a temp CSV so the email has an attachment
            import tempfile, csv as _csv, os as _os
            _tmp = tempfile.NamedTemporaryFile(
                mode='w', suffix='.csv', prefix='auto_portfolio_',
                delete=False, newline=''
            )
            _csv_path = _tmp.name
            _writer = _csv.DictWriter(_tmp, fieldnames=list(_signals[0].keys()))
            _writer.writeheader()
            _writer.writerows(_signals)
            _tmp.close()

            _notifier.send_all(
                subject=f"📋 Auto Portfolio: {len(added_syms)} position(s) added — {', '.join(added_syms)}",
                message=(
                    f"{len(added_syms)} new position(s) added to auto portfolio.\n\n"
                    + "\n".join(
                        f"• {s['Symbol']} [{s['Mode'].upper()}] @ ${s['Price']:.2f} | "
                        f"SL: ${s['Stop']:.2f} | TP: ${s['Target']:.2f} | "
                        f"R:R: {s['R:R']} | {s['Shares']} shares"
                        for s in _signals
                    )
                ),
                signals=_signals,
                csv_path=_csv_path,
                notification_type='signals',
            )
            _os.unlink(_csv_path)
        except Exception as _e:
            import logging as _log
            _log.getLogger(__name__).warning(f"Auto portfolio notification failed: {_e}")

    # Log new guard stats
    import logging as _log
    _logger = _log.getLogger(__name__)
    if skipped_cap:
        _logger.info(f"DAILY CAP: skipped {len(skipped_cap)} signals (max {MAX_ADDS_PER_SCAN}/scan): {', '.join(skipped_cap)}")
    if skipped_risk:
        _logger.info(f"ATR RISK: skipped {len(skipped_risk)} high-risk signals: {', '.join(skipped_risk)}")

    # Advisory health check — trigger when signals skipped due to low cash
    if skipped_cash:
        try:
            from position_health import run_health_check
            run_health_check(data)
        except Exception as _e:
            _logger.warning(f"Health check failed: {_e}")

    return _build_result(added_syms, skipped_dup, skipped_cash,
                         skipped_no_v9c, files_scanned, data)


def _queue_ib_order(pos: dict):
    """Queue an IB bracket order for a new position. Non-blocking."""
    import logging as _log
    _logger = _log.getLogger(__name__)
    try:
        from ib_executor import IB_EXEC_CONFIG, _log_order
        signal = {
            'symbol':      pos['symbol'],
            'entry_price': pos['entry_price'],
            'stop':        pos['stop'],
            'target':      pos['target'],
            'shares':      pos['shares'],
            'quality':     pos['quality'],
            'mode':        pos['mode'],
        }
        # Log the pending order — actual IB execution happens in scan_feedback_agent
        # or via CLI when IB connection is available
        _log_order({
            **signal,
            'action': 'BUY',
            'status': 'QUEUED',
            'live': not IB_EXEC_CONFIG.get('paper_mode', True),
        })
        _logger.info(f"IB order queued: BUY {pos['shares']} {pos['symbol']} @ ${pos['entry_price']:.2f}")
    except Exception as e:
        _logger.warning(f"IB queue failed for {pos['symbol']}: {e}")


def _compute_atr_pct(symbol: str) -> float | None:
    """Return ATR as a fraction of price for a symbol, or None on failure."""
    try:
        from yfinance_adapter import YFinanceAdapter
        from indicators import calculate_atr
        from datetime import datetime, timedelta

        adapter = YFinanceAdapter(use_disk_cache=True)
        end = datetime.now().strftime('%Y-%m-%d')
        start = (datetime.now() - timedelta(days=45)).strftime('%Y-%m-%d')
        df = adapter.get_historical_data(symbol, '1 day',
                                         start_date=start, end_date=end)
        if df is None or len(df) < 15:
            return None
        atr_series = calculate_atr(df, period=14)
        atr_val = float(atr_series.iloc[-1])
        close_val = float(df['close'].iloc[-1])
        if close_val > 0 and atr_val > 0:
            return atr_val / close_val
    except Exception:
        pass
    return None


def _compute_atr_adjustment(symbol: str) -> float:
    """
    Compute ATR-based position size adjustment.
    High ATR% → smaller position (returns < 1.0).
    Low ATR% → capped at 1.0 (no oversizing).
    """
    from config import ATR_SIZING
    if not ATR_SIZING.get('enabled'):
        return 1.0
    atr_pct = _compute_atr_pct(symbol)
    if atr_pct is None:
        return 1.0
    ref = ATR_SIZING['reference_atr_pct']
    raw = ref / atr_pct
    return max(ATR_SIZING['min_adjustment'],
               min(ATR_SIZING['max_adjustment'], raw))


def _portfolio_atr_risk(data: dict) -> float:
    """
    Compute total portfolio-weighted ATR risk.

    Sum of (position_value / capital) × stock_atr_pct for all open positions.
    A value of 0.10 means the portfolio's total weighted daily risk is ~10%.
    """
    capital = data.get('capital', INITIAL_CAPITAL)
    if capital <= 0:
        return 0.0
    positions = data.get('positions', [])
    if not positions:
        return 0.0

    total_risk = 0.0
    for pos in positions:
        atr_pct = _compute_atr_pct(pos['symbol'])
        if atr_pct is None or atr_pct <= 0:
            continue
        pos_value = pos.get('shares', 0) * pos.get('entry_price', 0)
        weight = pos_value / capital
        total_risk += weight * atr_pct

    return total_risk


def _check_portfolio_balance(data: dict, new_symbol: str,
                             new_mode: str,
                             pos_pct: float = POSITION_SIZE_PCT) -> dict:
    """
    Enforce portfolio balance rules before adding a new position.

    Returns dict:
        blocked: bool      — True if position should be rejected
        reason: str        — human-readable reason
        sizing_mult: float — multiplier to shrink position (1.0 = full, <1 = reduced)

    Rules enforced:
      1. Sector concentration: max 3 positions per sector (from config)
      2. Mode concentration: max 5 positions per mode
      3. Regime cash reserve: keep 30%/40% cash in bearish/red markets
      4. Loss concentration: if ≥50% of positions underwater, half-size new entries
      5. Sector drawdown guard: block if same-sector + 2+ losers
      6. Portfolio ATR risk cap: total weighted ATR% can't exceed MAX_PORTFOLIO_ATR_RISK
      7. ATR outlier rejection: block stocks >3× portfolio avg ATR%
      8. ATR-aware sizing: scale down high-ATR adds even when below cap
    """
    from config import CASH_MANAGEMENT

    positions = data.get('positions', [])
    capital = data.get('capital', INITIAL_CAPITAL)
    result = {'blocked': False, 'reason': '', 'sizing_mult': 1.0}

    max_per_sector = CASH_MANAGEMENT.get('max_per_sector', 3)
    max_per_mode = CASH_MANAGEMENT.get('max_per_mode', 5)

    # ── 1. Sector concentration ──────────────────────────────────────────
    try:
        from sentiment import get_sector_for_ticker
        new_sector = get_sector_for_ticker(new_symbol)
    except Exception:
        new_sector = ''

    if new_sector:
        sector_count = sum(
            1 for p in positions
            if p.get('sector', '') == new_sector
        )
        if sector_count >= max_per_sector:
            result['blocked'] = True
            result['reason'] = (
                f"sector '{new_sector}' already has {sector_count} positions "
                f"(max {max_per_sector})"
            )
            return result

    # ── 2. Mode concentration ────────────────────────────────────────────
    mode_count = sum(1 for p in positions if p.get('mode', '') == new_mode)
    if mode_count >= max_per_mode:
        result['blocked'] = True
        result['reason'] = (
            f"mode '{new_mode}' already has {mode_count} positions "
            f"(max {max_per_mode})"
        )
        return result

    # ── 3. Regime-based cash reserve ─────────────────────────────────────
    try:
        from yfinance_adapter import YFinanceAdapter
        from datetime import datetime, timedelta
        _adapter = YFinanceAdapter(use_disk_cache=True)
        _end = datetime.now().strftime('%Y-%m-%d')
        _start = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        spy_df = _adapter.get_historical_data('SPY', '1 day',
                                              start_date=_start, end_date=_end)
        if spy_df is not None and len(spy_df) >= 15:
            spy_ret_15d = (
                float(spy_df['close'].iloc[-1]) /
                float(spy_df['close'].iloc[-15]) - 1
            )
            if spy_ret_15d <= -0.015:
                min_cash_pct = 0.40
                regime_label = 'RED_MARKET'
            elif spy_ret_15d <= -0.005:
                min_cash_pct = 0.30
                regime_label = 'BEARISH'
            else:
                min_cash_pct = 0.0
                regime_label = None

            if min_cash_pct > 0:
                cash = available_cash(data)
                cash_pct = cash / capital if capital > 0 else 0
                if cash_pct < min_cash_pct:
                    result['blocked'] = True
                    result['reason'] = (
                        f"{regime_label} regime — cash {cash_pct:.0%} < "
                        f"required reserve {min_cash_pct:.0%}"
                    )
                    return result
    except Exception:
        pass

    # ── 4. Loss concentration ────────────────────────────────────────────
    if len(positions) >= 4:
        underwater = sum(
            1 for p in positions
            if p.get('current_price', p.get('entry_price', 0)) < p.get('entry_price', 0)
        )
        underwater_pct = underwater / len(positions)
        if underwater_pct >= 0.75:
            result['sizing_mult'] = 0.25
        elif underwater_pct >= 0.50:
            result['sizing_mult'] = 0.50

    # ── 5. Sector drawdown guard ─────────────────────────────────────────
    if new_sector:
        sector_positions = [p for p in positions if p.get('sector') == new_sector]
        if sector_positions:
            sector_losses = sum(
                1 for p in sector_positions
                if p.get('current_price', p.get('entry_price', 0)) < p.get('entry_price', 0)
            )
            if sector_losses >= 2:
                result['blocked'] = True
                result['reason'] = (
                    f"sector '{new_sector}' has {sector_losses} underwater positions"
                )
                return result
            elif sector_losses == 1:
                result['sizing_mult'] = min(result['sizing_mult'], 0.50)

    # ── 6. Portfolio ATR risk cap ────────────────────────────────────────
    # Total portfolio-weighted ATR% must not exceed MAX_PORTFOLIO_ATR_RISK.
    sig_atr_pct = _compute_atr_pct(new_symbol)
    if sig_atr_pct and capital > 0:
        existing_risk = _portfolio_atr_risk(data)
        est_contribution = pos_pct * result['sizing_mult'] * sig_atr_pct
        projected_risk = existing_risk + est_contribution

        if projected_risk > MAX_PORTFOLIO_ATR_RISK:
            result['blocked'] = True
            result['reason'] = (
                f"portfolio ATR risk would be {projected_risk:.1%} "
                f"(cap {MAX_PORTFOLIO_ATR_RISK:.0%}), "
                f"stock ATR% = {sig_atr_pct:.1%}, "
                f"existing = {existing_risk:.1%}"
            )
            return result

        # ── 7+8. ATR-aware sizing ────────────────────────────────────────
        # Scale position size inversely with volatility relative to portfolio.
        # High-ATR stocks are allowed but get smaller positions so they
        # contribute equal risk to the portfolio.
        #   >4× avg → 20% size    (e.g. CIFR 10% ATR vs 2% avg)
        #   >3× avg → 25% size
        #   >2× avg → 50% size
        #   >1.5× avg → 75% size
        if positions:
            portfolio_avg_atr = _portfolio_avg_atr(data)
            if portfolio_avg_atr and portfolio_avg_atr > 0:
                atr_ratio = sig_atr_pct / portfolio_avg_atr
                if atr_ratio > 4.0:
                    result['sizing_mult'] = min(result['sizing_mult'], 0.20)
                elif atr_ratio > 3.0:
                    result['sizing_mult'] = min(result['sizing_mult'], 0.25)
                elif atr_ratio > 2.0:
                    result['sizing_mult'] = min(result['sizing_mult'], 0.50)
                elif atr_ratio > 1.5:
                    result['sizing_mult'] = min(result['sizing_mult'], 0.75)

    return result


def _portfolio_avg_atr(data: dict) -> float | None:
    """
    Average ATR% across all open positions.

    Used to detect outliers — a stock 3× the portfolio average would
    disproportionately dominate the risk budget.
    """
    positions = data.get('positions', [])
    if not positions:
        return None

    values = []
    for p in positions:
        atr_pct = _compute_atr_pct(p['symbol'])
        if atr_pct and atr_pct > 0:
            values.append(atr_pct)

    if not values:
        return None
    return sum(values) / len(values)


def _compute_priority_score(quality: str, win_prob: float, rr: float, vol: float) -> float:
    """Composite score to rank skipped signals — higher = better predicted outcome.

    When WinProb is available (BREAKOUT path): WinProb × quality_mult × rr_bonus × vol_bonus
    When WinProb is 0 (BOUNCE/SMA20_CROSS path): falls back to quality_base × rr_bonus × vol_bonus
      quality_base : GOLD=75, PREMIUM=50, HIGH=35  (empirical win-rate proxies)
      rr_bonus     : min(R:R / 2.0, 1.5)  — caps at 3:1
      vol_bonus    : min(Vol / 1.5, 1.3)  — rewards strong volume but caps
    """
    quality_base = {'GOLD': 75.0, 'PREMIUM': 50.0, 'HIGH': 35.0}.get(quality, 30.0)
    base         = win_prob if win_prob > 0 else quality_base
    rr_capped    = min(rr, 5.0)   # cap at 5:1 — avoids inflated S/R targets
    rr_bonus     = min(rr_capped / 2.0, 1.5) if rr_capped > 0 else 0.5
    vol_bonus    = min(vol / 1.5, 1.3) if vol > 0 else 1.0
    return round(base * rr_bonus * vol_bonus, 1)


def _build_skipped_entry(
    row,
    sym: str,
    date_str: str,
    mode: str,
    quality: str,
    minervini: int,
    entry_price: float,
    current_price: float,
    stop: float,
    target: float,
    shares: int = 0,
    cost: float = 0.0,
    skip_reason: str = 'cash',
) -> dict:
    """Build a skipped-signal record with full predictive metadata.

    Captures Vol, R:R, Type, WinProb, WinGrade from the signal row so the
    Streamlit UI can rank missed opportunities by predicted outcome quality.
    missed_pnl_pct shows what the trade would have returned by now.
    """
    vol      = _safe_float(row.get('Vol'))       if row is not None else 0.0
    rr       = _safe_float(row.get('R:R'))       if row is not None else 0.0
    win_prob = _safe_float(row.get('WinProb'))   if row is not None else 0.0
    win_grade = str(row.get('WinGrade', ''))     if row is not None else ''
    sig_type  = str(row.get('Type', ''))         if row is not None else ''
    sector    = str(row.get('Sector', ''))       if row is not None else ''
    rsi       = _safe_float(row.get('RSI'))      if row is not None else 0.0

    missed_pnl_pct = round((current_price / entry_price - 1) * 100, 2) if entry_price > 0 else 0.0
    priority       = _compute_priority_score(quality, win_prob, rr, vol)

    if shares == 0 and entry_price > 0:
        from config import ATR_SIZING
        pos_value = INITIAL_CAPITAL * POSITION_SIZE_PCT
        shares    = max(1, int(pos_value / entry_price))
        cost      = round(shares * entry_price, 2)

    return {
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
        'vol':             round(vol, 2),
        'rr':              round(rr, 2),
        'win_prob':        round(win_prob, 1),
        'win_grade':       win_grade,
        'type':            sig_type,
        'sector':          sector,
        'rsi':             round(rsi, 1) if rsi else '',
        'missed_pnl_pct':  missed_pnl_pct,
        'priority_score':  priority,
        'skip_reason':     skip_reason,
    }


def _safe_float(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _detect_split_factor(symbol: str, since_date: str) -> float:
    """
    Return the cumulative forward-split ratio for symbol since since_date.
    1.0 = no split; 2.0 = 2:1 split (share count doubled, price halved).

    yfinance auto_adjust=True backward-adjusts historical closes by the split
    factor, so a pre-split csv_price and the adjusted entry_price will differ
    by exactly this ratio.  Dividing csv stop/target by the factor realigns
    them with the current (post-split) price scale.
    """
    cache_key = f"{symbol}|{since_date}"
    if cache_key in _SPLIT_CACHE:
        return _SPLIT_CACHE[cache_key]
    try:
        import yfinance as yf
        import pandas as pd
        splits = yf.Ticker(symbol.replace(' ', '-')).splits
        if splits is None or splits.empty:
            _SPLIT_CACHE[cache_key] = 1.0
            return 1.0
        idx = splits.index
        if hasattr(idx, 'tz') and idx.tz is not None:
            idx = idx.tz_localize(None)
        relevant = splits[idx >= pd.Timestamp(since_date)]
        factor = float(relevant.prod()) if not relevant.empty else 1.0
        _SPLIT_CACHE[cache_key] = factor
        return factor
    except Exception:
        _SPLIT_CACHE[cache_key] = 1.0
        return 1.0


def _fetch_entry_and_current(symbol: str, date_added: str,
                              csv_price: float) -> tuple[float, float]:
    """
    Fetch entry price (close on date_added) and current price (latest close).
    Falls back to csv_price if yfinance fails.

    Cache strategy:
    - entry_price: disk-backed (immutable historical data, survives restarts)
    - current_price: in-memory with 1-hour TTL (changes daily)
    """
    import yfinance as yf
    import pandas as pd

    entry_key = f"{symbol}|{date_added}"
    entry_cached = _ENTRY_PRICE_CACHE.get(entry_key)

    now = time.time()
    current_record = _CURRENT_PRICE_CACHE.get(symbol)
    current_cached = current_record[0] if (current_record and now - current_record[1] < _CURRENT_PRICE_TTL) else None

    # Both cached — no network call needed
    if entry_cached is not None and current_cached is not None:
        return entry_cached, current_cached

    try:
        if entry_cached is None:
            # Full history fetch: need close on date_added + latest close
            hist = yf.Ticker(symbol.replace(' ', '-')).history(
                start=date_added, auto_adjust=True
            )
            if hist is None or hist.empty:
                return csv_price, csv_price

            idx = hist.index
            if hasattr(idx, 'tz') and idx.tz is not None:
                idx = idx.tz_localize(None)
            dates = idx.normalize()
            target_dt = pd.Timestamp(date_added)
            on_or_after = hist.loc[dates >= target_dt]
            entry_cached = round(float(on_or_after['Close'].iloc[0]) if not on_or_after.empty else csv_price, 4)
            current_cached = round(float(hist['Close'].dropna().iloc[-1]), 4)

            _ENTRY_PRICE_CACHE[entry_key] = entry_cached
            _CURRENT_PRICE_CACHE[symbol] = (current_cached, now)
        else:
            # Entry cached; only need a fresh current price (tiny 2-day fetch)
            hist = yf.Ticker(symbol.replace(' ', '-')).history(period='2d', auto_adjust=True)
            current_cached = round(float(hist['Close'].dropna().iloc[-1]), 4) if (hist is not None and not hist.empty) else entry_cached
            _CURRENT_PRICE_CACHE[symbol] = (current_cached, now)

        return entry_cached, current_cached
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

def refresh_prices(user_id: str | None = None) -> dict:
    """
    Fetch current prices for all open positions.
    Auto-close any position where current_price <= stop.
    Returns {'closed': [symbols], 'data': data}
    """
    import yfinance as yf

    data = load(user_id=user_id)
    if not data['positions']:
        _save(data, user_id=user_id)
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
    # Realized P&L flows back into capital
    if closed_now:
        for t in data['closed'][-len(closed_now):]:
            data['capital'] += t['pnl']
    _save(data, user_id=user_id)
    return {'closed': closed_now, 'updated': len(prices), 'data': data}


# ── Trailing stop simulation ──────────────────────────────────────────────────

def _compute_atr_series(hist: 'pd.DataFrame', period: int = 14) -> 'pd.Series':
    """Compute Wilder's ATR from yfinance history (capitalized columns).

    Uses EWM (exponential smoothing) — Wilder's original definition —
    which is more responsive than a simple rolling mean.
    """
    import pandas as pd
    tr = pd.concat([
        hist['High'] - hist['Low'],
        (hist['High'] - hist['Close'].shift(1)).abs(),
        (hist['Low']  - hist['Close'].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def simulate_trailing_stops(trail_pct: float = TRAIL_PCT,
                             atr_mult: float = 0.0,
                             atr_period: int = 14,
                             user_id: str | None = None) -> dict:
    """
    Walk through every trading day from each position's date_added to today.

    Two trailing-stop modes (mutually exclusive):

    PCT mode (atr_mult == 0):
        trail_stop = max(initial_stop, highest_close * (1 - trail_pct))

    ATR mode (atr_mult > 0):
        trail_stop = max(initial_stop, highest_close - atr_mult * ATR14)
        ATR is Wilder's EWM-smoothed ATR on daily bars.
        trail_pct is ignored in this mode.

    Close the position on the first day its low touches the trailing stop.
    Returns {'closed': [symbols], 'checked': int, 'data': data}
    """
    import yfinance as yf
    import pandas as pd

    data = load(user_id=user_id)
    if not data['positions']:
        _save(data, user_id=user_id)
        return {'closed': [], 'checked': 0, 'data': data}

    use_atr    = atr_mult > 0
    closed_now = []
    still_open = []

    for p in data['positions']:
        sym          = p['symbol']
        date_added   = p['date_added']
        initial_stop = p['stop']
        entry_price  = p['entry_price']

        try:
            # Fetch extra history before entry for ATR warm-up (45 calendar days)
            if use_atr:
                warmup_start = (
                    pd.Timestamp(date_added) - pd.Timedelta(days=45)
                ).strftime('%Y-%m-%d')
            else:
                warmup_start = date_added

            hist = yf.Ticker(sym.replace(' ', '-')).history(
                start=warmup_start,
                auto_adjust=True,
            )
            if hist is None or hist.empty:
                still_open.append(p)
                continue

            # Strip timezone
            idx = hist.index
            if hasattr(idx, 'tz') and idx.tz is not None:
                idx = idx.tz_localize(None)
            hist.index = idx

            # Pre-compute ATR series for the full history
            atr_series = _compute_atr_series(hist, atr_period) if use_atr else None

            # Trim to entry date for the walk-forward loop
            entry_dt = pd.Timestamp(date_added)
            hist_from_entry = hist[hist.index >= entry_dt]
            if hist_from_entry.empty:
                still_open.append(p)
                continue

            # Walk each trading day chronologically
            highest_close = entry_price
            closed_this   = False
            first_bar     = True

            for date, row in hist_from_entry.iterrows():
                close = float(row['Close'])
                low   = float(row['Low'])

                if first_bar:
                    if close > highest_close:
                        highest_close = close
                    first_bar = False
                    continue

                # Compute trailing stop level
                if use_atr:
                    atr_val = float(atr_series.get(date, atr_series.dropna().iloc[-1]))
                    trail_stop = max(initial_stop,
                                     round(highest_close - atr_mult * atr_val, 4))
                else:
                    trail_stop = max(initial_stop,
                                     round(highest_close * (1.0 - trail_pct), 4))

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
                        'trail_pct':      trail_pct if not use_atr else None,
                        'atr_mult':       atr_mult  if use_atr else None,
                        'highest_close':  round(highest_close, 4),
                    })
                    closed_now.append(sym)
                    closed_this = True
                    break

                if close > highest_close:
                    highest_close = close

            if not closed_this:
                last_close = float(hist_from_entry['Close'].dropna().iloc[-1])
                p['current_price'] = round(last_close, 4)
                highest = max(entry_price,
                              float(hist_from_entry['Close'].dropna().max()))
                if use_atr:
                    atr_now = float(atr_series.dropna().iloc[-1])
                    p['trail_stop'] = max(initial_stop,
                                          round(highest - atr_mult * atr_now, 4))
                else:
                    p['trail_stop'] = max(initial_stop,
                                          round(highest * (1.0 - trail_pct), 4))
                still_open.append(p)

        except Exception:
            still_open.append(p)

    data['positions'] = still_open
    # Realized P&L flows back into capital
    for t in data['closed'][-len(closed_now):] if closed_now else []:
        data['capital'] += t['pnl']
    _save(data, user_id=user_id)
    return {'closed': closed_now, 'checked': len(data['positions']) + len(closed_now),
            'data': data}


# ── Missed-trade discovery ───────────────────────────────────────────────────

def rebuild_skipped_cash(user_id: str | None = None) -> dict:
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

    data      = load(user_id=user_id)
    taken_syms = ({p['symbol'] for p in data['positions']} |
                  {t['symbol'] for t in data['closed']})

    all_fnames = sorted(list_files(_SIGNALS_DIR, 'signals_*.csv'))

    # Collect all symbols that appear in signal files so we know which
    # skipped_cash entries were sourced from CSVs vs. added via live
    # BREAKOUT detection (feedback agent). Live entries are preserved —
    # only signal-file-sourced entries are rebuilt.
    signal_syms: set[str] = set()
    for fname in all_fnames:
        df = load_data(f"{_SIGNALS_DIR}/{fname}")
        if df is None or df.empty:
            continue
        df.columns = [c.strip() for c in df.columns]
        col = 'Symbol' if 'Symbol' in df.columns else ('symbol' if 'symbol' in df.columns else None)
        if col:
            signal_syms.update(str(s).strip().upper() for s in df[col] if str(s).strip().upper() != 'NAN')

    # Keep live-breakout entries (not in any signal file) so they survive the rebuild.
    # Re-validate their stop/target in case the original scanner used bad price data.
    live_entries = []
    for e in data.get('skipped_cash', []):
        if e.get('symbol', '').upper() not in signal_syms:
            entry = e.get('entry_price', 0) or 0
            stop  = e.get('stop', 0) or 0
            target = e.get('target', 0) or 0
            if entry > 0 and (stop <= 0 or stop >= entry or (entry - stop) / entry > 0.30):
                e = dict(e, stop=round(entry * 0.95, 4))
            if entry > 0 and (target <= 0 or target <= entry):
                e = dict(e, target=round(entry * 1.10, 4))
            live_entries.append(e)

    data['skipped_cash'] = list(live_entries)   # start with preserved live entries
    seen_syms = {e['symbol'].upper() for e in live_entries}

    if not all_fnames:
        _save(data, user_id=user_id)
        return {'found': len(data['skipped_cash']), 'data': data}

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
        v9h = df[mask]

        for _, row in v9h.iterrows():
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
            _ms = _safe_float(row.get('MinerviniScore'))
            minervini = int(_ms) if (_ms is not None and _ms == _ms) else 0
            mode = str(row.get('Mode', file_mode)).lower().strip() or file_mode

            entry_price, current_price = _fetch_entry_and_current(sym, date_str, price)

            # Guard: if yfinance fell back to the CSV price (rate-limit / stale data),
            # do a quick live-price sanity check using a broader daily fetch.
            if price > 0 and entry_price == price:
                try:
                    import yfinance as _yf
                    _live = _yf.Ticker(sym).history(period='5d', auto_adjust=True)
                    if _live is not None and not _live.empty:
                        _live_price = float(_live['Close'].dropna().iloc[-1])
                        # If live price differs by >20%, it means csv_price was stale
                        if _live_price > 0 and max(_live_price, price) / min(_live_price, price) > 1.20:
                            # Re-fetch with the corrected price reference
                            entry_price, current_price = _fetch_entry_and_current(sym, date_str, _live_price)
                except Exception:
                    pass

            # Guard: stop must be below entry and not based on wrong price scale
            if stop >= entry_price or (entry_price > 0 and (entry_price - stop) / entry_price > 0.30):
                stop = round(entry_price * 0.95, 4)
            if target <= entry_price:
                target = round(entry_price * 1.10, 4)

            position_value = data['capital'] * POSITION_SIZE_PCT
            shares = max(1, int(position_value / entry_price))
            cost   = round(shares * entry_price, 2)

            data['skipped_cash'].append(_build_skipped_entry(
                row, sym, date_str, mode, quality, minervini,
                entry_price, current_price, stop, target,
                shares=shares, cost=cost, skip_reason='unknown',
            ))
            seen_syms.add(sym)

    data['skipped_cash'].sort(key=lambda e: e.get('priority_score', 0), reverse=True)
    _save(data, user_id=user_id)
    return {'found': len(data['skipped_cash']), 'data': data}


# ── Direct add (feedback agent) ───────────────────────────────────────────────

def add_position_direct(
    symbol: str,
    entry_price: float,
    stop: float,
    target: float,
    mode: str = 'swing',
    quality: str = '',
    vol_ratio: float = 0.0,
    position_pct: float | None = None,
    user_id: str | None = None,
) -> dict:
    """
    Directly add a position without scanning CSV files.
    Used by scan_feedback_agent.py when a real-time BREAKOUT is detected.
    Returns {'added': bool, 'reason': str}.

    Load + duplicate check + save are performed atomically under the file lock
    to prevent two concurrent processes from adding the same symbol.
    """
    from utils import save_json, load_json, _is_cloud, _to_local_abs
    import fcntl, os

    path = _portfolio_path_for(user_id)
    abs_path = _to_local_abs(path)
    lock_path = abs_path + '.lock'
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    with open(lock_path, 'w') as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            # Re-load inside lock to get latest state
            data = load(user_id=user_id)

            if symbol.upper() in open_symbols(data):
                return {'added': False, 'reason': 'duplicate'}

            pct   = position_pct if position_pct is not None else POSITION_SIZE_PCT
            shares = int(data['capital'] * pct / entry_price)
            if shares < 1:
                return {'added': False, 'reason': 'shares_zero'}

            cost = round(entry_price * shares, 2)
            if cost > available_cash(data):
                return {'added': False, 'reason': 'no_cash'}

            # Guard: stop must be BELOW entry price
            if stop >= entry_price:
                _trail_pct = {'longterm': 0.05, 'swing': 0.03, 'daytrade': 0.01, 'scalping': 0.01}
                stop = entry_price * (1.0 - _trail_pct.get(mode, 0.05))

            now_str = datetime.now(_NY_TZ).strftime('%Y-%m-%d %H:%M')
            position = {
                'symbol':          symbol.upper(),
                'date_added':      now_str,
                'mode':            mode,
                'quality':         quality,
                'minervini_score': 0,
                'entry_price':     round(entry_price, 4),
                'stop':            round(stop, 4),
                'target':          round(target, 4),
                'shares':          shares,
                'cost':            cost,
                'current_price':   round(entry_price, 4),
                'vol_ratio':       round(vol_ratio, 2),
            }
            data['positions'].append(position)
            data['last_updated'] = datetime.now(_NY_TZ).isoformat()
            save_json(data, path)
            return {'added': True, 'reason': 'ok'}
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ── Mode promotion (exit_evaluator PROMOTE_MODE) ─────────────────────────────

def promote_position_mode(symbol: str, new_mode: str, user_id: str | None = None) -> dict:
    """Change a position's mode (e.g. daytrade → swing) and re-stamp date_added
    so MAX_HOLD_BARS starts counting from the promotion date, not original entry.
    Returns {'promoted': bool, 'reason': str, 'old_mode': str}.
    """
    data = load(user_id=user_id)
    sym = symbol.upper()
    for p in data['positions']:
        if p['symbol'].upper() == sym:
            old = p.get('mode', '')
            if old == new_mode:
                return {'promoted': False, 'reason': 'same_mode', 'old_mode': old}
            p['mode'] = new_mode
            p['date_added'] = datetime.now(_NY_TZ).strftime('%Y-%m-%d %H:%M')
            _save(data, user_id=user_id)
            return {'promoted': True, 'reason': 'ok', 'old_mode': old}
    return {'promoted': False, 'reason': 'not_found', 'old_mode': ''}


# ── Manual close (UI) ─────────────────────────────────────────────────────────

def close_position(symbol: str, exit_price: float, reason: str = 'manual', user_id: str | None = None) -> dict:
    """Close a specific position at given price."""
    data = load(user_id=user_id)
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
    if closed_rec:
        data['capital'] += closed_rec['pnl']  # realized P&L flows back into capital
    _save(data, user_id=user_id)
    return closed_rec or {}


# ── Rebalance ────────────────────────────────────────────────────────────────

def rebalance(dry_run: bool = True, user_id: str | None = None) -> dict:
    """
    Enforce portfolio balance rules on EXISTING positions.

    Scans open positions and either closes or trims those that violate:
      1. Mode concentration (>max_per_mode → close worst P&L in that mode)
      2. Sector concentration (>max_per_sector → close worst P&L in sector)
      3. ATR-aware sizing (position too large for its volatility → trim shares)
      4. Cash reserve (cash% below safe threshold → close worst performers)

    Args:
        dry_run: If True, only prints actions without executing. Default True
                 for safety — run with dry_run=False to actually rebalance.

    Returns dict with lists of actions taken.
    """
    import logging
    logger = logging.getLogger(__name__)

    from config import CASH_MANAGEMENT

    data = load(user_id=user_id)
    positions = data['positions']
    capital = data['capital']

    if not positions:
        print("No open positions — nothing to rebalance.")
        return {'closed': [], 'trimmed': []}

    max_per_sector = CASH_MANAGEMENT.get('max_per_sector', 3)
    max_per_mode = CASH_MANAGEMENT.get('max_per_mode', 5)
    low_cash_pct = CASH_MANAGEMENT.get('low_cash_pct', 0.15)

    # Refresh current prices
    _refresh_current_prices(data)

    actions_close = []
    actions_trim = []

    # Helper: P&L% for sorting (worst first)
    def _pnl_pct(p):
        cur = p.get('current_price', p['entry_price'])
        return (cur - p['entry_price']) / p['entry_price'] if p['entry_price'] > 0 else 0

    # ── 1. Mode concentration ────────────────────────────────────────────
    from collections import Counter
    mode_counts = Counter(p.get('mode', '') for p in positions)
    for mode, count in mode_counts.items():
        if count <= max_per_mode:
            continue
        excess = count - max_per_mode
        # Close the worst performers in this mode
        mode_positions = sorted(
            [p for p in positions if p.get('mode') == mode],
            key=_pnl_pct
        )
        for p in mode_positions[:excess]:
            cur = p.get('current_price', p['entry_price'])
            pnl = _pnl_pct(p) * 100
            actions_close.append({
                'symbol': p['symbol'],
                'reason': f"mode '{mode}' over limit ({count}>{max_per_mode})",
                'exit_price': cur,
                'pnl_pct': round(pnl, 1),
            })

    # ── 2. Sector concentration ──────────────────────────────────────────
    sector_counts = Counter(p.get('sector', '') for p in positions if p.get('sector'))
    for sector, count in sector_counts.items():
        if count <= max_per_sector:
            continue
        excess = count - max_per_sector
        sector_positions = sorted(
            [p for p in positions if p.get('sector') == sector],
            key=_pnl_pct
        )
        for p in sector_positions[:excess]:
            # Skip if already marked for close by mode rule
            if any(a['symbol'] == p['symbol'] for a in actions_close):
                continue
            cur = p.get('current_price', p['entry_price'])
            pnl = _pnl_pct(p) * 100
            actions_close.append({
                'symbol': p['symbol'],
                'reason': f"sector '{sector}' over limit ({count}>{max_per_sector})",
                'exit_price': cur,
                'pnl_pct': round(pnl, 1),
            })

    # ── 3. ATR-aware sizing — trim oversized volatile positions ──────────
    avg_atr = _portfolio_avg_atr(data)
    if avg_atr and avg_atr > 0:
        for p in positions:
            # Skip if already marked for close
            if any(a['symbol'] == p['symbol'] for a in actions_close):
                continue
            atr = _compute_atr_pct(p['symbol'])
            if not atr or atr <= 0:
                continue
            ratio = atr / avg_atr
            if ratio <= 1.5:
                continue  # fine

            # Determine target sizing mult
            if ratio > 4.0:
                target_mult = 0.20
            elif ratio > 3.0:
                target_mult = 0.25
            elif ratio > 2.0:
                target_mult = 0.50
            else:  # 1.5-2.0
                target_mult = 0.75

            # Current position value vs what it should be
            cur_price = p.get('current_price', p['entry_price'])
            cur_shares = p['shares']
            cur_value = cur_shares * cur_price

            base_value = capital * POSITION_SIZE_PCT
            target_value = base_value * target_mult
            if cur_value <= target_value * 1.1:
                continue  # within 10% tolerance

            target_shares = max(1, int(target_value / cur_price))
            trim_shares = cur_shares - target_shares
            if trim_shares < 1:
                continue

            actions_trim.append({
                'symbol': p['symbol'],
                'reason': f"ATR {atr:.1%} ({ratio:.1f}x avg) → {target_mult:.0%} size",
                'current_shares': cur_shares,
                'target_shares': target_shares,
                'trim_shares': trim_shares,
                'trim_value': round(trim_shares * cur_price, 2),
                'current_price': cur_price,
            })

    # ── 4. Cash reserve — close worst performers if cash too low ─────────
    # Calculate projected cash after closes above
    projected_cash = available_cash(data)
    for a in actions_close:
        # Each close frees up the cost basis
        pos = next((p for p in positions if p['symbol'] == a['symbol']), None)
        if pos:
            projected_cash += pos['cost']
    for a in actions_trim:
        projected_cash += a['trim_value']

    projected_cash_pct = projected_cash / capital if capital > 0 else 0

    if projected_cash_pct < low_cash_pct:
        needed = capital * low_cash_pct - projected_cash
        # Close worst P&L positions until we meet the target
        remaining = sorted(
            [p for p in positions if not any(a['symbol'] == p['symbol'] for a in actions_close)],
            key=_pnl_pct
        )
        freed = 0
        for p in remaining:
            if freed >= needed:
                break
            cur = p.get('current_price', p['entry_price'])
            pnl = _pnl_pct(p) * 100
            actions_close.append({
                'symbol': p['symbol'],
                'reason': f"cash reserve — need {low_cash_pct:.0%}, "
                          f"projected {projected_cash_pct:.1%}",
                'exit_price': cur,
                'pnl_pct': round(pnl, 1),
            })
            freed += p['cost']

    # ── Print plan ───────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"REBALANCE {'(DRY RUN)' if dry_run else 'EXECUTING'}")
    print(f"{'='*70}")
    print(f"Open positions: {len(positions)} | Cash: ${available_cash(data):,.2f} "
          f"({available_cash(data)/capital:.1%})")

    if actions_close:
        print(f"\n  CLOSE ({len(actions_close)} positions):")
        for a in actions_close:
            print(f"    {a['symbol']:<8} P&L {a['pnl_pct']:>+5.1f}% "
                  f"@ ${a['exit_price']:>8.2f} — {a['reason']}")
    if actions_trim:
        print(f"\n  TRIM ({len(actions_trim)} positions):")
        for a in actions_trim:
            print(f"    {a['symbol']:<8} {a['current_shares']}→{a['target_shares']} shares "
                  f"(-{a['trim_shares']}, frees ${a['trim_value']:,.0f}) — {a['reason']}")

    if not actions_close and not actions_trim:
        print("\n  Portfolio is balanced — no actions needed.")
        return {'closed': [], 'trimmed': []}

    # Estimate post-rebalance state
    est_cash = available_cash(data)
    for a in actions_close:
        pos = next((p for p in positions if p['symbol'] == a['symbol']), None)
        if pos:
            est_cash += pos['cost'] + (a['exit_price'] - pos['entry_price']) * pos['shares']
    for a in actions_trim:
        est_cash += a['trim_value']
    print(f"\n  Estimated cash after rebalance: ${est_cash:,.2f} ({est_cash/capital:.1%})")

    # ── Execute ──────────────────────────────────────────────────────────
    if dry_run:
        print("\n  → Dry run — no changes made. Run rebalance(dry_run=False) to execute.")
        return {'closed': actions_close, 'trimmed': actions_trim}

    # Execute closes
    for a in actions_close:
        result = close_position(a['symbol'], a['exit_price'],
                                reason=f"rebalance: {a['reason']}")
        if result:
            logger.info(f"REBALANCE CLOSED {a['symbol']} @ ${a['exit_price']:.2f} "
                        f"({a['reason']})")

    # Execute trims (reduce shares, free cash)
    data = load(user_id=user_id)  # reload after closes
    for a in actions_trim:
        for p in data['positions']:
            if p['symbol'] == a['symbol']:
                old_shares = p['shares']
                p['shares'] = a['target_shares']
                p['cost'] = round(p['shares'] * p['entry_price'], 2)
                freed = round((old_shares - a['target_shares']) * a['current_price'], 2)
                # Freed capital goes back to cash (realized at current price)
                pnl_on_trimmed = (a['current_price'] - p['entry_price']) * (old_shares - a['target_shares'])
                data['capital'] += pnl_on_trimmed  # P&L on trimmed shares
                logger.info(
                    f"REBALANCE TRIMMED {a['symbol']} {old_shares}→{a['target_shares']} shares "
                    f"(freed ${freed:,.0f}, {a['reason']})"
                )
                break
    _save(data, user_id=user_id)

    print(f"\n  ✓ Rebalance complete. Closed {len(actions_close)}, trimmed {len(actions_trim)}.")
    return {'closed': actions_close, 'trimmed': actions_trim}


def _refresh_current_prices(data: dict):
    """Update current_price for all open positions."""
    positions = data.get('positions', [])
    if not positions:
        return
    try:
        from yfinance_adapter import YFinanceAdapter
        adapter = YFinanceAdapter(use_disk_cache=True)
        for p in positions:
            df = adapter.get_historical_data(p['symbol'], '1 day',
                                             start_date=(datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d'),
                                             end_date=datetime.now().strftime('%Y-%m-%d'))
            if df is not None and len(df) > 0:
                p['current_price'] = float(df['close'].iloc[-1])
    except Exception:
        pass


# ── Reset ─────────────────────────────────────────────────────────────────────

def reset(user_id: str | None = None) -> dict:
    data = _empty()
    _save(data, user_id=user_id)
    return data


def recalculate(position_pct: float = POSITION_SIZE_PCT,
                min_date: str | None = None,
                user_id: str | None = None) -> dict:
    """
    Reset the portfolio and rescan all signal files from scratch.

    Args:
        position_pct: Position size fraction (e.g. 0.05 for 5%, 0.10 for 10%).
        min_date: Optional start date 'YYYY-MM-DD'. If set, only process files
                  on or after this date.
    Returns the scan_and_add result dict.
    """
    reset(user_id=user_id)
    result = scan_and_add(min_date=min_date, position_pct=position_pct, user_id=user_id, notify=False)
    _save_entry_price_cache()
    return result


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

    all_dates = [p.get('date_added', '') for p in data.get('positions', [])] + \
                [t.get('date_added', '') for t in data.get('closed', [])]
    start_date = min(d for d in all_dates if d) if any(all_dates) else None

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
        'start_date':   start_date,
    }


# ── Swap Advisor ──────────────────────────────────────────────────────────────

_SWAP_WEAK_PNL_PCT    = -2.0   # position must be down ≥2% to be considered weak
_SWAP_STOP_PROXIMITY  = 0.04   # OR within 4% of its stop
_SWAP_MIN_FRESH_DAYS  = 5      # skipped signal must be ≤5 trading days old
_SWAP_MIN_SCORE_DELTA = 20.0   # skipped priority_score must beat pos score by ≥20pts
_SWAP_MIN_MOMENTUM    = 0.0    # skipped signal must be up since signal date (not falling)


def _position_weakness_score(pos: dict) -> float:
    """Score how weak/risky an open position is — higher = weaker.

    Factors:
      - current P&L% (negative amplifies weakness)
      - proximity to stop (< 4% buffer is dangerous)
      - days held without progress (stagnant capital)
      - quality tier (LOW quality held longer = more liability)
    """
    entry   = pos.get('entry_price', 0) or 1
    current = pos.get('current_price', entry)
    stop    = pos.get('stop', entry * 0.95)

    pnl_pct      = (current - entry) / entry * 100
    stop_dist_pct = (current - stop) / current * 100 if current > 0 else 0

    try:
        date_added = pos.get('date_added', '')[:10]
        days_held  = (datetime.now(_NY_TZ).date() -
                      datetime.strptime(date_added, '%Y-%m-%d').date()).days
    except Exception:
        days_held = 0

    quality_penalty = {'GOLD': 0, 'PREMIUM': 5, 'HIGH': 15}.get(
        pos.get('quality', 'PREMIUM'), 10
    )

    score = 0.0
    score -= pnl_pct * 2           # down 5% → +10 pts weakness
    score += max(0, 4 - stop_dist_pct) * 5   # tight stop → up to +20 pts
    score += max(0, days_held - 10) * 0.3    # stagnant >10 days
    score += quality_penalty
    return round(score, 1)


def suggest_swaps(
    max_suggestions: int = 3,
    fresh_days: int = _SWAP_MIN_FRESH_DAYS,
    min_score_delta: float = _SWAP_MIN_SCORE_DELTA,
    notify: bool = True,
    user_id: str | None = None,
) -> list[dict]:
    """Compare open positions against top skipped signals and suggest swaps.

    A swap is suggested when:
      1. An open position is weak (down ≥2% OR within 4% of stop)
      2. A recent skipped signal (≤fresh_days old) has priority_score ≥ the
         weak position's implied score + min_score_delta
      3. The skipped signal has positive momentum since signal date

    Returns list of swap dicts, sorted by improvement desc.
    Sends a notification if notify=True and any swaps found.
    """
    data      = load(user_id=user_id)
    positions = data.get('positions', [])
    skipped   = data.get('skipped_cash', [])

    if not positions or not skipped:
        return []

    today = datetime.now(_NY_TZ).date()

    # Filter skipped: fresh, not already open, positive momentum, sane target
    open_syms = {p['symbol'] for p in positions}
    fresh_skipped = []
    for s in skipped:
        if s.get('symbol') in open_syms:
            continue
        try:
            sig_date = datetime.strptime(s.get('date_added', '')[:10], '%Y-%m-%d').date()
            age_days = (today - sig_date).days
        except Exception:
            continue
        if age_days > fresh_days:
            continue
        if s.get('missed_pnl_pct', 0) < _SWAP_MIN_MOMENTUM:
            continue
        # Reject corrupted targets (S/R resolver artefact: target > 3× entry)
        ep = s.get('entry_price', 0) or 0
        tgt = s.get('target', 0) or 0
        if ep > 0 and tgt > ep * 3.0:
            continue
        # Reject if target upside < 3% (daytrade signals have tiny targets, not suitable as replacements)
        if ep > 0 and tgt > 0 and (tgt - ep) / ep < 0.03:
            continue
        fresh_skipped.append(s)

    if not fresh_skipped:
        return []

    # Score each open position for weakness
    weak_positions = []
    for p in positions:
        entry   = p.get('entry_price', 0) or 1
        current = p.get('current_price', entry)
        stop    = p.get('stop', entry * 0.95)

        pnl_pct       = (current - entry) / entry * 100
        stop_dist_pct = (current - stop) / current * 100 if current > 0 else 0

        is_weak = pnl_pct <= _SWAP_WEAK_PNL_PCT or stop_dist_pct <= _SWAP_STOP_PROXIMITY * 100
        if not is_weak:
            continue

        weakness = _position_weakness_score(p)
        # Implied priority score of the open position (estimated from quality + R:R)
        held_rr = ((p.get('target', entry * 1.1) - entry) /
                   max(entry - stop, entry * 0.01))
        implied_score = _compute_priority_score(
            p.get('quality', 'PREMIUM'), 50.0, held_rr, 1.0
        )
        weak_positions.append({**p, '_weakness': weakness, '_implied_score': implied_score})

    if not weak_positions:
        return []

    # Sort weak positions worst-first
    weak_positions.sort(key=lambda p: p['_weakness'], reverse=True)

    # Match each weak position to the best available skipped signal
    swaps = []
    used_skipped = set()

    for pos in weak_positions:
        if len(swaps) >= max_suggestions:
            break
        best_skip = None
        best_delta = min_score_delta - 1

        for s in fresh_skipped:
            if s['symbol'] in used_skipped:
                continue
            delta = s.get('priority_score', 0) - pos['_implied_score']
            if delta > best_delta:
                best_delta = delta
                best_skip  = s

        if best_skip is None:
            continue

        used_skipped.add(best_skip['symbol'])
        entry   = pos.get('entry_price', 0) or 1
        current = pos.get('current_price', entry)
        pnl_pct = (current - entry) / entry * 100

        swaps.append({
            'close_symbol':    pos['symbol'],
            'close_pnl_pct':   round(pnl_pct, 2),
            'close_current':   current,
            'close_stop':      pos.get('stop'),
            'close_quality':   pos.get('quality'),
            'close_weakness':  pos['_weakness'],
            'open_symbol':     best_skip['symbol'],
            'open_quality':    best_skip.get('quality'),
            'open_win_prob':   best_skip.get('win_prob'),
            'open_win_grade':  best_skip.get('win_grade'),
            'open_rr':         best_skip.get('rr'),
            'open_vol':        best_skip.get('vol'),
            'open_type':       best_skip.get('type'),
            'open_entry':      best_skip.get('entry_price'),
            'open_stop':       best_skip.get('stop'),
            'open_target':     best_skip.get('target'),
            'open_momentum':   best_skip.get('missed_pnl_pct'),
            'open_priority':   best_skip.get('priority_score'),
            'score_improvement': round(best_delta, 1),
            'signal_date':     best_skip.get('date_added', '')[:10],
            'skip_reason':     best_skip.get('skip_reason', ''),
        })

    if not swaps or not notify:
        return swaps

    try:
        from notifier import Notifier
        lines = []
        for sw in swaps:
            lines.append(
                f"• CLOSE {sw['close_symbol']} ({sw['close_pnl_pct']:+.1f}%) → "
                f"OPEN {sw['open_symbol']} [{sw['open_quality']}]  "
                f"R:R {sw['open_rr']} | WinProb {sw['open_win_prob']}% | "
                f"+{sw['open_momentum']:.1f}% since signal  "
                f"(score +{sw['score_improvement']:.0f}pts)"
            )
        Notifier().send_all(
            subject=f"⚡ Swap Advisor: {len(swaps)} opportunity{'s' if len(swaps) > 1 else ''}",
            message=(
                "Better signals are available to replace weaker positions:\n\n"
                + "\n".join(lines)
                + "\n\nThese are suggestions only — review before acting."
            ),
            signals=None,
            notification_type='signals',
            force=True,
        )
    except Exception as _e:
        import logging as _log
        _log.getLogger(__name__).warning(f"Swap advisor notification failed: {_e}")

    return swaps


def _fetch_live_price(symbol: str) -> Optional[float]:
    """Fetch the most recent close for `symbol` via yfinance. None on failure."""
    try:
        import yfinance as yf
        hist = yf.Ticker(symbol.replace(' ', '-')).history(period='2d')
        if hist is not None and not hist.empty:
            return float(hist['Close'].dropna().iloc[-1])
    except Exception:
        return None
    return None


def execute_swap(close_symbol: str, open_symbol: str, user_id: str | None = None) -> dict:
    """Close a weak position and open a fresh one from the skipped_cash list.

    Uses live (yfinance) prices for BOTH the close exit and the new entry.
    The skipped signal's stop/target levels (technical S/R) are preserved;
    `add_position_direct` auto-adjusts the stop if the live entry drifted
    above the signal's original stop.

    Returns {'ok': bool, 'reason': str, 'closed': {...}, 'opened': {...}}.
    """
    close_sym = close_symbol.upper()
    open_sym  = open_symbol.upper()

    data = load(user_id=user_id)

    open_pos = next(
        (p for p in data.get('positions', []) if p['symbol'].upper() == close_sym),
        None,
    )
    if open_pos is None:
        return {'ok': False, 'reason': f'{close_sym} not in open positions'}

    skipped = data.get('skipped_cash', [])
    skip_rec = next(
        (s for s in skipped if s.get('symbol', '').upper() == open_sym),
        None,
    )
    if skip_rec is None:
        return {'ok': False, 'reason': f'{open_sym} not in skipped signals'}

    exit_price = _fetch_live_price(close_sym)
    if exit_price is None or exit_price <= 0:
        return {'ok': False, 'reason': f'live price unavailable for {close_sym}'}

    entry_price = _fetch_live_price(open_sym)
    if entry_price is None or entry_price <= 0:
        return {'ok': False, 'reason': f'live price unavailable for {open_sym}'}

    # Snapshot pre-swap state for undo
    pre_position = dict(open_pos)
    pre_skipped  = dict(skip_rec)

    closed = close_position(close_sym, exit_price, reason='swap', user_id=user_id)
    if not closed:
        return {'ok': False, 'reason': f'close failed for {close_sym}'}

    stop   = float(skip_rec.get('stop') or 0.0)
    target = float(skip_rec.get('target') or 0.0)
    mode   = skip_rec.get('mode') or 'swing'
    quality = skip_rec.get('quality') or ''
    vol    = float(skip_rec.get('vol') or 0.0)

    result = add_position_direct(
        symbol=open_sym,
        entry_price=entry_price,
        stop=stop,
        target=target,
        mode=mode,
        quality=quality,
        vol_ratio=vol,
        user_id=user_id,
    )
    if not result.get('added'):
        return {
            'ok': False,
            'reason': f"close succeeded but open failed: {result.get('reason')}",
            'closed': closed,
        }

    # Remove the consumed skipped signal and stash undo snapshot
    data2 = load(user_id=user_id)
    data2['skipped_cash'] = [
        s for s in data2.get('skipped_cash', [])
        if s.get('symbol', '').upper() != open_sym
    ]
    data2['last_swap'] = {
        'ts':            datetime.now(_NY_TZ).isoformat(),
        'close_symbol':  close_sym,
        'open_symbol':   open_sym,
        'pre_position':  pre_position,
        'pre_skipped':   pre_skipped,
        'closed_record': closed,
    }
    _save(data2, user_id=user_id)

    return {
        'ok': True,
        'reason': 'ok',
        'closed': closed,
        'opened': {
            'symbol':      open_sym,
            'entry_price': round(entry_price, 4),
            'stop':        round(stop, 4),
            'target':      round(target, 4),
            'mode':        mode,
            'quality':     quality,
        },
    }


def undo_last_swap(user_id: str | None = None) -> dict:
    """Reverse the most recent `execute_swap()`.

    Restores the original position (with its original entry/shares/date),
    removes the swap-opened position, puts the skipped signal back in the
    list, and rewinds the realized P&L that was booked on close.

    Returns {'ok': bool, 'reason': str}.
    """
    data = load(user_id=user_id)
    snap = data.get('last_swap')
    if not snap:
        return {'ok': False, 'reason': 'no recent swap to undo'}

    close_sym = (snap.get('close_symbol') or '').upper()
    open_sym  = (snap.get('open_symbol')  or '').upper()
    pre_position = snap.get('pre_position') or {}
    pre_skipped  = snap.get('pre_skipped')  or {}
    closed_rec   = snap.get('closed_record') or {}

    # 1. Remove the swap-opened position without booking any P&L
    data['positions'] = [
        p for p in data.get('positions', [])
        if p.get('symbol', '').upper() != open_sym
    ]

    # 2. Remove the swap-close record from closed[] and rewind realized P&L
    def _is_swap_close(r):
        return (
            r.get('symbol', '').upper() == close_sym
            and r.get('close_reason') == 'swap'
            and r.get('date_closed') == closed_rec.get('date_closed')
            and abs(float(r.get('exit_price') or 0) - float(closed_rec.get('exit_price') or 0)) < 0.01
        )
    removed_pnl = 0.0
    new_closed = []
    removed_once = False
    for r in data.get('closed', []):
        if not removed_once and _is_swap_close(r):
            removed_pnl = float(r.get('pnl') or 0.0)
            removed_once = True
            continue
        new_closed.append(r)
    data['closed'] = new_closed
    if removed_once:
        data['capital'] = round(float(data.get('capital', 0)) - removed_pnl, 2)

    # 3. Restore the original open position
    if pre_position and not any(
        p.get('symbol', '').upper() == close_sym for p in data['positions']
    ):
        data['positions'].append(pre_position)

    # 4. Restore the skipped_cash entry if not already present
    if pre_skipped and not any(
        s.get('symbol', '').upper() == open_sym
        for s in data.get('skipped_cash', [])
    ):
        data.setdefault('skipped_cash', []).append(pre_skipped)
        data['skipped_cash'].sort(
            key=lambda e: e.get('priority_score', 0), reverse=True
        )

    data['last_swap'] = None
    _save(data, user_id=user_id)
    return {
        'ok': True,
        'reason': 'ok',
        'restored_position': close_sym,
        'removed_position':  open_sym,
    }
