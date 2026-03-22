"""
Utility functions for file I/O and helpers.

Provides hybrid local/S3 I/O for Streamlit pages:
  - load_data(local_path, s3_path)   → pd.DataFrame (CSV)
  - save_data(df, local_path, s3_path)
  - load_text(local_path, s3_path)   → str
  - save_text(content, local_path, s3_path)
  - load_json(local_path, s3_path)   → dict/list
  - save_json(data, local_path, s3_path)
  - list_files(local_dir, s3_prefix, pattern) → list[str]

All functions try S3 first (if st.secrets has "connections"),
then fall back to local filesystem.
"""

import csv
import json
import logging
import os

# ---------------------------------------------------------------------------
# Custom SCAN log level (15) — between DEBUG (10) and INFO (20).
# Use logger.scan() for per-symbol scanner decisions (skip reasons, scores).
# This lets --debug suppress noisy library logs while keeping scanner output.
# ---------------------------------------------------------------------------
SCAN_LEVEL = 15
logging.addLevelName(SCAN_LEVEL, 'SCAN')

def _scan_log(self, message, *args, **kwargs):
    if self.isEnabledFor(SCAN_LEVEL):
        self._log(SCAN_LEVEL, message, args, **kwargs)

logging.Logger.scan = _scan_log

# Noisy third-party loggers to silence when --debug is active
_QUIET_LIBS = [
    'yfinance', 'peewee', 'urllib3', 'urllib3.connectionpool',
    'httpx', 'httpcore', 'requests', 'charset_normalizer',
]
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd
from zoneinfo import ZoneInfo

from streamlit.logger import get_logger

_NY_TZ = ZoneInfo('America/New_York')

logger = logging.getLogger(__name__)

# S3 bucket name (must match .streamlit/secrets.toml)
S3_BUCKET = "stocks-breakout-scanner-s3-bucket"

# Project root — used to convert absolute local paths to relative S3 keys
_PROJECT_ROOT = str(Path(__file__).parent)
PROJECT_ROOT = Path(_PROJECT_ROOT)  # exported for pages (single source of truth)


def _to_local_abs(path: str) -> str:
    """Resolve a path to absolute, anchored to project root if relative.

    Ensures local fallback works regardless of CWD.
    """
    if os.path.isabs(path):
        return path
    return str(PROJECT_ROOT / path)


def _to_s3_key(local_path: str) -> str:
    """Convert a local path (absolute or relative) to an S3 key.

    Example:
        /Users/gil/.../stocksBreakout/scanner_output/signals/file.csv
        → stocks-breakout-scanner-s3-bucket/scanner_output/signals/file.csv

        scanner_output/portfolio/portfolio.json  (relative, CWD = project root)
        → stocks-breakout-scanner-s3-bucket/scanner_output/portfolio/portfolio.json
    """
    abs_path = os.path.abspath(local_path)
    # Guard: ensure we're truly under the project root (not a sibling dir)
    root_prefix = _PROJECT_ROOT + os.sep
    if abs_path.startswith(root_prefix):
        rel = abs_path[len(root_prefix):]
    elif abs_path == _PROJECT_ROOT:
        rel = ""
    else:
        # Path is outside the project root — use relative portion as-is
        rel = local_path.lstrip(os.sep)
    return f"{S3_BUCKET}/{rel}" if rel else S3_BUCKET


# ─── Hybrid I/O helpers (local + S3) ───────────────────────────────────────

def _is_cloud() -> bool:
    """Return True when S3 storage should be used.

    Detects any of:
      - Streamlit Cloud deployment path (/mount/src/)
      - [connections.s3] in Streamlit secrets
      - Flat AWS keys in Streamlit secrets (AWS_ACCESS_KEY_ID, etc.)
      - AWS_ACCESS_KEY_ID in environment (EC2/ECS/Lambda/CI)
    """
    # Streamlit Cloud: apps always deploy under /mount/src/
    if _PROJECT_ROOT.startswith('/mount/src/'):
        return True
    try:
        import streamlit as st
        secrets = st.secrets
        if "connections" in secrets:
            return True
        if any(k in secrets for k in ("AWS_ACCESS_KEY_ID", "aws_access_key_id",
                                       "AWS_S3_BUCKET", "S3_BUCKET")):
            return True
    except Exception:
        pass
    return bool(os.environ.get("AWS_ACCESS_KEY_ID"))


