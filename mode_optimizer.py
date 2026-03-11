#!/usr/bin/env python3
"""
mode_optimizer.py — Multi-Mode Parameter Optimizer
====================================================
Finds the best parameter set for scalping, daytrade, and swing modes by
backtesting across bull, bear, and mixed market regimes using Optuna TPE.

Objective:  mean(Sharpe per opt-regime)  − 0.5 × std(Sharpe per opt-regime)
            → rewards configs that work consistently in ALL conditions,
              not just cherry-picked bull markets.

Regimes
-------
  • Bear  2022    2022-01-01 → 2022-12-31  (SPY −19.4%)  [optimize]
  • Bull  2023    2023-01-01 → 2023-12-31  (SPY +26.3%)  [optimize]
  • Mixed 2024H1  2024-01-01 → 2024-06-30  (SPY +15.3%)  [optimize]
  • Hold-out 2024H2  2024-07-01 → 2024-12-31            [validate only]

Usage
-----
    python mode_optimizer.py                            # all modes, 80 trials
    python mode_optimizer.py --mode swing               # swing only
    python mode_optimizer.py --mode daytrade,scalping   # two modes
    python mode_optimizer.py --mode all --trials 200 --symbols 50
    python mode_optimizer.py --mode swing --no-patch    # skip config update
"""

import argparse
import copy
import json
import logging
import sys
import warnings
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ── silence noise ────────────────────────────────────────────────────────────
warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.WARNING, format='%(message)s')
logger = logging.getLogger(__name__)

try:
    import asyncio
    asyncio.get_event_loop()
except RuntimeError:
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())


# ─── Market Regime Definitions ───────────────────────────────────────────────
# data_start:  how far back to fetch for SMA-150 indicator warmup (365+ days)
REGIMES = {
    'bear_2022': {
        'label':        'Bear 2022',
        'scan_start':   '2022-01-01',
        'scan_end':     '2022-12-31',
        'data_start':   '2021-01-01',
        'spy_expected': -19.4,
        'role':         'optimize',
    },
    'bull_2023': {
        'label':        'Bull 2023',
        'scan_start':   '2023-01-01',
        'scan_end':     '2023-12-31',
        'data_start':   '2022-01-01',
        'spy_expected': +26.3,
        'role':         'optimize',
    },
    'mixed_2024h1': {
        'label':        'Mixed 2024H1',
        'scan_start':   '2024-01-01',
        'scan_end':     '2024-06-30',
        'data_start':   '2023-01-01',
        'spy_expected': +15.3,
        'role':         'optimize',
    },
    'holdout_2024h2': {
        'label':        'Hold-out 2024H2',
        'scan_start':   '2024-07-01',
        'scan_end':     '2024-12-31',
        'data_start':   '2023-07-01',
        'spy_expected': +14.0,
        'role':         'validate',      # never used in objective
    },
}

# ─── Parameter Spaces ────────────────────────────────────────────────────────
# (name, low, high, type)
# type: 'float' | 'int' | 'bool' | 'cat'
# For 'cat', low/high = list of choices
PARAM_SPACES = {
    'swing': [
        ('vol_thresh',              0.8,  2.5,   'float'),
        ('atr_mult',                0.1,  1.5,   'float'),
        ('sl_mult',                 1.5,  6.0,   'float'),
        ('tp_mult',                 3.0,  16.0,  'float'),
        ('min_rr',                  0.5,  3.0,   'float'),
        ('max_wick_atr',            0.2,  1.2,   'float'),
        ('max_body_top_pct',        0.15, 0.50,  'float'),
        ('lookback',                5,    30,    'int'),
        ('min_consolidation_bars',  1,    10,    'int'),
        ('tp_as_trail',             None, None,  'bool'),
        ('minervini_min',           0,    9,     'int'),    # 0 = no filter
        ('quality_filter',          ['STANDARD', 'HIGH', 'PREMIUM'], None, 'cat'),
    ],
    'daytrade': [
        ('vol_thresh',              0.8,  3.0,   'float'),
        ('atr_mult',                0.05, 0.60,  'float'),
        ('sl_mult',                 0.5,  4.0,   'float'),
        ('tp_mult',                 1.5,  9.0,   'float'),
        ('min_rr',                  0.5,  2.5,   'float'),
        ('max_wick_atr',            0.3,  1.5,   'float'),
        ('max_body_top_pct',        0.15, 0.60,  'float'),
        ('lookback',                5,    25,    'int'),
        ('min_consolidation_bars',  1,    5,     'int'),
        ('tp_as_trail',             None, None,  'bool'),
        ('minervini_min',           0,    9,     'int'),
        ('quality_filter',          ['STANDARD', 'HIGH', 'PREMIUM'], None, 'cat'),
    ],
    'scalping': [
        ('vol_thresh',              1.0,  4.5,   'float'),
        ('atr_mult',                0.05, 0.50,  'float'),
        ('sl_fixed_cents',          1,    8,     'int'),
        ('tp_fixed_cents',          3,    20,    'int'),
        ('min_rr',                  0.5,  3.0,   'float'),
        ('max_wick_atr',            0.5,  2.0,   'float'),
        ('max_body_top_pct',        0.2,  0.70,  'float'),
        ('lookback',                3,    15,    'int'),
        ('min_price',               2.0,  30.0,  'float'),
        ('tp_as_trail',             None, None,  'bool'),
        ('quality_filter',          ['STANDARD', 'HIGH', 'PREMIUM'], None, 'cat'),
    ],
}

