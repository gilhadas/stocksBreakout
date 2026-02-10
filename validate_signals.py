#!/usr/bin/env python3
"""
Signal Validation & Learning Module
Validates past breakout signals against actual price action.
Generates learning recommendations for weight tuning.

Usage:
    python validate_signals.py validate --date 2026-02-10 --mode swing
    python validate_signals.py validate-all --min-age-days 3
    python validate_signals.py learn --min-signals 20
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from config import MODES, OUTPUT_DIR
from yfinance_adapter import YFinanceAdapter

logger = logging.getLogger(__name__)

SIGNALS_DIR = Path(OUTPUT_DIR, 'signals')
OUTCOMES_DIR = Path(OUTPUT_DIR, 'outcomes')
ADJUSTMENTS_FILE = Path(OUTPUT_DIR, 'score_adjustments.json')

SWING_LOOKFORWARD_DAYS = 10
DAYTRADE_LOOKFORWARD_HOURS = 7

# Scoring weights from scanner.py — imported for learning reference
try:
    from scanner import BreakoutDetector
    SCORING_WEIGHTS = BreakoutDetector.SCORING_WEIGHTS
except Exception:
    SCORING_WEIGHTS = {
        'vol_confirm': 16, 'trend_ok': 16, 'momentum_strong': 13,
        'dist_confirm': 10, 'candle_ok': 8, 'rr_ok': 10,
        'no_vol_divergence': 5, 'conviction_strong': 8, 'rs_ok': 8,
        'consolidation': 8, 'has_bullish_pattern': 10,
    }


# ---------------------------------------------------------------------------
# Signal Loading
# ---------------------------------------------------------------------------

def load_signals_for_date(target_date: str, mode: str = None) -> pd.DataFrame:
    """
    Load all signal CSVs for a given date, deduplicated by (Symbol, Mode).
    Keeps earliest entry per symbol.
    """
    # Normalize date format
    date_str = target_date.replace('-', '')
    if len(date_str) != 8:
        logger.error(f"Invalid date format: {target_date}. Use YYYY-MM-DD or YYYYMMDD")
        return pd.DataFrame()

    pattern = f"signals_{mode or '*'}_{date_str}*.csv"
    files = sorted(SIGNALS_DIR.glob(pattern))

    if not files:
        logger.info(f"No signal files found for {target_date} (mode={mode or 'all'})")
        return pd.DataFrame()

    frames = []
    for f in files:
        try:
            df = pd.read_csv(f)
            # Extract timestamp from filename: signals_swing_20260210_104412.csv
            parts = f.stem.split('_')
            ts_str = '_'.join(parts[-2:])  # e.g. "20260210_104412"
            df['SignalTimestamp'] = ts_str
            df['SignalFile'] = f.name
            # Extract mode from filename if not in CSV
            if 'Mode' not in df.columns and len(parts) >= 3:
                df['Mode'] = parts[1]
            frames.append(df)
        except Exception as e:
            logger.warning(f"Failed to read {f}: {e}")

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)

    # Normalize column names (some CSVs use Symbol, others symbol)
    if 'symbol' in combined.columns and 'Symbol' not in combined.columns:
        combined.rename(columns={'symbol': 'Symbol'}, inplace=True)

    # Fill missing optional columns
    for col in ['Sector', 'Sentiment', 'Buzz']:
        if col not in combined.columns:
            combined[col] = ''

    # Deduplicate: keep earliest signal per (Symbol, Mode)
    combined.sort_values('SignalTimestamp', inplace=True)
    combined.drop_duplicates(subset=['Symbol', 'Mode'], keep='first', inplace=True)

    # Add signal date
    combined['SignalDate'] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    logger.info(f"Loaded {len(combined)} unique signals for {target_date} from {len(files)} files")
    return combined.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Outcome Evaluation
# ---------------------------------------------------------------------------

def fetch_outcome_data(symbol: str, signal_date: str, mode: str,
                       yf_adapter: YFinanceAdapter) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV data after the signal date for outcome evaluation.
    """
    try:
        sig_dt = datetime.strptime(signal_date, '%Y-%m-%d')
    except ValueError:
        return None

    if mode == 'daytrade':
        # Intraday: fetch 15-min bars for signal date + next day
        start = signal_date
        end = (sig_dt + timedelta(days=2)).strftime('%Y-%m-%d')
        df = yf_adapter.get_historical_data(symbol, '15 mins', start, end)
        if df is not None and len(df) > 0:
            # Keep only bars from signal date onward
            df = df[df.index >= sig_dt]
    else:
        # Swing/longterm: daily bars, next 15 calendar days
        start = signal_date
        end = (sig_dt + timedelta(days=SWING_LOOKFORWARD_DAYS + 5)).strftime('%Y-%m-%d')
        df = yf_adapter.get_historical_data(symbol, '1 day', start, end)
        if df is not None and len(df) > 0:
            # Include signal date bar + subsequent bars
            df = df[df.index >= sig_dt]

    if df is None or len(df) == 0:
        logger.debug(f"No outcome data for {symbol} after {signal_date}")
        return None

    return df


