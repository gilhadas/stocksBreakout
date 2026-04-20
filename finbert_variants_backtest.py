#!/usr/bin/env python3
"""
finbert_variants_backtest.py
============================
Compare advanced FinBERT usage variants vs current baseline.

Variants
--------
  A: Baseline HIGH+              — reference (no FinBERT)
  B: Baseline PREMIUM+           — reference (no FinBERT, stricter)
  C: FinBERT PREMIUM+            — current promotion behavior
  D: FinBERT + Bearish Veto      — drop signals with strongly bearish FinBERT
  E: FinBERT + Veto + Boosted    — D + 2x sizing cap on promoted signals

The "bearish veto" removes any baseline signal where FinBERT returns
bearish with score ≥ 0.85 and net ≤ -0.30.

The "boosted sizing" temporarily raises ATR_SIZING['max_single_position_pct']
from 0.10 → 0.20 and QUALITY_SIZING['GOLD'] from 2.0 → 4.0 for the duration
of the simulation, then restores them. This tests whether higher conviction
(FinBERT-promoted) signals benefit from larger allocations.

Usage
-----
  venv/bin/python finbert_variants_backtest.py --watchlist input/thematic_all.txt
  venv/bin/python finbert_variants_backtest.py --start 2026-03-08 --end 2026-04-18
"""

import argparse
import asyncio
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import warnings
warnings.filterwarnings('ignore')

import pandas as pd

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

sys.path.insert(0, str(Path(__file__).parent))

from enhanced_backtest import (
    fetch_all_data, load_symbols, run_all_scans,
    run_simulation, calculate_spy_benchmark,
)
from finbert_backtest import enrich_and_promote
import config as _cfg
from mock_trader import MockTrader

# Bearish veto thresholds
VETO_SCORE = 0.85
VETO_NET   = -0.30

_HIGH_PLUS    = {'HIGH', 'PREMIUM', 'GOLD'}
_PREMIUM_PLUS = {'PREMIUM', 'GOLD'}


def _filter(signals, tiers):
    return [s for s in signals if s.get('quality') in tiers]


def apply_bearish_veto(signals: List[Dict]) -> List[Dict]:
    """Drop signals where FinBERT returned strong bearish sentiment."""
    kept = []
    vetoed = []
    for s in signals:
        if (s.get('finbert_label') == 'bearish'
            and s.get('finbert_score', 0) >= VETO_SCORE
            and s.get('finbert_net', 0) <= VETO_NET):
            vetoed.append(s)
        else:
            kept.append(s)
    return kept, vetoed


@contextmanager
def boosted_sizing(position_cap=0.20, gold_mult=4.0, premium_mult=3.0):
    """Temporarily raise position caps + GOLD/PREMIUM multipliers."""
    orig_cap  = _cfg.ATR_SIZING['max_single_position_pct']
    orig_gold = _cfg.QUALITY_SIZING['GOLD']
    orig_prem = _cfg.QUALITY_SIZING['PREMIUM']
    orig_mt_gold = MockTrader.QUALITY_MULTIPLIERS['GOLD']
    orig_mt_prem = MockTrader.QUALITY_MULTIPLIERS['PREMIUM']

    _cfg.ATR_SIZING['max_single_position_pct'] = position_cap
    _cfg.QUALITY_SIZING['GOLD']    = gold_mult
    _cfg.QUALITY_SIZING['PREMIUM'] = premium_mult
    MockTrader.QUALITY_MULTIPLIERS['GOLD']    = gold_mult
    MockTrader.QUALITY_MULTIPLIERS['PREMIUM'] = premium_mult
    try:
        yield
    finally:
        _cfg.ATR_SIZING['max_single_position_pct'] = orig_cap
        _cfg.QUALITY_SIZING['GOLD']    = orig_gold
        _cfg.QUALITY_SIZING['PREMIUM'] = orig_prem
        MockTrader.QUALITY_MULTIPLIERS['GOLD']    = orig_mt_gold
        MockTrader.QUALITY_MULTIPLIERS['PREMIUM'] = orig_mt_prem


def _row(label, report, n_sig):
    if report is None:
        return f"  {label:<38} {'—':>8}  {'—':>7}  {'—':>9}  {'—':>7}  (no trades)"
    return (f"  {label:<38} {report.get('total_return', 0):>+7.1f}%  "
            f"{report.get('sharpe_ratio', 0):>7.2f}  "
            f"{report.get('max_drawdown', 0):>+9.1f}%  "
            f"{report.get('win_rate', 0):>6.1f}%  "
            f"[{report.get('total_trades', 0)} trades / {n_sig} signals]")