# Keys that belong to run_simulation(), NOT config.MODES
_SIM_KEYS = {'tp_as_trail', 'minervini_min', 'quality_filter'}

# ─── Diverse symbol universe ──────────────────────────────────────────────────
# 40 symbols spanning sectors / mkt-cap / beta — chosen to provide enough
# signal variety across both bull and bear regimes.
DEFAULT_SYMBOLS = [
    # Large-cap tech / mega
    'AAPL', 'MSFT', 'NVDA', 'META', 'GOOGL', 'AMZN', 'TSLA', 'AMD',
    # Growth / high-beta
    'COIN', 'PLTR', 'CRWD', 'DKNG', 'SOFI', 'MELI', 'SHOP',
    # Financials
    'JPM', 'GS', 'BAC', 'V', 'MA',
    # Healthcare / biotech
    'LLY', 'UNH', 'ABBV', 'MRNA', 'ISRG',
    # Energy
    'XOM', 'CVX', 'SLB', 'OXY',
    # Consumer / retail
    'COST', 'WMT', 'HD', 'MCD', 'NKE',
    # Semiconductors / enterprise tech
    'AVGO', 'QCOM', 'AMAT', 'ORCL', 'CRM',
]


# ─── Data Fetching ────────────────────────────────────────────────────────────

def fetch_all_data(symbols: list, data_start: str, data_end: str) -> dict:
    """
    Fetch daily OHLCV for all symbols + SPY covering the full date span.
    Returns {symbol: DataFrame}.  Raises on empty result.
    """
    from yfinance_adapter import YFinanceAdapter

    yf = YFinanceAdapter()
    historical = {}

    # SPY first (needed for RS calculation in scanner)
    spy_df = yf.get_historical_data('SPY', '1 day',
                                    start_date=data_start, end_date=data_end)
    if spy_df is not None and len(spy_df) > 20:
        historical['SPY'] = spy_df

    for i, sym in enumerate(symbols):
        if sym == 'SPY':
            continue
        try:
            df = yf.get_historical_data(sym, '1 day',
                                        start_date=data_start, end_date=data_end)
            if df is not None and len(df) > 100:
                historical[sym] = df
        except Exception:
            pass
        if (i + 1) % 10 == 0:
            print(f"    {i+1}/{len(symbols)} fetched...", end='\r')

    print(f"  Data ready: {len(historical)-1} stocks + SPY       ")
    return historical


def _end_prices_for_regime(historical: dict, scan_end: str) -> dict:
    """Extract the closing price for each symbol at or before scan_end."""
    ed = pd.to_datetime(scan_end)
    ep = {}
    for sym, df in historical.items():
        if sym == 'SPY':
            continue
        sub = df[df.index <= ed]
        if not sub.empty:
            ep[sym] = float(sub.iloc[-1]['close'])
    return ep


