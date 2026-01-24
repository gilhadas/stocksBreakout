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