def evaluate_signal_outcome(entry: float, stop: float, target: float,
                            bars: pd.DataFrame, mode: str) -> Dict:
    """
    Evaluate a single signal against subsequent price bars.
    Returns outcome dict with MFE, MAE, RealizedR, etc.
    """
    if bars is None or len(bars) == 0:
        return {
            'Outcome': 'DATA_UNAVAILABLE', 'ExitPrice': None,
            'MFE': 0.0, 'MAE': 0.0, 'RealizedR': 0.0,
            'Duration': 0, 'HighestHigh': entry, 'LowestLow': entry,
        }

    # Limit evaluation window
    if mode == 'daytrade':
        eval_bars = bars
    else:
        eval_bars = bars.head(SWING_LOOKFORWARD_DAYS + 1)  # +1 includes entry day

    # Skip the first bar (signal bar itself) — we evaluate subsequent price action
    if len(eval_bars) > 1:
        subsequent = eval_bars.iloc[1:]
    else:
        subsequent = eval_bars

    target_dist = max(target - entry, 1e-6)
    stop_dist = max(entry - stop, 1e-6)

    outcome = 'OPEN'
    exit_price = None
    exit_idx = len(subsequent)
    highest_high = entry
    lowest_low = entry

    for i, (_, bar) in enumerate(subsequent.iterrows()):
        bar_high = bar['high']
        bar_low = bar['low']
        bar_open = bar['open']

        highest_high = max(highest_high, bar_high)
        lowest_low = min(lowest_low, bar_low)

        # Check if both stop and target hit in same bar
        stop_hit = bar_low <= stop
        target_hit = bar_high >= target

        if stop_hit and target_hit:
            # Ambiguous — use open to determine which was hit first
            if bar_open <= stop:
                outcome = 'HIT_STOP'
                exit_price = min(bar_open, stop)
            elif bar_open >= target:
                outcome = 'HIT_TARGET'
                exit_price = max(bar_open, target)
            else:
                # Conservative: assume stop hit first
                outcome = 'HIT_STOP'
                exit_price = stop
            exit_idx = i + 1
            break
        elif stop_hit:
            outcome = 'HIT_STOP'
            exit_price = min(bar_open, stop) if bar_open <= stop else stop
            exit_idx = i + 1
            break
        elif target_hit:
            outcome = 'HIT_TARGET'
            exit_price = max(bar_open, target) if bar_open >= target else target
            exit_idx = i + 1
            break

    # Calculate metrics
    mfe = max(0.0, (highest_high - entry) / target_dist)
    mae = max(0.0, (entry - lowest_low) / stop_dist)

    if outcome == 'OPEN':
        # Use last close for unrealized R
        last_close = subsequent.iloc[-1]['close'] if len(subsequent) > 0 else entry
        realized_r = (last_close - entry) / stop_dist
        exit_price = last_close
    else:
        realized_r = (exit_price - entry) / stop_dist

    return {
        'Outcome': outcome,
        'ExitPrice': round(exit_price, 2) if exit_price else None,
        'MFE': round(mfe, 3),
        'MAE': round(mae, 3),
        'RealizedR': round(realized_r, 2),
        'Duration': exit_idx,
        'HighestHigh': round(highest_high, 2),
        'LowestLow': round(lowest_low, 2),
    }


# ---------------------------------------------------------------------------
# Main Validation
# ---------------------------------------------------------------------------

