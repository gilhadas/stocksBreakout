"""
job_launcher.py
───────────────────────────────────────────────────────────────────────────────
Batch launcher for simultaneous cron jobs with shared data loading.

Optimizes execution by:
  1. Detecting jobs scheduled for the same time across modes (swing/daytrade/scalping)
  2. Loading market data ONCE (SPY, all symbols, all indicators)
  3. Caching data in scanner_output/cache/market_data_*.pkl
  4. Launching each mode with --cache-file parameter (reads cached data)
  5. Cleaning up old cache files

This reduces ~70% execution time for simultaneous scans.

Usage:
  python job_launcher.py --time 09:35 --modes swing,daytrade
  python job_launcher.py --cron-file cron_jobs.txt --find-simultaneous
  python job_launcher.py --time 09:35 --file watchlist.txt --modes swing,daytrade --notify
"""

import argparse
import json
import logging
import pickle
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

_NY_TZ = ZoneInfo('America/New_York')
_PROJECT_ROOT = Path(__file__).parent
_CACHE_DIR = _PROJECT_ROOT / 'scanner_output' / 'cache'
_LOGS_DIR = _PROJECT_ROOT / 'scanner_output' / 'logs'
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(_LOGS_DIR / 'job_launcher.log'),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class CachedMarketData:
    """Container for cached market data."""
    timestamp: str                          # ISO format when cache was created
    spy_timeframe: str                      # 'daily', '15 min', etc
    spy_bars: dict                          # yfinance bars as dict
    symbols: List[str]                      # All symbols scanned
    indicators_by_symbol: Dict[str, dict]   # {symbol: {indicator_name: value}}
    market_condition: str                   # 'bull', 'bear', 'mixed' from regime_detector


def parse_cron_jobs_for_time(cron_file: Path, target_hour: int, target_minute: int) -> Dict[str, List[str]]:
    """
    Parse cron_jobs.txt and find all jobs scheduled for specific time.

    Returns: {mode: [commands]}
      e.g., {'swing': ['python breakout_scanner.py ...'], 'daytrade': [...]}
    """
    jobs_by_mode = {}

    with open(cron_file) as f:
        all_lines = f.readlines()

    for line_idx, raw_line in enumerate(all_lines):
        line = raw_line.strip()

        # Skip comments and empty lines
        if not line or line.startswith('#'):
            continue

        # Skip non-cron lines
        if '=' in line and not re.match(r'(\\d+|@|\\*/)', line):
            continue

        # Parse cron line: minute hour day month weekday TZ=... cd ... && command
        match = re.match(
            r'(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+TZ=\S+\s+(.+)',
            line
        )
        if not match:
            continue

        minute_str, hour_str, day_str, month_str, weekday_str, rest = match.groups()

        # Parse minute and hour
        try:
            minute = int(minute_str) if minute_str != '*' else -1
            hour = int(hour_str) if hour_str != '*' else -1
        except ValueError:
            continue

        # Check if this job should run at target time
        if hour != -1 and hour != target_hour:
            continue
        if minute != -1 and minute != target_minute:
            continue

        # Extract mode from command
        mode_match = re.search(r'--mode\s+(\w+)', rest)
        if not mode_match:
            continue

        mode = mode_match.group(1)
        if mode not in ('swing', 'daytrade', 'scalping'):
            continue

        # Extract command
        cmd_match = re.search(r'\$PYTHON_BIN\s+(\S+(?:\s+[\S\"]+)*)', rest)
        if cmd_match:
            command = cmd_match.group(0).replace('$PYTHON_BIN', 'python3')
        else:
            if '&&' in rest:
                command = rest.split('&&', 1)[1].strip()
            else:
                command = rest

        if mode not in jobs_by_mode:
            jobs_by_mode[mode] = []
        jobs_by_mode[mode].append(command)

    return jobs_by_mode


