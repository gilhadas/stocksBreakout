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


def _s3_fs():
    """Return an s3fs.S3FileSystem using credentials from st.secrets or environment.

    Supports three secret layouts:
      1. [connections.s3]  aws_access_key_id / aws_secret_access_key
      2. Flat keys:        AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
      3. No explicit keys: boto3 falls back to instance profile / env vars
    """
    import s3fs
    key, secret, region = None, None, 'us-east-1'
    try:
        import streamlit as st
        s = st.secrets
        # Layout 1: Streamlit Connections format
        if "connections" in s and "s3" in s.connections:
            c = s.connections.s3
            key    = c.get('aws_access_key_id') or c.get('key')
            secret = c.get('aws_secret_access_key') or c.get('secret')
            region = c.get('region_name') or c.get('region', 'us-east-1')
        # Layout 2: flat AWS keys in secrets
        elif any(k in s for k in ("AWS_ACCESS_KEY_ID", "aws_access_key_id")):
            key    = s.get('AWS_ACCESS_KEY_ID') or s.get('aws_access_key_id')
            secret = s.get('AWS_SECRET_ACCESS_KEY') or s.get('aws_secret_access_key')
            region = s.get('AWS_DEFAULT_REGION', 'us-east-1')
    except Exception:
        pass

    if key and secret:
        return s3fs.S3FileSystem(key=key, secret=secret,
                                  client_kwargs={'region_name': region})
    # Layout 3: let boto3 pick up credentials from environment / instance role
    return s3fs.S3FileSystem()


# ─── CSV ────────────────────────────────────────────────────────────────────

def load_data(local_path: str, s3_path: str = None) -> Optional[pd.DataFrame]:
    """Load CSV as DataFrame — tries S3 first, falls back to local.

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
            with fs.open(s3_path, 'rb') as f:
                return pd.read_csv(f)
        except Exception as e:
            logger.warning(f"S3 read failed for {s3_path}, falling back to local: {e}")

    abs_path = _to_local_abs(local_path)
    if os.path.exists(abs_path):
        return pd.read_csv(abs_path)

    logger.warning(f"File not found: {abs_path}")
    return None


def save_data(df: pd.DataFrame, local_path: str, s3_path: str = None):
    """Save DataFrame as CSV — writes to S3 if cloud, else local.

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
            return
        except Exception as e:
            logger.warning(f"S3 write failed for {s3_path}, falling back to local: {e}")

    abs_path = _to_local_abs(local_path)
    os.makedirs(os.path.dirname(abs_path) or '.', exist_ok=True)
    df.to_csv(abs_path, index=False)


# ─── Text (TXT, watchlists) ────────────────────────────────────────────────

def load_text(local_path: str, s3_path: str = None) -> Optional[str]:
    """Load text file — tries S3 first, falls back to local."""
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
    """Save text file — writes to S3 if cloud, else local."""
    if s3_path is None:
        s3_path = _to_s3_key(local_path)

    if _is_cloud():
        try:
            fs = _s3_fs()
            with fs.open(s3_path, 'w') as f:
                f.write(content)
            return
        except Exception as e:
            logger.warning(f"S3 text write failed for {s3_path}: {e}")

    abs_path = _to_local_abs(local_path)
    os.makedirs(os.path.dirname(abs_path) or '.', exist_ok=True)
    Path(abs_path).write_text(content)


# ─── JSON ───────────────────────────────────────────────────────────────────

def load_json(local_path: str, s3_path: str = None):
    """Load JSON file — tries S3 first, falls back to local."""
    if s3_path is None:
        s3_path = _to_s3_key(local_path)

    if _is_cloud():
        try:
            fs = _s3_fs()
            with fs.open(s3_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"S3 JSON read failed for {s3_path}: {e}")

    abs_path = _to_local_abs(local_path)
    if os.path.exists(abs_path):
        with open(abs_path) as f:
            return json.load(f)

    logger.warning(f"JSON file not found: {abs_path}")
    return None


def save_json(data, local_path: str, s3_path: str = None):
    """Save JSON file — writes to S3 if cloud, else local."""
    if s3_path is None:
        s3_path = _to_s3_key(local_path)

    content = json.dumps(data, indent=2, default=str)

    if _is_cloud():
        try:
            fs = _s3_fs()
            with fs.open(s3_path, 'w') as f:
                f.write(content)
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
            fs = _s3_fs()
            all_keys = fs.ls(s3_prefix, detail=False)
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
    Load watchlist from file

    Format:
        AAPL, MSFT, GOOGL
        ### Comments start with ###
        TSLA
    """
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
        })
        existing_symbols.add(symbol.upper())

    if not new_rows:
        logger.info(f"No new {min_quality}+ signals to append to {positions_file}")
        return 0

    write_header = not file_exists
    with open(positions_file, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['symbol', 'mode', 'entry', 'entry_date', 'stop', 'target', 'timeframe'])
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
        with open(positions_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['symbol', 'mode', 'entry', 'entry_date', 'stop', 'target', 'timeframe'])
            writer.writeheader()
            writer.writerows(positions)

        symbols = [u['symbol'] for u in updated]
        logger.info(f"Trailing stop updated for {len(updated)} positions in {positions_file}: {', '.join(symbols)}")

    return updated


def classify_market_regime(spy_perf: float, spy_vol: float) -> str:
    """
    Classify market regime based on SPY performance and volatility

    Returns: CHOPPY | EXPANSION | NORMAL
    """
    from config import REGIME_CONFIG

    if abs(spy_perf) < REGIME_CONFIG['CHOPPY']['spy_perf_threshold'] and \
       spy_vol < REGIME_CONFIG['CHOPPY']['spy_vol_threshold']:
        return 'CHOPPY'

    if abs(spy_perf) > REGIME_CONFIG['EXPANSION']['spy_perf_threshold'] and \
       spy_vol > REGIME_CONFIG['EXPANSION']['spy_vol_threshold']:
        return 'EXPANSION'

    return 'NORMAL'


def setup_logging(log_file: str = None):
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

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

    # Reduce ib_insync verbosity
    logging.getLogger('ib_insync').setLevel(logging.WARNING)
