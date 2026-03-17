#!/usr/bin/env python3
"""
Regime Deep Investigation
==========================
Answers: "Why doesn't fewer-but-better regime-filtered trades beat V9-C significantly?"

Phase 1 — Diagnosis
  • Collect ALL V9-C trades with their regime tags
  • Print per-regime P&L breakdown: WR, avg win, avg loss, expectancy, count
  • Show which regimes are hurting V9-C vs. helping it

Phase 2 — Optuna Optimization
  • Treat regime thresholds + position-size multipliers as hyperparameters
  • Walk-forward: optimise on 2022-2023, validate on 2024
  • Objective: maximise Sharpe on held-out year
  • Print optimal params and projected vs V9-C baseline

Usage:
  python regime_deep_investigation.py
  python regime_deep_investigation.py --years 2022,2023,2024 --trials 200
"""

import argparse
import asyncio
import sys
import logging
from datetime import timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from backtest_regime_compare import (
    load_symbols, fetch_all_data, run_scan, spy_benchmark
)

logging.basicConfig(level=logging.WARNING, format='%(message)s')

# ─── Configurable regime classifier ───────────────────────────────────────────
def classify_regime(spy_df, sim_date, params):
    """
    Parametric version of classify_day_regime.
    params keys: lookback, red_thresh, bear_thresh, choppy_pct, choppy_vol, exp_thresh
    """
    lookback = int(params.get('lookback', 15))
    mask = spy_df.index <= sim_date
    df_s = spy_df[mask].tail(lookback + 5)
    if len(df_s) < 3:
        return 'NORMAL'

    spy_pct = float((df_s['close'].iloc[-1] / df_s['close'].iloc[-lookback] - 1) * 100) \
        if len(df_s) >= lookback else 0.0
    daily_rets = df_s['close'].pct_change().dropna()
    vol_pct = float(daily_rets.std() * 100) if len(daily_rets) >= 5 else 0.5

    if spy_pct <= params.get('red_thresh', -1.5):
        return 'RED_MARKET'
    elif spy_pct <= params.get('bear_thresh', -0.5):
        return 'BEARISH'
    elif abs(spy_pct) < params.get('choppy_pct', 0.5) and vol_pct < params.get('choppy_vol', 0.35):
        return 'CHOPPY'
    elif spy_pct >= params.get('exp_thresh', 2.0):
        return 'EXPANSION'
    else:
        return 'NORMAL'