# ─── Scanner (per-regime) ─────────────────────────────────────────────────────

def scan_regime(
    historical: dict,
    scan_start: str,
    scan_end: str,
    mode_name: str,
    date_step: int = 10,
) -> list:
    """
    Scan a single time-regime with the CURRENT config.MODES and thresholds=0
    so every candidate that passes candle/vol/rr logic is captured.

    Returns a list of raw signal dicts (quality label still populated for
    post-filtering by objective function).
    """
    import config
    from scanner import BreakoutDetector
    from enhanced_backtest import calculate_spy_perf_on_date

    detector = BreakoutDetector()
    spy_df   = historical.get('SPY')
    symbols  = [s for s in historical if s != 'SPY']

    all_dates = pd.date_range(start=scan_start, end=scan_end, freq='B')
    sim_dates = all_dates[::date_step]

    # Temporarily set score thresholds → 0 so all scoring-passing candidates
    # are captured; we post-filter by quality in the objective function.
    orig_thresh = copy.deepcopy(config.SCORE_THRESHOLDS)
    config.SCORE_THRESHOLDS.update({'GOLD': 0, 'PREMIUM': 0, 'HIGH': 0, 'STANDARD': 0})

    try:
        candidates = []
        cooldowns  = {}

        for sim_date in sim_dates:
            spy_perf = 0.0
            if spy_df is not None:
                spy_perf = calculate_spy_perf_on_date(spy_df, sim_date, lookback=15)

            for symbol in symbols:
                df = historical.get(symbol)
                if df is None:
                    continue
                df_slice = df[df.index <= sim_date]
                if len(df_slice) < 150:
                    continue
                if df_slice.index[-1].date() < (sim_date - pd.Timedelta(days=7)).date():
                    continue

                last_sig = cooldowns.get(symbol)
                if last_sig and (sim_date - last_sig).days < 10:
                    continue

                try:
                    sig = detector.detect(
                        df_slice, symbol, mode_name, '1 day', spy_perf,
                        use_scoring=True,
                        use_legacy_momentum=True,
                        use_v4_overextension=False,
                        reference_date=sim_date,
                    )
                except Exception:
                    sig = None

                if sig:
                    candidates.append({
                        'date':            sim_date,
                        'symbol':          symbol,
                        'action':          'BUY',
                        'price':           sig['Price'],
                        'entry_price':     sig['Price'],
                        'stop_loss':       sig['Stop'],
                        'take_profit':     sig['Target'],
                        'quality':         sig['Quality'],
                        'mode':            mode_name,
                        'type':            'BREAKOUT',
                        'win_probability': sig.get('WinProb', 0.50),
                        'rr_grade':        sig.get('RR_Grade', ''),
                        'minervini_score': sig.get('MinerviniScore') or 0,
                    })
                    cooldowns[symbol] = sim_date

        return candidates

    finally:
        config.SCORE_THRESHOLDS.update(orig_thresh)


def filter_signals(
    candidates: list,
    quality_filter: str = 'STANDARD',
    minervini_min: int = 0,
) -> list:
    """Post-filter candidates by quality label and Minervini score."""
    _hierarchy = {'STANDARD': 0, 'HIGH': 1, 'PREMIUM': 2, 'GOLD': 3}
    min_lvl = _hierarchy.get(quality_filter, 0)
    out = []
    for c in candidates:
        if _hierarchy.get(c.get('quality', 'STANDARD'), 0) < min_lvl:
            continue
        if minervini_min > 0 and (c.get('minervini_score') or 0) < minervini_min:
            continue
        out.append(c)
    return out


# ─── Simulation wrapper ───────────────────────────────────────────────────────

def simulate(
    signals: list,
    historical: dict,
    scan_start: str,
    scan_end: str,
    tp_as_trail: bool = False,
) -> dict | None:
    """
    Thin wrapper around run_simulation from enhanced_backtest.
    Returns the report dict or None if no signals.
    """
    if not signals:
        return None
    from enhanced_backtest import run_simulation

    end_prices = _end_prices_for_regime(historical, scan_end)
    return run_simulation(
        signals, scan_start, scan_end,
        end_prices, historical,
        initial_capital=100_000,
        tp_as_trail=tp_as_trail,
        tp_trail_atr_mult=2.0,
    )