def _s3_conn():
    """Get Streamlit S3 connection (official recommended approach)."""
    import streamlit as st
    from st_files_connection import FilesConnection
    return st.connection('s3', type=FilesConnection)


def _s3_fs():
    """Return the raw s3fs filesystem (bypasses Streamlit's @st.cache_data layer)."""
    return _s3_conn().fs


# ─── CSV ────────────────────────────────────────────────────────────────────

def load_data(local_path: str, s3_path: str = None) -> Optional[pd.DataFrame]:
    """Load CSV as DataFrame — tries S3 first (via raw s3fs), falls back to local.

    Uses conn.fs directly to bypass Streamlit's @st.cache_data layer so reads
    always reflect the latest S3 state after a write.

    Args:
        local_path: Local filesystem path (relative or absolute)
        s3_path:    S3 key (e.g. "bucket/scanner_output/signals/file.csv")
                    Auto-generated from local_path if None.
    """
    if s3_path is None:
        s3_path = _to_s3_key(local_path)

    if _is_cloud():
        try:
            fs = _s3_fs()
            with fs.open(s3_path, 'r') as f:
                return pd.read_csv(f)
        except Exception as e:
            logger.warning(f"S3 CSV read failed for {s3_path}, falling back to local: {e}")

    abs_path = _to_local_abs(local_path)
    if os.path.exists(abs_path):
        return pd.read_csv(abs_path)

    logger.warning(f"File not found: {abs_path}")
    return None


def save_data(df: pd.DataFrame, local_path: str, s3_path: str = None):
    """Save DataFrame as CSV — writes to S3 (via raw s3fs) if cloud, else local.

    After writing, invalidates the s3fs cache so subsequent reads are fresh.

    Args:
        df:         DataFrame to save
        local_path: Local filesystem path
        s3_path:    S3 key (auto-generated if None)
    """
    if s3_path is None:
        s3_path = _to_s3_key(local_path)

    if _is_cloud():
        try:
            fs = _s3_fs()
            with fs.open(s3_path, 'w') as f:
                df.to_csv(f, index=False)
            fs.invalidate_cache(s3_path)
            return
        except Exception as e:
            logger.warning(f"S3 CSV write failed for {s3_path}, falling back to local: {e}")

    abs_path = _to_local_abs(local_path)
    os.makedirs(os.path.dirname(abs_path) or '.', exist_ok=True)
    df.to_csv(abs_path, index=False)


# ─── Text (TXT, watchlists) ────────────────────────────────────────────────

def load_text(local_path: str, s3_path: str = None) -> Optional[str]:
    """Load text file — tries S3 first (via raw s3fs), falls back to local."""
    if s3_path is None:
        s3_path = _to_s3_key(local_path)
    if _is_cloud(): 
        try:
            fs = _s3_fs()
            with fs.open(s3_path, 'r') as f:
                return f.read().strip()
        except Exception as e:
            logger.warning(f"S3 text read failed for {s3_path}: {e}")

    abs_path = _to_local_abs(local_path)
    if os.path.exists(abs_path):
        return Path(abs_path).read_text().strip()

    logger.warning(f"Text file not found: {abs_path}")
    return None


def save_text(content: str, local_path: str, s3_path: str = None):
    """Save text file — writes to S3 (via raw s3fs) if cloud, else local.

    After writing, invalidates the s3fs cache so subsequent reads are fresh.
    """
    if s3_path is None:
        s3_path = _to_s3_key(local_path)

    if _is_cloud():
        try:
            fs = _s3_fs()
            with fs.open(s3_path, 'w') as f:
                f.write(content)
            fs.invalidate_cache(s3_path)
            return
        except Exception as e:
            logger.warning(f"S3 text write failed for {s3_path}: {e}")

    abs_path = _to_local_abs(local_path)
    os.makedirs(os.path.dirname(abs_path) or '.', exist_ok=True)
    Path(abs_path).write_text(content)


# ─── JSON ───────────────────────────────────────────────────────────────────