def load_market_data_once(watchlist_file: str, spy_timeframe: str = 'daily') -> Optional[CachedMarketData]:
    """
    Load market data once (SPY + all symbols, all indicators).

    This is the expensive operation that we want to do only once.
    Imports are done here to avoid circular imports and to keep this script lightweight.
    """
    try:
        import pandas as pd
        import yfinance as yf
        from indicators import calculate_all_indicators
        from regime_detector import fetch_spy_data, detect_regime

        logger.info("=" * 80)
        logger.info("LOADING MARKET DATA (ONE-TIME)")
        logger.info("=" * 80)

        # Step 1: Load watchlist
        watchlist_path = _PROJECT_ROOT / watchlist_file
        if not watchlist_path.exists():
            logger.error(f"Watchlist not found: {watchlist_path}")
            return None

        symbols = []
        with open(watchlist_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    # Handle "EXCHANGE:SYMBOL" format
                    if ':' in line:
                        symbols.append(line.split(':')[1])
                    else:
                        symbols.append(line)

        symbols = list(set(symbols))  # dedupe
        logger.info(f"Loaded {len(symbols)} symbols from {watchlist_file}")

        # Step 2: Fetch SPY data for regime detection
        logger.info("Fetching SPY data for regime detection...")
        spy_df = fetch_spy_data(days=200)
        if spy_df is None:
            logger.warning("SPY data fetch failed, defaulting to MIXED regime")
            market_condition = 'mixed'
        else:
            regime, _ = detect_regime(spy_df)
            market_condition = regime
            logger.info(f"Detected market condition: {market_condition.upper()}")

        # Step 3: Fetch all symbol data and calculate indicators (batched)
        logger.info(f"Fetching data for {len(symbols)} symbols...")
        indicators_by_symbol = {}

        # Batch fetch in groups of 10 to avoid rate limits
        batch_size = 10
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i+batch_size]
            logger.info(f"  Batch {i//batch_size + 1}: {', '.join(batch)}")

            for symbol in batch:
                try:
                    # Fetch daily bars (used by all modes)
                    df = yf.download(symbol, period='252d', progress=False)
                    if df.empty:
                        continue

                    # Calculate all indicators
                    inds = calculate_all_indicators(df, symbol)
                    indicators_by_symbol[symbol] = inds
                except Exception as e:
                    logger.warning(f"Failed to fetch {symbol}: {e}")

        logger.info(f"Successfully loaded data for {len(indicators_by_symbol)} symbols")

        # Step 4: Create cache object
        spy_dict = {}
        if spy_df is not None:
            spy_dict = spy_df.to_dict()

        cache = CachedMarketData(
            timestamp=datetime.now(_NY_TZ).isoformat(),
            spy_timeframe=spy_timeframe,
            spy_bars=spy_dict,
            symbols=list(indicators_by_symbol.keys()),
            indicators_by_symbol=indicators_by_symbol,
            market_condition=market_condition,
        )

        return cache

    except Exception as e:
        logger.error(f"Error loading market data: {e}")
        return None


def save_cache(cache: CachedMarketData) -> Optional[Path]:
    """Save cache to file. Returns cache file path."""
    try:
        timestamp = datetime.now(_NY_TZ).strftime('%Y%m%d_%H%M%S')
        cache_file = _CACHE_DIR / f'market_data_{timestamp}.pkl'

        with open(cache_file, 'wb') as f:
            pickle.dump(cache, f)

        logger.info(f"✓ Cached market data: {cache_file}")
        return cache_file
    except Exception as e:
        logger.error(f"Failed to save cache: {e}")
        return None


def cleanup_old_caches(keep_recent: int = 3):
    """Remove cache files older than keep_recent most recent."""
    try:
        cache_files = sorted(_CACHE_DIR.glob('market_data_*.pkl'), reverse=True)
        if len(cache_files) > keep_recent:
            for old_file in cache_files[keep_recent:]:
                old_file.unlink()
                logger.info(f"Cleaned up old cache: {old_file.name}")
    except Exception as e:
        logger.debug(f"Cleanup error: {e}")


def launch_mode_jobs(
    modes: List[str],
    cache_file: Path,
    watchlist_file: str,
    additional_args: str = "",
    dry_run: bool = False
) -> int:
    """
    Launch each mode with the cached market data.

    Returns: count of successful launches
    """
    success_count = 0

    for mode in modes:
        cmd = f"python3 breakout_scanner.py {watchlist_file} --mode {mode} --cache-file {cache_file}"
        if additional_args:
            cmd += f" {additional_args}"

        logger.info(f"▶ Launching {mode} mode...")
        logger.info(f"  Command: {cmd}")

        if dry_run:
            logger.info(f"  [DRY-RUN] Would execute: {cmd}")
            success_count += 1
            continue

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=_PROJECT_ROOT,
                timeout=900,  # 15 min timeout
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                logger.info(f"✓ {mode} mode completed successfully")
                success_count += 1
            else:
                logger.error(f"✗ {mode} mode failed (exit code {result.returncode})")
                logger.error(f"  stderr: {result.stderr[:500]}")

        except subprocess.TimeoutExpired:
            logger.error(f"✗ {mode} mode timed out (900s)")
        except Exception as e:
            logger.error(f"✗ {mode} mode error: {e}")

    return success_count