# ─── SPY Benchmark (Sharpe) per regime ───────────────────────────────────────

def _spy_sharpe(historical: dict, scan_start: str, scan_end: str) -> float:
    """Sharpe of SPY buy-and-hold for this regime (annualised)."""
    spy_df = historical.get('SPY')
    if spy_df is None:
        return 0.0
    sub = spy_df[(spy_df.index >= pd.to_datetime(scan_start)) &
                 (spy_df.index <= pd.to_datetime(scan_end))]
    if len(sub) < 5:
        return 0.0
    rets = sub['close'].pct_change().dropna()
    std  = rets.std()
    if std == 0:
        return 0.0
    return float((rets.mean() / std) * np.sqrt(252))


# ─── Optuna objective ─────────────────────────────────────────────────────────

def make_objective(
    all_historical: dict,
    opt_regimes: list,
    mode_name: str,
    date_step: int = 10,
):
    """
    Returns an Optuna objective function closed over pre-fetched data.

    Objective score = mean(Sharpe over opt-regimes) − 0.5 × std(Sharpe)
    Penalises parameter sets that only work in one market condition.
    """
    import config

    param_defs = PARAM_SPACES[mode_name]

    def objective(trial) -> float:
        # ── 1. Sample parameters ──────────────────────────────────────────
        trial_params = {}
        for pdef in param_defs:
            name = pdef[0]
            if pdef[3] == 'float':
                trial_params[name] = trial.suggest_float(name, pdef[1], pdef[2])
            elif pdef[3] == 'int':
                trial_params[name] = trial.suggest_int(name, pdef[1], pdef[2])
            elif pdef[3] == 'bool':
                trial_params[name] = trial.suggest_categorical(name, [True, False])
            elif pdef[3] == 'cat':
                trial_params[name] = trial.suggest_categorical(name, pdef[1])

        # Separate mode-config params from simulation/filter params
        mode_patch = {k: v for k, v in trial_params.items() if k not in _SIM_KEYS}
        quality_filter  = trial_params.get('quality_filter',  'HIGH')
        minervini_min   = trial_params.get('minervini_min',   0)
        tp_as_trail     = trial_params.get('tp_as_trail',     False)

        # ── 2. Patch config.MODES for this trial ──────────────────────────
        orig_mode_cfg = copy.deepcopy(config.MODES[mode_name])
        config.MODES[mode_name].update(mode_patch)

        try:
            regime_sharpes = []

            for rkey in opt_regimes:
                r = REGIMES[rkey]
                hist = all_historical[rkey]

                # Build slice: only data up to scan_end (for end_prices)
                cands = scan_regime(hist, r['scan_start'], r['scan_end'],
                                    mode_name, date_step=date_step)
                sigs  = filter_signals(cands, quality_filter, minervini_min)
                rpt   = simulate(sigs, hist, r['scan_start'], r['scan_end'],
                                 tp_as_trail=tp_as_trail)

                if rpt is None or rpt.get('total_trades', 0) < 3:
                    # Too few trades — penalise (probably over-filtered)
                    regime_sharpes.append(-1.0)
                else:
                    regime_sharpes.append(float(rpt.get('sharpe_ratio', -1.0)))

            arr = np.array(regime_sharpes)
            return float(arr.mean() - 0.5 * arr.std())

        finally:
            config.MODES[mode_name] = orig_mode_cfg

    return objective


# ─── Per-regime result table ──────────────────────────────────────────────────