def validate_signals(target_date: str, mode: str = None,
                     save: bool = True) -> pd.DataFrame:
    """
    Main validation: load signals, fetch outcomes, evaluate, save & print.
    """
    signals = load_signals_for_date(target_date, mode)
    if signals.empty:
        return signals

    yf = YFinanceAdapter()
    outcomes = []

    modes_to_process = [mode] if mode else signals['Mode'].unique().tolist()

    for m in modes_to_process:
        mode_signals = signals[signals['Mode'] == m]
        print(f"\nValidating {len(mode_signals)} {m} signals...")

        for idx, sig in mode_signals.iterrows():
            symbol = sig['Symbol']
            entry = float(sig['Price'])
            stop = float(sig['Stop'])
            target = float(sig['Target'])
            signal_date = sig['SignalDate']

            print(f"  [{idx+1}/{len(signals)}] {symbol}", end='', flush=True)

            bars = fetch_outcome_data(symbol, signal_date, m, yf)
            result = evaluate_signal_outcome(entry, stop, target, bars, m)

            # Merge signal data with outcome
            row = sig.to_dict()
            row.update(result)

            # Add volume bucket
            vol = float(sig.get('Vol', 0))
            row['VolBucket'] = 'HIGH' if vol > 2.0 else 'MEDIUM' if vol >= 1.0 else 'LOW'

            outcomes.append(row)

            status = result['Outcome']
            r = result['RealizedR']
            marker = '+' if r > 0 else '-' if r < 0 else '~'
            print(f"  {status:14s} {marker}{abs(r):.1f}R")

    if not outcomes:
        return pd.DataFrame()

    results = pd.DataFrame(outcomes)

    # Save outcomes
    if save:
        OUTCOMES_DIR.mkdir(parents=True, exist_ok=True)
        for m in results['Mode'].unique():
            mode_results = results[results['Mode'] == m]
            date_str = target_date.replace('-', '')
            outfile = OUTCOMES_DIR / f"outcomes_{m}_{date_str}.csv"
            mode_results.to_csv(outfile, index=False)
            logger.info(f"Saved {len(mode_results)} outcomes to {outfile}")

    print_summary(results)
    return results


def print_summary(df: pd.DataFrame) -> None:
    """Print formatted validation summary."""
    if df.empty:
        print("No signals to summarize.")
        return

    resolved = df[df['Outcome'].isin(['HIT_TARGET', 'HIT_STOP'])]
    open_signals = df[df['Outcome'] == 'OPEN']
    unavailable = df[df['Outcome'] == 'DATA_UNAVAILABLE']

    print(f"\n{'='*70}")
    print(f" SIGNAL VALIDATION SUMMARY")
    print(f"{'='*70}")

    # Per-quality breakdown
    header = f"{'Quality':<10} | {'Signals':>7} | {'Resolved':>8} | {'Win%':>6} | {'Avg R':>7} | {'Avg MFE':>7} | {'Avg MAE':>7}"
    print(header)
    print('-' * len(header))

    for quality in ['PREMIUM', 'HIGH', 'STANDARD']:
        q_all = df[df['Quality'] == quality]
        q_resolved = resolved[resolved['Quality'] == quality]
        count = len(q_all)
        res_count = len(q_resolved)
        if res_count > 0:
            wins = len(q_resolved[q_resolved['Outcome'] == 'HIT_TARGET'])
            win_pct = (wins / res_count) * 100
            avg_r = q_resolved['RealizedR'].mean()
            avg_mfe = q_resolved['MFE'].mean()
            avg_mae = q_resolved['MAE'].mean()
            print(f"{quality:<10} | {count:>7} | {res_count:>8} | {win_pct:>5.1f}% | {avg_r:>+6.2f} | {avg_mfe:>7.2f} | {avg_mae:>7.2f}")
        else:
            print(f"{quality:<10} | {count:>7} | {res_count:>8} |    N/A |    N/A |     N/A |     N/A")

    print('-' * len(header))

    # Totals
    total = len(df)
    res_total = len(resolved)
    if res_total > 0:
        total_wins = len(resolved[resolved['Outcome'] == 'HIT_TARGET'])
        total_win_pct = (total_wins / res_total) * 100
        total_avg_r = resolved['RealizedR'].mean()
        total_avg_mfe = resolved['MFE'].mean()
        total_avg_mae = resolved['MAE'].mean()
        print(f"{'TOTAL':<10} | {total:>7} | {res_total:>8} | {total_win_pct:>5.1f}% | {total_avg_r:>+6.2f} | {total_avg_mfe:>7.2f} | {total_avg_mae:>7.2f}")
    else:
        print(f"{'TOTAL':<10} | {total:>7} | {res_total:>8} |    N/A |    N/A |     N/A |     N/A")

    if len(open_signals) > 0:
        avg_unrealized = open_signals['RealizedR'].mean()
        print(f"{'OPEN':<10} | {len(open_signals):>7} |      N/A |    N/A | {avg_unrealized:>+6.2f} |     N/A |     N/A")
    if len(unavailable) > 0:
        print(f"{'NO DATA':<10} | {len(unavailable):>7} |      N/A |    N/A |    N/A |     N/A |     N/A")

    # Best and worst signals
    if res_total > 0:
        best = resolved.nlargest(3, 'RealizedR')
        worst = resolved.nsmallest(3, 'RealizedR')
        print(f"\nBest:  ", end='')
        print(', '.join(f"{r['Symbol']} {r['RealizedR']:+.2f}R ({r['Quality']})" for _, r in best.iterrows()))
        print(f"Worst: ", end='')
        print(', '.join(f"{r['Symbol']} {r['RealizedR']:+.2f}R ({r['Quality']})" for _, r in worst.iterrows()))

    print()


