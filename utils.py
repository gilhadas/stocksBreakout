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
import tempfile
import threading

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

def _load_secrets_toml() -> dict:
    """Read .streamlit/secrets.toml directly (for cron/CLI outside Streamlit).

    Returns an empty dict if the file doesn't exist or can't be parsed.
    """
    secrets_path = PROJECT_ROOT / '.streamlit' / 'secrets.toml'
    if not secrets_path.exists():
        return {}
    try:
        import toml
        return toml.loads(secrets_path.read_text())
    except Exception:
        try:
            # Fallback: simple TOML key=value parser (no toml package needed)
            result: dict = {}
            for line in secrets_path.read_text().splitlines():
                line = line.strip()
                if '=' in line and not line.startswith('[') and not line.startswith('#'):
                    k, _, v = line.partition('=')
                    result[k.strip()] = v.strip().strip('"').strip("'")
            return result
        except Exception:
            return {}


def _is_cloud() -> bool:
    """Return True when S3 storage should be used.

    Detects any of:
      - Streamlit Cloud deployment path (/mount/src/)
      - [connections.s3] in Streamlit secrets
      - Flat AWS keys in Streamlit secrets (AWS_ACCESS_KEY_ID, etc.)
      - AWS_ACCESS_KEY_ID in environment (EC2/ECS/Lambda/CI)
      - AWS keys in .streamlit/secrets.toml (cron/CLI on local machine)
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
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        return True
    # Outside Streamlit (cron/CLI): check secrets.toml directly
    _toml = _load_secrets_toml()
    return bool(
        _toml.get("AWS_ACCESS_KEY_ID") or _toml.get("aws_access_key_id")
        or (_toml.get("connections", {}) or {})
    )


def _in_streamlit() -> bool:
    """Return True when running inside an active Streamlit session."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False


def _s3_conn():
    """Get Streamlit S3 connection (official recommended approach)."""
    import streamlit as st
    from st_files_connection import FilesConnection
    return st.connection('s3', type=FilesConnection)


# One filesystem per credential set, for the lifetime of the process.
#
# WHY: constructing an S3FileSystem is cheap, but the first operation on each new
# instance builds a fresh aiobotocore/botocore client, which JSON-parses the S3
# service model and never releases it — a measured **12.8 MB per call, perfectly
# linear** (CLAUDE.md §19). Reusing one instance costs 12.8 MB *once* and 0.0 MB
# thereafter. That per-call term is what made the §18 archive replay fatal: 858
# files ≈ 11 GB of demand, so the process filled whatever mem_limit it was given
# and every recorded "peak" was really the cap.
#
# `skip_instance_cache=True` is preserved deliberately — it is the 4007cd0
# workaround for the AioSession kwarg bug in s3fs ≥2025 + aiobotocore ≥3.x. We
# memoize *around* fsspec's instance cache rather than re-enabling it.
_S3_FS_CACHE: dict = {}
_S3_FS_LOCK = threading.Lock()


def _s3_credentials() -> tuple:
    """(key, secret, region) from env, else secrets.toml. Also the cache key,
    so rotated credentials naturally produce a new filesystem."""
    _toml = _load_secrets_toml()
    _conn = _toml.get('connections', {}) or {}
    key    = os.environ.get('AWS_ACCESS_KEY_ID')    or _toml.get('AWS_ACCESS_KEY_ID')    or _toml.get('aws_access_key_id')    or _conn.get('key')
    secret = os.environ.get('AWS_SECRET_ACCESS_KEY') or _toml.get('AWS_SECRET_ACCESS_KEY') or _toml.get('aws_secret_access_key') or _conn.get('secret')
    region = os.environ.get('AWS_DEFAULT_REGION')    or _toml.get('AWS_DEFAULT_REGION')    or _conn.get('region', 'eu-central-1')
    return key, secret, region


def _s3_fs():
    """Return the shared s3fs filesystem for the current credentials.

    - Inside a Streamlit session: goes through st.connection (caches credentials).
    - Outside Streamlit (cron/CLI): one memoized S3FileSystem per credential set.
    """
    if _in_streamlit():
        return _s3_conn().fs
    import s3fs
    creds = _s3_credentials()
    with _S3_FS_LOCK:
        fs = _S3_FS_CACHE.get(creds)
        if fs is None:
            key, secret, region = creds
            fs = s3fs.S3FileSystem(
                key=key, secret=secret,
                client_kwargs={'region_name': region},
                skip_instance_cache=True,
            )
            _S3_FS_CACHE[creds] = fs
        return fs


