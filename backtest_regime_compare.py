#!/usr/bin/env python3
"""
Regime-Aware Backtest: V9-C (OLD) vs NEW (Regime-Adaptive) — 5-year ablation harness
======================================================================================
Champion config: NEW PREMIUM+ pooled-cap=10, --no-tc, --bounce-bear-gate 15
Years: 2022 (Bear), 2023 (Bull), 2024 (Bull), 2025 (Mixed), 2026 (YTD)

Ablation axes:
  --pooled-cap N        Max signals per day via Quality→WinProb→R:R ranking (default 10)
  --selective           Apply BOUNCE/CONTINUATION/TREND_CONFIRM type-filter to NEW signals
  --bounce-bear-gate N  Skip BOUNCE+RED_MARKET when SPY >= N consecutive days below SMA200
  --no-tc               Disable TREND_CONFIRM (reproduces pre-TC +195% baseline)
  --full-compare        Also run retired V9-H / V9-H2 / V9-H3 / V9-D sections

Usage:
  python backtest_regime_compare.py --no-tc --bounce-bear-gate 15
  python backtest_regime_compare.py --no-tc --bounce-bear-gate 15 --pooled-cap 2 --selective --trades-log
"""

import argparse
import asyncio
import sys
import logging
from datetime import timedelta
from pathlib import Path

import pandas as pd
import numpy as np

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from scanner import BreakoutDetector
from config import TREND_CONFIRM as _TC_CFG
from yfinance_adapter import YFinanceAdapter

logging.basicConfig(level=logging.WARNING, format='%(message)s')
logger = logging.getLogger(__name__)

_sharpe_accum: dict[str, list[float]] = {}

# ─── SPY annual returns for reference ─────────────────────────────────────────
SPY_ANNUAL = {
    '2022': -18.1,
    '2023': +26.3,
    '2024': +23.3,
    '2025': +18.9,  # actual full-year 2025
    '2026': +2.0,   # YTD partial (through Apr 2026)
}
YEAR_TYPE = {
    '2022': 'BEARISH',
    '2023': 'BULLISH',
    '2024': 'BULLISH',
    '2025': 'MIXED',
    '2026': 'MIXED',
}


# ─── Load symbols ─────────────────────────────────────────────────────────────
def load_symbols(path=None, limit=0, shuffle=False, seed=42):
    import random
    paths = [path] if path else [
        'input/optimizer_watch.txt', 'input/watchlist3.txt', 'input/all.txt'
    ]
    for p in paths:
        if p and Path(p).exists():
            raw = Path(p).read_text().splitlines()
            syms = [s.strip().split()[0] for s in raw
                    if s.strip() and not s.startswith('#')]
            if shuffle:
                rng = random.Random(seed)
                rng.shuffle(syms)
                print(f"  Shuffled with seed={seed}")
            if limit:
                syms = syms[:limit]
            print(f"  Loaded {len(syms)} symbols from {p}")
            return syms
    return []


# ─── Fetch data ───────────────────────────────────────────────────────────────
def fetch_all_data(symbols, start_date, end_date):
    adapter = YFinanceAdapter(use_disk_cache=True)
    all_syms = ['SPY'] + [s for s in symbols if s != 'SPY']
    historical = {}
    end_prices = {}

    # Fetch extra history for indicators
    fetch_start = (pd.Timestamp(start_date) - timedelta(days=400)).strftime('%Y-%m-%d')

    for i, sym in enumerate(all_syms):
        df = adapter.get_historical_data(sym, '1 day', start_date=fetch_start, end_date=end_date)
        if df is not None and len(df) >= 50:
            historical[sym] = df
            end_prices[sym] = float(df['close'].iloc[-1])
        if (i + 1) % 20 == 0 or (i + 1) == len(all_syms):
            pct = (i + 1) / len(all_syms) * 100
            print(f"    Fetched {i+1}/{len(all_syms)} ({pct:.1f}%)...", flush=True)

    print(f"  Loaded {len(historical)-1} symbols (+ SPY)")
    return historical, end_prices


# ─── Regime classification ────────────────────────────────────────────────────
def classify_day_regime(spy_df, sim_date, lookback=15):
    """Returns (regime, spy_pct, vol_pct)"""
    mask = spy_df.index <= sim_date
    df_s = spy_df[mask].tail(lookback + 5)
    if len(df_s) < 3:
        return 'NORMAL', 0.0, 0.0

    # SPY % over last N bars
    spy_pct = float((df_s['close'].iloc[-1] / df_s['close'].iloc[-lookback] - 1) * 100) \
        if len(df_s) >= lookback else 0.0

    # Daily volatility (std of daily returns)
    daily_rets = df_s['close'].pct_change().dropna()
    vol_pct = float(daily_rets.std() * 100) if len(daily_rets) >= 5 else 0.5

    if spy_pct <= -1.5:
        return 'RED_MARKET', spy_pct, vol_pct
    elif spy_pct <= -0.5:
        return 'BEARISH', spy_pct, vol_pct
    elif abs(spy_pct) < 0.5 and vol_pct < 0.35:
        return 'CHOPPY', spy_pct, vol_pct
    elif spy_pct >= 2.0:
        return 'EXPANSION', spy_pct, vol_pct
    else:
        return 'NORMAL', spy_pct, vol_pct


# ─── SPY SMA200 macro filter ─────────────────────────────────────────────────
def is_spy_below_sma200(spy_df, sim_date):
    """True when SPY close < 200-day SMA — structural bear market."""
    mask = spy_df.index <= sim_date
    df_s = spy_df[mask].tail(201)
    if len(df_s) < 200:
        return False
    sma200 = float(df_s['close'].iloc[-200:].mean())
    return float(df_s['close'].iloc[-1]) < sma200


# ─── Signal detectors ─────────────────────────────────────────────────────────
def collect_signals_old(detector, df_slice, symbol, mode, spy_perf):
    """OLD config: breakout + SMA20_CROSS (vol>=1.8, RSI 48-62) + BOUNCE (no quality gate)"""
    sig = None
    # Breakout
    try:
        sig = detector.detect(df_slice, symbol, mode, '1 day', spy_perf, use_scoring=True,
                              use_legacy_momentum=False, use_v4_overextension=False)
    except Exception:
        pass
    if sig:
        return sig

    # Bounce
    try:
        sig = detector.detect_bounce(df_slice, symbol, mode, '1 day')
    except Exception:
        pass
    if sig:
        return sig

    # SMA20_CROSS
    try:
        sig = detector.detect_sma20_cross(df_slice, symbol, mode, '1 day', spy_perf)
    except Exception:
        pass
    return sig


def collect_signals_new(detector, df_slice, symbol, mode, spy_perf, regime):
    """
    NEW config: same detectors BUT with:
    - Vol>=2.5, RSI bimodal in detect_sma20_cross (enforced by scanner already)
    - Regime gating applied here
    """
    sig = None

    # Breakout always allowed unless RED_MARKET (quality filtered)
    try:
        sig = detector.detect(df_slice, symbol, mode, '1 day', spy_perf, use_scoring=True,
                              use_legacy_momentum=False, use_v4_overextension=False)
    except Exception:
        pass

    if sig and regime == 'RED_MARKET' and sig.get('Quality') not in ('GOLD',):
        sig = None  # RED: only GOLD breakouts

    if sig:
        return sig

    # Bounce — PREMIUM+ allowed in all regimes EXCEPT BEARISH
    # Data shows: RED_MARKET = +55.8% of total P&L (+1.99% avg) → keep it
    #             BEARISH = -13.4% drag (22.2% WR, -3.28% avg) → block it
    try:
        bounce = detector.detect_bounce(df_slice, symbol, mode, '1 day')
    except Exception:
        bounce = None
    if bounce and bounce.get('Quality') in ('GOLD', 'PREMIUM'):
        if regime == 'BEARISH':
            pass  # BEARISH only: block bounces (-3.28% avg P&L, 22.2% WR)
        else:
            # Bounce quality filters (diagnose_signal_diff findings):
            b_rsi = float(bounce.get('RSI', 50))
            b_vol = float(bounce.get('Vol', 1.0))
            b_rr  = float(bounce.get('R:R', 0))

            # F1: NORMAL regime bounces require genuine oversold (RSI<40)
            #     NORMAL+RSI40-60 = 18% WR, -10% avg in 2026; 21.7% WR, -6.7% in 2022
            if regime == 'NORMAL' and b_rsi >= 40:
                bounce = None

            # F2: NORMAL + formulaic R:R + low vol = weakest bounce profile
            #     Catches mid-fall fakes that have no volume or upside target
            elif regime == 'NORMAL' and b_rr <= 2.01 and b_vol < 1.5:
                bounce = None

            if bounce:
                sig = bounce
    if sig:
        return sig

    # SMA20_CROSS — blocked in CHOPPY, BEARISH, RED_MARKET
    if regime not in ('RED_MARKET', 'BEARISH', 'CHOPPY'):
        try:
            sig = detector.detect_sma20_cross(df_slice, symbol, mode, '1 day', spy_perf)
        except Exception:
            sig = None
    if sig:
        return sig

    # TREND_CONFIRM — final fallback (Apr 2026, commit 0e79c17).  Catches the
    # SMA150+MACD+RSI+vol momentum pattern that the consolidation breakout
    # detector misses (INTC/AMD/NVDA/MU April 2026 rally).  Same regime-block
    # rules as SMA20_CROSS — skip in BEARISH/RED_MARKET to be conservative.
    # Respects TREND_CONFIRM['enabled'] so that setting it to False in config.py
    # reproduces the pre-TC baseline (the +195% NEW-no-TC result).
    if _TC_CFG.get('enabled') and regime not in ('RED_MARKET', 'BEARISH'):
        try:
            sig = detector.detect_trend_confirm(df_slice, symbol, mode, '1 day', spy_perf)
        except Exception:
            sig = None
    return sig


