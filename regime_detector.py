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

# ─── Regime Parameter Sets ──────────────────────────────────────────────────
# Based on mode_optimizer results (Mar 2026)

REGIME_PARAMS = {
    'swing': {
        'bull': {
            'vol_thresh': 0.85,
            'atr_mult': 0.78,
            'sl_mult': 3.04,
            'tp_mult': 12.11,
            'min_rr': 0.53,
            'minervini_min': 3,
            'quality_filter': 'PREMIUM',
            'description': 'Aggressive: tight stops, wide targets (Bull 2023: +18%)',
        },
        'mixed': {
            'vol_thresh': 0.90,
            'atr_mult': 0.75,
            'sl_mult': 3.00,
            'tp_mult': 10.00,
            'min_rr': 0.55,
            'minervini_min': 2,
            'quality_filter': 'HIGH',
            'description': 'Balanced: mixed volatility (Mixed 2024H1: +29%)',
        },
        'bear': {
            'vol_thresh': 1.20,
            'atr_mult': 0.85,
            'sl_mult': 2.50,
            'tp_mult': 6.00,
            'min_rr': 0.80,
            'minervini_min': 5,
            'quality_filter': 'PREMIUM',
            'description': 'Defensive: wider stops, tight targets (minimize bear risk)',
        },
    },
    'daytrade': {
        'bull': {
            'vol_thresh': 0.95,
            'atr_mult': 0.60,
            'sl_mult': 3.01,
            'tp_mult': 5.47,
            'min_rr': 0.82,
            'minervini_min': 1,
            'quality_filter': 'STANDARD',
            'description': 'Aggressive: fast entries, frequent exits (Bull 2023: +23%)',
        },
        'mixed': {
            'vol_thresh': 1.00,
            'atr_mult': 0.65,
            'sl_mult': 3.00,
            'tp_mult': 5.00,
            'min_rr': 0.80,
            'minervini_min': 2,
            'quality_filter': 'HIGH',
            'description': 'Balanced: moderate turnover (Mixed 2024H1: +24%)',
        },
        'bear': {
            'vol_thresh': 1.30,
            'atr_mult': 0.75,
            'sl_mult': 2.50,
            'tp_mult': 4.00,
            'min_rr': 1.00,
            'minervini_min': 4,
            'quality_filter': 'PREMIUM',
            'description': 'Defensive: high RR, few trades (minimize bear losses)',
        },
    },
}


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


def detect_regime(df: pd.DataFrame) -> Tuple[str, dict]:
    """
    Detect market regime from SPY daily data.

    Returns: (regime_name, metrics_dict)
      regime_name: 'bull', 'bear', or 'mixed'
    """
    if df is None or len(df) < 20:
        return 'mixed', {}

    # ── Handle multi-level columns from yfinance ──────────────────────────
    if isinstance(df.columns, pd.MultiIndex):
        close_col = 'Close'
        high_col = 'High'
        low_col = 'Low'
        vol_col = 'Volume'
    else:
        close_col = 'Close'
        high_col = 'High'
        low_col = 'Low'
        vol_col = 'Volume'

    # ── Trend: SMA 50 vs SMA 200 ──────────────────────────────────────────
    close_series = df[close_col].iloc[:, 0] if isinstance(df[close_col], pd.DataFrame) else df[close_col]
    current_price_raw = close_series.iloc[-1]
    current_price = float(current_price_raw) if pd.api.types.is_scalar(current_price_raw) else float(current_price_raw.iloc[0])

    sma50_raw = close_series.rolling(50).mean().iloc[-1]
    sma200_raw = close_series.rolling(200).mean().iloc[-1]

    # Handle NaN values from rolling windows
    sma50_val = float(sma50_raw) if pd.notna(sma50_raw) else current_price
    sma200_val = float(sma200_raw) if pd.notna(sma200_raw) else current_price

    trend_score = 0
    if current_price > sma50_val and sma50_val > sma200_val:
        trend_score = 2  # strong uptrend
    elif current_price > sma50_val:
        trend_score = 1  # weak uptrend
    elif current_price < sma50_val and sma50_val < sma200_val:
        trend_score = -2  # strong downtrend
    elif current_price < sma50_val:
        trend_score = -1  # weak downtrend
    else:
        trend_score = 0  # choppy

    # ── Volatility: ATR% and Bollinger Band width ──────────────────────────
    high_series = df[high_col].iloc[:, 0] if isinstance(df[high_col], pd.DataFrame) else df[high_col]
    low_series = df[low_col].iloc[:, 0] if isinstance(df[low_col], pd.DataFrame) else df[low_col]

    atr_raw = (high_series - low_series).rolling(14).mean().iloc[-1]
    atr = float(atr_raw) if pd.notna(atr_raw) else 0
    atr_pct = (atr / current_price * 100) if atr > 0 else 0

    # Bollinger Bands (20-day)
    bb_mid_raw = close_series.rolling(20).mean().iloc[-1]
    bb_std_raw = close_series.rolling(20).std().iloc[-1]
    bb_mid = float(bb_mid_raw) if pd.notna(bb_mid_raw) else 0
    bb_std = float(bb_std_raw) if pd.notna(bb_std_raw) else 0

    bb_width = (2 * bb_std / bb_mid * 100) if bb_mid > 0 else 0

    # Volatility classification
    if atr_pct > 2.5 or bb_width > 10:
        vol_score = 2  # HIGH volatility
    elif atr_pct > 1.5 or bb_width > 6:
        vol_score = 1  # MODERATE volatility
    else:
        vol_score = 0  # LOW volatility

    # ── Win Rate Proxy: % of days closing > open ────────────────────────────
    open_series = df['Open'].iloc[:, 0] if isinstance(df['Open'], pd.DataFrame) else df['Open']
    up_days = (close_series > open_series).sum()
    win_rate = float(up_days) / len(close_series)

    # ── Volume Trend ───────────────────────────────────────────────────────
    vol_series = df[vol_col].iloc[:, 0] if isinstance(df[vol_col], pd.DataFrame) else df[vol_col]
    avg_vol_recent = vol_series.iloc[-20:].mean()
    avg_vol_hist = vol_series.iloc[-60:-20].mean()
    vol_trend = float(avg_vol_recent) / float(avg_vol_hist) if avg_vol_hist > 0 else 1.0

    # ── Regime Classification ──────────────────────────────────────────────
    metrics = {
        'price': current_price,
        'sma50': sma50_val,
        'sma200': sma200_val,
        'atr_pct': atr_pct,
        'bb_width': bb_width,
        'win_rate': win_rate,
        'vol_trend': vol_trend,
        'trend_score': trend_score,
        'vol_score': vol_score,
    }

    # Decision tree
    if trend_score >= 1 and vol_score <= 1 and win_rate > 0.55:
        regime = 'bull'
    elif trend_score <= -1 and vol_score >= 1:
        regime = 'bear'
    else:
        regime = 'mixed'

    return regime, metrics


def suggest_params(mode: str, regime: str) -> dict:
    """Return parameter set for given mode + regime."""
    params = REGIME_PARAMS.get(mode, {}).get(regime, {})
    if not params:
        logger.warning(f"No params for mode={mode}, regime={regime}")
        return {}
    return params.copy()


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