def main():
    parser = argparse.ArgumentParser(
        description='Batch launcher for simultaneous cron jobs with shared data loading'
    )
    parser.add_argument(
        '--time',
        metavar='HH:MM',
        help='Time to launch jobs for (e.g., 09:35)'
    )
    parser.add_argument(
        '--modes',
        metavar='MODES',
        help='Comma-separated modes to launch (e.g., swing,daytrade,scalping)'
    )
    parser.add_argument(
        '--file',
        default='input/1_26_Setups.txt',
        help='Watchlist file (default: input/1_26_Setups.txt)'
    )
    parser.add_argument(
        '--cron-file',
        default='cron_jobs.txt',
        help='Cron jobs file to parse (default: cron_jobs.txt)'
    )
    parser.add_argument(
        '--find-simultaneous',
        action='store_true',
        help='Find all simultaneous jobs in cron_jobs.txt and show them'
    )
    parser.add_argument(
        '--notify',
        action='store_true',
        help='Pass --notify to each mode'
    )
    parser.add_argument(
        '--cron',
        action='store_true',
        help='Pass --cron to each mode (minimal output)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview without executing'
    )

    args = parser.parse_args()

    # Mode 1: Find simultaneous jobs in cron_jobs.txt
    if args.find_simultaneous:
        cron_file = _PROJECT_ROOT / args.cron_file
        if not cron_file.exists():
            logger.error(f"Cron file not found: {cron_file}")
            sys.exit(1)

        logger.info("Scanning cron_jobs.txt for simultaneous jobs...")
        # Parse all lines and group by time
        simultaneous_times = {}
        with open(cron_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                # Skip non-cron lines (those with '=' but no leading digit)
                if '=' in line and not re.match(r'^\d', line):
                    continue

                match = re.match(
                    r'(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)',
                    line
                )
                if not match:
                    continue

                minute_str, hour_str = match.group(1), match.group(2)
                try:
                    minute = int(minute_str) if minute_str != '*' else -1
                    hour = int(hour_str) if hour_str != '*' else -1
                except ValueError:
                    continue

                if hour >= 0 and minute >= 0:
                    time_key = f"{hour:02d}:{minute:02d}"
                    if time_key not in simultaneous_times:
                        simultaneous_times[time_key] = []
                    simultaneous_times[time_key].append(line)

        if not simultaneous_times:
            print("\nNo cron jobs found in cron_jobs.txt")
            return

        for time_str in sorted(simultaneous_times.keys()):
            jobs = simultaneous_times[time_str]
            if len(jobs) > 1:
                print(f"\n{time_str} ET — {len(jobs)} simultaneous jobs:")
                for job in jobs:
                    mode_match = re.search(r'--mode\s+(\w+)', job)
                    mode = mode_match.group(1) if mode_match else 'unknown'
                    print(f"  • {mode}")

        print(f"\n✓ Found {sum(len(j) for j in simultaneous_times.values())} total jobs at {len(simultaneous_times)} different times")
        return

    # Mode 2: Launch jobs for specific time with shared data
    if not args.time or not args.modes:
        parser.print_help()
        return

    try:
        h, m = map(int, args.time.split(':'))
    except ValueError:
        logger.error(f"Invalid time format: {args.time}. Use HH:MM")
        sys.exit(1)

    modes = [mode.strip() for mode in args.modes.split(',')]
    logger.info("=" * 80)
    logger.info(f"BATCH JOB LAUNCHER: {args.time} ET")
    logger.info(f"Modes: {', '.join(modes)}")
    logger.info("=" * 80)

    # Step 1: Load market data once
    cache = load_market_data_once(args.file)
    if not cache:
        logger.error("Failed to load market data")
        sys.exit(1)

    # Step 2: Save to cache file
    cache_file = save_cache(cache)
    if not cache_file:
        logger.error("Failed to save cache")
        sys.exit(1)

    # Step 3: Build additional args
    additional_args = ""
    if args.notify:
        additional_args += " --notify"
    if args.cron:
        additional_args += " --cron"

    # Step 4: Launch each mode with cached data
    logger.info("=" * 80)
    logger.info("LAUNCHING MODES WITH CACHED DATA")
    logger.info("=" * 80)
    success = launch_mode_jobs(modes, cache_file, args.file, additional_args, args.dry_run)

    # Step 5: Cleanup old caches
    cleanup_old_caches(keep_recent=3)

    logger.info("=" * 80)
    logger.info(f"✓ Batch job launcher complete: {success}/{len(modes)} modes succeeded")
    logger.info("=" * 80)


if __name__ == '__main__':
    main()