def _drop_s3_fs() -> None:
    """Discard the memoized filesystem so the next call builds a fresh one."""
    with _S3_FS_LOCK:
        _S3_FS_CACHE.clear()


def _s3_call(op):
    """Run ``op(fs)`` on the shared filesystem, self-healing on failure.

    A memoized filesystem holds a connection pool for the life of the process,
    and `sb-api` runs for days — the one failure mode reuse introduces is a pool
    that has gone stale while idle. So on *any* error we discard the instance,
    rebuild, and retry exactly once; a second failure propagates to the caller,
    whose existing handler falls back to local disk.

    Every operation passed here is a whole-object read, a whole-object
    overwrite, or a listing — all idempotent, so the retry is safe.
    """
    try:
        return op(_s3_fs())
    except Exception as first:
        logger.debug(f"S3 op failed on the cached filesystem ({first}); "
                     f"rebuilding and retrying once")
        _drop_s3_fs()
        return op(_s3_fs())


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
            def _read(fs):
                with fs.open(s3_path, 'r') as f:
                    return pd.read_csv(f)
            return _s3_call(_read)
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
            def _write(fs):
                with fs.open(s3_path, 'w') as f:
                    df.to_csv(f, index=False)
                fs.invalidate_cache(s3_path)
            _s3_call(_write)
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
            def _read(fs):
                with fs.open(s3_path, 'r') as f:
                    return f.read().strip()
            return _s3_call(_read)
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
            def _write(fs):
                with fs.open(s3_path, 'w') as f:
                    f.write(content)
                fs.invalidate_cache(s3_path)
            _s3_call(_write)
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
            def _read(fs):
                with fs.open(s3_path, 'r') as f:
                    return json.loads(f.read())
            return _s3_call(_read)
        except Exception as e:
            logger.warning(f"S3 JSON read failed for {s3_path}: {e}")

    abs_path = _to_local_abs(local_path)
    if os.path.exists(abs_path):
        with open(abs_path) as f:
            return json.load(f)

    logger.warning(f"JSON file not found: {abs_path}")
    return None


def save_json(data, local_path: str, s3_path: str = None):
    """Save JSON file — always writes locally; also writes to S3 when cloud creds present.

    After writing, invalidates the s3fs cache so subsequent reads are fresh.
    This ensures Reset / Scan buttons take effect immediately without any TTL delay.
    """
    if s3_path is None:
        s3_path = _to_s3_key(local_path)

    content = json.dumps(data, indent=2, default=str)

    # Always write locally so the local filesystem stays in sync with S3.
    # Write to a sibling temp file and os.replace() it into place — `open(...,
    # 'w')` truncates before writing a byte, so a process killed mid-write
    # (OOM, container restart — this box has taken 13+ OOM kills, §17-§21)
    # left the book at 0 bytes or a half-written object. os.replace() is
    # atomic on POSIX: a reader always sees either the complete old file or
    # the complete new one. The temp file lives next to the target so the
    # replace stays on one filesystem (no cross-device rename failure).
    abs_path = _to_local_abs(local_path)
    abs_dir = os.path.dirname(abs_path) or '.'
    os.makedirs(abs_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=abs_dir, prefix='.tmp-', suffix='.json')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, abs_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    if _is_cloud():
        try:
            def _write(fs):
                with fs.open(s3_path, 'w') as f:
                    f.write(content)
                fs.invalidate_cache(s3_path)
            _s3_call(_write)
        except Exception as e:
            logger.warning(f"S3 JSON write failed for {s3_path}: {e}")


def delete_file(local_path: str, s3_path: str = None) -> bool:
    """Delete a file locally and on S3. Returns True if either side removed it.

    Idempotent by design: a path that does not exist is a success, not an error.
    That matters for the retry inside ``_s3_call`` — the existence check lives
    *inside* the op, so a rebuild-and-retry cannot turn "already gone" into a
    raised FileNotFoundError. It also makes the caller safe to re-run after a
    partial failure, which is the whole point of archive-then-delete flows.

    Unlike ``save_json``, an S3 failure is NOT swallowed. Callers delete only
    after verifying a copy exists elsewhere, so a silent failure here would
    orphan the original — the exact bug this helper exists to prevent.
    """
    if s3_path is None:
        s3_path = _to_s3_key(local_path)

    removed = False

    abs_path = _to_local_abs(local_path)
    if os.path.exists(abs_path):
        os.remove(abs_path)
        removed = True

    if _is_cloud():
        def _rm(fs):
            fs.invalidate_cache(s3_path)
            if fs.exists(s3_path):
                fs.rm(s3_path)
                fs.invalidate_cache(s3_path)
                return True
            return False
        removed = _s3_call(_rm) or removed

    return removed


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

            def _ls(fs):
                # Listings are the one thing fsspec caches across calls, so a
                # reused filesystem must invalidate before every ls. This call
                # predates the memoization and is exactly what makes it safe.
                fs.invalidate_cache(s3_prefix)
                return fs.ls(s3_prefix, detail=False)
            all_keys = _s3_call(_ls)
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


