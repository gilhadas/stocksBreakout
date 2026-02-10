"""
Utility functions for file I/O and helpers
"""

import csv
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


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
    
    Example:
        symbol,mode,entry,stop,target,timeframe
        AAPL,swing,185.50,180.00,195.00,1 day
        NVDA,daytrade,520.30,515.00,530.00,15 mins
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
    import os
    from config import MODES

    quality_rank = {'PREMIUM': 3, 'HIGH': 2, 'STANDARD': 1, 'REJECT': 0}
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
        new_rows.append({
            'symbol': symbol,
            'mode': mode,
            'entry': sig.get('Price', 0),
            'stop': sig.get('Stop', 0),
            'target': sig.get('Target', 0),
            'timeframe': timeframe,
        })
        existing_symbols.add(symbol.upper())

    if not new_rows:
        logger.info(f"No new {min_quality}+ signals to append to {positions_file}")
        return 0

    write_header = not file_exists
    with open(positions_file, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['symbol', 'mode', 'entry', 'stop', 'target', 'timeframe'])
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)

    symbols = [r['symbol'] for r in new_rows]
    logger.info(f"Appended {len(new_rows)} {min_quality}+ positions to {positions_file}: {', '.join(symbols)}")
    return len(new_rows)


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