def evaluate_params(
    all_historical: dict,
    regimes_to_eval: list,
    mode_name: str,
    best_params: dict,
    date_step: int = 5,
) -> list:
    """
    Full evaluation of best_params across a list of regimes.
    Returns list of result dicts (one per regime).
    """
    import config

    mode_patch     = {k: v for k, v in best_params.items() if k not in _SIM_KEYS}
    quality_filter = best_params.get('quality_filter', 'HIGH')
    minervini_min  = best_params.get('minervini_min', 0)
    tp_as_trail    = best_params.get('tp_as_trail', False)

    orig = copy.deepcopy(config.MODES[mode_name])
    config.MODES[mode_name].update(mode_patch)

    results = []
    try:
        for rkey in regimes_to_eval:
            r    = REGIMES[rkey]
            hist = all_historical[rkey]

            cands = scan_regime(hist, r['scan_start'], r['scan_end'],
                                mode_name, date_step=date_step)
            sigs  = filter_signals(cands, quality_filter, minervini_min)
            rpt   = simulate(sigs, hist, r['scan_start'], r['scan_end'],
                             tp_as_trail=tp_as_trail)
            spy_sh = _spy_sharpe(hist, r['scan_start'], r['scan_end'])

            results.append({
                'regime':       r['label'],
                'role':         r['role'],
                'signals':      len(sigs),
                'trades':       rpt.get('total_trades', 0)   if rpt else 0,
                'win_rate':     rpt.get('win_rate', 0.0)     if rpt else 0.0,
                'total_return': rpt.get('total_return', 0.0) if rpt else 0.0,
                'sharpe':       rpt.get('sharpe_ratio', 0.0) if rpt else 0.0,
                'max_dd':       rpt.get('max_drawdown', 0.0) if rpt else 0.0,
                'spy_sharpe':   spy_sh,
            })
    finally:
        config.MODES[mode_name] = orig

    return results


# ─── Pretty printing ──────────────────────────────────────────────────────────

def print_results_table(results: list, mode_name: str):
    """Print a nicely formatted result table."""
    header = f"\n{'─'*72}\n  {mode_name.upper()} — Best Params Evaluation\n{'─'*72}"
    print(header)
    fmt = "  {:<18} {:>6} {:>7} {:>7} {:>9} {:>8} {:>8}  {}"
    print(fmt.format('Regime', 'Sigs', 'Trades', 'WR%', 'Return%', 'Sharpe', 'MaxDD%', 'vs SPY'))
    print('  ' + '─' * 70)
    for r in results:
        vs_spy = '★ BEATS' if r['sharpe'] > r['spy_sharpe'] else '  below'
        tag    = ' [HOLD-OUT]' if r['role'] == 'validate' else ''
        print(fmt.format(
            r['regime'] + tag,
            r['signals'],
            r['trades'],
            f"{r['win_rate']:.1f}",
            f"{r['total_return']:+.1f}",
            f"{r['sharpe']:.2f}",
            f"{r['max_dd']:.1f}",
            vs_spy,
        ))
    print()


def print_config_patch(mode_name: str, best_params: dict):
    """Print the MODES config patch ready to paste into config.py."""
    import config

    mode_patch = {k: v for k, v in best_params.items() if k not in _SIM_KEYS}
    print(f"\n{'─'*72}")
    print(f"  CONFIG.PY PATCH — MODES['{mode_name}'] (paste these values)")
    print(f"{'─'*72}")
    current = config.MODES.get(mode_name, {})
    for key, val in sorted(mode_patch.items()):
        old = current.get(key, 'N/A')
        marker = '  ← changed' if val != old else ''
        if isinstance(val, float):
            print(f"    '{key}': {val:.4g},   # was {old}{marker}")
        else:
            print(f"    '{key}': {val!r},   # was {old!r}{marker}")

    # Simulation/filter params
    print(f"\n  # Simulation / filter params (pass at runtime):")
    for key in sorted(_SIM_KEYS):
        if key in best_params:
            print(f"    {key} = {best_params[key]!r}")
    print()


def apply_config_patch(mode_name: str, best_params: dict):
    """Write best mode params directly into config.py (in-place)."""
    import config

    mode_patch = {k: v for k, v in best_params.items() if k not in _SIM_KEYS}
    for key, val in mode_patch.items():
        config.MODES[mode_name][key] = val

    # Persist to config.py
    config_path = Path(__file__).parent / 'config.py'
    src = config_path.read_text()

    # Find the mode block and update values
    for key, val in mode_patch.items():
        import re
        if isinstance(val, float):
            new_val = f'{val:.4g}'
        elif isinstance(val, bool):
            new_val = str(val)
        else:
            new_val = repr(val)

        # Match lines like:  'key': <old_value>,  (with optional comment)
        pattern = rf"([ \t]*'{re.escape(key)}'[ \t]*:[ \t]*)([^,\n]+)(,?[ \t]*(?:#[^\n]*)?)"
        replacement = rf"\g<1>{new_val}\g<3>"
        src_new = re.sub(pattern, replacement, src, count=1)
        if src_new != src:
            src = src_new
            print(f"  config.py: updated MODES['{mode_name}']['{key}'] → {new_val}")

    config_path.write_text(src)
    print(f"  ✓ config.py patched for mode '{mode_name}'")