def collect_signals_hybrid(detector, df_slice, symbol, mode, spy_perf, regime, bear_macro):
    """
    V9-H HYBRID: SPY SMA200 quality escalation + BEARISH block.
    - bear_macro=True (SPY < SMA200): GOLD breakouts only, no BOUNCE/SMA20_CROSS
    - bear_macro=False (SPY >= SMA200): PREMIUM+ with BEARISH block on BOUNCE
      and SMA20_CROSS blocked only in BEARISH (keep RED_MARKET unlike NEW)
    """
    sig = None

    # Breakout detection — always attempt
    try:
        sig = detector.detect(df_slice, symbol, mode, '1 day', spy_perf, use_scoring=True,
                              use_legacy_momentum=False, use_v4_overextension=False)
    except Exception:
        pass

    # Quality gate based on macro regime
    if sig:
        qual = sig.get('Quality', 'STANDARD')
        if bear_macro:
            # BEAR_MACRO: only GOLD breakouts pass
            if qual != 'GOLD':
                sig = None
        else:
            # BULL_MACRO: PREMIUM+ (same as NEW)
            if qual not in ('GOLD', 'PREMIUM'):
                sig = None
    if sig:
        return sig

    # In bear macro: no BOUNCE or SMA20_CROSS — preserve capital
    if bear_macro:
        return None

    # ── BULL_MACRO below ──

    # Bounce — PREMIUM+ in all regimes EXCEPT BEARISH (proven -13.4% drag)
    try:
        bounce = detector.detect_bounce(df_slice, symbol, mode, '1 day')
    except Exception:
        bounce = None
    if bounce and bounce.get('Quality') in ('GOLD', 'PREMIUM'):
        if regime != 'BEARISH':
            sig = bounce
    if sig:
        return sig

    # SMA20_CROSS — blocked only in BEARISH (keep RED_MARKET = +55.8% P&L)
    if regime != 'BEARISH':
        try:
            sig = detector.detect_sma20_cross(df_slice, symbol, mode, '1 day', spy_perf)
        except Exception:
            sig = None
    return sig


# ─── Main scan loop ───────────────────────────────────────────────────────────
def run_scan(historical, start_date, end_date, modes, config='new'):
    """Scan all symbols and dates, return signal list."""
    detector = BreakoutDetector()
    spy_df = historical.get('SPY')
    symbols = [s for s in historical if s != 'SPY']
    cooldowns = {}  # symbol → last signal date

    signals = []
    sim_dates = pd.date_range(start=start_date, end=end_date, freq='B')
    regime_counts = {}

    print(f"  [{config.upper()}] Scanning {len(symbols)} symbols × {len(sim_dates)} days × {len(modes)} modes...")

    total_days = len(sim_dates)
    for day_idx, sim_date in enumerate(sim_dates):
        if (day_idx + 1) % 25 == 0 or day_idx == 0 or (day_idx + 1) == total_days:
            pct = (day_idx + 1) / total_days * 100
            print(f"    [{config.upper()}] Progress: {day_idx+1}/{total_days} days ({pct:.1f}%) — {sim_date.strftime('%Y-%m-%d')}", flush=True)
        # Day-level regime
        regime, spy_pct, vol_pct = ('NORMAL', 0.0, 0.5) if spy_df is None \
            else classify_day_regime(spy_df, sim_date)
        regime_counts[regime] = regime_counts.get(regime, 0) + 1

        spy_perf_frac = spy_pct / 100.0

        # Compute bear_macro once per day (only needed for hybrid)
        if config == 'hybrid' and spy_df is not None:
            bear_macro = is_spy_below_sma200(spy_df, sim_date)
        else:
            bear_macro = False

        for symbol in symbols:
            # Cooldown
            last = cooldowns.get(symbol)
            if last and (sim_date - last).days < 10:
                continue

            df = historical.get(symbol)
            if df is None:
                continue
            df_slice = df[df.index <= sim_date]
            if len(df_slice) < 150:
                continue
            if df_slice.index[-1].date() != sim_date.date():
                continue

            for mode in modes:
                if config == 'old':
                    sig = collect_signals_old(detector, df_slice, symbol, mode, spy_perf_frac)
                elif config == 'hybrid':
                    sig = collect_signals_hybrid(detector, df_slice, symbol, mode, spy_perf_frac, regime, bear_macro)
                else:
                    sig = collect_signals_new(detector, df_slice, symbol, mode, spy_perf_frac, regime)

                if sig:
                    quality = sig.get('Quality', 'STANDARD')
                    # Old config: V9-C filter = PREMIUM+ with Minervini≥7
                    if config == 'old':
                        min_score = sig.get('MinerviniScore', 0) or 0
                        if quality not in ('GOLD', 'PREMIUM') or min_score < 7:
                            # Also allow SMA20_CROSS/BOUNCE without Minervini req
                            if sig.get('Type') not in ('SMA20_CROSS', 'BOUNCE', 'CONTINUATION', 'Momentum'):
                                continue

                    signals.append({
                        'date': sim_date,
                        'symbol': symbol,
                        'action': 'BUY',
                        'price': sig['Price'],
                        'entry_price': sig['Price'],
                        'stop_loss': sig['Stop'],
                        'take_profit': sig['Target'],
                        'quality': quality,
                        'mode': mode,
                        'type': sig.get('Type', 'BREAKOUT'),
                        'regime': regime,
                        'minervini_score': sig.get('MinerviniScore', 0) or 0,
                        'is_momentum': sig.get('Type') == 'Momentum',
                        'is_vcp': bool(sig.get('VCP', False)),
                        'checks': sig.get('Checks', {}),
                        'bear_macro': bear_macro,
                        # Pooled-cap ranking fields (mirrors auto_portfolio.py sort)
                        'rr':          float(sig.get('R:R', 0) or 0),
                        'win_prob':    float(sig.get('WinProb', 0) or 0),
                        'sma_dist_pct': float(sig.get('SMA_Dist%', 0) or 0),
                    })
                    cooldowns[symbol] = sim_date
                    break  # one signal per symbol per day

        if (day_idx + 1) % 60 == 0:
            print(f"    Day {day_idx+1}/{len(sim_dates)}: {len(signals)} signals")

    # Print regime distribution
    total_days = sum(regime_counts.values())
    print(f"\n  Regime distribution ({total_days} trading days):")
    for r, n in sorted(regime_counts.items()):
        print(f"    {r:<14}: {n:>3} days ({n/total_days*100:.0f}%)")
    print(f"  Total signals: {len(signals)}")
    if signals:
        sig_df = pd.DataFrame(signals)
        for q in ['GOLD', 'PREMIUM', 'HIGH', 'STANDARD']:
            n = (sig_df['quality'] == q).sum()
            if n: print(f"    {q}: {n}")
        for t in ['SMA20_CROSS', 'BOUNCE', 'CONTINUATION', 'Momentum', 'BREAKOUT', 'PULLBACK']:
            n = (sig_df.get('type', pd.Series()) == t).sum() if 'type' in sig_df.columns else 0
            if n: print(f"    Type={t}: {n}")
    return signals


# ─── Pooled-cap helper ────────────────────────────────────────────────────────
# Mirrors auto_portfolio.py's cross-day pooled cap logic:
#   rank signals within each date by Quality → WinProb → R:R → Dist≤25% → mode
#   keep top N per day  (N = MAX_ADDS_PER_SCAN = 10 in production)
# Without this, the backtest enters unlimited positions per day — not what
# live auto_portfolio.py does, and the source of the +195% irreproducibility.

_QUALITY_RANK = {'GOLD': 0, 'PREMIUM': 1, 'HIGH': 2, 'STANDARD': 3}


def _pooled_cap(signals, max_per_day: int = 10):
    """Return a new signal list with at most *max_per_day* entries per trading
    day, selected by the same ranking used in auto_portfolio.py."""
    from collections import defaultdict
    by_date: dict = defaultdict(list)
    for s in signals:
        by_date[pd.Timestamp(s['date']).normalize()].append(s)

    result = []
    for dt in sorted(by_date.keys()):
        day_sigs = sorted(
            by_date[dt],
            key=lambda s: (
                _QUALITY_RANK.get(s.get('quality', 'STANDARD'), 9),
                -float(s.get('win_prob', 0) or 0),       # higher WinProb first
                -float(s.get('rr', 0) or 0),             # higher R:R first
                # Dist≤25% preferred; beyond 25% sorted last (YPF-style filter)
                0 if float(s.get('sma_dist_pct', 0) or 0) <= 25 else 1,
                float(s.get('sma_dist_pct', 0) or 0),    # closer to trend first
            ),
        )
        result.extend(day_sigs[:max_per_day])
    return result



def _hold_split(trades: list, td_idx: dict) -> dict:
    """Split trades into <=15 and >15 trading-day hold buckets; return count+WR for each."""
    result = {}
    for bucket, pred in [('short', lambda d: d <= 15), ('long', lambda d: d > 15)]:
        sub = []
        for t in trades:
            entry = pd.Timestamp(t['entry_date']).normalize()
            exit_ = pd.Timestamp(t['exit_date']).normalize()
            hold_td = td_idx.get(exit_, 0) - td_idx.get(entry, 0)
            if pred(hold_td):
                sub.append(t)
        n = len(sub)
        wr = sum(1 for t in sub if t.get('win')) / n * 100 if n else float('nan')
        result[bucket] = {'n': n, 'wr': wr}
    return result


