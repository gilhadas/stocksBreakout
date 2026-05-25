"""
regime_detector.py
──────────────────
Detect current market regime (bull/bear/mixed) and suggest parameter set.

Regimes:
  • BULL   — SPY uptrend, low volatility, >60% win rate optimal
  • BEAR   — SPY downtrend, high volatility, defensive params needed
  • MIXED  — SPY choppy, high volatility, balanced params needed

Usage:
    python regime_detector.py                       # detect current regime
    python regime_detector.py --suggest swing       # suggest params for swing mode
    python regime_detector.py --apply swing         # detect & apply to config.py
"""

import argparse
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple, Optional

import pandas as pd
import yfinance as yf
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(message)s')

_NY_TZ = ZoneInfo('America/New_York')

# Core detection logic lives in quantkit.regime (canonical implementation)
from quantkit.regime import REGIME_PARAMS, detect_regime, suggest_params  # noqa: E402

# ─── Regime Detection Logic ──────────────────────────────────────────────────

def fetch_spy_data(days: int = 60) -> Optional[pd.DataFrame]:
    """Fetch SPY daily OHLCV for regime detection."""
    try:
        end = datetime.now(_NY_TZ).date()
        start = end - timedelta(days=days)
        df = yf.download('SPY', start=str(start), end=str(end), progress=False)
        if df.empty:
            logger.warning("SPY data fetch failed")
            return None
        return df
    except Exception as e:
        logger.error(f"Error fetching SPY: {e}")
        return None


def apply_params_to_config(mode: str, regime: str) -> bool:
    """Update config.py with regime-optimized parameters in MODES[mode] dict."""
    params = suggest_params(mode, regime)
    if not params:
        return False

    config_path = Path(__file__).parent / 'config.py'
    if not config_path.exists():
        logger.error(f"config.py not found: {config_path}")
        return False

    try:
        content = config_path.read_text()

        # Update MODES[mode] dictionary entries
        # Pattern: 'key': value,  →  'key': new_value,
        updates = {
            'vol_thresh': params.get('vol_thresh'),
            'atr_mult': params.get('atr_mult'),
            'sl_mult': params.get('sl_mult'),
            'tp_mult': params.get('tp_mult'),
            'min_rr': params.get('min_rr'),
        }

        lines = content.split('\n')
        in_mode_section = False
        for i, line in enumerate(lines):
            # Find start of MODES[mode] section
            if f"'{mode}': {{" in line:
                in_mode_section = True
                continue

            # Stop when we hit next mode or end of MODES dict
            if in_mode_section and (line.strip().startswith('}') and "'" not in line):
                in_mode_section = False
            elif in_mode_section and "': {" in line and mode not in line:
                in_mode_section = False

            # Update parameters within the mode section
            if in_mode_section:
                for key, value in updates.items():
                    if value is None:
                        continue
                    pattern = f"'{key}': "
                    if pattern in line:
                        # Preserve indentation and replace value
                        indent = len(line) - len(line.lstrip())
                        lines[i] = ' ' * indent + f"'{key}': {value},"
                        break

        content = '\n'.join(lines)

        # Write back (with backup)
        backup_path = config_path.with_suffix('.py.bak')
        backup_path.write_text(config_path.read_text())
        config_path.write_text(content)

        logger.info(f"✓ Updated config.py for {mode} mode, {regime} regime")
        logger.info(f"  Backup: {backup_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to update config.py: {e}")
        return False


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Detect market regime and suggest parameters')
    parser.add_argument('--suggest', choices=['swing', 'daytrade'],
                        help='Suggest params for this mode (requires regime detection)')
    parser.add_argument('--apply', choices=['swing', 'daytrade'],
                        help='Detect regime and apply params to config.py')
    parser.add_argument('--days', type=int, default=60,
                        help='Days of historical data for regime detection (default 60)')
    args = parser.parse_args()

    # Fetch SPY data
    print(f"\nFetching SPY data ({args.days} days)...")
    df = fetch_spy_data(args.days)

    if df is None:
        print("ERROR: Could not fetch SPY data")
        return

    # Detect regime
    regime, metrics = detect_regime(df)

    print(f"\n{'='*70}")
    print(f"  MARKET REGIME DETECTION")
    print(f"{'='*70}")
    print(f"\n  Regime: {regime.upper()}")
    print(f"\n  Metrics:")
    print(f"    Price:        ${metrics.get('price', '?'):.2f}")
    print(f"    SMA 50:       ${metrics.get('sma50', '?'):.2f}")
    print(f"    SMA 200:      ${metrics.get('sma200', '?'):.2f}")
    print(f"    ATR %:        {metrics.get('atr_pct', '?'):.2f}%")
    print(f"    BB Width:     {metrics.get('bb_width', '?'):.2f}%")
    print(f"    Win Rate:     {metrics.get('win_rate', 0)*100:.1f}%")
    print(f"    Vol Trend:    {metrics.get('vol_trend', '?'):.2f}x")

    # Suggest params if requested
    if args.suggest:
        params = suggest_params(args.suggest, regime)
        print(f"\n  Suggested Parameters for {args.suggest.upper()} (regime: {regime}):")
        print(f"    {params.get('description', '')}")
        for k, v in sorted(params.items()):
            if k not in ('description',):
                print(f"    {k}: {v}")

    # Apply params if requested
    if args.apply:
        print(f"\n  Applying parameters to config.py...")
        if apply_params_to_config(args.apply, regime):
            params = suggest_params(args.apply, regime)
            print(f"\n  ✓ Config updated!")
            print(f"    Mode:   {args.apply}")
            print(f"    Regime: {regime}")
        else:
            print(f"  ✗ Failed to update config.py")

    print(f"\n{'='*70}\n")


if __name__ == '__main__':
    main()