# ─── Main optimisation driver ─────────────────────────────────────────────────

def optimize_mode(
    mode_name: str,
    all_historical: dict,
    opt_regimes: list,
    n_trials: int = 80,
    date_step: int = 10,
) -> dict:
    """
    Run Optuna TPE optimisation for a single mode.
    Returns the best params dict.
    """
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        print("ERROR: optuna not installed.  Run:  pip install optuna")
        sys.exit(1)

    print(f"\n{'='*72}")
    print(f"  Optimising MODE: {mode_name.upper()}  ({n_trials} trials, "
          f"date_step={date_step})")
    print(f"  Regimes: {', '.join(REGIMES[r]['label'] for r in opt_regimes)}")
    print(f"{'='*72}")

    sampler = optuna.samplers.TPESampler(seed=42, n_startup_trials=15)
    study   = optuna.create_study(direction='maximize', sampler=sampler)
    obj     = make_objective(all_historical, opt_regimes, mode_name, date_step)

    best_score = [float('-inf')]

    def _callback(study, trial):
        if trial.value is not None and trial.value > best_score[0]:
            best_score[0] = trial.value
            print(f"  Trial {trial.number:3d}  score={trial.value:+.3f}  "
                  f"(new best)  "
                  f"vol={trial.params.get('vol_thresh', '-'):.2f}  "
                  f"sl={trial.params.get('sl_mult', '-')}  "
                  f"tp={trial.params.get('tp_mult', '-')}")
        elif trial.number % 10 == 0:
            print(f"  Trial {trial.number:3d}  score={trial.value:+.3f}  "
                  f"best so far={best_score[0]:+.3f}")

    study.optimize(obj, n_trials=n_trials, callbacks=[_callback],
                   show_progress_bar=False)

    best = study.best_params
    print(f"\n  ✓ Best score: {study.best_value:+.4f}")
    print(f"  Best params: {best}")
    return best


# ─── CLI ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description='Multi-mode parameter optimizer for scalping / daytrade / swing'
    )
    p.add_argument('--mode', default='all',
                   help='Mode(s) to optimise: swing | daytrade | scalping | all '
                        '(or comma-separated, e.g. swing,scalping)')
    p.add_argument('--trials', type=int, default=80,
                   help='Optuna trials per mode (default: 80)')
    p.add_argument('--symbols', type=int, default=0,
                   help='Number of symbols to use (0 = all 40 defaults)')
    p.add_argument('--date-step', type=int, default=10,
                   help='Scan cadence in trading days (default 10 = bi-weekly)')
    p.add_argument('--no-patch', action='store_true',
                   help='Skip patching config.py with best params')
    p.add_argument('--watchlist', default=None,
                   help='Custom symbol file (one per line or comma-separated)')
    p.add_argument('--output-dir', default='scanner_output/backtests/mode_optimizer',
                   help='Directory for result JSON files')
    return p.parse_args()


def load_symbols_from_file(path: str) -> list:
    try:
        text = Path(path).read_text()
        syms = []
        for tok in text.replace('\n', ',').split(','):
            tok = tok.strip()
            if not tok or tok.startswith('#'):
                continue
            if ':' in tok:
                tok = tok.split(':')[1]
            if tok:
                syms.append(tok)
        return sorted(set(syms))
    except FileNotFoundError:
        print(f"WARNING: watchlist {path} not found, using defaults")
        return []