# ─── Simulation ───────────────────────────────────────────────────────────────
def simulate(signals, start_date, end_date, end_prices, historical,
             capital=100_000, tp_as_trail=True, label='', regime_sizing=False,
             slippage_pct=0.0, commission=0.0,
             max_positions=0, dd_breaker_pct=0.0,
             bear_macro_mult=1.0, output_dir=None,
             stall_exit=False, stall_sma_period=20,
             bounce_bear_gate=0,
             selective_max_per_day=0,
             sma150_slope_gate=False, sma150_exit=False,
             breakeven_r=0.0, breakeven_bear_gate=0,
             atr_trail_always=False, atr_trail_mult=2.0):
    """
    Simple, self-contained simulation that avoids SimulationMode bugs.
    - Enters at signal price on signal date
    - Checks daily H/L for stop or TP hit each bar
    - tp_as_trail=True: when TP hit, activates 2×ATR trailing stop (V9-C mode)
    - tp_as_trail=False: exits hard at TP price
    - atr_trail_always=True: ATR×atr_trail_mult trail active from entry day 1,
      replacing the TP trigger entirely (champion exit, validated 2026-05-07:
      +234% 5yr vs +137% post-TP on optimizer_watch.txt). Fixed stop_loss is
      the absolute floor; effective stop only ever moves up. Takes priority
      over tp_as_trail. Mirrors auto_portfolio._raise_atr_trail().
    - atr_trail_mult: trail distance in ATRs (default 2.0, sweep winner)
    - stall_exit=False: exits after MAX_HOLD (30d) bars regardless
    - stall_exit=True:  exits when close < SMA(stall_sma_period) for 2 consecutive bars;
                        MaxHold becomes a 60-day safety cap only (stall_sma_period: 20 or 50)
    - bounce_bear_gate: if > 0, skip BOUNCE+RED_MARKET entries when SPY has been below
                        its SMA200 for >= N consecutive trading days (sustained bear filter)
    - Position size: min(10% capital by value, 2% capital by risk)
    - regime_sizing=True: scales position size down in weak regimes
    - max_positions: cap on concurrent open positions (0=unlimited)
    - dd_breaker_pct: if portfolio DD exceeds this (e.g. 0.15), cut size to 25%
    - bear_macro_mult: position size multiplier when signal.bear_macro=True (1.0=full, 0.5=half)
    - sma150_slope_gate: skip entry if symbol's SMA150 is declining (today < 10 bars ago)
    - sma150_exit: exit when close crosses below SMA150 (90d safety cap)
    - breakeven_r: if > 0, once a position has reached entry + N×(initial risk), raise its
                   stop to breakeven (entry). Targets the ≤15d giveback where a winner
                   reverses to a full stop-loss before TP. Uses prior-bar peak only (no
                   intrabar lookahead). 0 = off.
    - breakeven_bear_gate: if > 0, SKIP the breakeven move on days where SPY has been below
                   SMA200 for >= N consecutive days (sustained bear). In a bear year, moving
                   to BE scratches the few winners before they run; gating it off there kept
                   the mixed/bull-year gains without the 2022 damage. 0 = always apply.
    """
    if not signals:
        print(f"  [{label}] No signals → skip")
        return None

    MAX_HOLD = 60 if stall_exit else 30   # calendar days; stall_exit uses 60d as safety cap
    ATR_TRAIL_MULT = atr_trail_mult
    MAX_POS_PCT   = 0.10
    MAX_RISK_PCT  = 0.02
    # Only reduce BEARISH (22.2% WR, -3.28% avg P&L, -13.4% P&L share)
    # RED_MARKET = +55.8% P&L share — do NOT cut it
    REGIME_SIZE   = {'EXPANSION': 1.0, 'NORMAL': 1.0, 'CHOPPY': 0.8,
                     'BEARISH': 0.25, 'RED_MARKET': 1.0}

    # Pre-compute SMA150 per symbol (only if needed)
    sma150_cache: dict = {}
    if sma150_slope_gate or sma150_exit:
        for sym, df in historical.items():
            if len(df) >= 150:
                sma150_cache[sym] = df['close'].rolling(150).mean()

    cap = float(capital)
    max_deployed_pct = 0.0   # peak fraction of starting capital simultaneously in open positions
    trades = []          # closed trades: {pnl, pnl_pct, win, entry, exit}
    open_pos = {}        # symbol → {entry_price, stop, take_profit, qty, entry_date,
                         #            cost, _tp_hit, _trail_stop}
    equity_curve = []    # (date, equity)
    peak_equity = float(capital)  # for DD breaker

    # Build a sorted list of (signal_date, signal_dict)
    sig_list = sorted(signals, key=lambda s: s['date'])

    # All unique trading dates in year
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

    td_idx = {pd.Timestamp(d).normalize(): i for i, d in enumerate(trading_days)}

    # Pre-compute SPY consecutive days below SMA200 (for bounce_bear_gate)
    spy_consec_below: dict[pd.Timestamp, int] = {}
    if (bounce_bear_gate > 0 or breakeven_bear_gate > 0) and spy is not None:
        sma200 = spy['close'].rolling(200).mean()
        count = 0
        for dt, cl, sm in zip(spy.index, spy['close'], sma200):
            count = count + 1 if (not pd.isna(sm) and cl < sm) else 0
            spy_consec_below[pd.Timestamp(dt).normalize()] = count

    for today in trading_days:
        today_norm = today.normalize()

        # --- Drawdown circuit breaker ---
        # Estimate current equity (cash + open positions at last known price)
        open_val_est = sum(
            historical[s].loc[historical[s].index.normalize() <= today_norm, 'close'].iloc[-1]
            * p['qty']
            if s in historical and not historical[s][historical[s].index.normalize() <= today_norm].empty
            else p['entry_price'] * p['qty']
            for s, p in open_pos.items()
        ) if open_pos else 0.0
        current_equity = cap + open_val_est
        peak_equity = max(peak_equity, current_equity)
        dd_mult = 1.0
        if dd_breaker_pct > 0 and peak_equity > 0:
            current_dd = current_equity / peak_equity - 1.0
            if current_dd < -dd_breaker_pct:
                dd_mult = 0.25
            # else stays at 1.0

        # --- Dynamic position cap (bear_macro aware) ---
        effective_max_pos = max_positions if max_positions > 0 else 999
        if max_positions > 0 and spy is not None:
            # Check if any of today's signals are in bear_macro
            today_sigs = sig_by_date.get(today_norm, [])
            if today_sigs and today_sigs[0].get('bear_macro', False):
                effective_max_pos = min(3, max_positions)

        # --- Enter new positions ---
        adds_today = 0
        for sig in sig_by_date.get(today_norm, []):
            sym = sig['symbol']
            if sym in open_pos:
                continue  # already have a position
            # Position cap check
            if len(open_pos) >= effective_max_pos:
                break  # no more room
            # Selective per-day cap (matches auto_portfolio.py SELECTIVE_MODE['max_adds_per_scan'])
            if selective_max_per_day > 0 and adds_today >= selective_max_per_day:
                break
            # Bounce-bear gate: skip BOUNCE in RED_MARKET during sustained bear
            if (bounce_bear_gate > 0
                    and sig.get('type') == 'BOUNCE'
                    and sig.get('regime') == 'RED_MARKET'
                    and spy_consec_below.get(today_norm, 0) >= bounce_bear_gate):
                continue

            # SMA150 slope gate: skip entry if SMA150 is declining (today < 10 bars ago)
            if sma150_slope_gate:
                sma_series = sma150_cache.get(sym)
                if sma_series is None:
                    continue
                past_sma = sma_series[sma_series.index.normalize() <= today_norm].dropna()
                if len(past_sma) < 11 or float(past_sma.iloc[-1]) < float(past_sma.iloc[-11]):
                    continue

            price = float(sig.get('price', 0)) * (1 + slippage_pct)  # slippage on entry
            stop  = float(sig.get('stop_loss', price * 0.95))
            tp    = float(sig.get('take_profit', price * 1.10))
            if price <= 0 or stop <= 0:
                continue
            risk_per_share = abs(price - stop)
            if risk_per_share <= 0:
                continue
            mult = 1.0
            mult *= dd_mult  # apply DD breaker sizing
            if regime_sizing:
                mult *= REGIME_SIZE.get(sig.get('regime', 'NORMAL'), 1.0)
            if bear_macro_mult < 1.0 and sig.get('bear_macro', False):
                mult *= bear_macro_mult
            qty_by_val  = int(cap * MAX_POS_PCT * mult / price)
            qty_by_risk = int(cap * MAX_RISK_PCT * mult / risk_per_share)
            qty = min(qty_by_val, qty_by_risk)
            if qty < 1:
                if cap >= price:
                    qty = 1
                else:
                    continue
            cost = price * qty + commission  # commission on entry
            if cost > cap:
                qty = max(1, int((cap - commission) / price))
                cost = price * qty + commission
            cap -= cost
            open_pos[sym] = {
                'entry_price': price, 'stop': stop, 'take_profit': tp,
                'qty': qty, 'cost': cost, 'entry_date': today_norm,
                '_tp_hit': False, '_trail_stop': None, '_below_sma20': False,
                'init_risk': risk_per_share, '_peak_high': price, '_be_moved': False,
                'quality': sig.get('quality', ''), 'regime': sig.get('regime', ''),
                'signal_type': sig.get('type', ''),
            }
            adds_today += 1

        # --- Check exits ---
        for sym, pos in list(open_pos.items()):
            df = historical.get(sym)
            if df is None:
                continue
            bar = df[df.index.normalize() == today_norm]
            if bar.empty:
                continue
            hi  = float(bar.iloc[-1]['high'])
            lo  = float(bar.iloc[-1]['low'])
            cl  = float(bar.iloc[-1]['close'])
            op  = float(bar.iloc[-1]['open'])

            days_held = (today_norm - pos['entry_date']).days

            # Breakeven-after-R: once the position has PREVIOUSLY reached
            # entry + N×(initial risk) (peak from prior bars only — no intrabar
            # lookahead), lock the stop at breakeven. Bridges the pre-TP giveback gap.
            # Regime gate: skip during sustained bear (BE scratches rare bear winners).
            if (breakeven_r > 0 and not pos['_be_moved']
                    and pos['_peak_high'] >= pos['entry_price'] + breakeven_r * pos['init_risk']
                    and not (breakeven_bear_gate > 0
                             and spy_consec_below.get(today_norm, 0) >= breakeven_bear_gate)):
                if pos['entry_price'] > pos['stop']:
                    pos['stop'] = pos['entry_price']
                pos['_be_moved'] = True

            exit_price = None
            exit_reason = None

            # Skip same-day exits
            if today_norm == pos['entry_date']:
                # No exit checks on entry day, but the always-on trail arms
                # tonight — live refresh_prices runs the same evening the
                # position opens ("trail activates from entry bar 1").
                if atr_trail_always:
                    df_atr = df[df.index <= today].tail(15)
                    if len(df_atr) >= 14:
                        tr = pd.concat([
                            df_atr['high'] - df_atr['low'],
                            (df_atr['high'] - df_atr['close'].shift(1)).abs(),
                            (df_atr['low']  - df_atr['close'].shift(1)).abs(),
                        ], axis=1).max(axis=1)
                        pos['_trail_stop'] = cl - ATR_TRAIL_MULT * float(tr.mean())

            elif atr_trail_always:
                # Champion exit: ATR trail rides up from entry day 1 — no TP
                # trigger. CLOSE-based, mirroring auto_portfolio.refresh_prices
                # exactly: the position closes when the daily close crosses the
                # trailed stop (intraday dips below it do NOT exit — that's the
                # whipsaw the champion avoids), booked at the stop level. The
                # trail is then raised with today's close; today's raise can
                # never trigger today (close − mult×ATR < close), so the
                # binding level is the ratchet from prior days. Fixed stop is
                # the absolute floor and the effective stop never moves down.
                eff_stop = (max(pos['stop'], pos['_trail_stop'])
                            if pos['_trail_stop'] is not None else pos['stop'])
                if cl <= eff_stop:
                    exit_price  = eff_stop
                    exit_reason = ('TrailStop'
                                   if (pos['_trail_stop'] is not None
                                       and pos['_trail_stop'] > pos['stop'])
                                   else 'StopLoss')
                else:
                    df_atr = df[df.index <= today].tail(15)
                    if len(df_atr) >= 14:
                        tr = pd.concat([
                            df_atr['high'] - df_atr['low'],
                            (df_atr['high'] - df_atr['close'].shift(1)).abs(),
                            (df_atr['low']  - df_atr['close'].shift(1)).abs(),
                        ], axis=1).max(axis=1)
                        atr = tr.mean()
                        new_trail = cl - ATR_TRAIL_MULT * atr
                        if pos['_trail_stop'] is None or new_trail > pos['_trail_stop']:
                            pos['_trail_stop'] = new_trail

            elif tp_as_trail:
                # TP activates trailing stop
                if not pos['_tp_hit'] and hi >= pos['take_profit']:
                    pos['_tp_hit'] = True
                    # Set initial trail stop at TP - 2×ATR
                    df_atr = df[df.index <= today].tail(15)
                    if len(df_atr) >= 14:
                        tr = pd.concat([
                            df_atr['high'] - df_atr['low'],
                            (df_atr['high'] - df_atr['close'].shift(1)).abs(),
                            (df_atr['low']  - df_atr['close'].shift(1)).abs(),
                        ], axis=1).max(axis=1)
                        atr = tr.mean()
                        pos['_trail_stop'] = cl - ATR_TRAIL_MULT * atr
                    else:
                        pos['_trail_stop'] = pos['take_profit'] * 0.95

                # Update trail stop upward
                if pos['_tp_hit'] and pos['_trail_stop'] is not None:
                    # Tighten trail with today's close
                    df_atr = df[df.index <= today].tail(15)
                    if len(df_atr) >= 14:
                        tr = pd.concat([
                            df_atr['high'] - df_atr['low'],
                            (df_atr['high'] - df_atr['close'].shift(1)).abs(),
                            (df_atr['low']  - df_atr['close'].shift(1)).abs(),
                        ], axis=1).max(axis=1)
                        atr = tr.mean()
                        new_trail = cl - ATR_TRAIL_MULT * atr
                        if new_trail > pos['_trail_stop']:
                            pos['_trail_stop'] = new_trail
                    # Check trailing stop hit
                    if lo <= pos['_trail_stop']:
                        exit_price  = max(pos['_trail_stop'], op)
                        exit_reason = 'TrailStop'
                elif lo <= pos['stop']:
                    exit_price  = min(pos['stop'], op) if op < pos['stop'] else pos['stop']
                    exit_reason = 'StopLoss'

            else:
                # Hard TP + stop
                if lo <= pos['stop']:
                    exit_price  = min(pos['stop'], op) if op < pos['stop'] else pos['stop']
                    exit_reason = 'StopLoss'
                elif hi >= pos['take_profit']:
                    exit_price  = pos['take_profit']
                    exit_reason = 'TakeProfit'

            # Momentum stall: close < SMA(stall_sma_period) for 2 consecutive bars (min 3d held)
            if stall_exit and exit_price is None and days_held >= 3:
                recent = df[df.index.normalize() <= today_norm].tail(stall_sma_period + 1)
                if len(recent) >= stall_sma_period:
                    sma_val = float(recent['close'].iloc[-stall_sma_period:].mean())
                    below_now = cl < sma_val
                    if below_now and pos.get('_below_sma20', False):
                        exit_price  = cl
                        exit_reason = 'MomentumStall'
                    pos['_below_sma20'] = below_now

            # SMA150 cross-down exit: close was above SMA150, now below
            if sma150_exit and exit_price is None and today_norm != pos['entry_date']:
                sma_series = sma150_cache.get(sym)
                if sma_series is not None and df is not None:
                    past_sma = sma_series[sma_series.index.normalize() <= today_norm].dropna()
                    if len(past_sma) >= 2:
                        sma_today_val = float(past_sma.iloc[-1])
                        sma_prev_val  = float(past_sma.iloc[-2])
                        prev_bars = df[df.index.normalize() < today_norm]
                        if not prev_bars.empty:
                            prev_cl = float(prev_bars.iloc[-1]['close'])
                            if prev_cl >= sma_prev_val and cl < sma_today_val:
                                exit_price  = cl
                                exit_reason = 'SMA150CrossDown'

            # Max hold safety cap (30d default; 60d when stall_exit=True; 90d when sma150_exit=True)
            hold_cap = 90 if sma150_exit else MAX_HOLD
            if exit_price is None and days_held >= hold_cap:
                exit_price  = cl
                exit_reason = 'MaxHold'

            if exit_price is not None:
                qty  = pos['qty']
                exit_net = exit_price * (1 - slippage_pct) - commission / qty  # slippage + commission on exit
                pnl  = (exit_net - pos['entry_price']) * qty
                cost = pos['cost']
                trades.append({
                    'symbol':      sym,
                    'entry_date':  pos['entry_date'].strftime('%Y-%m-%d'),
                    'exit_date':   today_norm.strftime('%Y-%m-%d'),
                    'qty':         qty,
                    'entry':       round(pos['entry_price'], 4),
                    'exit':        round(exit_net, 4),
                    'stop':        round(pos['stop'], 4),
                    'take_profit': round(pos['take_profit'], 4),
                    'pnl':         round(pnl, 2),
                    'pnl_pct':     round(pnl / cost * 100, 4),
                    'win':         pnl > 0,
                    'reason':      exit_reason,
                    'quality':     pos.get('quality', ''),
                    'regime':      pos.get('regime', ''),
                    'signal_type': pos.get('signal_type', ''),
                })
                cap += exit_net * qty
                del open_pos[sym]
            else:
                # Position stays open — track peak high for breakeven-after-R.
                # Updated AFTER exit checks so it reflects prior bars only next day.
                if hi > pos['_peak_high']:
                    pos['_peak_high'] = hi

        # Mark-to-market for equity curve (open positions at close)
        open_val = sum(
            historical[s].loc[historical[s].index.normalize() == today_norm, 'close'].iloc[-1]
            * p['qty']
            if s in historical and not historical[s][historical[s].index.normalize() == today_norm].empty
            else p['entry_price'] * p['qty']
            for s, p in open_pos.items()
        )
        if open_pos:
            deployed = sum(p['cost'] for p in open_pos.values())
            max_deployed_pct = max(max_deployed_pct, deployed / capital)
        equity_curve.append(cap + open_val)

    # Close remaining positions at end_prices
    for sym, pos in list(open_pos.items()):
        ep  = end_prices.get(sym, pos['entry_price'])
        ep_net = ep * (1 - slippage_pct) - commission / max(pos['qty'], 1)
        qty = pos['qty']
        pnl = (ep_net - pos['entry_price']) * qty
        trades.append({
            'symbol':      sym,
            'entry_date':  pos['entry_date'].strftime('%Y-%m-%d'),
            'exit_date':   end_date,
            'qty':         qty,
            'entry':       round(pos['entry_price'], 4),
            'exit':        round(ep_net, 4),
            'stop':        round(pos['stop'], 4),
            'take_profit': round(pos['take_profit'], 4),
            'pnl':         round(pnl, 2),
            'pnl_pct':     round(pnl / pos['cost'] * 100, 4),
            'win':         pnl > 0,
            'reason':      'SimEnd',
            'quality':     pos.get('quality', ''),
            'regime':      pos.get('regime', ''),
            'signal_type': pos.get('signal_type', ''),
        })
        cap += ep_net * qty

    if not trades:
        return None

    # Metrics
    total_return = (cap - capital) / capital * 100
    n_trades = len(trades)
    n_wins   = sum(1 for t in trades if t.get('win', False) is True)
    win_rate = (n_wins / n_trades * 100) if n_trades > 0 else 0.0

    eq = np.array(equity_curve) if equity_curve else np.array([float(capital)])
    daily_ret = np.diff(eq) / eq[:-1]
    sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)
              if daily_ret.std() > 0 else 0)
    max_dd_arr = (eq / np.maximum.accumulate(eq)) - 1
    max_drawdown = float(max_dd_arr.min() * 100)

    if output_dir and trades:
        safe_label = label.replace(' ', '_').replace('/', '_').replace('+', 'plus')
        if atr_trail_always:
            safe_label += f'_ATRalways-{atr_trail_mult:g}'
        year_str = str(start_date)[:4]
        out_path = Path(output_dir) / f'trades_{year_str}_{safe_label}.csv'
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(trades).to_csv(out_path, index=False)

    return {
        'total_trades':     n_trades,
        'win_rate':         win_rate,
        'total_return':     total_return,
        'sharpe_ratio':     sharpe,
        'max_drawdown':     max_drawdown,
        'total_pnl':        cap - capital,
        'final_capital':    cap,
        'avg_win':          np.mean([t['pnl_pct'] for t in trades if t['win']]) if n_wins else 0,
        'avg_loss':         np.mean([t['pnl_pct'] for t in trades if not t['win']]) if (n_trades-n_wins) else 0,
        'max_deployed_pct': max_deployed_pct,
        'trades':           trades,
        'td_idx':           td_idx,
    }