def load_json(local_path: str, s3_path: str = None):
    """Load JSON file — tries S3 first (via raw s3fs), falls back to local.

    Uses conn.fs directly to bypass Streamlit's @st.cache_data layer, ensuring
    reads always reflect the latest S3 state (critical for mutable portfolio data).
    """
    if s3_path is None:
        s3_path = _to_s3_key(local_path)

    if _is_cloud():
        try:
            fs = _s3_fs()
            with fs.open(s3_path, 'r') as f:
                return json.loads(f.read())
        except Exception as e:
            logger.warning(f"S3 JSON read failed for {s3_path}: {e}")

    abs_path = _to_local_abs(local_path)
    if os.path.exists(abs_path):
        with open(abs_path) as f:
            return json.load(f)

    logger.warning(f"JSON file not found: {abs_path}")
    return None


def save_json(data, local_path: str, s3_path: str = None):
    """Save JSON file — writes to S3 (via raw s3fs) if cloud, else local.

    After writing, invalidates the s3fs cache so subsequent reads are fresh.
    This ensures Reset / Scan buttons take effect immediately without any TTL delay.
    """
    if s3_path is None:
        s3_path = _to_s3_key(local_path)

    content = json.dumps(data, indent=2, default=str)

    if _is_cloud():
        try:
            fs = _s3_fs()
            with fs.open(s3_path, 'w') as f:
                f.write(content)
            fs.invalidate_cache(s3_path)
            return
        except Exception as e:
            logger.warning(f"S3 JSON write failed for {s3_path}: {e}")

    abs_path = _to_local_abs(local_path)
    os.makedirs(os.path.dirname(abs_path) or '.', exist_ok=True)
    with open(abs_path, 'w') as f:
        f.write(content)


# ─── File listing (glob) ───────────────────────────────────────────────────

def list_files(local_dir: str, pattern: str = "*",
               s3_prefix: str = None) -> List[str]:
    """List files matching pattern — tries S3 first, falls back to local.

    Returns list of filenames (not full paths) sorted newest-first.
    """
    if s3_prefix is None:
        s3_prefix = _to_s3_key(local_dir)

    if _is_cloud():
        try:
            import fnmatch
            conn = _s3_conn()
            all_keys = conn.fs.ls(s3_prefix, detail=False)
            names = [os.path.basename(k) for k in all_keys]
            matched = [n for n in names if fnmatch.fnmatch(n, pattern)]
            return sorted(matched, reverse=True)
        except Exception as e:
            logger.warning(f"S3 list failed for {s3_prefix}: {e}")

    local = Path(_to_local_abs(local_dir))
    if local.exists():
        files = sorted(local.glob(pattern), reverse=True)
        return [f.name for f in files]

    return []


# ─── Original utility functions (used by CLI scripts) ──────────────────────

def get_watchlist_from_file(file_path: str) -> List[str]:
    """
    Load watchlist from a .txt or .csv file.

    .txt format:
        AAPL, MSFT, GOOGL      # comma-separated on one line, or one per line
        ### Comments ignored

    .csv format:
        Must have a 'Symbol' or 'symbol' column; all other columns are ignored.
        Used so positions CSVs (positions_swing_mock.csv, etc.) can serve
        directly as Phase-2 scan watchlists without a separate .txt export.
    """
    if file_path.lower().endswith('.csv'):
        try:
            df = pd.read_csv(file_path)
            col = 'Symbol' if 'Symbol' in df.columns else 'symbol'
            symbols = df[col].dropna().astype(str).str.strip().unique().tolist()
            return [s for s in symbols if s]
        except FileNotFoundError:
            logger.warning(f"Watchlist CSV not found: {file_path}")
            return []
        except Exception as e:
            logger.error(f"Failed to load watchlist from CSV {file_path}: {e}")
            return []

    watchlist = []
    try:
        with open(file_path, 'r') as f:
            for line in f.read().splitlines():
                line = line.strip()
                if not line or line.startswith('###'):
                    continue

                for s in line.split(','):
                    s = s.strip()
                    if s and not s.startswith('###'):
                        # Extract symbol (handle "EXCHANGE:SYMBOL" format)
                        clean = s.split(':')[-1]

                        # Handle special cases
                        if clean == 'BRK.B':
                            clean = 'BRK B'

                        # Skip ETFs starting with XL
                        if not (clean.startswith('XL') and len(clean) <= 4):
                            watchlist.append(clean)

        return list(set(watchlist))  # Remove duplicates

    except Exception as e:
        logger.error(f"Failed to load watchlist from {file_path}: {e}")
        return []