def classify_market_regime(spy_perf: float, spy_vol: float,
                           surge_context: dict | None = None) -> str:
    """
    Classify market regime based on SPY performance and volatility.

    Returns: SURGE | RED_MARKET | BEARISH | CHOPPY | EXPANSION | NORMAL

    SURGE (highest priority): broad market gap-up day detected from premarket data.
      Requires SPY gap >= threshold AND breadth (gapper count) >= threshold.

    V9-H thresholds (15-day SPY return, proven in 2022/2023/2024 backtest):
      RED_MARKET : spy_perf <= -1.5%  (SPY in strong downtrend — keep trading, +55.8% P&L share)
      BEARISH    : spy_perf <= -0.5%  (mild pullback — block BOUNCE/SMA20_CROSS, 22.2% WR)
      CHOPPY     : |spy_perf| < 0.5% AND spy_vol < 0.35%
      EXPANSION  : spy_perf >= +2.0%
      NORMAL     : everything else
    """
    from config import V9H_REGIME_GATE, SURGE_DAY_CONFIG

    # SURGE takes highest priority — broad market gap-up day
    if SURGE_DAY_CONFIG.get('enabled') and surge_context:
        spy_gap = surge_context.get('spy_gap_pct', 0)
        # None means "breadth was never measured" (premarket scan didn't run,
        # or its file is missing/stale) — a genuinely-measured zero gappers is
        # a real int 0, not None. These must not be treated the same: a calm
        # breadth reading (0 gappers) on a day SPY still gapped hard is
        # evidence AGAINST a broad surge, while a missing reading is simply
        # unknown. Writers must emit None, never 0, to mean "no data."
        breadth = surge_context.get('num_gappers')
        # Pre-market path: SPY gap + breadth confirmation (breadth must be a
        # real measurement)
        if (breadth is not None
                and spy_gap >= SURGE_DAY_CONFIG['spy_gap_min_pct']
                and breadth >= SURGE_DAY_CONFIG['breadth_min_gappers']):
            return 'SURGE'
        # Intraday fallback: SPY moved strongly from open AND breadth is
        # genuinely unavailable (not just measured-and-zero)
        intraday_thresh = SURGE_DAY_CONFIG.get('spy_intraday_fallback_pct', 1.5)
        if spy_gap >= intraday_thresh and breadth is None:
            return 'SURGE'

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


