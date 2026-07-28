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
    # Bound the scan to real data (mirrors simulate()'s spy.index-derived trading_days):
    # a raw business-day range through end_date runs past the last real bar whenever
    # end_date is in the future (e.g. a YTD request with the default Dec-31 end), padding
    # the loop with dates no symbol has data for. The per-symbol exact-date match below
    # already prevents phantom signals on those days, but it repeats the same stale
    # end-of-data regime classification for every padding day, inflating the printed
    # "Regime distribution" diagnostic. Cap end_date at SPY's real last bar instead.
    real_end = pd.Timestamp(end_date)
    if spy_df is not None and len(spy_df) > 0:
        real_end = min(real_end, spy_df.index.max())
    sim_dates = pd.date_range(start=start_date, end=real_end, freq='B')
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
                        'vol':         float(sig.get('Vol', 0) or 0),  # live Vol tiebreak
                        'rsi':         float(sig.get('RSI', 0) or 0),  # H6: RSI rank-scores candidate
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


def _apply_bounce_sma200_gate(signals, historical, spy_below_dates=None):
    """Drop BOUNCE signals fired while the stock traded below its OWN 200-day SMA.

    spy_below_dates: optional set of normalized dates — when given, the gate is
    conditional: it only applies on days SPY itself was below its SMA200
    (bear-only variant). Rationale from the 2026-07-22 unconditional A/B: the
    always-on gate rescued 2022 (+0.83 Sharpe realistic) but destroyed the 2023
    recovery (−1.27) because post-bottom V-recovery entries are also below
    their SMA200s; conditioning on SPY's own bear state keeps both.

    Rationale (2026-07-22 research review, CLAUDE.md §12): dip-buying only has an
    edge in stocks above their own long-term trend — below it, "dips" chain into
    falling knives (IREN's three consecutive 2022 stop-outs). BBG15 already gates
    on SPY's SMA200; this is the per-stock analog. Applied BEFORE pooling so gated
    signals don't consume pooled-cap slots (mirrors live, where detect_bounce would
    simply not fire). Signals from symbols with <200 bars of history pass through
    ungated (SMA200 undefined — same permissive default as BBG's warmup).

    Returns (kept_signals, gated_count).
    """
    sma_cache: dict = {}
    kept, gated = [], 0
    for s in signals:
        if s.get('type') != 'BOUNCE':
            kept.append(s)
            continue
        if spy_below_dates is not None and \
                pd.Timestamp(s['date']).normalize() not in spy_below_dates:
            kept.append(s)          # bear-only variant: SPY healthy → gate off
            continue
        sym = s['symbol']
        ser = sma_cache.get(sym)
        if ser is None:
            df = historical.get(sym)
            ser = df['close'].rolling(200).mean() if df is not None else None
            sma_cache[sym] = ser if ser is not None else False
        if ser is None or ser is False:
            kept.append(s)
            continue
        d = pd.Timestamp(s['date']).normalize()
        upto = ser[ser.index.normalize() <= d]
        if upto.empty or pd.isna(upto.iloc[-1]):
            kept.append(s)          # <200 bars — SMA200 undefined, pass
            continue
        df = historical[sym]
        px = df['close'][df.index.normalize() <= d]
        if not px.empty and float(px.iloc[-1]) < float(upto.iloc[-1]):
            gated += 1
        else:
            kept.append(s)
    return kept, gated


def _compute_panic_days(spy_df, sma_window: int = 200, vol_window: int = 20,
                        vol_thresh_daily: float = 0.015):
    """Return the set of normalized dates in a Daniel-Moskowitz 'panic state':

        SPY close < its 200-day SMA  AND  20-day realized daily vol > 1.5%
        (≈24% annualized — elevated-vol regime)

    D&M: momentum crashes concentrate in panic states — post-decline, high-vol
    periods — and fire on the market rebounds within them (loser beta > 3).
    Used by --panic-throttle to halve entry size on those days (their dynamic
    strategy scales exposure by forecast mean/vol; this is the simplest
    single-lever version of that idea).
    """
    if spy_df is None or len(spy_df) < sma_window:
        return set()
    close = spy_df['close']
    sma = close.rolling(sma_window).mean()
    vol = close.pct_change().rolling(vol_window).std()
    mask = (close < sma) & (vol > vol_thresh_daily)
    return {pd.Timestamp(d).normalize() for d in spy_df.index[mask.fillna(False)]}


def _stamp_residual_momentum(signals, historical, ret_window: int = 15,
                             beta_window: int = 60):
    """Stamp each signal with 'resid_mom' — Blitz-style residual momentum (%):

        resid = stock 15d return − β₆₀ · SPY 15d return

    β₆₀ is the rolling OLS beta of the stock's daily returns on SPY's over the
    prior *beta_window* trading days, as of the signal date. Raw prior-return
    ranking is exactly what admits ten correlated high-beta names on a market
    rebound day (2022 EXPANSION / Feb-2026 clusters, CLAUDE.md §12); the
    residual strips the market-beta component so the tiebreak rewards
    idiosyncratic strength instead. Signals with insufficient history get
    resid_mom = 0 (neutral).
    """
    spy = historical.get('SPY')
    spy_ret = spy['close'].pct_change() if spy is not None else None
    for s in signals:
        s['resid_mom'] = 0.0
        if spy_ret is None:
            continue
        df = historical.get(s['symbol'])
        if df is None:
            continue
        d = pd.Timestamp(s['date']).normalize()
        px = df['close'][df.index.normalize() <= d]
        sp = spy['close'][spy.index.normalize() <= d]
        if len(px) < beta_window + 2 or len(sp) < beta_window + 2:
            continue
        r_s = px.pct_change().iloc[-beta_window:]
        r_m = sp.pct_change().iloc[-beta_window:]
        joined = pd.concat([r_s, r_m], axis=1, join='inner').dropna()
        if len(joined) < beta_window // 2:
            continue
        var_m = float(joined.iloc[:, 1].var())
        beta = float(joined.iloc[:, 0].cov(joined.iloc[:, 1])) / var_m if var_m > 0 else 1.0
        n = min(ret_window, len(px) - 1, len(sp) - 1)
        stock_ret = float(px.iloc[-1] / px.iloc[-1 - n] - 1) * 100
        spy_ret_n = float(sp.iloc[-1] / sp.iloc[-1 - n] - 1) * 100
        s['resid_mom'] = stock_ret - beta * spy_ret_n