# ---------------------------------------------------------------------------
# Validate-All
# ---------------------------------------------------------------------------

def validate_all_unprocessed(mode: str = None, min_age_days: int = 1) -> None:
    """Find signal dates without outcomes and validate them."""
    today = datetime.now().date()

    # Find all signal dates
    signal_dates = set()
    for f in SIGNALS_DIR.glob(f"signals_{mode or '*'}_*.csv"):
        parts = f.stem.split('_')
        if len(parts) >= 3:
            date_str = parts[2]
            if len(date_str) == 8:
                try:
                    sig_date = datetime.strptime(date_str, '%Y%m%d').date()
                    if (today - sig_date).days >= min_age_days:
                        m = parts[1]
                        signal_dates.add((date_str, m))
                except ValueError:
                    pass

    # Find existing outcome dates
    existing = set()
    if OUTCOMES_DIR.exists():
        for f in OUTCOMES_DIR.glob("outcomes_*.csv"):
            parts = f.stem.split('_')
            if len(parts) >= 3:
                existing.add((parts[2], parts[1]))

    # Process unprocessed dates
    unprocessed = signal_dates - existing
    if not unprocessed:
        print("All signal dates have been validated.")
        return

    print(f"Found {len(unprocessed)} unprocessed date/mode pairs")
    for date_str, m in sorted(unprocessed):
        formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        if mode and m != mode:
            continue
        print(f"\n{'='*70}")
        print(f" Validating {m} signals from {formatted}")
        print(f"{'='*70}")
        validate_signals(formatted, m)


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------

def load_all_outcomes() -> pd.DataFrame:
    """Load and concatenate all outcome CSVs."""
    if not OUTCOMES_DIR.exists():
        return pd.DataFrame()

    files = sorted(OUTCOMES_DIR.glob("outcomes_*.csv"))
    if not files:
        return pd.DataFrame()

    frames = []
    for f in files:
        try:
            frames.append(pd.read_csv(f))
        except Exception as e:
            logger.warning(f"Failed to read {f}: {e}")

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined.drop_duplicates(subset=['Symbol', 'SignalDate', 'Mode'], keep='last', inplace=True)
    return combined