# ─── SPY benchmark ────────────────────────────────────────────────────────────
def spy_benchmark(historical, start_date, end_date, capital=100_000):
    spy = historical.get('SPY')
    if spy is None: return None
    mask = (spy.index >= start_date) & (spy.index <= end_date)
    spy_period = spy[mask]
    if spy_period.empty: return None
    ret = (spy_period['close'].iloc[-1] / spy_period['close'].iloc[0] - 1) * 100
    # Rough Sharpe
    daily = spy_period['close'].pct_change().dropna()
    sharpe = (daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0
    dd = ((spy_period['close'] / spy_period['close'].cummax()) - 1).min() * 100
    return {'return': ret, 'sharpe': sharpe, 'drawdown': dd}


# ─── Report ───────────────────────────────────────────────────────────────────
def print_report(report, label, n_signals, spy_info=None, show_hold_split=False):
    if report is None:
        print(f"  {label:<42} NO DATA")
        return
    ret      = report.get('total_return', report.get('total_return_pct', 0)) or 0
    sharpe   = report.get('sharpe_ratio', 0) or 0
    dd       = report.get('max_drawdown', report.get('max_drawdown_pct', 0)) or 0
    wr       = report.get('win_rate', 0) or 0
    n_trades = report.get('total_trades', 0)
    spy_diff = (ret - spy_info['return']) if spy_info else 0
    spy_str  = f"{spy_diff:>+8.2f}%" if spy_info else "     N/A"
    print(f"  {label:<42} {n_signals:>7} {n_trades:>7} {ret:>+9.2f}% {wr:>7.1f}% "
          f"{sharpe:>7.2f} {dd:>+7.2f}%  {spy_str}")
    if show_hold_split:
        trades = report.get('trades', [])
        td_idx = report.get('td_idx', {})
        if trades and td_idx:
            split = _hold_split(trades, td_idx)
            s = split['short']
            l = split['long']
            s_wr = f"{s['wr']:.1f}%" if not (isinstance(s['wr'], float) and s['wr'] != s['wr']) else 'N/A'
            l_wr = f"{l['wr']:.1f}%" if not (isinstance(l['wr'], float) and l['wr'] != l['wr']) else 'N/A'
            print(f"    {'':42} Hold ≤15d: {s['n']:>4} trades WR={s_wr:<6}  "
                  f">15d: {l['n']:>4} trades WR={l_wr}")
    if sharpe != 0:
        _sharpe_accum.setdefault(label.strip(), []).append(sharpe)


def section_header():
    print(f"\n  {'Strategy':<42} {'Sigs':>7} {'Trades':>7} {'Return':>9} "
          f"{'WR%':>7} {'Sharpe':>7} {'MaxDD':>7}  {'vsΔSPY':>8}")
    print("  " + "-" * 105)


# ─── Run one year period ───────────────────────────────────────────────────────
def run_year(year, symbols, capital,
             slippage_pct=0.0, commission=0.0, trades_log=False, compare_stall=False,
             bounce_bear_gate=0, selective=False, pooled_cap=10, full_compare=False,
             skip_old=False, breakeven_r=0.0, breakeven_bear_gate=0,
             atr_trail_always=False, atr_trail_mult=2.0, end_date_override=None):
    start = f"{year}-01-01"
    end   = f"{year}-12-31"
    if end_date_override and end_date_override[:4] == str(year):
        end = end_date_override
    year_type = YEAR_TYPE.get(str(year), 'UNKNOWN')
    spy_expected = SPY_ANNUAL.get(str(year), 0)
    modes = ['swing', 'longterm']

    print(f"\n{'='*80}")
    print(f"YEAR {year}  |  {year_type}  |  SPY expected: {spy_expected:+.1f}%")
    print(f"{'='*80}")

    print("\nFetching data...")
    historical, end_prices = fetch_all_data(symbols, start, end)
    if len(historical) < 5:
        print("  Not enough data, skipping.")
        return

    spy_info = spy_benchmark(historical, start, end, capital)
    if spy_info:
        print(f"  SPY actual: {spy_info['return']:+.2f}%  Sharpe={spy_info['sharpe']:.2f}  MaxDD={spy_info['drawdown']:+.2f}%")

    # ── OLD config signals ─────────────────────────────────────────────────
    if skip_old:
        print(f"\n[OLD CONFIG — skipped via --skip-old]")
        v9c_signals = []
    else:
        print(f"\n[OLD CONFIG — V9-C baseline]")
        old_signals = run_scan(historical, start, end, modes, config='old')
        # V9-C filter: PREMIUM+ with Minervini≥7
        v9c_signals = [s for s in old_signals
                       if s.get('quality') in ('GOLD', 'PREMIUM')
                       and (s.get('minervini_score', 0) >= 7
                            or s.get('type') in ('SMA20_CROSS', 'BOUNCE', 'CONTINUATION', 'Momentum'))]
        print(f"  V9-C filter: {len(v9c_signals)} signals (from {len(old_signals)} total)")

        # Selective filter: drop SMA20_CROSS/Momentum types (canonical 5-yr data shows
        # SMA20_CROSS = 10 trades / -30% sum; Momentum = 1 trade). BOUNCE/CONTINUATION/
        # TREND_CONFIRM kept. Daytrade is already excluded — modes=['swing','longterm'].
        if selective:
            keep = ('BOUNCE', 'CONTINUATION', 'TREND_CONFIRM')
            before = len(v9c_signals)
            v9c_signals = [s for s in v9c_signals if s.get('type') in keep]
            print(f"  [SELECTIVE] drop SMA20_CROSS/Momentum: {before} → {len(v9c_signals)} signals")

    # ── NEW config signals ─────────────────────────────────────────────────
    print(f"\n[NEW CONFIG — Regime-Adaptive]")
    new_signals = run_scan(historical, start, end, modes, config='new')
    # New: PREMIUM+ (BOUNCE requires GOLD, SMA20_CROSS vol>=2.5/RSI-bimodal already enforced)
    new_premium = [s for s in new_signals if s.get('quality') in ('GOLD', 'PREMIUM')]
    new_all     = [s for s in new_signals if s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH')]
    print(f"  New PREMIUM+: {len(new_premium)} signals | HIGH+: {len(new_all)}")

    if selective:
        _sel_keep = ('BOUNCE', 'CONTINUATION', 'TREND_CONFIRM')
        before_new = len(new_premium)
        new_premium = [s for s in new_premium if s.get('type') in _sel_keep]
        new_all     = [s for s in new_all     if s.get('type') in _sel_keep]
        print(f"  [SELECTIVE] NEW filtered: {before_new} → {len(new_premium)} PREMIUM+ signals")

    # ── Simulate ──────────────────────────────────────────────────────────
    print(f"\n[RESULTS]")
    section_header()

    # Benchmarks
    if spy_info:
        print(f"  {'SPY Buy & Hold':<42} {'N/A':>7} {'N/A':>7} "
              f"{spy_info['return']:>+9.2f}% {'N/A':>7} {spy_info['sharpe']:>7.2f} "
              f"{spy_info['drawdown']:>+7.2f}%  {'(benchmark)':>8}")

    output_dir = 'scanner_output/backtests' if trades_log else None
    # atr_trail_always applies to every row in the run (one run = one exit
    # policy) — the always-on branch takes priority over tp_as_trail inside
    # simulate(), so per-row tp_as_trail=True is overridden when the flag is set.
    sim_kw = dict(slippage_pct=slippage_pct, commission=commission, output_dir=output_dir,
                  bounce_bear_gate=bounce_bear_gate, breakeven_r=breakeven_r,
                  breakeven_bear_gate=breakeven_bear_gate,
                  atr_trail_always=atr_trail_always, atr_trail_mult=atr_trail_mult)

    if not skip_old:
        # OLD V9-C (best previous)
        rpt = simulate(v9c_signals, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='OLD V9-C', **sim_kw)
        print_report(rpt, f'OLD V9-C  PREMIUM+ TP→Trail', len(v9c_signals), spy_info)

        # OLD V9-C + regime-based position sizing
        rpt = simulate(v9c_signals, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='OLD V9-C RegimeSized', regime_sizing=True, **sim_kw)
        print_report(rpt, f'OLD V9-C  PREMIUM+ TP→Trail + RegimeSizing', len(v9c_signals), spy_info)

    # NEW PREMIUM+ (unlimited — legacy baseline)
    rpt = simulate(new_premium, start, end, end_prices, historical, capital,
                   tp_as_trail=True, label='NEW PREMIUM+', **sim_kw)
    print_report(rpt, f'NEW Regime-Adaptive  PREMIUM+ TP→Trail', len(new_premium), spy_info)

    # NEW PREMIUM+ pooled-cap — mirrors auto_portfolio.py MAX_ADDS_PER_SCAN logic.
    # Default pooled_cap=10 reproduces the documented +195% 5yr compound.
    # Requires --no-tc + --bounce-bear-gate 15 to reproduce the +195% baseline.
    new_premium_pooled = _pooled_cap(new_premium, max_per_day=pooled_cap)
    rpt = simulate(new_premium_pooled, start, end, end_prices, historical, capital,
                   tp_as_trail=True, label=f'NEW PREMIUM+ pooled-{pooled_cap}', **sim_kw)
    print_report(rpt, f'NEW Regime-Adaptive  PREMIUM+ pooled-cap={pooled_cap} ★', len(new_premium_pooled), spy_info, show_hold_split=True)

    # Ablation row — always emitted alongside the champion for direct comparison.
    # cap=2: hypothesis that a tighter daily gate keeps only the highest-conviction
    # setups and improves Sharpe without destroying total return.
    # If pooled_cap is already 2 this row is skipped (redundant).
    if pooled_cap != 2:
        new_premium_cap2 = _pooled_cap(new_premium, max_per_day=2)
        rpt = simulate(new_premium_cap2, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='NEW PREMIUM+ pooled-2', **sim_kw)
        print_report(rpt, f'NEW Regime-Adaptive  PREMIUM+ pooled-cap=2  ★★', len(new_premium_cap2), spy_info, show_hold_split=True)

    # SELECTIVE NEW — when --selective is active, new_premium is already filtered;
    # otherwise compute it here for the standalone comparison row.
    # Uses _pooled_cap(cap=2) for ranking consistency with the champion row.
    if not selective:
        _sel_types = ('BOUNCE', 'CONTINUATION', 'TREND_CONFIRM')
        selective_new = [s for s in new_premium if s.get('type') in _sel_types]
    else:
        selective_new = new_premium  # already filtered by Fix 7
    selective_new_pooled = _pooled_cap(selective_new, max_per_day=2)
    rpt = simulate(selective_new_pooled, start, end, end_prices, historical, capital,
                   tp_as_trail=True, label='SELECTIVE_NEW', **sim_kw)
    print_report(rpt, f'SELECTIVE NEW  PREMIUM+ no-SMA20X pooled-cap=2', len(selective_new_pooled), spy_info, show_hold_split=True)

    # NEW HIGH+
    rpt = simulate(new_all, start, end, end_prices, historical, capital,
                   tp_as_trail=True, label='NEW HIGH+', **sim_kw)
    print_report(rpt, f'NEW Regime-Adaptive  HIGH+    TP→Trail', len(new_all), spy_info)

    # NEW HIGH+ + regime sizing
    rpt = simulate(new_all, start, end, end_prices, historical, capital,
                   tp_as_trail=True, label='NEW HIGH+ RegimeSized', regime_sizing=True, **sim_kw)
    print_report(rpt, f'NEW Regime-Adaptive  HIGH+    TP→Trail + RegimeSizing', len(new_all), spy_info)

    # NEW PREMIUM+ no trailing
    rpt = simulate(new_premium, start, end, end_prices, historical, capital,
                   tp_as_trail=False, label='NEW PREMIUM+ NoTrail', **sim_kw)
    print_report(rpt, f'NEW Regime-Adaptive  PREMIUM+ Fixed TP', len(new_premium), spy_info)

    # Regime-specific breakdown (new config)
    if new_premium:
        sig_df = pd.DataFrame(new_premium)
        print(f"\n  New PREMIUM+ by regime:")
        for r in ['RED_MARKET', 'BEARISH', 'CHOPPY', 'NORMAL', 'EXPANSION']:
            rsigs = sig_df[sig_df['regime'] == r]
            if rsigs.empty: continue
            r_signals = [s for s in new_premium if s.get('regime') == r]
            rpt_r = simulate(r_signals, start, end, end_prices, historical, capital,
                             tp_as_trail=True, **sim_kw)
            print_report(rpt_r, f'  ↳ {r}', len(r_signals), spy_info)

    if full_compare:
        # ── HYBRID config — V9-H ─────────────────────────────────────────────
        print(f"\n[HYBRID CONFIG — V9-H]")
        hybrid_signals = run_scan(historical, start, end, modes, config='hybrid')
        hybrid_premium = [s for s in hybrid_signals if s.get('quality') in ('GOLD', 'PREMIUM')]
        hybrid_all     = [s for s in hybrid_signals if s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH')]
        n_bear_macro = sum(1 for s in hybrid_signals if s.get('bear_macro'))
        n_bull_macro = len(hybrid_signals) - n_bear_macro
        print(f"  Hybrid PREMIUM+: {len(hybrid_premium)} signals | HIGH+: {len(hybrid_all)}")
        print(f"  Macro split: {n_bull_macro} BULL_MACRO, {n_bear_macro} BEAR_MACRO signals")

        # V9-H Base: hybrid signals, TP→Trail (no caps/breakers)
        rpt = simulate(hybrid_premium, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-H Base', **sim_kw)
        print_report(rpt, 'V9-H  SMA200+BEARISH Block', len(hybrid_premium), spy_info)

        # V9-H + MaxPos: + position cap (8 bull / 3 bear)
        rpt = simulate(hybrid_premium, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-H+Cap', max_positions=8, **sim_kw)
        print_report(rpt, 'V9-H  + MaxPos=8/3', len(hybrid_premium), spy_info)

        # V9-H Full: + position cap + DD breaker at 15%
        rpt = simulate(hybrid_premium, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-H Full', max_positions=8,
                       dd_breaker_pct=0.15, **sim_kw)
        print_report(rpt, 'V9-H  + MaxPos + DD15%', len(hybrid_premium), spy_info)

        # V9-H HIGH+: broader quality gate
        rpt = simulate(hybrid_all, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-H HIGH+', max_positions=8,
                       dd_breaker_pct=0.15, **sim_kw)
        print_report(rpt, 'V9-H  HIGH+ + MaxPos + DD15%', len(hybrid_all), spy_info)

        # Regime-specific breakdown (hybrid PREMIUM+)
        if hybrid_premium:
            sig_df = pd.DataFrame(hybrid_premium)
            print(f"\n  V9-H PREMIUM+ by regime:")
            for r in ['RED_MARKET', 'BEARISH', 'CHOPPY', 'NORMAL', 'EXPANSION']:
                rsigs = sig_df[sig_df['regime'] == r]
                if rsigs.empty: continue
                r_signals = [s for s in hybrid_premium if s.get('regime') == r]
                rpt_r = simulate(r_signals, start, end, end_prices, historical, capital,
                                 tp_as_trail=True, **sim_kw)
                print_report(rpt_r, f'  ↳ {r}', len(r_signals), spy_info)

            # Bear/Bull macro breakdown
            print(f"\n  V9-H by macro regime:")
            for macro_label, macro_val in [('BULL_MACRO', False), ('BEAR_MACRO', True)]:
                m_sigs = [s for s in hybrid_premium if s.get('bear_macro') == macro_val]
                if not m_sigs: continue
                rpt_m = simulate(m_sigs, start, end, end_prices, historical, capital,
                                 tp_as_trail=True, **sim_kw)
                print_report(rpt_m, f'  ↳ {macro_label}', len(m_sigs), spy_info)

        # ── V9-H2: V9-C signals + SMA200 kill switch at simulate level ───────
        # Use SAME V9-C signal pipeline but filter out BEAR_MACRO signals
        # and add DD breaker + position cap. This preserves V9-C's proven
        # signal population while adding bear-market protection.
        print(f"\n[V9-H2 — V9-C signals + SMA200 Defense]")

        # Tag V9-C signals with bear_macro flag
        spy_df_raw = historical.get('SPY')
        v9c_tagged_h2 = []
        for s in v9c_signals:
            s_copy = dict(s)
            if spy_df_raw is not None:
                s_copy['bear_macro'] = is_spy_below_sma200(spy_df_raw, s['date'])
            else:
                s_copy['bear_macro'] = False
            v9c_tagged_h2.append(s_copy)

        n_bear = sum(1 for s in v9c_tagged_h2 if s['bear_macro'])
        n_bull = len(v9c_tagged_h2) - n_bear
        v9c_bull_only = [s for s in v9c_tagged_h2 if not s['bear_macro']]
        print(f"  V9-C signals: {len(v9c_tagged_h2)} total | {n_bull} BULL_MACRO, {n_bear} BEAR_MACRO")
        print(f"  After SMA200 filter: {len(v9c_bull_only)} signals")

        # V9-H2 Base: V9-C but skip BEAR_MACRO signals entirely
        rpt = simulate(v9c_bull_only, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-H2 Base', **sim_kw)
        print_report(rpt, 'V9-H2  V9-C + SMA200 filter', len(v9c_bull_only), spy_info)

        # V9-H2 + MaxPos cap (8 bull)
        rpt = simulate(v9c_bull_only, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-H2+Cap', max_positions=8, **sim_kw)
        print_report(rpt, 'V9-H2  + MaxPos=8', len(v9c_bull_only), spy_info)

        # V9-H2 + DD breaker (15%)
        rpt = simulate(v9c_bull_only, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-H2+DD', dd_breaker_pct=0.15, **sim_kw)
        print_report(rpt, 'V9-H2  + DD15%', len(v9c_bull_only), spy_info)

        # V9-H2 Full: MaxPos + DD breaker
        rpt = simulate(v9c_bull_only, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-H2 Full', max_positions=8,
                       dd_breaker_pct=0.15, **sim_kw)
        print_report(rpt, 'V9-H2  + MaxPos=8 + DD15%', len(v9c_bull_only), spy_info)

        # V9-H2 with BEAR_MACRO at reduced size (not blocked, 25% size)
        rpt = simulate(v9c_tagged_h2, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-H2 Reduced', max_positions=8,
                       dd_breaker_pct=0.15, **sim_kw)
        print_report(rpt, 'V9-H2  All sigs + MaxPos + DD15%', len(v9c_tagged_h2), spy_info)

        # ── V9-H3: V9-C pure + DD breaker only (no SMA200, no MaxPos) ────────
        # Isolate DD circuit breaker effect on V9-C signals
        print(f"\n[V9-H3 — V9-C + DD Breaker Only]")

        # V9-H3 DD10%: cut to 25% size at 10% drawdown
        rpt = simulate(v9c_signals, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-H3 DD10', dd_breaker_pct=0.10, **sim_kw)
        print_report(rpt, 'V9-H3  V9-C + DD10%', len(v9c_signals), spy_info)

        # V9-H3 DD15%: cut to 25% size at 15% drawdown
        rpt = simulate(v9c_signals, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-H3 DD15', dd_breaker_pct=0.15, **sim_kw)
        print_report(rpt, 'V9-H3  V9-C + DD15%', len(v9c_signals), spy_info)

        # V9-H3 DD20%: cut to 25% size at 20% drawdown
        rpt = simulate(v9c_signals, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-H3 DD20', dd_breaker_pct=0.20, **sim_kw)
        print_report(rpt, 'V9-H3  V9-C + DD20%', len(v9c_signals), spy_info)

        # V9-H3 MaxPos only: position cap without DD breaker
        rpt = simulate(v9c_signals, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-H3 MaxPos', max_positions=8, **sim_kw)
        print_report(rpt, 'V9-H3  V9-C + MaxPos=8', len(v9c_signals), spy_info)

        # V9-H3 Combined: MaxPos + DD15%
        rpt = simulate(v9c_signals, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-H3 Full', max_positions=8,
                       dd_breaker_pct=0.15, **sim_kw)
        print_report(rpt, 'V9-H3  V9-C + MaxPos=8 + DD15%', len(v9c_signals), spy_info)

        # ── V9-D: Regime as POST-FILTER + POSITION SIZING (not signal blocker) ──
        # Uses V9-C signal pipeline (proven best), applies regime intelligence via:
        #   1. BEARISH post-filter: drop signals generated during BEARISH regime
        #   2. Bear macro sizing: reduce position size when SPY < SMA200
        #   3. Combined: both BEARISH filter + bear_macro sizing
        # Key insight: regime info is valuable for SIZING, not for BLOCKING signals
        print(f"\n{'─'*80}")
        print(f"[V9-D — V9-C signals + Regime as Post-Filter & Sizing]")
        print(f"  Approach: same V9-C signals, regime used for filtering/sizing ONLY")
        print(f"{'─'*80}")

        # Tag V9-C signals with bear_macro + regime
        # NOTE: run_scan(config='old') sets bear_macro=False for all signals,
        # so we ALWAYS recompute it here from SPY data
        spy_df_raw = historical.get('SPY')
        v9c_tagged = []
        for s in v9c_signals:
            s_copy = dict(s)
            if spy_df_raw is not None:
                s_copy['bear_macro'] = is_spy_below_sma200(spy_df_raw, s['date'])
                regime_tag, _, _ = classify_day_regime(spy_df_raw, s['date'])
                s_copy['regime'] = regime_tag
            v9c_tagged.append(s_copy)

        n_bearish = sum(1 for s in v9c_tagged if s.get('regime') == 'BEARISH')
        n_bear_macro = sum(1 for s in v9c_tagged if s.get('bear_macro'))
        n_red = sum(1 for s in v9c_tagged if s.get('regime') == 'RED_MARKET')
        print(f"  V9-C signals: {len(v9c_tagged)} total")
        print(f"    BEARISH regime: {n_bearish} | RED_MARKET: {n_red} | bear_macro (SPY<SMA200): {n_bear_macro}")

        # ── V9-D1: Drop BEARISH regime only (22.2% WR, -3.28% avg, -13.4% P&L share)
        v9d_no_bearish = [s for s in v9c_tagged if s.get('regime') != 'BEARISH']
        print(f"\n  V9-D1: Drop BEARISH signals → {len(v9d_no_bearish)} signals ({n_bearish} dropped)")

        rpt = simulate(v9d_no_bearish, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-D1', **sim_kw)
        print_report(rpt, 'V9-D1  V9-C − BEARISH', len(v9d_no_bearish), spy_info)

        # ── V9-D2: Bear macro position sizing (reduce, not block)
        for bm_mult in [0.25, 0.50, 0.75]:
            rpt = simulate(v9c_tagged, start, end, end_prices, historical, capital,
                           tp_as_trail=True, label=f'V9-D2 bm={bm_mult}',
                           bear_macro_mult=bm_mult, **sim_kw)
            print_report(rpt, f'V9-D2  V9-C + bear_macro size={bm_mult:.0%}',
                         len(v9c_tagged), spy_info)

        # ── V9-D3: Combined — drop BEARISH + bear_macro sizing
        for bm_mult in [0.25, 0.50]:
            rpt = simulate(v9d_no_bearish, start, end, end_prices, historical, capital,
                           tp_as_trail=True, label=f'V9-D3 bm={bm_mult}',
                           bear_macro_mult=bm_mult, **sim_kw)
            print_report(rpt, f'V9-D3  − BEARISH + bear_macro={bm_mult:.0%}',
                         len(v9d_no_bearish), spy_info)

        # ── V9-D4: Combined — drop BEARISH + regime-based sizing (no bear_macro)
        rpt = simulate(v9d_no_bearish, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-D4', regime_sizing=True, **sim_kw)
        print_report(rpt, 'V9-D4  − BEARISH + regime sizing', len(v9d_no_bearish), spy_info)

        # ── V9-D5: Combined — drop BEARISH + bear_macro sizing + regime sizing
        rpt = simulate(v9d_no_bearish, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-D5',
                       regime_sizing=True, bear_macro_mult=0.50, **sim_kw)
        print_report(rpt, 'V9-D5  − BEARISH + regime + bm=50%', len(v9d_no_bearish), spy_info)

        # ── V9-D6: BEARISH block + bear_macro block (compare sizing vs blocking)
        v9d_no_bear_all = [s for s in v9c_tagged
                           if s.get('regime') != 'BEARISH' and not s.get('bear_macro')]
        print(f"\n  V9-D6 (block both): {len(v9d_no_bear_all)} signals "
              f"(dropped {len(v9c_tagged) - len(v9d_no_bear_all)})")
        rpt = simulate(v9d_no_bear_all, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='V9-D6', **sim_kw)
        print_report(rpt, 'V9-D6  − BEARISH − bear_macro (block)', len(v9d_no_bear_all), spy_info)

        # ── Summary: V9-C bear_macro trade quality breakdown
        if v9c_tagged:
            bear_sigs = [s for s in v9c_tagged if s.get('bear_macro')]
            bull_sigs = [s for s in v9c_tagged if not s.get('bear_macro')]
            print(f"\n  Signal breakdown:")
            print(f"    BULL_MACRO: {len(bull_sigs)} signals")
            print(f"    BEAR_MACRO: {len(bear_sigs)} signals")
            for regime in ['RED_MARKET', 'BEARISH', 'NORMAL', 'EXPANSION', 'CHOPPY']:
                n = sum(1 for s in v9c_tagged if s.get('regime') == regime)
                if n:
                    bm = sum(1 for s in v9c_tagged
                             if s.get('regime') == regime and s.get('bear_macro'))
                    print(f"      {regime}: {n} total ({bm} during bear_macro)")

    if not full_compare:
        v9d_no_bearish = []
        v9c_tagged = []

    # ── Stall-Exit Comparison ─────────────────────────────────────────────
    # Re-runs key strategies with stall_exit=True (SMA20 crossunder instead
    # of 30-day MaxHold) so results are directly comparable to the rows above.
    if compare_stall:
        print(f"\n{'─'*80}")
        print(f"[STALL-EXIT COMPARISON — SMA20 crossunder replaces 30d MaxHold]")
        print(f"  Exit when close < SMA20 for 2 consecutive bars (min 3d held)")
        print(f"  Safety cap: 60 days  |  Stop/TP/Trail logic unchanged")
        print(f"{'─'*80}")
        section_header()

        for sma_p, tag in [(20, 'SMA20'), (50, 'SMA50')]:
            stall_kw = {**sim_kw, 'stall_exit': True, 'stall_sma_period': sma_p}
            print(f"\n  ── {tag} crossunder (2 consecutive bars) ──")

            rpt = simulate(v9c_signals, start, end, end_prices, historical, capital,
                           tp_as_trail=True, label=f'SE{sma_p} V9-C', **stall_kw)
            print_report(rpt, f'SE{sma_p}  V9-C  PREMIUM+ TP→Trail', len(v9c_signals), spy_info)

            rpt = simulate(new_premium, start, end, end_prices, historical, capital,
                           tp_as_trail=True, label=f'SE{sma_p} NEW PREMIUM+', **stall_kw)
            print_report(rpt, f'SE{sma_p}  NEW PREMIUM+ TP→Trail', len(new_premium), spy_info)

            rpt = simulate(new_all, start, end, end_prices, historical, capital,
                           tp_as_trail=True, label=f'SE{sma_p} NEW HIGH+', **stall_kw)
            print_report(rpt, f'SE{sma_p}  NEW HIGH+ TP→Trail', len(new_all), spy_info)

            rpt = simulate(v9d_no_bearish, start, end, end_prices, historical, capital,
                           tp_as_trail=True, label=f'SE{sma_p} V9-D1', **stall_kw)
            print_report(rpt, f'SE{sma_p}  V9-D1  V9-C − BEARISH', len(v9d_no_bearish), spy_info)

            rpt = simulate(v9c_signals, start, end, end_prices, historical, capital,
                           tp_as_trail=True, label=f'SE{sma_p} V9-C RegimeSized',
                           regime_sizing=True, **stall_kw)
            print_report(rpt, f'SE{sma_p}  V9-C  + RegimeSizing', len(v9c_signals), spy_info)

        # Regime breakdown under SMA20 stall-exit only
        stall20_kw = {**sim_kw, 'stall_exit': True, 'stall_sma_period': 20}
        if new_premium:
            sig_df = pd.DataFrame(new_premium)
            print(f"\n  Stall-exit SMA20 NEW PREMIUM+ by regime:")
            for r in ['RED_MARKET', 'BEARISH', 'CHOPPY', 'NORMAL', 'EXPANSION']:
                rsigs = sig_df[sig_df['regime'] == r]
                if rsigs.empty:
                    continue
                r_signals = [s for s in new_premium if s.get('regime') == r]
                rpt_r = simulate(r_signals, start, end, end_prices, historical, capital,
                                 tp_as_trail=True, **stall20_kw)
                print_report(rpt_r, f'  ↳ {r}', len(r_signals), spy_info)

    # ── Bounce-Bear-Gate Comparison ───────────────────────────────────────
    # Runs key strategies with bounce_bear_gate=15 alongside the standard runs.
    # Gate: skip BOUNCE+RED_MARKET entries when SPY has been below SMA200
    # for >= 15 consecutive trading days (sustained bear, not a brief dip).
    if bounce_bear_gate == 0:
        print(f"\n{'─'*80}")
        print(f"[BOUNCE-BEAR-GATE COMPARISON — gate=15 consecutive days SPY<SMA200]")
        print(f"  Skips BOUNCE+RED_MARKET entries during sustained bear (>=15d below SMA200)")
        print(f"{'─'*80}")
        section_header()
        bbg_kw   = {**sim_kw, 'bounce_bear_gate': 15}
        bbg10_kw = {**sim_kw, 'bounce_bear_gate': 10}

        rpt = simulate(v9c_signals, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='BBG15 V9-C', **bbg_kw)
        print_report(rpt, 'BBG15  V9-C  PREMIUM+ TP→Trail', len(v9c_signals), spy_info)

        rpt = simulate(new_premium, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='BBG15 NEW PREMIUM+', **bbg_kw)
        print_report(rpt, 'BBG15  NEW PREMIUM+ TP→Trail', len(new_premium), spy_info)

        rpt = simulate(v9d_no_bearish, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='BBG15 V9-D1', **bbg_kw)
        print_report(rpt, 'BBG15  V9-D1  V9-C − BEARISH', len(v9d_no_bearish), spy_info)

        rpt = simulate(v9c_signals, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='BBG15 V9-C RegimeSized',
                       regime_sizing=True, **bbg_kw)
        print_report(rpt, 'BBG15  V9-C  + RegimeSizing', len(v9c_signals), spy_info)

        rpt = simulate(v9c_signals, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='BBG10 V9-C', **bbg10_kw)
        print_report(rpt, 'BBG10  V9-C  PREMIUM+ (gate=10d)', len(v9c_signals), spy_info)

        rpt = simulate(new_premium, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label='BBG10 NEW PREMIUM+', **bbg10_kw)
        print_report(rpt, 'BBG10  NEW PREMIUM+ (gate=10d)', len(new_premium), spy_info)


# ─── CLI ──────────────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(
        description='Regime-Aware Backtest: Old vs New',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
EXAMPLE USAGE:
  # Champion config on 50-symbol curated list (5 years)
  python backtest_regime_compare.py --no-tc --bounce-bear-gate 15 --watchlist input/optimizer_watch.txt

  # Champion config on large screener (NEW only, skip OLD to save time)
  python backtest_regime_compare.py --no-tc --bounce-bear-gate 15 --watchlist input/screener.txt --skip-old

  # Ablation: test cap=2 vs cap=10
  python backtest_regime_compare.py --no-tc --bounce-bear-gate 15 --pooled-cap 2

  # Full comparison (slow)
  python backtest_regime_compare.py --no-tc --bounce-bear-gate 15 --full-compare --trades-log

PARAMETER REFERENCE:
'''
    )
    p.add_argument('--watchlist',   default=None,
                   help='Path to watchlist file (e.g., input/optimizer_watch.txt). One symbol per line. Default: all symbols in config.')

    p.add_argument('--years',       default='2022,2023,2024,2025,2026',
                   help='Comma-separated list of years to backtest (default: 2022,2023,2024,2025,2026). Example: --years 2023,2024,2025')

    p.add_argument('--limit',       type=int,   default=0,
                   help='Max symbols to test (0=all, default 0). Use with --shuffle for random sampling. Example: --limit 50 --shuffle')

    p.add_argument('--capital',     type=int,   default=100_000,
                   help='Starting capital in USD (default: 100000). Used to calculate percentage returns and position sizing.')

    p.add_argument('--slippage',    type=float, default=0.0,
                   help='Slippage per side as decimal fraction (default: 0.0). Example: 0.001 = 0.1%% slippage per entry/exit.')

    p.add_argument('--commission',  type=float, default=0.0,
                   help='Flat commission per trade side in USD (default: 0.0). Modern brokers typically $0.')

    p.add_argument('--trades-log',  action='store_true',
                   help='Write per-trade CSV logs to scanner_output/backtests/ (one file per year/config).')

    p.add_argument('--stall-exit',  action='store_true',
                   help='Add comparison section: SMA20 crossunder as alternative exit (replaces fixed MaxHold timer).')

    p.add_argument('--bounce-bear-gate', type=int, default=0,
                   help='Block BOUNCE+RED_MARKET entries when SPY has been below SMA200 for >=N consecutive days (0=off). '
                        'Recommended: 15 (sustained bear filter). Example: --bounce-bear-gate 15')
    p.add_argument('--breakeven-r', type=float, default=0.0,
                   help='Move stop to breakeven once a position reaches entry + N×(initial risk) (0=off). '
                        'Targets the ≤15d giveback. Try 1.0 or 2.0. Example: --breakeven-r 2.0')
    p.add_argument('--breakeven-bear-gate', type=int, default=0,
                   help='Skip the breakeven move when SPY has been below SMA200 for >=N consecutive days '
                        '(0=off). Keeps mixed/bull-year gains without bear-year damage. Example: --breakeven-bear-gate 15')

    p.add_argument('--selective', action='store_true',
                   help='Enable SELECTIVE_MODE: drop SMA20_CROSS + Momentum types, keep only BOUNCE/CONTINUATION/TREND_CONFIRM. '
                        'Caps at ~1 entry per day (~100 trades/yr). For comparing signal-type filtering.')

    p.add_argument('--full-compare', action='store_true',
                   help='Run retired V9-H / V9-H2 / V9-H3 / V9-D config comparison rows (much slower, for deep analysis only).')

    p.add_argument('--no-tc', action='store_true',
                   help='Disable TREND_CONFIRM multi-gate check in signal collection. TREND_CONFIRM Path B destroys edge (-24pts); '
                        'Path A is kept but minimal (+2.7pts). Use --no-tc to reproduce pre-TC +195%% baseline or disable all TREND_CONFIRM logic.')

    p.add_argument('--pooled-cap', type=int, default=10,
                   help='Max NEW signals to admit per calendar day in pooled-cap★ row (default: 10). '
                        'Ranks signals globally by Quality→WinProb→R:R→Dist≤25%% before capping. '
                        'Use 2 for tight cap ablation (current best: cap=10 with BBG15). Example: --pooled-cap 2')

    p.add_argument('--shuffle', action='store_true',
                   help='Shuffle watchlist before applying --limit (random sample vs first-N). '
                        'Use --seed to fix reproducibility. Example: --limit 100 --shuffle --seed 42')

    p.add_argument('--seed', type=int, default=42,
                   help='Random seed for --shuffle (default: 42). Same seed always produces identical symbol set.')

    p.add_argument('--no-aroon', action='store_true',
                   help='Disable Aroon oscillator confirmation gate (sets threshold=999). For ablation testing of Aroon impact.')

    p.add_argument('--skip-old', action='store_true',
                   help='Skip OLD V9-C baseline rows; run only NEW champion config (cuts runtime ~50%%). '
                        'Use when you only care about NEW performance, not side-by-side comparison.')

    p.add_argument('--atr-trail-always', action='store_true',
                   help='Champion exit (validated 2026-05-07): ATR trailing stop active from entry day 1, '
                        'replacing the TP-triggered trail. Fixed stop is the floor; stop only moves up. '
                        'Applies to ALL rows in the run. +234%% 5yr vs +137%% post-TP on optimizer_watch.txt.')

    p.add_argument('--atr-trail-mult', type=float, default=2.0,
                   help='ATR multiplier for trailing stops (default: 2.0 = sweep winner, matches config.ATR_TRAIL_MULT). '
                        'Used by both --atr-trail-always and the post-TP trail. Example: --atr-trail-mult 2.5')

    p.add_argument('--no-winprob-cal', action='store_true',
                   help='Disable the empirical WinProb calibration table (scanner falls back to the confluence '
                        'heuristic; cascade detectors emit no WinProb). Use for baseline-vs-calibrated ablation.')
    p.add_argument('--end-date',    default=None,
                   help='Override end date (YYYY-MM-DD) for whichever requested year it falls in, e.g. '
                        'for a true YTD run instead of the default full Jan1-Dec31 window.')

    return p.parse_args()


def main():
    args = parse_args()
    years = [int(y.strip()) for y in args.years.split(',')]

    if args.no_tc:
        import config as _cfg
        _cfg.TREND_CONFIRM['enabled'] = False
        print("⚠  --no-tc: TREND_CONFIRM disabled for this run (reproducing NEW-no-TC baseline)")

    if args.no_aroon:
        import config as _cfg
        _cfg.AROON_CONFIRM_THRESHOLD = 999  # always-False → removes +5pt bonus
        print("⚠  --no-aroon: Aroon oscillator gate disabled (ablation)")

    if args.atr_trail_always:
        print(f"⚠  --atr-trail-always: ATR×{args.atr_trail_mult:g} trail from entry day 1 "
              f"(champion exit — replaces post-TP trail on ALL rows)")

    if args.no_winprob_cal:
        import config as _cfg
        _cfg.WINPROB_CALIBRATION['enabled'] = False
        print("⚠  --no-winprob-cal: empirical WinProb table disabled (heuristic-only baseline)")

    print("=" * 80)
    print("REGIME-AWARE BACKTEST: OLD CONFIG (V9-C) vs NEW CONFIG")
    print("=" * 80)
    print("New changes: SMA20_CROSS vol>=2.5, RSI <48|55-68, BOUNCE GOLD-only,")
    print("            4-regime gate (NORMAL/CHOPPY/BEARISH/RED_MARKET)")
    print(f"Years: {years}  |  Capital: ${args.capital:,}  |  "
          f"Slippage: {args.slippage*100:.2f}%  Commission: ${args.commission:.2f}/side")

    symbols = load_symbols(args.watchlist, args.limit, shuffle=args.shuffle, seed=args.seed)
    if not symbols:
        print("No symbols found.")
        return

    for year in years:
        run_year(year, symbols, args.capital,
                 slippage_pct=args.slippage, commission=args.commission,
                 trades_log=args.trades_log, compare_stall=args.stall_exit,
                 bounce_bear_gate=args.bounce_bear_gate,
                 selective=args.selective,
                 pooled_cap=args.pooled_cap,
                 full_compare=args.full_compare,
                 skip_old=args.skip_old,
                 breakeven_r=args.breakeven_r,
                 breakeven_bear_gate=args.breakeven_bear_gate,
                 atr_trail_always=args.atr_trail_always,
                 atr_trail_mult=args.atr_trail_mult,
                 end_date_override=args.end_date)

    if len(years) > 1 and _sharpe_accum:
        print(f"\n{'='*80}")
        print(f"MULTI-YEAR SHARPE SUMMARY ({len(years)} years)")
        print(f"{'='*80}")
        print(f"  {'Strategy':<50} {'Avg Sharpe':>10}  {'Years':>5}")
        print("  " + "-" * 70)
        for lbl, vals in sorted(_sharpe_accum.items(), key=lambda x: -sum(x[1])/len(x[1])):
            avg = sum(vals) / len(vals)
            print(f"  {lbl:<50} {avg:>+10.2f}   {len(vals):>5}")

    print(f"\n{'='*80}")
    print("BACKTEST COMPLETE")
    print("=" * 80)


if __name__ == '__main__':
    main()
