#!/usr/bin/env python3
"""
Regime-Aware Backtest: Old Config vs New Config
================================================
Compares the last best version (V9-C: V8+TP→Trail, PREMIUM+) against the
new regime-adaptive config across three market environments:

  2022: Bearish  (SPY -18.1%)
  2023: Bullish  (SPY +26.3%)
  2024: Mixed/Bull (SPY +23.3%)

New config changes tested:
  - SMA20_CROSS: vol >= 2.5 (was 1.8), RSI <48 or 55-68 (was 48-62)
  - BOUNCE: quality GOLD only for auto-entry (PREMIUM blocked)
  - Regime gate: RED_MARKET blocks longs, BEARISH blocks SMA20_CROSS,
                 CHOPPY blocks SMA20_CROSS + BOUNCE non-GOLD

Usage:
  python backtest_regime_compare.py
  python backtest_regime_compare.py --watchlist input/optimizer_watch.txt --years 2022,2023,2024
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
from yfinance_adapter import YFinanceAdapter

logging.basicConfig(level=logging.WARNING, format='%(message)s')
logger = logging.getLogger(__name__)

# ─── SPY annual returns for reference ─────────────────────────────────────────
SPY_ANNUAL = {
    '2022': -18.1,
    '2023': +26.3,
    '2024': +23.3,
    '2025': -5.0,   # approx through Mar 2026
}
YEAR_TYPE = {
    '2022': 'BEARISH',
    '2023': 'BULLISH',
    '2024': 'BULLISH',
    '2025': 'MIXED',
}


# ─── Load symbols ─────────────────────────────────────────────────────────────
def load_symbols(path=None, limit=0):
    paths = [path] if path else [
        'input/optimizer_watch.txt', 'input/watchlist3.txt', 'input/all.txt'
    ]
    for p in paths:
        if p and Path(p).exists():
            raw = Path(p).read_text().splitlines()
            syms = [s.strip().split()[0] for s in raw
                    if s.strip() and not s.startswith('#')]
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
        if (i + 1) % 20 == 0:
            print(f"    Fetched {i+1}/{len(all_syms)}...")

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
            sig = bounce
    if sig:
        return sig

    # SMA20_CROSS — blocked in CHOPPY, BEARISH, RED_MARKET
    if regime not in ('RED_MARKET', 'BEARISH', 'CHOPPY'):
        try:
            sig = detector.detect_sma20_cross(df_slice, symbol, mode, '1 day', spy_perf)
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

    for day_idx, sim_date in enumerate(sim_dates):
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


# ─── Simulation ───────────────────────────────────────────────────────────────
def simulate(signals, start_date, end_date, end_prices, historical,
             capital=100_000, tp_as_trail=True, label='', regime_sizing=False,
             slippage_pct=0.0, commission=0.0,
             max_positions=0, dd_breaker_pct=0.0):
    """
    Simple, self-contained simulation that avoids SimulationMode bugs.
    - Enters at signal price on signal date
    - Checks daily H/L for stop or TP hit each bar
    - tp_as_trail=True: when TP hit, activates 2×ATR trailing stop (V9-C mode)
    - tp_as_trail=False: exits hard at TP price
    - Exits after MAX_HOLD bars at close price regardless
    - Position size: min(10% capital by value, 2% capital by risk)
    - regime_sizing=True: scales position size down in weak regimes
    - max_positions: cap on concurrent open positions (0=unlimited)
    - dd_breaker_pct: if portfolio DD exceeds this (e.g. 0.15), cut size to 25%
    """
    if not signals:
        print(f"  [{label}] No signals → skip")
        return None

    MAX_HOLD = 30   # trading days
    ATR_TRAIL_MULT = 2.0
    MAX_POS_PCT   = 0.10
    MAX_RISK_PCT  = 0.02
    QUALITY_MULT  = {'GOLD': 1.0, 'PREMIUM': 1.0, 'HIGH': 1.0, 'STANDARD': 1.0}
    # Only reduce BEARISH (22.2% WR, -3.28% avg P&L, -13.4% P&L share)
    # RED_MARKET = +55.8% P&L share — do NOT cut it
    REGIME_SIZE   = {'EXPANSION': 1.0, 'NORMAL': 1.0, 'CHOPPY': 0.8,
                     'BEARISH': 0.25, 'RED_MARKET': 1.0}

    cap = float(capital)
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
                dd_mult = 0.25  # cut to 25% size during drawdown
            elif current_dd < -dd_breaker_pct * 0.33:
                dd_mult = 1.0   # recovered within 1/3 of threshold → full size
            # else stays at 1.0

        # --- Dynamic position cap (bear_macro aware) ---
        effective_max_pos = max_positions if max_positions > 0 else 999
        if max_positions > 0 and spy is not None:
            # Check if any of today's signals are in bear_macro
            today_sigs = sig_by_date.get(today_norm, [])
            if today_sigs and today_sigs[0].get('bear_macro', False):
                effective_max_pos = min(3, max_positions)

        # --- Enter new positions ---
        for sig in sig_by_date.get(today_norm, []):
            sym = sig['symbol']
            if sym in open_pos:
                continue  # already have a position
            # Position cap check
            if len(open_pos) >= effective_max_pos:
                break  # no more room
            price = float(sig.get('price', 0)) * (1 + slippage_pct)  # slippage on entry
            stop  = float(sig.get('stop_loss', price * 0.95))
            tp    = float(sig.get('take_profit', price * 1.10))
            if price <= 0 or stop <= 0:
                continue
            risk_per_share = abs(price - stop)
            if risk_per_share <= 0:
                continue
            qual = sig.get('quality', 'STANDARD')
            mult = QUALITY_MULT.get(qual, 1.0)
            mult *= dd_mult  # apply DD breaker sizing
            if regime_sizing:
                mult *= REGIME_SIZE.get(sig.get('regime', 'NORMAL'), 1.0)
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
                '_tp_hit': False, '_trail_stop': None,
            }

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

            exit_price = None
            exit_reason = None

            # Skip same-day exits
            if today_norm == pos['entry_date']:
                pass  # no exit checks on entry day

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

            # Max hold
            if exit_price is None and days_held >= MAX_HOLD:
                exit_price  = cl
                exit_reason = 'MaxHold'

            if exit_price is not None:
                qty  = pos['qty']
                exit_net = exit_price * (1 - slippage_pct) - commission / qty  # slippage + commission on exit
                pnl  = (exit_net - pos['entry_price']) * qty
                cost = pos['cost']
                trades.append({
                    'pnl': pnl,
                    'pnl_pct': pnl / cost * 100,
                    'win': pnl > 0,
                    'entry': pos['entry_price'],
                    'exit': exit_net,
                    'reason': exit_reason,
                })
                cap += exit_net * qty
                del open_pos[sym]

        # Mark-to-market for equity curve (open positions at close)
        open_val = sum(
            historical[s].loc[historical[s].index.normalize() == today_norm, 'close'].iloc[-1]
            * p['qty']
            if s in historical and not historical[s][historical[s].index.normalize() == today_norm].empty
            else p['entry_price'] * p['qty']
            for s, p in open_pos.items()
        )
        equity_curve.append(cap + open_val)

    # Close remaining positions at end_prices
    for sym, pos in list(open_pos.items()):
        ep  = end_prices.get(sym, pos['entry_price'])
        ep_net = ep * (1 - slippage_pct) - commission / max(pos['qty'], 1)
        qty = pos['qty']
        pnl = (ep_net - pos['entry_price']) * qty
        trades.append({
            'pnl': pnl,
            'pnl_pct': pnl / pos['cost'] * 100,
            'win': pnl > 0,
            'entry': pos['entry_price'],
            'exit': ep_net,
            'reason': 'SimEnd',
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

    return {
        'total_trades':  n_trades,
        'win_rate':      win_rate,
        'total_return':  total_return,
        'sharpe_ratio':  sharpe,
        'max_drawdown':  max_drawdown,
        'total_pnl':     cap - capital,
        'avg_win':       np.mean([t['pnl_pct'] for t in trades if t['win']]) if n_wins else 0,
        'avg_loss':      np.mean([t['pnl_pct'] for t in trades if not t['win']]) if (n_trades-n_wins) else 0,
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
def print_report(report, label, n_signals, spy_info=None):
    if report is None:
        print(f"  {label:<42} NO DATA")
        return
    # mock_trader uses 'total_return' (not 'total_return_pct'), 'max_drawdown' (not '_pct')
    # win_rate is already a percentage (0-100)
    ret    = report.get('total_return', report.get('total_return_pct', 0)) or 0
    sharpe = report.get('sharpe_ratio', 0) or 0
    dd     = report.get('max_drawdown', report.get('max_drawdown_pct', 0)) or 0
    wr     = report.get('win_rate', 0) or 0        # already 0-100
    n_trades = report.get('total_trades', 0)
    spy_diff = (ret - spy_info['return']) if spy_info else 0
    spy_str = f"{spy_diff:>+8.2f}%" if spy_info else "     N/A"
    print(f"  {label:<42} {n_signals:>7} {n_trades:>7} {ret:>+9.2f}% {wr:>7.1f}% "
          f"{sharpe:>7.2f} {dd:>+7.2f}%  {spy_str}")


def section_header():
    print(f"\n  {'Strategy':<42} {'Sigs':>7} {'Trades':>7} {'Return':>9} "
          f"{'WR%':>7} {'Sharpe':>7} {'MaxDD':>7}  {'vsΔSPY':>8}")
    print("  " + "-" * 105)


# ─── Run one year period ───────────────────────────────────────────────────────
def run_year(year, symbols, watchlist_path, limit, capital,
             slippage_pct=0.0, commission=0.0):
    start = f"{year}-01-01"
    end   = f"{year}-12-31"
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
    print(f"\n[OLD CONFIG — V9-C baseline]")
    old_signals = run_scan(historical, start, end, modes, config='old')
    # V9-C filter: PREMIUM+ with Minervini≥7
    v9c_signals = [s for s in old_signals
                   if s.get('quality') in ('GOLD', 'PREMIUM')
                   and (s.get('minervini_score', 0) >= 7
                        or s.get('type') in ('SMA20_CROSS', 'BOUNCE', 'CONTINUATION', 'Momentum'))]
    print(f"  V9-C filter: {len(v9c_signals)} signals (from {len(old_signals)} total)")

    # ── NEW config signals ─────────────────────────────────────────────────
    print(f"\n[NEW CONFIG — Regime-Adaptive]")
    new_signals = run_scan(historical, start, end, modes, config='new')
    # New: PREMIUM+ (BOUNCE requires GOLD, SMA20_CROSS vol>=2.5/RSI-bimodal already enforced)
    new_premium = [s for s in new_signals if s.get('quality') in ('GOLD', 'PREMIUM')]
    new_all     = [s for s in new_signals if s.get('quality') in ('GOLD', 'PREMIUM', 'HIGH')]
    print(f"  New PREMIUM+: {len(new_premium)} signals | HIGH+: {len(new_all)}")

    # ── Simulate ──────────────────────────────────────────────────────────
    print(f"\n[RESULTS]")
    section_header()

    # Benchmarks
    if spy_info:
        print(f"  {'SPY Buy & Hold':<42} {'N/A':>7} {'N/A':>7} "
              f"{spy_info['return']:>+9.2f}% {'N/A':>7} {spy_info['sharpe']:>7.2f} "
              f"{spy_info['drawdown']:>+7.2f}%  {'(benchmark)':>8}")

    sim_kw = dict(slippage_pct=slippage_pct, commission=commission)

    # OLD V9-C (best previous)
    rpt = simulate(v9c_signals, start, end, end_prices, historical, capital,
                   tp_as_trail=True, label='OLD V9-C', **sim_kw)
    print_report(rpt, f'OLD V9-C  PREMIUM+ TP→Trail', len(v9c_signals), spy_info)

    # OLD V9-C + regime-based position sizing
    rpt = simulate(v9c_signals, start, end, end_prices, historical, capital,
                   tp_as_trail=True, label='OLD V9-C RegimeSized', regime_sizing=True, **sim_kw)
    print_report(rpt, f'OLD V9-C  PREMIUM+ TP→Trail + RegimeSizing', len(v9c_signals), spy_info)

    # NEW PREMIUM+
    rpt = simulate(new_premium, start, end, end_prices, historical, capital,
                   tp_as_trail=True, label='NEW PREMIUM+', **sim_kw)
    print_report(rpt, f'NEW Regime-Adaptive  PREMIUM+ TP→Trail', len(new_premium), spy_info)

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
    v9c_tagged = []
    for s in v9c_signals:
        s_copy = dict(s)
        if spy_df_raw is not None:
            s_copy['bear_macro'] = is_spy_below_sma200(spy_df_raw, s['date'])
        else:
            s_copy['bear_macro'] = False
        v9c_tagged.append(s_copy)

    n_bear = sum(1 for s in v9c_tagged if s['bear_macro'])
    n_bull = len(v9c_tagged) - n_bear
    v9c_bull_only = [s for s in v9c_tagged if not s['bear_macro']]
    print(f"  V9-C signals: {len(v9c_tagged)} total | {n_bull} BULL_MACRO, {n_bear} BEAR_MACRO")
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
    rpt = simulate(v9c_tagged, start, end, end_prices, historical, capital,
                   tp_as_trail=True, label='V9-H2 Reduced', max_positions=8,
                   dd_breaker_pct=0.15, **sim_kw)
    print_report(rpt, 'V9-H2  All sigs + MaxPos + DD15%', len(v9c_tagged), spy_info)

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


# ─── CLI ──────────────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description='Regime-Aware Backtest: Old vs New')
    p.add_argument('--watchlist',   default=None,  help='Watchlist path')
    p.add_argument('--years',       default='2022,2023,2024', help='Years to test (comma-sep)')
    p.add_argument('--limit',       type=int,   default=0,     help='Symbol limit (0=all)')
    p.add_argument('--capital',     type=int,   default=100_000)
    p.add_argument('--slippage',    type=float, default=0.0,   help='Slippage fraction per side (e.g. 0.001 = 0.1 pct)')
    p.add_argument('--commission',  type=float, default=0.0,   help='Flat commission $ per trade side')
    return p.parse_args()


def main():
    args = parse_args()
    years = [int(y.strip()) for y in args.years.split(',')]

    print("=" * 80)
    print("REGIME-AWARE BACKTEST: OLD CONFIG (V9-C) vs NEW CONFIG")
    print("=" * 80)
    print("New changes: SMA20_CROSS vol>=2.5, RSI <48|55-68, BOUNCE GOLD-only,")
    print("            4-regime gate (NORMAL/CHOPPY/BEARISH/RED_MARKET)")
    print(f"Years: {years}  |  Capital: ${args.capital:,}  |  "
          f"Slippage: {args.slippage*100:.2f}%  Commission: ${args.commission:.2f}/side")

    symbols = load_symbols(args.watchlist, args.limit)
    if not symbols:
        print("No symbols found.")
        return

    for year in years:
        run_year(year, symbols, args.watchlist, args.limit, args.capital,
                 slippage_pct=args.slippage, commission=args.commission)

    print(f"\n{'='*80}")
    print("BACKTEST COMPLETE")
    print("=" * 80)


if __name__ == '__main__':
    main()