def generate_learning_output(df: pd.DataFrame, save: bool = True) -> Dict:
    """
    Analyze accumulated outcomes and generate scoring recommendations.
    """
    resolved = df[df['Outcome'].isin(['HIT_TARGET', 'HIT_STOP'])].copy()

    if len(resolved) == 0:
        print("No resolved signals to analyze.")
        return {}

    resolved['Win'] = (resolved['Outcome'] == 'HIT_TARGET').astype(int)
    overall_win_rate = resolved['Win'].mean()
    overall_avg_r = resolved['RealizedR'].mean()

    output = {
        'status': 'advisory',
        'last_updated': datetime.now().isoformat(),
        'total_signals_analyzed': len(df),
        'total_resolved': len(resolved),
        'overall_win_rate': round(overall_win_rate, 3),
        'overall_avg_R': round(overall_avg_r, 2),
    }

    # --- By Quality ---
    by_quality = {}
    for q in ['PREMIUM', 'HIGH', 'STANDARD']:
        grp = resolved[resolved['Quality'] == q]
        if len(grp) > 0:
            by_quality[q] = {
                'count': len(grp),
                'win_rate': round(grp['Win'].mean(), 3),
                'avg_R': round(grp['RealizedR'].mean(), 2),
                'avg_MFE': round(grp['MFE'].mean(), 3),
                'avg_MAE': round(grp['MAE'].mean(), 3),
            }
    output['by_quality'] = by_quality

    # --- By Pattern ---
    by_pattern = {}
    if 'Patterns' in resolved.columns:
        for _, row in resolved.iterrows():
            patterns = str(row.get('Patterns', ''))
            if patterns and patterns != 'nan':
                # Track the full combo and individual patterns
                if patterns not in by_pattern:
                    by_pattern[patterns] = {'wins': 0, 'total': 0, 'sum_r': 0.0}
                by_pattern[patterns]['total'] += 1
                by_pattern[patterns]['wins'] += row['Win']
                by_pattern[patterns]['sum_r'] += row['RealizedR']

    output['by_pattern'] = {
        k: {
            'count': v['total'],
            'win_rate': round(v['wins'] / v['total'], 3) if v['total'] > 0 else 0,
            'avg_R': round(v['sum_r'] / v['total'], 2) if v['total'] > 0 else 0,
        }
        for k, v in by_pattern.items() if v['total'] >= 3
    }

    # --- By RR_Grade ---
    by_grade = {}
    if 'RR_Grade' in resolved.columns:
        for g in ['A', 'B', 'C']:
            grp = resolved[resolved['RR_Grade'] == g]
            if len(grp) > 0:
                by_grade[g] = {
                    'count': len(grp),
                    'win_rate': round(grp['Win'].mean(), 3),
                    'avg_R': round(grp['RealizedR'].mean(), 2),
                }
    output['by_rr_grade'] = by_grade

    # --- By Volume Bucket ---
    by_vol = {}
    if 'VolBucket' in resolved.columns:
        for b in ['LOW', 'MEDIUM', 'HIGH']:
            grp = resolved[resolved['VolBucket'] == b]
            if len(grp) > 0:
                by_vol[b] = {
                    'count': len(grp),
                    'win_rate': round(grp['Win'].mean(), 3),
                    'avg_R': round(grp['RealizedR'].mean(), 2),
                }
    output['by_volume_bucket'] = by_vol

    # --- By Sector ---
    by_sector = {}
    if 'Sector' in resolved.columns:
        for sector in resolved['Sector'].dropna().unique():
            if not sector or sector == 'nan':
                continue
            grp = resolved[resolved['Sector'] == sector]
            if len(grp) >= 3:
                by_sector[sector] = {
                    'count': len(grp),
                    'win_rate': round(grp['Win'].mean(), 3),
                    'avg_R': round(grp['RealizedR'].mean(), 2),
                }
    output['by_sector'] = by_sector

    # --- WinProb Calibration ---
    calibration = {}
    if 'WinProb' in resolved.columns:
        resolved['WinProb'] = pd.to_numeric(resolved['WinProb'], errors='coerce')
        bins = [(0.0, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 1.01)]
        for lo, hi in bins:
            grp = resolved[(resolved['WinProb'] >= lo) & (resolved['WinProb'] < hi)]
            if len(grp) >= 3:
                predicted = grp['WinProb'].mean()
                actual = grp['Win'].mean()
                calibration[f"{lo:.2f}-{hi:.2f}"] = {
                    'predicted_avg': round(predicted, 3),
                    'actual_rate': round(actual, 3),
                    'count': len(grp),
                    'error': round(abs(predicted - actual), 3),
                }
    output['win_prob_calibration'] = calibration

    # --- Feature Effectiveness ---
    feature_proxies = {
        'vol_confirm': ('Vol', lambda x: pd.to_numeric(x, errors='coerce') >= 1.3),
        'has_bullish_pattern': ('Patterns', lambda x: x.astype(str).str.len() > 3),
        'rr_ok': ('R:R', lambda x: pd.to_numeric(x, errors='coerce') >= 1.5),
        'conviction_strong': ('WinProb', lambda x: pd.to_numeric(x, errors='coerce') >= 0.65),
    }

    effectiveness = []
    for feature, (col, fn) in feature_proxies.items():
        if col not in resolved.columns:
            continue
        try:
            mask = fn(resolved[col])
            grp_true = resolved[mask]
            grp_false = resolved[~mask]
            if len(grp_true) >= 3 and len(grp_false) >= 3:
                wr_true = grp_true['Win'].mean()
                wr_false = grp_false['Win'].mean()
                delta = wr_true - wr_false
                effectiveness.append({
                    'feature': feature,
                    'current_weight': SCORING_WEIGHTS.get(feature, 0),
                    'win_rate_when_true': round(wr_true, 3),
                    'win_rate_when_false': round(wr_false, 3),
                    'win_rate_delta': round(delta, 3),
                    'count_true': len(grp_true),
                    'count_false': len(grp_false),
                })
        except Exception:
            pass

    effectiveness.sort(key=lambda x: x['win_rate_delta'], reverse=True)
    for i, e in enumerate(effectiveness):
        e['rank'] = i + 1
    output['feature_effectiveness'] = effectiveness

    # --- Weight Recommendations ---
    recommendations = []
    for e in effectiveness:
        current = e['current_weight']
        delta = e['win_rate_delta']
        if delta > 0.10 and current < 20:
            recommendations.append({
                'feature': e['feature'],
                'current_weight': current,
                'recommended_weight': current + 2,
                'reason': f"Strong positive delta (+{delta:.0%}): signals with this feature win {e['win_rate_when_true']:.0%} vs {e['win_rate_when_false']:.0%} without",
            })
        elif delta < -0.05 and current > 5:
            recommendations.append({
                'feature': e['feature'],
                'current_weight': current,
                'recommended_weight': current - 2,
                'reason': f"Negative delta ({delta:+.0%}): feature presence doesn't help win rate",
            })
    output['weight_recommendations'] = recommendations

    # --- Print & Save ---
    print(f"\n{'='*70}")
    print(f" LEARNING REPORT — {len(resolved)} resolved signals")
    print(f"{'='*70}")
    print(f" Overall win rate: {overall_win_rate:.1%} | Avg R: {overall_avg_r:+.2f}")
    print()

    if by_quality:
        print(" By Quality:")
        for q, v in by_quality.items():
            print(f"   {q:<10} {v['count']:>4} signals | Win: {v['win_rate']:.1%} | Avg R: {v['avg_R']:+.2f} | MFE: {v['avg_MFE']:.2f} | MAE: {v['avg_MAE']:.2f}")

    if effectiveness:
        print(f"\n Feature Effectiveness:")
        for e in effectiveness:
            direction = '+' if e['win_rate_delta'] > 0 else ''
            print(f"   #{e['rank']} {e['feature']:<22} delta: {direction}{e['win_rate_delta']:.1%}  (weight: {e['current_weight']})")

    if recommendations:
        print(f"\n Weight Recommendations:")
        for r in recommendations:
            print(f"   {r['feature']}: {r['current_weight']} -> {r['recommended_weight']}  ({r['reason']})")
    else:
        print(f"\n No weight changes recommended (all features performing as expected)")

    print()

    if save:
        with open(ADJUSTMENTS_FILE, 'w') as f:
            json.dump(output, f, indent=2, default=str)
        print(f"Saved learning data to {ADJUSTMENTS_FILE}")

    return output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(message)s',
    )

    parser = argparse.ArgumentParser(
        description='Validate breakout signals and generate learning data'
    )
    subparsers = parser.add_subparsers(dest='command')

    # validate
    val_p = subparsers.add_parser('validate', help='Validate signals for a date')
    val_p.add_argument('--date', type=str, default=None,
                       help='Date to validate (YYYY-MM-DD). Default: today')
    val_p.add_argument('--mode', choices=['swing', 'daytrade', 'longterm'],
                       default=None, help='Mode filter. Default: all')
    val_p.add_argument('--no-save', action='store_true')

    # validate-all
    all_p = subparsers.add_parser('validate-all', help='Validate all unprocessed dates')
    all_p.add_argument('--mode', choices=['swing', 'daytrade', 'longterm'], default=None)
    all_p.add_argument('--min-age-days', type=int, default=1,
                       help='Min age in days before validating (default: 1)')

    # learn
    lrn_p = subparsers.add_parser('learn', help='Generate learning recommendations')
    lrn_p.add_argument('--min-signals', type=int, default=20,
                       help='Min resolved signals required (default: 20)')
    lrn_p.add_argument('--no-save', action='store_true')

    args = parser.parse_args()

    if args.command == 'validate':
        target = args.date or datetime.now().strftime('%Y-%m-%d')
        validate_signals(target, args.mode, save=not args.no_save)

    elif args.command == 'validate-all':
        validate_all_unprocessed(args.mode, args.min_age_days)

    elif args.command == 'learn':
        df = load_all_outcomes()
        resolved = df[df['Outcome'].isin(['HIT_TARGET', 'HIT_STOP'])] if not df.empty else df
        if len(resolved) < args.min_signals:
            print(f"Only {len(resolved)} resolved signals. Need {args.min_signals}+.")
            print("Keep running validation daily to accumulate data.")
            return
        generate_learning_output(df, save=not args.no_save)

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