def get_positions_from_file(file_path: str) -> List[Dict]:
    """
    Load positions from CSV file

    Required columns:
        symbol, mode, entry, stop, target, timeframe
    Optional columns:
        entry_date  (YYYY-MM-DD; blank for legacy rows)

    Example:
        symbol,mode,entry,entry_date,stop,target,timeframe
        AAPL,swing,185.50,2026-02-18,180.00,195.00,1 day
        NVDA,daytrade,520.30,,515.00,530.00,15 mins
    """
    positions = []
    try:
        with open(file_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    positions.append({
                        'symbol': row['symbol'].strip(),
                        'mode': row['mode'].strip(),
                        'entry': float(row['entry']),
                        'entry_date': row.get('entry_date', '').strip(),
                        'stop': float(row['stop']),
                        'target': float(row['target']),
                        'timeframe': row['timeframe'].strip(),
                        # quality: default PREMIUM for legacy rows that predate this column
                        'quality': (row.get('quality', '') or 'PREMIUM').strip().upper(),
                    })
                except (KeyError, ValueError) as e:
                    logger.warning(f"Skip invalid row in {file_path}: {row} ({e})")

    except FileNotFoundError:
        logger.error(f"Positions file not found: {file_path}")
    except Exception as e:
        logger.error(f"Failed to load positions from {file_path}: {e}")

    return positions


def append_signals_to_positions(signals: List[Dict], positions_file: str,
                                mode: str, min_quality: str = 'PREMIUM') -> int:
    """
    Auto-append qualifying signals to a positions CSV file.

    - Filters by min_quality (default: PREMIUM only)
    - Deduplicates: skips symbols already in the file
    - Creates file with headers if it doesn't exist
    - Returns number of new positions appended
    """
    from config import MODES

    quality_rank = {'GOLD': 4, 'PREMIUM': 3, 'HIGH': 2, 'STANDARD': 1, 'REJECT': 0}
    min_rank = quality_rank.get(min_quality, 3)

    timeframe = MODES.get(mode, {}).get('default_timeframe', '1 day')

    # Load existing symbols to avoid duplicates
    existing_symbols = set()
    file_exists = os.path.exists(positions_file)
    if file_exists:
        for pos in get_positions_from_file(positions_file):
            existing_symbols.add(pos['symbol'].upper())

    # Detect if existing file already has a 'quality' column
    has_quality_col = False
    if file_exists:
        with open(positions_file, 'r') as _f:
            _hdr = csv.DictReader(_f)
            has_quality_col = 'quality' in (_hdr.fieldnames or [])

    # Filter and convert signals
    new_rows = []
    for sig in signals:
        quality = sig.get('Quality', 'REJECT')
        if quality_rank.get(quality, 0) < min_rank:
            continue
        symbol = (sig.get('Symbol') or sig.get('symbol', '')).strip()
        if not symbol or symbol.upper() in existing_symbols:
            continue
        entry_price = sig.get('Price', 0)
        new_rows.append({
            'symbol': symbol,
            'mode': mode,
            'entry': entry_price,
            'entry_date': datetime.now(_NY_TZ).strftime('%Y-%m-%d'),
            'stop': round(entry_price * 0.99, 2),  # 1% below breakout
            'target': sig.get('Target', 0),
            'timeframe': timeframe,
            'quality': quality,
        })
        existing_symbols.add(symbol.upper())

    if not new_rows:
        logger.info(f"No new {min_quality}+ signals to append to {positions_file}")
        return 0

    # Include quality column for new files or files that already have it
    fieldnames = ['symbol', 'mode', 'entry', 'entry_date', 'stop', 'target', 'timeframe']
    if not file_exists or has_quality_col:
        fieldnames.append('quality')

    write_header = not file_exists
    with open(positions_file, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)

    symbols = [r['symbol'] for r in new_rows]
    logger.info(f"Appended {len(new_rows)} {min_quality}+ positions to {positions_file}: {', '.join(symbols)}")
    return len(new_rows)


def update_position_stops(positions_file: str, price_map: Dict[str, float]) -> List[Dict]:
    """
    Trail stops upward: for each position where current price > entry,
    set stop = max(current_stop, current_price * 0.99).
    Rewrites the positions file with updated stops.

    Args:
        positions_file: path to positions CSV
        price_map: {symbol: current_price} dict

    Returns:
        list of dicts for positions whose stops were updated
    """
    positions = get_positions_from_file(positions_file)
    if not positions:
        return []

    updated = []
    for pos in positions:
        symbol = pos['symbol']
        current_price = price_map.get(symbol)
        if current_price is None:
            continue

        new_trailing_stop = round(current_price * 0.99, 2)
        old_stop = pos['stop']

        # Only ratchet stop UP, never down
        if new_trailing_stop > old_stop:
            pos['stop'] = new_trailing_stop
            updated.append({
                'symbol': symbol,
                'old_stop': old_stop,
                'new_stop': new_trailing_stop,
                'price': current_price,
            })

    if updated:
        # Rewrite the entire file with updated stops
        # Preserve 'quality' column if it exists in the data
        base_fields = ['symbol', 'mode', 'entry', 'entry_date', 'stop', 'target', 'timeframe']
        has_quality = any('quality' in pos for pos in positions)
        fieldnames = base_fields + (['quality'] if has_quality else [])
        with open(positions_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(positions)

        symbols = [u['symbol'] for u in updated]
        logger.info(f"Trailing stop updated for {len(updated)} positions in {positions_file}: {', '.join(symbols)}")

    return updated


def classify_market_regime(spy_perf: float, spy_vol: float) -> str:
    """
    Classify market regime based on SPY performance and volatility.

    Returns: RED_MARKET | BEARISH | CHOPPY | EXPANSION | NORMAL

    V9-H thresholds (15-day SPY return, proven in 2022/2023/2024 backtest):
      RED_MARKET : spy_perf <= -1.5%  (SPY in strong downtrend — keep trading, +55.8% P&L share)
      BEARISH    : spy_perf <= -0.5%  (mild pullback — block BOUNCE/SMA20_CROSS, 22.2% WR)
      CHOPPY     : |spy_perf| < 0.5% AND spy_vol < 0.35%
      EXPANSION  : spy_perf >= +2.0%
      NORMAL     : everything else
    """
    from config import V9H_REGIME_GATE

    if spy_perf <= V9H_REGIME_GATE['red_market_thresh']:
        return 'RED_MARKET'

    if spy_perf <= V9H_REGIME_GATE['bearish_thresh']:
        return 'BEARISH'

    if (abs(spy_perf) < V9H_REGIME_GATE['choppy_perf_abs'] and
            spy_vol < V9H_REGIME_GATE['choppy_vol']):
        return 'CHOPPY'

    if spy_perf >= V9H_REGIME_GATE['expansion_thresh']:
        return 'EXPANSION'

    return 'NORMAL'


def get_smoothed_regime(raw_regime: str) -> tuple:
    """
    Apply HMM-like temporal smoothing to regime classification.

    Requires N consecutive scans confirming a new regime before switching.
    RED_MARKET transitions are always immediate (safety-first).

    State is persisted to ``scanner_output/.regime_state.json`` between
    invocations so that the persistence counter and cooldown timestamp
    survive across separate CLI runs and cron jobs.

    Args:
        raw_regime: Instantaneous regime from classify_market_regime()
            (RED_MARKET | BEARISH | CHOPPY | EXPANSION | NORMAL).

    Returns:
        (effective_regime, debug_info) where debug_info contains:
            raw            – the raw_regime passed in
            effective      – the smoothed regime actually used
            pending        – regime awaiting confirmation (or None)
            count          – consecutive scans confirming pending regime
            threshold      – scans required to confirm (from config)
            regime_changed – True if effective regime changed this scan
            last_regime_change – ISO timestamp of most recent confirmed change
    """
    import json
    from pathlib import Path
    from datetime import datetime
    from config import V9H_REGIME_GATE, OUTPUT_DIR

    threshold = V9H_REGIME_GATE.get('persistence_threshold', 2)
    state_file = Path(OUTPUT_DIR) / '.regime_state.json'

    # Load persisted state
    state = {
        'current_regime': 'NORMAL',
        'pending_regime': None,
        'pending_count': 0,
        'history': [],
    }
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass  # corrupt file — reset to defaults

    current = state['current_regime']
    history = state.get('history', [])

    # Disabled or threshold=0: pass through raw regime
    if threshold <= 0:
        effective = raw_regime
    # RED_MARKET: immediate transition (protective — don't delay)
    elif raw_regime == 'RED_MARKET':
        effective = 'RED_MARKET'
    # Raw matches current: no change, reset any pending
    elif raw_regime == current:
        state['pending_regime'] = None
        state['pending_count'] = 0
        effective = current
    # Raw matches pending: increment counter toward confirmation
    elif raw_regime == state.get('pending_regime'):
        state['pending_count'] += 1
        if state['pending_count'] >= threshold:
            effective = raw_regime
            state['pending_regime'] = None
            state['pending_count'] = 0
        else:
            effective = current
    # New pending regime: start counting
    else:
        state['pending_regime'] = raw_regime
        state['pending_count'] = 1
        effective = current

    # Detect regime change
    regime_changed = (effective != current)
    if regime_changed:
        state['last_regime_change'] = datetime.now().isoformat()
        state['previous_regime'] = current

    # Update state
    state['current_regime'] = effective
    state['last_updated'] = datetime.now().isoformat()
    history.append(raw_regime)
    state['history'] = history[-10:]  # keep last 10 for debugging

    # Persist
    try:
        state_file.write_text(json.dumps(state, indent=2))
    except OSError:
        pass  # non-critical — next scan will reset

    debug_info = {
        'raw': raw_regime,
        'effective': effective,
        'pending': state.get('pending_regime'),
        'count': state.get('pending_count', 0),
        'threshold': threshold,
        'regime_changed': regime_changed,
        'last_regime_change': state.get('last_regime_change'),
    }
    return effective, debug_info


def check_regime_cooldown(cooldown_hours: float) -> tuple:
    """
    Check if a post-regime-change cooldown is currently active.

    After a confirmed regime transition (via get_smoothed_regime), the
    cooldown window suppresses non-exempt signals to prevent whipsaw
    re-entries during regime instability.  This implements the "signal
    hysteresis" concept used in institutional HMM systems.

    Reads ``last_regime_change`` from ``.regime_state.json`` and compares
    against the cooldown window.

    Args:
        cooldown_hours: Duration of cooldown window in hours.
            0 or negative disables the cooldown entirely.

    Returns:
        (is_active, hours_remaining) — is_active is True when inside
        the cooldown window; hours_remaining is the time left (>0).
    """
    import json
    from pathlib import Path
    from datetime import datetime
    from config import OUTPUT_DIR

    if cooldown_hours <= 0:
        return False, 0.0

    state_file = Path(OUTPUT_DIR) / '.regime_state.json'
    if not state_file.exists():
        return False, 0.0

    try:
        state = json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return False, 0.0

    last_change = state.get('last_regime_change')
    if not last_change:
        return False, 0.0

    last_change_dt = datetime.fromisoformat(last_change)
    elapsed_hours = (datetime.now() - last_change_dt).total_seconds() / 3600
    remaining = cooldown_hours - elapsed_hours

    if remaining > 0:
        return True, remaining
    return False, 0.0


def setup_logging(log_file: str = None, debug: bool = False):
    """
    Setup logging configuration with output to nested folder
    """
    from datetime import datetime
    from pathlib import Path
    from config import OUTPUT_DIR

    # Ensure logs directory exists
    log_dir = Path(OUTPUT_DIR, 'logs')
    log_dir.mkdir(parents=True, exist_ok=True)

    if log_file is None:
        log_file = log_dir / f'scanner_{datetime.now():%Y%m%d}.log'

    log_level = SCAN_LEVEL if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s | %(levelname)s | %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

    # Silence noisy library loggers regardless of --debug
    for lib in _QUIET_LIBS + ['ib_insync']:
        logging.getLogger(lib).setLevel(logging.WARNING)