# ─── Simulation with per-trade logging ────────────────────────────────────────
def simulate_detailed(signals, start_date, end_date, end_prices, historical,
                      capital=100_000, tp_as_trail=True,
                      regime_mults=None, spy_df=None, regime_params=None):
    """
    Same simulation as backtest_regime_compare.simulate(), but:
      • Returns (metrics_dict, trades_list) where each trade has regime + pnl info
      • Uses configurable regime_mults for position sizing
      • Re-classifies regime using regime_params if provided
    """
    if not signals:
        return None, []

    MAX_HOLD       = 30
    ATR_TRAIL_MULT = 2.0
    MAX_POS_PCT    = 0.10
    MAX_RISK_PCT   = 0.02

    DEFAULT_MULTS = {'EXPANSION': 1.0, 'NORMAL': 1.0, 'CHOPPY': 1.0,
                     'BEARISH': 1.0, 'RED_MARKET': 1.0}
    rmults = regime_mults if regime_mults else DEFAULT_MULTS

    # Optionally re-classify regime per signal using new params
    if regime_params and spy_df is not None:
        for s in signals:
            s = dict(s)  # don't mutate originals
        reclassed = []
        for s in signals:
            s2 = dict(s)
            s2['regime'] = classify_regime(spy_df, s2['date'], regime_params)
            reclassed.append(s2)
        signals = reclassed

    cap = float(capital)
    trades = []
    open_pos = {}
    equity_curve = []

    sig_list = sorted(signals, key=lambda s: s['date'])
    spy = historical.get('SPY')
    if spy is not None:
        trading_days = sorted(spy.index[
            (spy.index >= pd.Timestamp(start_date)) &
            (spy.index <= pd.Timestamp(end_date))
        ])
    else:
        trading_days = pd.date_range(start=start_date, end=end_date, freq='B').tolist()

    sig_by_date = {}
    for s in sig_list:
        d = pd.Timestamp(s['date']).normalize()
        sig_by_date.setdefault(d, []).append(s)

    for today in trading_days:
        today_norm = today.normalize()

        for sig in sig_by_date.get(today_norm, []):
            sym = sig['symbol']
            if sym in open_pos:
                continue
            price = float(sig.get('price', 0))
            stop  = float(sig.get('stop_loss', price * 0.95))
            tp    = float(sig.get('take_profit', price * 1.10))
            if price <= 0 or stop <= 0:
                continue
            risk_per_share = abs(price - stop)
            if risk_per_share <= 0:
                continue
            regime = sig.get('regime', 'NORMAL')
            mult   = rmults.get(regime, 1.0)
            qty_by_val  = int(cap * MAX_POS_PCT * mult / price)
            qty_by_risk = int(cap * MAX_RISK_PCT * mult / risk_per_share)
            qty = min(qty_by_val, qty_by_risk)
            if qty < 1:
                qty = 1 if cap >= price else None
            if qty is None:
                continue
            cost = price * qty
            if cost > cap:
                qty = max(1, int(cap / price))
                cost = price * qty
            cap -= cost
            open_pos[sym] = {
                'entry_price': price, 'stop': stop, 'take_profit': tp,
                'qty': qty, 'cost': cost, 'entry_date': today_norm,
                'regime': regime, 'signal_type': sig.get('type', 'BREAKOUT'),
                '_tp_hit': False, '_trail_stop': None,
            }

        for sym, pos in list(open_pos.items()):
            df = historical.get(sym)
            if df is None:
                continue
            bar = df[df.index.normalize() == today_norm]
            if bar.empty:
                continue
            hi = float(bar.iloc[-1]['high'])
            lo = float(bar.iloc[-1]['low'])
            cl = float(bar.iloc[-1]['close'])
            op = float(bar.iloc[-1]['open'])
            days_held = (today_norm - pos['entry_date']).days
            exit_price = None
            exit_reason = None

            if today_norm == pos['entry_date']:
                pass
            elif tp_as_trail:
                if not pos['_tp_hit'] and hi >= pos['take_profit']:
                    pos['_tp_hit'] = True
                    df_atr = df[df.index <= today].tail(15)
                    if len(df_atr) >= 14:
                        tr = pd.concat([
                            df_atr['high'] - df_atr['low'],
                            (df_atr['high'] - df_atr['close'].shift(1)).abs(),
                            (df_atr['low']  - df_atr['close'].shift(1)).abs(),
                        ], axis=1).max(axis=1)
                        pos['_trail_stop'] = cl - ATR_TRAIL_MULT * tr.mean()
                    else:
                        pos['_trail_stop'] = pos['take_profit'] * 0.95
                if pos['_tp_hit'] and pos['_trail_stop'] is not None:
                    df_atr = df[df.index <= today].tail(15)
                    if len(df_atr) >= 14:
                        tr = pd.concat([
                            df_atr['high'] - df_atr['low'],
                            (df_atr['high'] - df_atr['close'].shift(1)).abs(),
                            (df_atr['low']  - df_atr['close'].shift(1)).abs(),
                        ], axis=1).max(axis=1)
                        new_trail = cl - ATR_TRAIL_MULT * tr.mean()
                        if new_trail > pos['_trail_stop']:
                            pos['_trail_stop'] = new_trail
                    if lo <= pos['_trail_stop']:
                        exit_price  = max(pos['_trail_stop'], op)
                        exit_reason = 'TrailStop'
                elif lo <= pos['stop']:
                    exit_price  = min(pos['stop'], op) if op < pos['stop'] else pos['stop']
                    exit_reason = 'StopLoss'
            else:
                if lo <= pos['stop']:
                    exit_price  = min(pos['stop'], op) if op < pos['stop'] else pos['stop']
                    exit_reason = 'StopLoss'
                elif hi >= pos['take_profit']:
                    exit_price  = pos['take_profit']
                    exit_reason = 'TakeProfit'

            if exit_price is None and days_held >= MAX_HOLD:
                exit_price  = cl
                exit_reason = 'MaxHold'

            if exit_price is not None:
                qty  = pos['qty']
                pnl  = (exit_price - pos['entry_price']) * qty
                cost = pos['cost']
                trades.append({
                    'symbol':      sym,
                    'regime':      pos['regime'],
                    'signal_type': pos['signal_type'],
                    'entry':       pos['entry_price'],
                    'exit':        exit_price,
                    'reason':      exit_reason,
                    'days_held':   days_held,
                    'pnl':         pnl,
                    'pnl_pct':     pnl / cost * 100,
                    'win':         pnl > 0,
                })
                cap += exit_price * qty
                del open_pos[sym]

        open_val = sum(
            historical[s].loc[historical[s].index.normalize() == today_norm, 'close'].iloc[-1] * p['qty']
            if s in historical and not historical[s][historical[s].index.normalize() == today_norm].empty
            else p['entry_price'] * p['qty']
            for s, p in open_pos.items()
        )
        equity_curve.append(cap + open_val)

    for sym, pos in list(open_pos.items()):
        ep  = end_prices.get(sym, pos['entry_price'])
        qty = pos['qty']
        pnl = (ep - pos['entry_price']) * qty
        trades.append({
            'symbol': sym, 'regime': pos['regime'],
            'signal_type': pos['signal_type'],
            'entry': pos['entry_price'], 'exit': ep,
            'reason': 'SimEnd', 'days_held': (trading_days[-1].normalize() - pos['entry_date']).days if trading_days else 0,
            'pnl': pnl, 'pnl_pct': pnl / pos['cost'] * 100, 'win': pnl > 0,
        })
        cap += ep * qty

    if not trades:
        return None, []

    n_trades = len(trades)
    n_wins   = sum(1 for t in trades if t.get('win', False) is True)
    win_rate = (n_wins / n_trades * 100) if n_trades > 0 else 0.0
    total_return = (cap - capital) / capital * 100
    eq = np.array(equity_curve) if equity_curve else np.array([float(capital)])
    daily_ret = np.diff(eq) / eq[:-1]
    sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)
              if len(daily_ret) > 1 and daily_ret.std() > 0 else 0.0)
    max_dd = float(((eq / np.maximum.accumulate(eq)) - 1).min() * 100)

    metrics = {
        'total_trades': n_trades, 'win_rate': win_rate,
        'total_return': total_return, 'sharpe_ratio': sharpe,
        'max_drawdown': max_dd, 'total_pnl': cap - capital,
        'avg_win':  np.mean([t['pnl_pct'] for t in trades if t['win']]) if n_wins else 0,
        'avg_loss': np.mean([t['pnl_pct'] for t in trades if not t['win']]) if (n_trades - n_wins) else 0,
    }
    return metrics, trades