def main():
    args = parse_args()

    # ── Determine modes to run ────────────────────────────────────────────
    all_modes = list(PARAM_SPACES.keys())
    if args.mode.lower() == 'all':
        modes = all_modes
    else:
        modes = [m.strip().lower() for m in args.mode.split(',')]
        for m in modes:
            if m not in PARAM_SPACES:
                print(f"ERROR: unknown mode '{m}'. Valid: {all_modes}")
                sys.exit(1)

    # ── Symbol universe ───────────────────────────────────────────────────
    if args.watchlist:
        symbols = load_symbols_from_file(args.watchlist)
    else:
        symbols = DEFAULT_SYMBOLS.copy()

    if args.symbols > 0:
        symbols = symbols[:args.symbols]

    print(f"\n{'='*72}")
    print(f"  Mode Optimizer — modes: {modes}  trials: {args.trials}")
    print(f"  Symbols: {len(symbols)}  date_step: {args.date_step}")
    print(f"{'='*72}")

    # ── Compute the full data range needed ────────────────────────────────
    all_data_start = min(r['data_start'] for r in REGIMES.values())
    all_data_end   = max(r['scan_end']   for r in REGIMES.values())

    print(f"\n  Fetching data: {all_data_start} → {all_data_end} "
          f"({len(symbols)} symbols)...")
    full_hist = fetch_all_data(symbols, all_data_start, all_data_end)

    # ── Slice historical data per regime ──────────────────────────────────
    # Each regime gets its own history slice (from data_start to scan_end)
    # to prevent future-data leakage.
    all_historical = {}
    for rkey, r in REGIMES.items():
        ds = pd.to_datetime(r['data_start'])
        de = pd.to_datetime(r['scan_end'])
        sliced = {}
        for sym, df in full_hist.items():
            sub = df[(df.index >= ds) & (df.index <= de)]
            if len(sub) > 50:
                sliced[sym] = sub
        all_historical[rkey] = sliced
        print(f"  {r['label']:18s}: {len(sliced)-1} symbols, "
              f"{r['data_start']} → {r['scan_end']}")

    opt_regimes = [k for k, r in REGIMES.items() if r['role'] == 'optimize']
    val_regimes = [k for k, r in REGIMES.items() if r['role'] == 'validate']

    # ── Output directory ──────────────────────────────────────────────────
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Run optimisation per mode ─────────────────────────────────────────
    all_best = {}
    for mode_name in modes:
        best_params = optimize_mode(
            mode_name, all_historical, opt_regimes,
            n_trials=args.trials, date_step=args.date_step,
        )
        all_best[mode_name] = best_params

        # ── Final evaluation on ALL regimes (incl. hold-out) ─────────────
        print(f"\n  Running final evaluation (date_step=5)...")
        all_eval_regimes = opt_regimes + val_regimes
        results = evaluate_params(
            all_historical, all_eval_regimes, mode_name,
            best_params, date_step=5,
        )
        print_results_table(results, mode_name)
        print_config_patch(mode_name, best_params)

        # ── Save results to JSON ──────────────────────────────────────────
        ts = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
        out_path = out_dir / f'{mode_name}_best_{ts}.json'
        payload = {
            'mode':        mode_name,
            'best_params': best_params,
            'results':     results,
            'trials':      args.trials,
            'symbols':     len(symbols),
            'date_step':   args.date_step,
            'timestamp':   ts,
        }
        with open(out_path, 'w') as f:
            json.dump(payload, f, indent=2, default=str)
        print(f"  Results saved → {out_path}")

        # ── Optionally patch config.py ────────────────────────────────────
        if not args.no_patch:
            print(f"\n  Patching config.py for mode '{mode_name}'...")
            apply_config_patch(mode_name, best_params)

    # ── Cross-mode summary ────────────────────────────────────────────────
    if len(modes) > 1:
        print(f"\n{'='*72}")
        print("  OPTIMISATION COMPLETE — Summary")
        print(f"{'='*72}")
        for mode_name, best in all_best.items():
            mf = best.get('quality_filter', 'HIGH')
            mm = best.get('minervini_min', 0)
            tp = best.get('tp_as_trail', False)
            print(f"  {mode_name:10s}  quality≥{mf}  "
                  f"minervini≥{mm}  tp_trail={tp}  "
                  f"vol={best.get('vol_thresh', '?')}  "
                  f"sl={best.get('sl_mult', '?')}×ATR  "
                  f"tp={best.get('tp_mult', '?')}×ATR")

    print("\n  Done.\n")


if __name__ == '__main__':
    main()