def filter_signals_by_regime(
    signals: list[dict],
    regime: str | None,
    v9h_enabled: bool,
) -> tuple[list[dict], int]:
    """Filter signals by market regime — single source of truth.

    Called by both portfolio auto-add and notification paths in
    breakout_scanner.py.  Keeps regime logic in one place instead of
    duplicating it across layers.

    Rules
    -----
    * V9-C (v9h_enabled=False):
      - BOUNCE requires GOLD quality (universal).
      - No regime blocking — all regimes trade normally.
    * V9-H (v9h_enabled=True):

      +--------------+----------------------------------------------+
      | Regime       | Allowed signals                              |
      +--------------+----------------------------------------------+
      | RED_MARKET   | CONTINUATION / Momentum — GOLD only          |
      | CHOPPY       | CONTINUATION always; BOUNCE/Momentum GOLD    |
      | BEARISH      | CONTINUATION/Momentum always; BOUNCE GOLD;   |
      |              | SMA20_CROSS blocked                          |
      | NORMAL /     | Everything; BOUNCE requires GOLD             |
      | EXPANSION    |                                              |
      +--------------+----------------------------------------------+

    Returns (filtered_signals, blocked_count).
    """
    if not signals:
        return [], 0

    def _bounce_gold_only(s: dict) -> bool:
        """Universal rule: BOUNCE requires GOLD quality."""
        return s.get('Type') != 'BOUNCE' or s.get('Quality') == 'GOLD'

    if not v9h_enabled:
        # V9-C: BOUNCE requires GOLD quality (universal rule only, no regime blocking)
        filtered = [s for s in signals if _bounce_gold_only(s)]
        return filtered, len(signals) - len(filtered)

    # V9-H: regime-aware filtering
    if regime == 'RED_MARKET':
        filtered = [
            s for s in signals
            if s.get('Type') in ('CONTINUATION', 'Momentum')
            and s.get('Quality') == 'GOLD'
        ]
    elif regime == 'CHOPPY':
        filtered = [
            s for s in signals
            if s.get('Type') == 'CONTINUATION'
            or (s.get('Type') in ('BOUNCE', 'Momentum')
                and s.get('Quality') == 'GOLD')
        ]
    elif regime == 'BEARISH':
        filtered = [
            s for s in signals
            if s.get('Type') in ('CONTINUATION', 'Momentum')
            or (s.get('Type') == 'BOUNCE' and s.get('Quality') == 'GOLD')
        ]
    else:
        # NORMAL / EXPANSION / SURGE / None — only BOUNCE-GOLD universal rule
        filtered = [s for s in signals if _bounce_gold_only(s)]

    return filtered, len(signals) - len(filtered)


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
        state['last_regime_change'] = datetime.now(_NY_TZ).isoformat()
        state['previous_regime'] = current

    # Update state
    state['current_regime'] = effective
    state['last_updated'] = datetime.now(_NY_TZ).isoformat()
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
    # Ensure both sides are timezone-aware (NY) for correct comparison
    now = datetime.now(_NY_TZ)
    if last_change_dt.tzinfo is None:
        last_change_dt = last_change_dt.replace(tzinfo=_NY_TZ)
    elapsed_hours = (now - last_change_dt).total_seconds() / 3600
    remaining = cooldown_hours - elapsed_hours

    if remaining > 0:
        return True, remaining
    return False, 0.0


def drop_incomplete_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows whose OHLC is not fully populated.

    yfinance emits a **trailing placeholder bar** for the most recent period
    with NaN Open/High/Low/Close — often with Volume already filled in, so an
    ``.empty`` or Volume-based check does not catch it. Measured 2026-08-04:
    every symbol tested, SPY included, carried exactly one such row at the end
    of a ``period='1y'`` fetch.

    That row breaks two things downstream:

    1. ``json.dumps`` serialises NaN as a bare ``NaN`` token, which is not
       valid JSON — the browser throws ``SyntaxError: Unexpected token 'N'``
       and the chart never renders (issue #4).
    2. ``int(row['Volume'])`` raises ``ValueError`` when Volume is NaN too.

    Applied at fetch time so every consumer of the frame is covered, rather
    than at each render site. Distinct from :func:`close_basis_history`, which
    drops a *complete but still-forming* bar for a different reason (its Close
    is a live price, not a close); this one drops bars that have no data at all.
    """
    if df is None or df.empty:
        return df
    cols = [c for c in ('Open', 'High', 'Low', 'Close') if c in df.columns]
    if not cols:
        return df
    return df.dropna(subset=cols)


def close_basis_history(hist, now_et) -> Optional[pd.DataFrame]:
    """
    Trim a daily OHLCV history to the basis usable for close-based decisions.

    A daily bar fetched mid-session (yfinance ``ticker.history()``, or the IB/
    yfinance blend behind ``MarketDataHandler.get_historical_data``) includes
    today's still-forming bar, whose Close is really the live/intraday price
    — not an actual close. Deciding a close-based rule (a trend/stop check
    that intraday noise must not trigger) off that row makes the check
    low-based instead of close-based whenever it runs intraday.

    Rule: drop today's partial bar unless we're in the late window (>= 15:30
    ET, where the near-close price is a fair proxy for today's close) or the
    session is over (>= 16:00 ET, bar is final).

    Shared by ``auto_portfolio.refresh_prices`` (ATR-trail stop/trail) and
    ``orchestrator.evaluate_exits`` (rule-based ``ExitEvaluator`` checks,
    e.g. "Trend broken"). Only meaningful for daily bars — callers must not
    apply this to intraday timeframes.
    """
    if hist is None or hist.empty:
        return hist
    last_ts = hist.index[-1]
    last_date = last_ts.date() if hasattr(last_ts, 'date') else last_ts
    in_late_window = (now_et.hour, now_et.minute) >= (15, 30)
    if last_date == now_et.date() and now_et.hour < 16 and not in_late_window:
        return hist.iloc[:-1]
    return hist


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