# ─── Phase 1: Per-regime breakdown ────────────────────────────────────────────
def phase1_diagnosis(symbols, years, capital):
    print("\n" + "="*80)
    print("PHASE 1 — DIAGNOSIS: V9-C Trade Quality per Regime")
    print("="*80)

    all_trades = []

    for year in years:
        start, end = f"{year}-01-01", f"{year}-12-31"
        print(f"\n  Loading {year}...")
        historical, end_prices = fetch_all_data(symbols, start, end)
        if len(historical) < 5:
            continue
        spy_df = historical.get('SPY')

        old_sigs = run_scan(historical, start, end, ['swing', 'longterm'], config='old')
        v9c_sigs = [s for s in old_sigs
                    if s.get('quality') in ('GOLD', 'PREMIUM')
                    and (s.get('minervini_score', 0) >= 7
                         or s.get('type') in ('SMA20_CROSS', 'BOUNCE', 'CONTINUATION', 'Momentum'))]

        _, trades = simulate_detailed(v9c_sigs, start, end, end_prices, historical,
                                      capital=capital, tp_as_trail=True)
        for t in trades:
            t['year'] = year
        all_trades.extend(trades)

    if not all_trades:
        print("  No trades collected.")
        return

    df = pd.DataFrame(all_trades)

    # ── Overall stats
    print(f"\n{'─'*70}")
    print(f"  OVERALL  |  {len(df)} trades  |  WR={df['win'].mean()*100:.1f}%  |  "
          f"Avg P&L={df['pnl_pct'].mean():.2f}%  |  Expectancy={df['pnl_pct'].mean():.2f}%")
    print(f"{'─'*70}")

    # ── Regime breakdown
    print(f"\n  {'Regime':<14} {'Count':>6} {'WR%':>7} {'AvgPnL%':>9} {'AvgWin%':>9} "
          f"{'AvgLoss%':>10} {'Expectancy':>11} {'Verdict':>10}")
    print(f"  {'─'*82}")

    regime_order = ['EXPANSION', 'NORMAL', 'CHOPPY', 'BEARISH', 'RED_MARKET']
    for regime in regime_order:
        sub = df[df['regime'] == regime]
        if sub.empty:
            continue
        n = len(sub)
        wr = sub['win'].mean() * 100
        avg_pnl = sub['pnl_pct'].mean()
        avg_win  = sub[sub['win']]['pnl_pct'].mean() if sub['win'].any() else 0
        avg_loss = sub[~sub['win']]['pnl_pct'].mean() if (~sub['win']).any() else 0
        # Expectancy = WR*avgWin + (1-WR)*avgLoss
        expectancy = (wr/100) * avg_win + (1 - wr/100) * avg_loss
        overall_avg = df['pnl_pct'].mean()
        verdict = 'KEEP  ✓' if avg_pnl > overall_avg * 0.8 else 'REVIEW' if avg_pnl > 0 else 'BLOCK ✗'
        print(f"  {regime:<14} {n:>6} {wr:>7.1f}% {avg_pnl:>+9.2f}% {avg_win:>+9.2f}% "
              f"{avg_loss:>+10.2f}% {expectancy:>+11.2f}% {verdict:>10}")

    # ── Exit reason breakdown per regime
    print(f"\n  Exit reason breakdown (% of trades ending each way per regime):")
    print(f"  {'Regime':<14} {'StopLoss':>9} {'TakeProfit':>11} {'TrailStop':>10} {'MaxHold':>9} {'SimEnd':>8}")
    print(f"  {'─'*65}")
    for regime in regime_order:
        sub = df[df['regime'] == regime]
        if sub.empty:
            continue
        n = len(sub)
        reasons = {r: f"{(sub['reason'] == r).sum()/n*100:>6.1f}%"
                   for r in ['StopLoss', 'TakeProfit', 'TrailStop', 'MaxHold', 'SimEnd']}
        print(f"  {regime:<14} {reasons['StopLoss']:>9} {reasons['TakeProfit']:>11} "
              f"{reasons['TrailStop']:>10} {reasons['MaxHold']:>9} {reasons['SimEnd']:>8}")

    # ── Per-year, per-regime heatmap
    print(f"\n  Avg P&L% by (Year × Regime):")
    pivot_data = {}
    for year in df['year'].unique():
        yr_df = df[df['year'] == year]
        pivot_data[year] = {r: yr_df[yr_df['regime'] == r]['pnl_pct'].mean()
                             for r in regime_order}

    header = f"  {'Regime':<14}" + "".join(f"{y:>10}" for y in sorted(pivot_data.keys()))
    print(header)
    print(f"  {'─'*60}")
    for regime in regime_order:
        row = f"  {regime:<14}"
        for year in sorted(pivot_data.keys()):
            val = pivot_data[year].get(regime, float('nan'))
            if pd.isna(val):
                row += f"{'N/A':>10}"
            else:
                row += f"{val:>+10.2f}%"
        print(row)

    # ── Key diagnostic: what % of total P&L comes from each regime
    print(f"\n  Share of total P&L by regime (shows which regimes are most valuable):")
    total_pnl_sum = df['pnl'].sum()
    for regime in regime_order:
        sub = df[df['regime'] == regime]
        if sub.empty:
            continue
        regime_pnl = sub['pnl'].sum()
        pct_of_total = (regime_pnl / total_pnl_sum * 100) if total_pnl_sum != 0 else 0
        bar = '█' * max(0, int(abs(pct_of_total) / 3)) + ('▌' if abs(pct_of_total) % 3 > 1.5 else '')
        sign = '+' if pct_of_total >= 0 else ''
        print(f"  {regime:<14} {sign}{pct_of_total:>6.1f}%  {bar}")

    return df