def load_rank_scores(path: str) -> dict:
    """Load a candidate ranking model's predictions: {(date, symbol): score}.

    This is the promotion path for a RANKING candidate. Before it, the only lever
    confirm_backtest.py could test was an ATR trail multiplier, so a per-signal
    ranking model (the "real ranking upgrade" §7 called for, and the direction the
    live-panel work points at) had no way to be validated against history at all.

    Format: CSV with columns date,symbol,score. Higher score = rank earlier. Signals
    absent from the file keep the default ordering behind every scored signal, so a
    partial model degrades gracefully instead of silently reordering everything.
    """
    df = pd.read_csv(path)
    missing = {'date', 'symbol', 'score'} - set(df.columns)
    if missing:
        raise ValueError(f"--rank-scores {path}: missing column(s) {sorted(missing)}; "
                         f"expected date,symbol,score")
    out = {}
    for r in df.itertuples(index=False):
        out[(pd.Timestamp(r.date).normalize(), str(r.symbol).strip().upper())] = float(r.score)
    return out


def rank_score_year_overlap(rank_scores: dict, years) -> tuple[list, list, list]:
    """(overlap, missing, score_years) between a scores file and the simulated years.

    Exists because a scores file covering none of the simulated years is a SILENT
    NO-OP — every signal falls into the unscored bucket and the run reproduces the
    baseline exactly, which reads as "the candidate changed nothing" rather than
    "the candidate was never applied". Caught by worker-picking on 2026-07-25: a
    model fitted on the panel covers Apr-Jul 2026, while the gate defaults to
    2022,2024.
    """
    score_years = sorted({d.year for d, _ in rank_scores})
    yrs = {int(y) for y in years}
    return sorted(yrs & set(score_years)), sorted(yrs - set(score_years)), score_years