def main():
    today = datetime.today()
    p = argparse.ArgumentParser()
    p.add_argument('--start', default=(today - timedelta(days=42)).strftime('%Y-%m-%d'))
    p.add_argument('--end',   default=(today - timedelta(days=1)).strftime('%Y-%m-%d'))
    p.add_argument('--watchlist', default='input/thematic_all.txt')
    p.add_argument('--capital', type=int, default=100_000)
    p.add_argument('--mode',    default='swing')
    args = p.parse_args()

    finnhub_key = os.environ.get('FINNHUB_API_KEY')
    cap = args.capital

    print("\n" + "═" * 78)
    print("  FINBERT VARIANTS BACKTEST (sizing + bearish veto)")
    print("═" * 78)
    print(f"  Period     : {args.start} → {args.end}")
    print(f"  Watchlist  : {args.watchlist}")
    print(f"  Capital    : ${cap:,}")
    print(f"  Veto rule  : bearish score ≥ {VETO_SCORE}  AND  net ≤ {VETO_NET:+.2f}")
    print(f"  Boost rule : max_pos_cap 10%→20%, GOLD 2.0→4.0x, PREMIUM 2.0→3.0x")
    print("─" * 78)

    symbols = load_symbols(args.watchlist)
    print(f"  Symbols: {len(symbols)}")

    print("  Fetching data…")
    historical, end_prices = fetch_all_data(symbols, args.start, args.end)

    all_signals = run_all_scans(historical, args.start, args.end, modes=[args.mode])
    baseline_raw = all_signals.get('v5x', [])

    baseline_high = _filter(baseline_raw, _HIGH_PLUS)
    baseline_prem = _filter(baseline_raw, _PREMIUM_PLUS)
    print(f"  Baseline: {len(baseline_raw)} total | {len(baseline_high)} HIGH+ | {len(baseline_prem)} PREMIUM+")

    print("\n  Enriching with FinBERT (Finnhub historical)…")
    promoted_all, _ = enrich_and_promote(
        baseline_high, max_headlines=8, max_age_hours=168, finnhub_key=finnhub_key,
    )

    # After promotion
    finbert_prem = _filter(promoted_all, _PREMIUM_PLUS)

    # Apply bearish veto to HIGH+ pool (removes strong-bearish from everywhere)
    veto_high, vetoed = apply_bearish_veto(promoted_all)
    veto_prem = _filter(veto_high, _PREMIUM_PLUS)

    n_prom = sum(1 for s in promoted_all if s.get('finbert_promoted'))
    print(f"  Promoted: {n_prom} | Vetoed: {len(vetoed)} (signals removed)")
    if vetoed:
        for v in vetoed:
            print(f"    ✂  {v['symbol']:<6} {str(v.get('date'))[:10]}  "
                  f"score={v.get('finbert_score',0):.2f}  "
                  f"net={v.get('finbert_net',0):+.2f}  quality={v.get('quality')}")

    # SPY benchmark trimmed to period
    spy_hist = dict(historical)
    if 'SPY' in historical:
        spy_hist['SPY'] = historical['SPY'][historical['SPY'].index >= pd.to_datetime(args.start)]
    spy_result = calculate_spy_benchmark(spy_hist, args.start, args.end, cap)

    print("\n  Running 5 simulations…")

    res_A = run_simulation(baseline_high, args.start, args.end, end_prices, historical, cap)
    res_B = run_simulation(baseline_prem, args.start, args.end, end_prices, historical, cap)
    res_C = run_simulation(finbert_prem,  args.start, args.end, end_prices, historical, cap)
    res_D = run_simulation(veto_prem,     args.start, args.end, end_prices, historical, cap)

    with boosted_sizing():
        res_E = run_simulation(veto_prem, args.start, args.end, end_prices, historical, cap)
        res_F = run_simulation(finbert_prem, args.start, args.end, end_prices, historical, cap)

    print("\n" + "═" * 78)
    print("  PERFORMANCE COMPARISON")
    print("─" * 78)
    print(f"  {'Config':<38} {'Return':>8}  {'Sharpe':>7}  {'Drawdown':>9}  {'WinRate':>7}  Trades")
    print("  " + "─" * 76)

    if spy_result:
        print(f"  {'SPY buy-and-hold':<38} {spy_result['total_return']:>+7.1f}%  "
              f"{spy_result['sharpe_ratio']:>7.2f}  {spy_result['max_drawdown']:>+9.1f}%  {'—':>7}")

    print("  " + "─" * 76)
    print(_row('A: Baseline HIGH+',                     res_A, len(baseline_high)))
    print(_row('B: Baseline PREMIUM+',                  res_B, len(baseline_prem)))
    print(_row('C: FinBERT PREMIUM+ (current)',         res_C, len(finbert_prem)))
    print(_row('D: FinBERT PREMIUM+ + Bearish Veto',    res_D, len(veto_prem)))
    print(_row('E: D + Boosted Sizing (2x caps)',       res_E, len(veto_prem)))
    print(_row('F: C + Boosted Sizing (no veto)',       res_F, len(finbert_prem)))
    print("  " + "─" * 76)

    # Deltas vs C (current FinBERT)
    if res_C:
        base_ret = res_C.get('total_return', 0) or 0
        base_sh  = res_C.get('sharpe_ratio', 0) or 0
        print(f"\n  ΔR/ΔSharpe vs C (current FinBERT PREMIUM+):")
        for label, r in (('D (veto)', res_D), ('E (veto+boost)', res_E), ('F (boost only)', res_F)):
            if r is None:
                continue
            dR = (r.get('total_return', 0) or 0) - base_ret
            dS = (r.get('sharpe_ratio', 0) or 0) - base_sh
            arrow = '⬆' if dR > 0 else '⬇' if dR < 0 else '→'
            print(f"    {label:<18}  ΔReturn {dR:>+5.1f}% {arrow}   ΔSharpe {dS:>+.2f}")

    print("\n" + "═" * 78 + "\n")


if __name__ == '__main__':
    main()