# ─── Phase 2: Optuna regime optimization ──────────────────────────────────────
def phase2_optuna(symbols, train_years, val_year, capital, n_trials):
    print("\n" + "="*80)
    print(f"PHASE 2 — OPTUNA: Optimise regime params  "
          f"(train={train_years}, validate={val_year})")
    print("="*80)

    # Pre-load all data once
    all_years = train_years + [val_year]
    year_data = {}
    for year in all_years:
        start, end = f"{year}-01-01", f"{year}-12-31"
        print(f"  Loading {year}...")
        historical, end_prices = fetch_all_data(symbols, start, end)
        old_sigs = run_scan(historical, start, end, ['swing', 'longterm'], config='old')
        v9c_sigs = [s for s in old_sigs
                    if s.get('quality') in ('GOLD', 'PREMIUM')
                    and (s.get('minervini_score', 0) >= 7
                         or s.get('type') in ('SMA20_CROSS', 'BOUNCE', 'CONTINUATION', 'Momentum'))]
        spy_df = historical.get('SPY')
        year_data[year] = dict(historical=historical, end_prices=end_prices,
                               signals=v9c_sigs, spy_df=spy_df)
        spy_info = spy_benchmark(historical, start, end, capital)
        print(f"    {len(v9c_sigs)} V9-C signals  |  SPY={spy_info['return']:+.1f}%")

    def objective(trial):
        # Regime threshold params
        params = {
            'lookback':    trial.suggest_int('lookback', 10, 30),
            'red_thresh':  trial.suggest_float('red_thresh', -4.0, -0.5),
            'bear_thresh': trial.suggest_float('bear_thresh', -2.0, 0.0),
            'choppy_pct':  trial.suggest_float('choppy_pct', 0.2, 1.5),
            'choppy_vol':  trial.suggest_float('choppy_vol', 0.2, 0.8),
            'exp_thresh':  trial.suggest_float('exp_thresh', 0.5, 4.0),
        }
        # Position sizing multipliers per regime
        rmults = {
            'EXPANSION':  trial.suggest_float('mult_exp',    0.5, 2.0),
            'NORMAL':     trial.suggest_float('mult_normal', 0.5, 1.5),
            'CHOPPY':     trial.suggest_float('mult_choppy', 0.0, 1.0),
            'BEARISH':    trial.suggest_float('mult_bear',   0.0, 0.8),
            'RED_MARKET': trial.suggest_float('mult_red',    0.0, 0.5),
        }

        train_sharpes = []
        for year in train_years:
            yd = year_data[year]
            metrics, _ = simulate_detailed(
                yd['signals'], f"{year}-01-01", f"{year}-12-31",
                yd['end_prices'], yd['historical'],
                capital=capital, tp_as_trail=True,
                regime_mults=rmults, spy_df=yd['spy_df'], regime_params=params,
            )
            if metrics:
                train_sharpes.append(metrics['sharpe_ratio'])

        return np.mean(train_sharpes) if train_sharpes else -999.0

    print(f"\n  Running {n_trials} Optuna trials...")
    study = optuna.create_study(direction='maximize',
                                sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best = study.best_params
    print(f"\n  Best train Sharpe: {study.best_value:.3f}")
    print(f"\n  Optimal regime parameters:")
    print(f"    Lookback:      {best['lookback']} days")
    print(f"    RED_MARKET ≤:  {best['red_thresh']:+.2f}%")
    print(f"    BEARISH ≤:     {best['bear_thresh']:+.2f}%")
    print(f"    CHOPPY when:   |SPY|<{best['choppy_pct']:.2f}% & vol<{best['choppy_vol']:.2f}%")
    print(f"    EXPANSION ≥:   {best['exp_thresh']:+.2f}%")
    print(f"\n  Optimal position-size multipliers:")
    for regime, key in [('EXPANSION','mult_exp'), ('NORMAL','mult_normal'),
                        ('CHOPPY','mult_choppy'), ('BEARISH','mult_bear'),
                        ('RED_MARKET','mult_red')]:
        bar = '█' * int(best[key] * 10)
        print(f"    {regime:<14} {best[key]:.2f}x  {bar}")

    # ── Validate on held-out year
    print(f"\n  {'─'*60}")
    print(f"  Validation on {val_year} (held-out):")
    best_params = {k: best[k] for k in ['lookback','red_thresh','bear_thresh',
                                          'choppy_pct','choppy_vol','exp_thresh']}
    best_rmults = {
        'EXPANSION':  best['mult_exp'],
        'NORMAL':     best['mult_normal'],
        'CHOPPY':     best['mult_choppy'],
        'BEARISH':    best['mult_bear'],
        'RED_MARKET': best['mult_red'],
    }
    yd = year_data[val_year]
    spy_info = spy_benchmark(yd['historical'], f"{val_year}-01-01", f"{val_year}-12-31", capital)

    # Baseline V9-C (no regime sizing)
    base_metrics, _ = simulate_detailed(
        yd['signals'], f"{val_year}-01-01", f"{val_year}-12-31",
        yd['end_prices'], yd['historical'], capital=capital, tp_as_trail=True)

    # Optimised regime-adaptive
    opt_metrics, opt_trades = simulate_detailed(
        yd['signals'], f"{val_year}-01-01", f"{val_year}-12-31",
        yd['end_prices'], yd['historical'],
        capital=capital, tp_as_trail=True,
        regime_mults=best_rmults, spy_df=yd['spy_df'], regime_params=best_params)

    def fmt(m, label, spy_ret):
        if m is None:
            print(f"  {label:<45} NO DATA")
            return
        diff = m['total_return'] - spy_ret
        print(f"  {label:<45} {m['total_return']:>+8.2f}%  WR={m['win_rate']:>5.1f}%  "
              f"Sharpe={m['sharpe_ratio']:>6.3f}  DD={m['max_drawdown']:>+7.2f}%  vsΔSPY={diff:>+7.2f}%")

    print(f"  {'SPY Buy & Hold':<45} {spy_info['return']:>+8.2f}%  "
          f"Sharpe={spy_info['sharpe']:>6.3f}  DD={spy_info['drawdown']:>+7.2f}%")
    fmt(base_metrics, f"V9-C baseline (no regime sizing)", spy_info['return'])
    fmt(opt_metrics,  f"V9-C + Optuna regime sizing",      spy_info['return'])

    # ── Per-regime breakdown of optimised result
    if opt_trades:
        df_opt = pd.DataFrame(opt_trades)
        print(f"\n  Optimised trade breakdown by regime ({val_year}):")
        print(f"  {'Regime':<14} {'Count':>6} {'WR%':>7} {'AvgPnL%':>9} {'SizeMult':>9}")
        for regime in ['EXPANSION', 'NORMAL', 'CHOPPY', 'BEARISH', 'RED_MARKET']:
            sub = df_opt[df_opt['regime'] == regime]
            if sub.empty:
                continue
            wr  = sub['win'].mean() * 100
            avg = sub['pnl_pct'].mean()
            m   = best_rmults.get(regime, 1.0)
            print(f"  {regime:<14} {len(sub):>6} {wr:>7.1f}% {avg:>+9.2f}% {m:>9.2f}x")

    # ── Importances
    try:
        importances = optuna.importance.get_param_importances(study)
        print(f"\n  Top hyperparameter importances (what matters most):")
        for k, v in sorted(importances.items(), key=lambda x: -x[1])[:8]:
            bar = '█' * int(v * 40)
            print(f"    {k:<22} {v:.3f}  {bar}")
    except Exception:
        pass

    return best_params, best_rmults


# ─── CLI ──────────────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--years',    default='2022,2023,2024', help='All years to analyse')
    p.add_argument('--val-year', type=int, default=2024,   help='Held-out validation year')
    p.add_argument('--limit',    type=int, default=0)
    p.add_argument('--capital',  type=int, default=100_000)
    p.add_argument('--trials',   type=int, default=150,    help='Optuna trial count')
    p.add_argument('--phase',    default='both',           help='1=diagnosis, 2=optuna, both')
    return p.parse_args()


def main():
    args = parse_args()
    years     = [int(y.strip()) for y in args.years.split(',')]
    val_year  = args.val_year
    train_yrs = [y for y in years if y != val_year]

    print("="*80)
    print("REGIME DEEP INVESTIGATION")
    print("="*80)
    print(f"Years: {years}  |  Train: {train_yrs}  |  Validate: {val_year}")
    print(f"Symbols: {'all' if not args.limit else args.limit}  |  "
          f"Capital: ${args.capital:,}  |  Optuna trials: {args.trials}")

    symbols = load_symbols(limit=args.limit)
    if not symbols:
        print("No symbols found.")
        return

    if args.phase in ('1', 'both'):
        phase1_diagnosis(symbols, years, args.capital)

    if args.phase in ('2', 'both'):
        if len(train_yrs) < 1:
            print("Need at least 1 train year for Optuna. Add more --years.")
        else:
            phase2_optuna(symbols, train_yrs, val_year, args.capital, args.trials)

    print(f"\n{'='*80}")
    print("INVESTIGATION COMPLETE")
    print("="*80)


if __name__ == '__main__':
    main()