def _pooled_cap(signals, max_per_day: int = 10, normal_bounce_cap: int = 0,
                residual_dist: bool = False, live_tiebreak: bool = False,
                sleeve_slots: int = 0, rank_scores: dict | None = None):
    """Return a new signal list with at most *max_per_day* entries per trading
    day, selected by the same ranking used in auto_portfolio.py.

    normal_bounce_cap: if > 0, additionally cap same-day BOUNCE signals fired
    in NORMAL regime to at most N within the ranked day (0=off, no change).
    Tests the cross-sectional-correlation hypothesis from the 2026 YTD
    NORMAL-regime dig: the NORMAL bounce admission filter is single-stock
    (RSI/R:R/vol only) with no correlation/sector check, so a single SPY
    relief-bounce day can fire a full slate of correlated high-beta names at
    once — e.g. 2026-02-06 admitted all 10/10 pooled slots on
    COIN/HOOD/RBLX/ORCL/SNOW/SOFI/CRWD/MDB/MARA/PLTR (all BOUNCE|PREMIUM|
    NORMAL), net -$1,495 with only 1 winner (PLTR).
    """
    from collections import defaultdict
    by_date: dict = defaultdict(list)
    for s in signals:
        by_date[pd.Timestamp(s['date']).normalize()].append(s)

    if residual_dist:
        # §12 Task 3 ablation: tiebreak on Blitz-style residual momentum
        # (stamped by _stamp_residual_momentum) instead of raw SMA distance —
        # higher idiosyncratic strength first, capped at 25 (YPF rationale:
        # anything more extended than 25% ties, secondary keys decide).
        def _tiebreak(s):
            return (-min(float(s.get('resid_mom', 0) or 0), 25.0),)
    elif live_tiebreak:
        # §12 tiebreak A/B: reproduce auto_portfolio.py's EXACT live semantic —
        # Dist DESCENDING clipped at 25 (parabolic names tie at the top), then
        # Vol descending. The opposite of the validated default below; isolates
        # the live-vs-backtest divergence as a single lever.
        def _tiebreak(s):
            return (
                -min(float(s.get('sma_dist_pct', 0) or 0), 25.0),  # higher Dist first
                -float(s.get('vol', 0) or 0),                      # higher Vol first
            )
    else:
        def _tiebreak(s):
            return (
                # Dist≤25% preferred; beyond 25% sorted last (YPF-style filter)
                0 if float(s.get('sma_dist_pct', 0) or 0) <= 25 else 1,
                float(s.get('sma_dist_pct', 0) or 0),    # closer to trend first
            )

    def _score_key(s, dt):
        """Candidate-model ordering, applied WITHIN the quality tier.

        Quality stays the primary key deliberately: the live-panel measurement found
        GOLD > PREMIUM is the one robust thing the current ranking does (+8.3pp at
        20d, significant in every month), while the sub-ranking WITHIN PREMIUM is
        inert. So a candidate model's job is to order the PREMIUM sea, not to
        relitigate the tier. Unscored signals sort behind every scored one.
        """
        if rank_scores is None:
            return ()
        hit = rank_scores.get((dt, str(s.get('symbol', '')).strip().upper()))
        return (0, -hit) if hit is not None else (1, 0.0)

    result = []
    for dt in sorted(by_date.keys()):
        day_sigs = sorted(
            by_date[dt],
            key=lambda s: (
                _QUALITY_RANK.get(s.get('quality', 'STANDARD'), 9),
            ) + _score_key(s, dt) + (
                -float(s.get('win_prob', 0) or 0),       # higher WinProb first
                -float(s.get('rr', 0) or 0),             # higher R:R first
            ) + _tiebreak(s),
        )
        if normal_bounce_cap > 0:
            day_result = []
            normal_bounce_seen = 0
            for s in day_sigs:
                if len(day_result) >= max_per_day:
                    break
                if s.get('regime') == 'NORMAL' and s.get('type') == 'BOUNCE':
                    if normal_bounce_seen >= normal_bounce_cap:
                        continue
                    normal_bounce_seen += 1
                day_result.append(s)
            result.extend(day_result)
        elif sleeve_slots > 0:
            # High-conviction sleeve: GUARANTEE up to sleeve_slots of the day's cap
            # to sleeve-tagged signals (s['sleeve']=True), then fill the remaining
            # slots from the overall ranking (core + any leftover sleeve). Neither
            # bucket wastes capacity — if fewer than sleeve_slots sleeve names exist
            # that day, core fills the rest, and vice-versa. Preserves the validated
            # ranking WITHIN each bucket; only guarantees representation.
            admitted, seen = [], set()
            for s in day_sigs:                       # reserved sleeve seats first
                if len([x for x in admitted if x.get('sleeve')]) >= sleeve_slots:
                    break
                if s.get('sleeve') and s['symbol'] not in seen:
                    admitted.append(s); seen.add(s['symbol'])
            for s in day_sigs:                       # fill remainder by overall rank
                if len(admitted) >= max_per_day:
                    break
                if s['symbol'] not in seen:
                    admitted.append(s); seen.add(s['symbol'])
            result.extend(admitted[:max_per_day])
        else:
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
             atr_trail_always=False, atr_trail_mult=2.0,
             realistic_sizing=False, swap_on_skip=False,
             panic_throttle=False, panic_bear_only=False):
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
    - panic_bear_only: if True (with panic_throttle=True), restrict panic days to those
                        where SPY has ALSO been below its SMA200 for >= 15 consecutive
                        days — mirrors BOUNCE_BEAR_GATE's validated sustained-bear
                        distinction (§12 Task 4b). Excludes brief high-vol dips (e.g. the
                        April-2025 tariff dip, 9-14 consecutive days) that panic_throttle
                        alone throttled at a real cost; keeps the 2022 sustained-bear
                        rescue where the base lever's edge concentrates.
    - Position size: min(10% capital by value, 2% capital by risk)
    - realistic_sizing=False (default, preserves all previously documented reproducible
      baselines): position size is computed off *remaining* cash and shrinks as capital
      gets tied up — a signal is only skipped if it can't afford even 1 share.
      realistic_sizing=True: mirrors auto_portfolio.py exactly — size is a fixed % of
      stable capital (only moves via realized P&L, never shrinks from open positions),
      and a signal is skipped outright (not downsized) if the full-size cost exceeds
      available cash. Found 2026-07-21: with realistic_sizing=False and a large
      universe (1375 symbols), 97.5% of trades ended up sized <50% of target — the
      backtest was taking near-worthless 1-share fills live would have just skipped.
    - swap_on_skip=True (requires realistic_sizing=True): when a signal is skipped for
      insufficient cash, mirrors auto_portfolio.suggest_swaps() — if a currently open
      position is "weak" (down >=2% or within 4% of its stop) and this signal's
      priority score beats that position's implied score by >=20pts, close the weak
      position now (reason='Swap') and open the new signal in its place instead of
      skipping.
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
    # capital_for_sizing mirrors auto_portfolio.py's data['capital']: only moves via
    # realized P&L on close, never reduced while a position is open. Used to size new
    # positions when realistic_sizing=True; `cap` (actual cash on hand) still gates
    # whether a position can be afforded at all.
    capital_for_sizing = float(capital)
    skipped_signals: list = []   # signals rejected for insufficient cash (realistic_sizing)
    swaps_executed = 0
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
    panic_days = _compute_panic_days(spy) if panic_throttle else set()
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

    # Pre-compute SPY consecutive days below SMA200 (for bounce_bear_gate, and
    # panic_bear_only below)
    spy_consec_below: dict[pd.Timestamp, int] = {}
    if (bounce_bear_gate > 0 or breakeven_bear_gate > 0 or panic_bear_only) and spy is not None:
        sma200 = spy['close'].rolling(200).mean()
        count = 0
        for dt, cl, sm in zip(spy.index, spy['close'], sma200):
            count = count + 1 if (not pd.isna(sm) and cl < sm) else 0
            spy_consec_below[pd.Timestamp(dt).normalize()] = count

    # §12 Task 4b: restrict panic days to a SUSTAINED bear (mirrors BOUNCE_BEAR_GATE's
    # validated 15-consecutive-day threshold) — see panic_bear_only docstring above.
    if panic_throttle and panic_bear_only:
        panic_days = {d for d in panic_days if spy_consec_below.get(d, 0) >= 15}

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
            if panic_throttle and today_norm in panic_days:
                mult *= 0.5   # D&M panic state: half size (§12 Task 4)
            if realistic_sizing:
                qty_by_val  = int(capital_for_sizing * MAX_POS_PCT * mult / price)
                qty_by_risk = int(capital_for_sizing * MAX_RISK_PCT * mult / risk_per_share)
                qty = max(1, min(qty_by_val, qty_by_risk))
                cost = price * qty + commission
                if cost > cap:
                    from auto_portfolio import _compute_priority_score
                    priority = _compute_priority_score(
                        sig.get('quality', 'PREMIUM'), sig.get('win_prob', 0) or 0,
                        sig.get('rr', 0) or 0, 1.0,
                    )
                    skipped_signals.append({'symbol': sym, 'date': today_norm})
                    swapped = False
                    if swap_on_skip and open_pos:
                        # Find the weakest currently-open position (down >=2% or within
                        # 4% of its stop) — mirrors auto_portfolio.suggest_swaps().
                        best_sym, best_weakness, best_implied = None, -1e18, None
                        for osym, opos in open_pos.items():
                            odf = historical.get(osym)
                            obar = (odf[odf.index.normalize() <= today_norm]
                                    if odf is not None else None)
                            ocur = (float(obar['close'].iloc[-1])
                                    if obar is not None and not obar.empty
                                    else opos['entry_price'])
                            opnl_pct = (ocur - opos['entry_price']) / opos['entry_price'] * 100
                            ostop_dist_pct = (ocur - opos['stop']) / ocur * 100 if ocur > 0 else 0
                            if not (opnl_pct <= -2.0 or ostop_dist_pct <= 4.0):
                                continue
                            odays_held = (today_norm - opos['entry_date']).days
                            oqual_penalty = {'GOLD': 0, 'PREMIUM': 5, 'HIGH': 15}.get(
                                opos.get('quality', 'PREMIUM'), 10)
                            oweakness = (-opnl_pct * 2 + max(0, 4 - ostop_dist_pct) * 5
                                         + max(0, odays_held - 10) * 0.3 + oqual_penalty)
                            oheld_rr = ((opos['take_profit'] - opos['entry_price'])
                                        / max(opos['entry_price'] - opos['stop'],
                                              opos['entry_price'] * 0.01))
                            oimplied = _compute_priority_score(
                                opos.get('quality', 'PREMIUM'), 50.0, oheld_rr, 1.0)
                            if oweakness > best_weakness:
                                best_weakness, best_sym, best_implied = oweakness, osym, oimplied
                        if best_sym is not None and (priority - best_implied) >= 20.0:
                            odf = historical.get(best_sym)
                            obar = odf[odf.index.normalize() <= today_norm]
                            ocur = float(obar['close'].iloc[-1])
                            opos = open_pos[best_sym]
                            oqty = opos['qty']
                            oexit_net = ocur * (1 - slippage_pct) - commission / oqty
                            freed_cash = oexit_net * oqty
                            # Only actually close the weak position if the replacement
                            # is guaranteed affordable with the freed cash — otherwise
                            # we'd liquidate a position for nothing in return.
                            if cost <= cap + freed_cash:
                                opnl = (oexit_net - opos['entry_price']) * oqty
                                trades.append({
                                    'symbol':      best_sym,
                                    'entry_date':  opos['entry_date'].strftime('%Y-%m-%d'),
                                    'exit_date':   today_norm.strftime('%Y-%m-%d'),
                                    'qty':         oqty,
                                    'entry':       round(opos['entry_price'], 4),
                                    'exit':        round(oexit_net, 4),
                                    'stop':        round(opos['stop'], 4),
                                    'take_profit': round(opos['take_profit'], 4),
                                    'pnl':         round(opnl, 2),
                                    'pnl_pct':     round(opnl / opos['cost'] * 100, 4),
                                    'win':         opnl > 0,
                                    'reason':      'Swap',
                                    'quality':     opos.get('quality', ''),
                                    'regime':      opos.get('regime', ''),
                                    'signal_type': opos.get('signal_type', ''),
                                })
                                cap += freed_cash
                                capital_for_sizing += opnl
                                del open_pos[best_sym]
                                cap -= cost
                                swapped = True
                                swaps_executed += 1
                    if not swapped:
                        continue
                else:
                    cap -= cost
            else:
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
                capital_for_sizing += pnl
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
        'skipped_signals':  len(skipped_signals),
        'swaps_executed':   swaps_executed,
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
             atr_trail_always=False, atr_trail_mult=2.0, end_date_override=None,
             normal_bounce_cap=0, start_date_override=None, realistic_sizing=False,
             bounce_sma200_gate=False, residual_dist=False, panic_throttle=False,
             bounce_sma200_bear_only=False, live_tiebreak=False,
             sleeve_symbols=None, sleeve_slots=0, panic_bear_only=False,
             rank_scores=None):
    start = f"{year}-01-01"
    end   = f"{year}-12-31"
    if start_date_override and start_date_override[:4] == str(year):
        start = start_date_override
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

    # High-conviction sleeve: tag signals whose symbol is in the sleeve universe
    # (union-fetched with the core). Used by --sleeve-slots reserved-slot admission.
    if sleeve_symbols:
        _slv = {s.upper() for s in sleeve_symbols}
        for s in new_premium:
            s['sleeve'] = s['symbol'].upper() in _slv
        n_slv = sum(1 for s in new_premium if s.get('sleeve'))
        print(f"  Sleeve-tagged (in {len(_slv)}-symbol sleeve universe): {n_slv} of {len(new_premium)} PREMIUM+ signals")

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
    new_premium_pooled = _pooled_cap(new_premium, max_per_day=pooled_cap,
                                     rank_scores=rank_scores)
    rpt = simulate(new_premium_pooled, start, end, end_prices, historical, capital,
                   tp_as_trail=True, label=f'NEW PREMIUM+ pooled-{pooled_cap}', **sim_kw)
    print_report(rpt, f'NEW Regime-Adaptive  PREMIUM+ pooled-cap={pooled_cap} ★', len(new_premium_pooled), spy_info, show_hold_split=True)

    # Realistic-sizing A/B: fixes the shrink-to-1-share sizing bug found 2026-07-21
    # (97.5% of trades undersized <50% of target on a 1375-symbol universe) by sizing
    # off stable capital and skipping outright when cash is short — then tests whether
    # swapping a skipped signal into a weak open position (mirrors
    # auto_portfolio.suggest_swaps()) beats just skipping it. Single-lever A/B: same
    # signal set, same everything else, only swap_on_skip differs.
    if realistic_sizing:
        rpt_a = simulate(new_premium_pooled, start, end, end_prices, historical, capital,
                          tp_as_trail=True, label='REALISTIC no-swap',
                          realistic_sizing=True, swap_on_skip=False, **sim_kw)
        print_report(rpt_a, f'REALISTIC sizing, no swap (A)', len(new_premium_pooled), spy_info, show_hold_split=True)
        print(f"    {'':42} Skipped for cash: {rpt_a.get('skipped_signals', 0)}")

        rpt_b = simulate(new_premium_pooled, start, end, end_prices, historical, capital,
                          tp_as_trail=True, label='REALISTIC swap-on-skip',
                          realistic_sizing=True, swap_on_skip=True, **sim_kw)
        print_report(rpt_b, f'REALISTIC sizing, swap-on-skip (B)', len(new_premium_pooled), spy_info, show_hold_split=True)
        print(f"    {'':42} Skipped for cash: {rpt_b.get('skipped_signals', 0)}  Swaps executed: {rpt_b.get('swaps_executed', 0)}")

        sharpe_delta = rpt_b['sharpe_ratio'] - rpt_a['sharpe_ratio']
        print(f"\n    A/B verdict: swap-on-skip Sharpe {'beats' if sharpe_delta > 0 else 'trails'} "
              f"no-swap by {sharpe_delta:+.2f} ({'ship' if sharpe_delta >= 0.10 else 'keep A (no-swap)' if sharpe_delta < 0.05 else 'inconclusive, needs more data'})")

    # Ablation rows — per-stock SMA200 gate on BOUNCE entries (CLAUDE.md §12 Task 2).
    # Gate applied pre-pooling, so the champion rows above are the ungated A arm and
    # these are the gated B arm of a single-lever A/B within one run.
    if bounce_sma200_gate:
        new_premium_g, n_gated = _apply_bounce_sma200_gate(new_premium, historical)
        new_premium_g_pooled = _pooled_cap(new_premium_g, max_per_day=pooled_cap)
        rpt_g = simulate(new_premium_g_pooled, start, end, end_prices, historical, capital,
                         tp_as_trail=True, label=f'NEW PREMIUM+ pooled-{pooled_cap}+SMA200gate', **sim_kw)
        print_report(rpt_g, f'NEW Regime-Adaptive  PREMIUM+ pooled-cap={pooled_cap} +BounceSMA200Gate',
                     len(new_premium_g_pooled), spy_info, show_hold_split=True)
        print(f"    {'':42} BOUNCE signals gated below own SMA200: {n_gated}")
        if realistic_sizing:
            rpt_gr = simulate(new_premium_g_pooled, start, end, end_prices, historical, capital,
                              tp_as_trail=True, label='REALISTIC SMA200gate',
                              realistic_sizing=True, swap_on_skip=False, **sim_kw)
            print_report(rpt_gr, f'REALISTIC sizing + BounceSMA200Gate', len(new_premium_g_pooled), spy_info, show_hold_split=True)
            print(f"    {'':42} Skipped for cash: {rpt_gr.get('skipped_signals', 0)}")

    # Ablation rows — bear-only conditional SMA200 gate (§12 Task 2b): per-stock
    # gate applies ONLY on days SPY itself closed below its SMA200. Motivated by
    # the unconditional gate's split verdict (2022 +0.83 Sharpe / 2023 −1.27).
    if bounce_sma200_bear_only:
        spy_df = historical.get('SPY')
        spy_below = set()
        if spy_df is not None and len(spy_df) >= 200:
            _sma = spy_df['close'].rolling(200).mean()
            _m = (spy_df['close'] < _sma).fillna(False)
            spy_below = {pd.Timestamp(d).normalize() for d in spy_df.index[_m]}
        new_premium_gb, n_gb = _apply_bounce_sma200_gate(new_premium, historical,
                                                         spy_below_dates=spy_below)
        new_premium_gb_pooled = _pooled_cap(new_premium_gb, max_per_day=pooled_cap)
        rpt_gb = simulate(new_premium_gb_pooled, start, end, end_prices, historical, capital,
                          tp_as_trail=True, label=f'NEW PREMIUM+ pooled-{pooled_cap}+SMA200bearOnly', **sim_kw)
        print_report(rpt_gb, f'NEW Regime-Adaptive  PREMIUM+ pooled-cap={pooled_cap} +SMA200GateBearOnly',
                     len(new_premium_gb_pooled), spy_info, show_hold_split=True)
        print(f"    {'':42} BOUNCE gated (below own SMA200, SPY-bear days only): {n_gb}")
        if realistic_sizing:
            rpt_gbr = simulate(new_premium_gb_pooled, start, end, end_prices, historical, capital,
                               tp_as_trail=True, label='REALISTIC SMA200bearOnly',
                               realistic_sizing=True, swap_on_skip=False, **sim_kw)
            print_report(rpt_gbr, f'REALISTIC sizing + SMA200GateBearOnly', len(new_premium_gb_pooled), spy_info, show_hold_split=True)
            print(f"    {'':42} Skipped for cash: {rpt_gbr.get('skipped_signals', 0)}")

    # Ablation rows — high-conviction sleeve (reserve N of the daily cap for
    # sleeve-universe names). Core = --watchlist (e.g. spy_plus), sleeve =
    # --sleeve-watchlist (e.g. plus.txt); union-fetched, sleeve-tagged above.
    # A/B vs the champion rows: does guaranteeing the curated sleeve seats help?
    if sleeve_slots > 0 and sleeve_symbols:
        new_premium_slv = _pooled_cap(new_premium, max_per_day=pooled_cap,
                                      sleeve_slots=sleeve_slots)
        rpt_slv = simulate(new_premium_slv, start, end, end_prices, historical, capital,
                           tp_as_trail=True, label=f'NEW PREMIUM+ pooled-{pooled_cap}+Sleeve{sleeve_slots}', **sim_kw)
        print_report(rpt_slv, f'NEW Regime-Adaptive  PREMIUM+ pooled-cap={pooled_cap} +Sleeve({sleeve_slots}/{pooled_cap})',
                     len(new_premium_slv), spy_info, show_hold_split=True)
        if realistic_sizing:
            rpt_slvr = simulate(new_premium_slv, start, end, end_prices, historical, capital,
                                tp_as_trail=True, label='REALISTIC Sleeve',
                                realistic_sizing=True, swap_on_skip=False, **sim_kw)
            print_report(rpt_slvr, f'REALISTIC sizing + Sleeve({sleeve_slots}/{pooled_cap})', len(new_premium_slv), spy_info, show_hold_split=True)
            print(f"    {'':42} Skipped for cash: {rpt_slvr.get('skipped_signals', 0)}")

    # Ablation rows — LIVE Dist-tiebreak semantic (§12 tiebreak validation).
    # Reproduces auto_portfolio.py's exact ranking (Dist desc/clip25 + Vol) as a
    # single-lever A/B vs the champion (Dist asc / back-bucket) rows above — tells
    # us whether live's divergent admission ranking helps or hurts.
    if live_tiebreak:
        new_premium_lt_pooled = _pooled_cap(new_premium, max_per_day=pooled_cap,
                                            live_tiebreak=True)
        rpt_lt = simulate(new_premium_lt_pooled, start, end, end_prices, historical, capital,
                          tp_as_trail=True, label=f'NEW PREMIUM+ pooled-{pooled_cap}+LiveTiebreak', **sim_kw)
        print_report(rpt_lt, f'NEW Regime-Adaptive  PREMIUM+ pooled-cap={pooled_cap} +LiveTiebreak(desc)',
                     len(new_premium_lt_pooled), spy_info, show_hold_split=True)
        if realistic_sizing:
            rpt_ltr = simulate(new_premium_lt_pooled, start, end, end_prices, historical, capital,
                               tp_as_trail=True, label='REALISTIC LiveTiebreak',
                               realistic_sizing=True, swap_on_skip=False, **sim_kw)
            print_report(rpt_ltr, f'REALISTIC sizing + LiveTiebreak(desc)', len(new_premium_lt_pooled), spy_info, show_hold_split=True)
            print(f"    {'':42} Skipped for cash: {rpt_ltr.get('skipped_signals', 0)}")

    # Ablation rows — residual-momentum tiebreak in the pooled cap (§12 Task 3).
    # Same signal set, same cap, only the tiebreak differs — single-lever A/B
    # against the champion rows above within one run.
    if residual_dist:
        _stamp_residual_momentum(new_premium, historical)
        new_premium_rd_pooled = _pooled_cap(new_premium, max_per_day=pooled_cap,
                                            residual_dist=True)
        rpt_rd = simulate(new_premium_rd_pooled, start, end, end_prices, historical, capital,
                          tp_as_trail=True, label=f'NEW PREMIUM+ pooled-{pooled_cap}+ResidDist', **sim_kw)
        print_report(rpt_rd, f'NEW Regime-Adaptive  PREMIUM+ pooled-cap={pooled_cap} +ResidualDist',
                     len(new_premium_rd_pooled), spy_info, show_hold_split=True)
        if realistic_sizing:
            rpt_rdr = simulate(new_premium_rd_pooled, start, end, end_prices, historical, capital,
                               tp_as_trail=True, label='REALISTIC ResidDist',
                               realistic_sizing=True, swap_on_skip=False, **sim_kw)
            print_report(rpt_rdr, f'REALISTIC sizing + ResidualDist', len(new_premium_rd_pooled), spy_info, show_hold_split=True)
            print(f"    {'':42} Skipped for cash: {rpt_rdr.get('skipped_signals', 0)}")

    # Ablation rows — Daniel-Moskowitz panic-state sizing throttle (§12 Task 4).
    # Same signals, same ranking — only entry size halves on panic days
    # (SPY < SMA200 AND 20d vol > 1.5%/day). Single-lever A/B vs champion rows.
    if panic_throttle:
        rpt_pt = simulate(new_premium_pooled, start, end, end_prices, historical, capital,
                          tp_as_trail=True, label=f'NEW PREMIUM+ pooled-{pooled_cap}+PanicThrottle',
                          panic_throttle=True, **sim_kw)
        print_report(rpt_pt, f'NEW Regime-Adaptive  PREMIUM+ pooled-cap={pooled_cap} +PanicThrottle',
                     len(new_premium_pooled), spy_info, show_hold_split=True)
        if realistic_sizing:
            rpt_ptr = simulate(new_premium_pooled, start, end, end_prices, historical, capital,
                               tp_as_trail=True, label='REALISTIC PanicThrottle',
                               realistic_sizing=True, swap_on_skip=False,
                               panic_throttle=True, **sim_kw)
            print_report(rpt_ptr, f'REALISTIC sizing + PanicThrottle', len(new_premium_pooled), spy_info, show_hold_split=True)
            print(f"    {'':42} Skipped for cash: {rpt_ptr.get('skipped_signals', 0)}")

    # Ablation rows — panic-throttle 4b: restrict to a SUSTAINED bear (§12 Task 4b).
    # Same lever as above, but panic days additionally require SPY >= 15 consecutive
    # days below its own SMA200 (mirrors BOUNCE_BEAR_GATE). Motivated by the base
    # lever's one real cost: the April-2025 tariff dip (9-14 consecutive days) briefly
    # qualified as panic and throttled entries that worked. This should exclude that
    # case while keeping the 2022 sustained-bear rescue (+0.51 Sharpe) intact.
    if panic_throttle and panic_bear_only:
        rpt_ptbo = simulate(new_premium_pooled, start, end, end_prices, historical, capital,
                            tp_as_trail=True, label=f'NEW PREMIUM+ pooled-{pooled_cap}+PanicThrottleBearOnly',
                            panic_throttle=True, panic_bear_only=True, **sim_kw)
        print_report(rpt_ptbo, f'NEW Regime-Adaptive  PREMIUM+ pooled-cap={pooled_cap} +PanicThrottleBearOnly',
                     len(new_premium_pooled), spy_info, show_hold_split=True)
        if realistic_sizing:
            rpt_ptbor = simulate(new_premium_pooled, start, end, end_prices, historical, capital,
                                 tp_as_trail=True, label='REALISTIC PanicThrottleBearOnly',
                                 realistic_sizing=True, swap_on_skip=False,
                                 panic_throttle=True, panic_bear_only=True, **sim_kw)
            print_report(rpt_ptbor, f'REALISTIC sizing + PanicThrottleBearOnly', len(new_premium_pooled), spy_info, show_hold_split=True)
            print(f"    {'':42} Skipped for cash: {rpt_ptbor.get('skipped_signals', 0)}")

    # Ablation row — same-day NORMAL+BOUNCE concentration cap (untested hypothesis
    # from the 2026 YTD NORMAL-regime dig; see _pooled_cap docstring).
    if normal_bounce_cap > 0:
        new_premium_nbc = _pooled_cap(new_premium, max_per_day=pooled_cap, normal_bounce_cap=normal_bounce_cap)
        rpt = simulate(new_premium_nbc, start, end, end_prices, historical, capital,
                       tp_as_trail=True, label=f'NEW PREMIUM+ pooled-{pooled_cap}+NBC{normal_bounce_cap}', **sim_kw)
        print_report(rpt, f'NEW Regime-Adaptive  PREMIUM+ pooled-cap={pooled_cap} +NormalBounceCap={normal_bounce_cap}',
                     len(new_premium_nbc), spy_info, show_hold_split=True)

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

    p.add_argument('--start-date',  default=None,
                   help='Override start date (YYYY-MM-DD) for whichever requested year it falls in, e.g. '
                        'for a recent N-month window instead of the default full Jan1 start. '
                        'fetch_all_data() always pulls 400 extra days before this for indicator lookback, '
                        'so SMA150/SMA200 etc. remain valid even with a short window.')

    p.add_argument('--bounce-sma200-gate', action='store_true',
                   help='Ablation (CLAUDE.md §12 Task 2): drop BOUNCE signals fired while the stock '
                        'trades below its OWN 200-day SMA (per-stock analog of BBG15, Connors-style '
                        'dip-buy conditioning). Adds gated pooled-cap and gated REALISTIC rows next to '
                        'the ungated champion rows — single-lever A/B in one run. Off by default.')

    p.add_argument('--sleeve-watchlist', default=None,
                   help='High-conviction sleeve universe (e.g. input/plus.txt) run ALONGSIDE the core '
                        '--watchlist (e.g. input/spy_plus.txt). Union-fetched; sleeve names get reserved '
                        'admission slots (--sleeve-slots). Adds a +Sleeve A/B row.')
    p.add_argument('--rank-scores', default=None, metavar='FILE',
                   help='CSV (date,symbol,score) of a candidate RANKING model\'s predictions. '
                        'Higher score ranks earlier, applied WITHIN the quality tier (GOLD>PREMIUM '
                        'is preserved — the live panel shows the tier is the one robust thing the '
                        'current ranking does, while the order within PREMIUM is inert). Signals '
                        'absent from the file sort behind every scored one. This is the promotion '
                        'path for a ranking candidate; without it only an ATR multiplier could be '
                        'confirmed against history.')
    p.add_argument('--sleeve-slots', type=int, default=3,
                   help='How many of the pooled daily cap to RESERVE for sleeve-universe names '
                        '(default 3 of 10). Only active with --sleeve-watchlist. Neither bucket wastes '
                        'capacity — unused sleeve seats fall through to the core pool and vice-versa.')

    p.add_argument('--live-tiebreak', action='store_true',
                   help='§12 tiebreak A/B: add rows using auto_portfolio.py’s EXACT live pooled-cap '
                        'Dist tiebreak (descending, clip 25, + Vol key) instead of the validated backtest '
                        'default (ascending, >25 back-bucket). Isolates the audited live-vs-backtest '
                        'ranking divergence. Pair with --realistic-sizing; run on all.txt (cap binds).')

    p.add_argument('--bounce-sma200-bear-only', action='store_true',
                   help='Ablation (§12 Task 2b): per-stock SMA200 gate on BOUNCE, applied ONLY on days '
                        'SPY itself is below its SMA200. Fixes the unconditional gate’s recovery-year '
                        'give-back (2023 −1.27 Sharpe) while keeping the 2022 bear rescue (+0.83).')

    p.add_argument('--panic-throttle', action='store_true',
                   help='Ablation (CLAUDE.md §12 Task 4): halve entry size on Daniel-Moskowitz panic '
                        'days (SPY < SMA200 AND 20d realized vol > 1.5%%/day). Momentum crashes '
                        'concentrate in these states. Adds PanicThrottle pooled + REALISTIC rows. '
                        'Off by default.')

    p.add_argument('--panic-throttle-bear-only', action='store_true',
                   help='Ablation (§12 Task 4b): restricts --panic-throttle to a SUSTAINED bear '
                        '(SPY >= 15 consecutive days below its own SMA200, mirroring '
                        'BOUNCE_BEAR_GATE\'s validated distinction). Fixes the base lever\'s one real '
                        'cost (the April-2025 tariff dip, 9-14 consecutive days, briefly qualified as '
                        'panic and throttled entries that worked) while keeping the 2022 sustained-bear '
                        'rescue (+0.51 Sharpe). Requires --panic-throttle; adds an additional '
                        'PanicThrottleBearOnly row alongside the base PanicThrottle row.')

    p.add_argument('--residual-dist', action='store_true',
                   help='Ablation (CLAUDE.md §12 Task 3): pooled-cap tiebreak on Blitz-style residual '
                        'momentum (stock 15d return − β₆₀·SPY 15d return, capped at 25) instead of raw '
                        'SMA distance. Targets the correlated high-beta rebound-day clusters. Adds '
                        'ResidualDist pooled + REALISTIC rows next to champion rows. Off by default.')

    p.add_argument('--realistic-sizing', action='store_true',
                   help='Fix the position-sizing bug found 2026-07-21: size positions off stable capital '
                        '(not shrinking cash) and skip outright — never downsize to 1 share — when the full '
                        'target size cannot be afforded. Adds a REALISTIC no-swap vs swap-on-skip A/B row '
                        '(mirrors auto_portfolio.suggest_swaps() for the swap variant). Off by default to '
                        'preserve all previously documented reproducible baselines.')

    p.add_argument('--normal-bounce-cap', type=int, default=0,
                   help='Cap same-day BOUNCE signals in NORMAL regime to at most N within the pooled-cap '
                        'ranking (0=off). Tests the cross-sectional-correlation hypothesis from the 2026 YTD '
                        'NORMAL-regime dig (2026-02-06 fired 10/10 pooled slots on one correlated growth/crypto '
                        'cluster, net -$1,495, 1 winner). Emits an extra comparison row. Example: --normal-bounce-cap 2')

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

    # High-conviction sleeve: union the sleeve universe into the fetch set so both
    # core and sleeve names are scanned; run_year tags the sleeve names for
    # reserved-slot admission.
    sleeve_symbols = None
    if args.sleeve_watchlist:
        sleeve_symbols = load_symbols(args.sleeve_watchlist, 0)
        merged = list(dict.fromkeys(list(symbols) + list(sleeve_symbols)))
        print(f"⚠  --sleeve-watchlist: core={len(symbols)} + sleeve={len(sleeve_symbols)} "
              f"→ {len(merged)} union symbols; reserving {args.sleeve_slots}/{args.pooled_cap} daily slots for sleeve")
        symbols = merged

    rank_scores = None
    if args.rank_scores:
        rank_scores = load_rank_scores(args.rank_scores)
        dates = {d for d, _ in rank_scores}
        print(f"⚠  --rank-scores: {len(rank_scores)} scored (date,symbol) pairs over "
              f"{len(dates)} dates from {args.rank_scores}. Applied WITHIN quality tier; "
              f"unscored signals rank behind scored ones.")
        print("   Judge on the REALISTIC arm (§11) and check >15d WR has not shrunk (§13). "
              "A model fitted on the same period you score here is IN-SAMPLE — walk it forward.")

        # A scores file that covers none of the simulated years is a SILENT NO-OP: every
        # signal falls into the unscored bucket and the run reproduces the baseline
        # exactly, looking like "the candidate changed nothing". Refuse instead.
        # (Caught by worker-picking on 2026-07-25: a model fitted on the panel covers
        # Apr-Jul 2026 only, while this gate defaults to 2022,2024.)
        overlap, missing, score_years = rank_score_year_overlap(rank_scores, years)
        if not overlap:
            print(f"\n✗ --rank-scores covers year(s) {score_years} but this run "
                  f"simulates {years} — ZERO overlap.")
            print("  Every signal would be unscored and the result would silently equal the "
                  "baseline.")
            print("  Either score the years you are simulating, or run --years "
                  f"{','.join(str(y) for y in score_years)}.")
            return
        if missing:
            print(f"   NOTE: no scores for {missing} — those year(s) run at BASELINE ranking. "
                  f"Only {overlap} actually tests the candidate.")

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
                 end_date_override=args.end_date,
                 normal_bounce_cap=args.normal_bounce_cap,
                 start_date_override=args.start_date,
                 realistic_sizing=args.realistic_sizing,
                 bounce_sma200_gate=args.bounce_sma200_gate,
                 residual_dist=args.residual_dist,
                 panic_throttle=args.panic_throttle,
                 bounce_sma200_bear_only=args.bounce_sma200_bear_only,
                 live_tiebreak=args.live_tiebreak,
                 sleeve_symbols=sleeve_symbols,
                 sleeve_slots=args.sleeve_slots,
                 panic_bear_only=args.panic_throttle_bear_only,
                 rank_scores=rank_scores)

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
