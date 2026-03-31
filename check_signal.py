"""
Manual signal checker — runs the full scanner pipeline on a single symbol.

Usage:
  python check_signal.py AAPL
  python check_signal.py AAPL --mode daytrade
  python check_signal.py AAPL TSLA NVDA
  python check_signal.py AAPL --mode swing --spy-perf 1.5
"""

import argparse
import asyncio
import logging

# Python 3.14 compatibility: must create event loop before importing ib_insync
# (pulled in transitively via scanner → market_data → ib_insync)
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import yfinance as yf
import numpy as np
import pandas as pd

import utils  # registers custom SCAN log level before scanner imports it
from config import MODES, SCORING_WEIGHTS, SCORE_THRESHOLDS
from indicators import calculate_all_indicators, calculate_minervini_template
from scanner import BreakoutDetector
from yfinance_adapter import YFinanceAdapter

logging.basicConfig(level=logging.WARNING)  # suppress scanner noise


# ── Helpers ───────────────────────────────────────────────────────────────

def _v(val, fmt='.2f'):
    """Format a value, returning '—' if NaN/missing."""
    try:
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return '—'
        return format(val, fmt)
    except Exception:
        return str(val)


def _bar(value, low, high, width=20, fmt='.1f'):
    """Render a horizontal bar showing position within a range."""
    if high == low:
        pos = 0
    else:
        pos = max(0.0, min(1.0, (value - low) / (high - low)))
    filled = int(pos * width)
    bar = '█' * filled + '░' * (width - filled)
    return f"[{bar}] {value:{fmt}}"


def get_spy_perf(lookback: int) -> float:
    try:
        df = yf.Ticker('SPY').history(period='3mo')
        if len(df) >= lookback:
            return (df['Close'].iloc[-1] / df['Close'].iloc[-lookback] - 1) * 100
    except Exception:
        pass
    return 0.0


# ── Print helpers ─────────────────────────────────────────────────────────

def print_indicators(df: pd.DataFrame, mode: str):
    cfg    = MODES[mode]
    latest = df.iloc[-1]
    prev   = df.iloc[-2] if len(df) >= 2 else latest

    high_52w = df['high'].rolling(min(252, len(df))).max().iloc[-1]
    low_52w  = df['low'].rolling(min(252, len(df))).min().iloc[-1]
    prev_high = df['high'].rolling(cfg['lookback']).max().iloc[-2]

    rsi        = latest.get('RSI', float('nan'))
    macd       = latest.get('MACD', float('nan'))
    macd_sig   = latest.get('MACD_Signal', float('nan'))
    macd_hist  = latest.get('MACD_Hist', float('nan'))
    macd_prev  = prev.get('MACD_Hist', float('nan'))
    adx        = latest.get('ADX', float('nan'))
    atr        = latest.get('ATR', float('nan'))
    bb_upper   = latest.get('BB_Upper', float('nan'))
    bb_lower   = latest.get('BB_Lower', float('nan'))
    bb_width   = latest.get('BB_Width', float('nan'))
    bb_trend   = latest.get('BB_Trend', '—')
    trend_line = latest.get('Trend_Line', float('nan'))
    vol_ratio  = latest.get('Vol_Ratio', float('nan'))
    momentum   = latest.get('Momentum_Score', float('nan'))
    conviction = latest.get('Conviction_Score', float('nan'))
    ema9       = latest.get('EMA_9', float('nan'))
    ema21      = latest.get('EMA_21', float('nan'))
    stoch_k    = latest.get('StochRSI_K', float('nan'))
    stoch_d    = latest.get('StochRSI_D', float('nan'))
    close      = latest['close']

    dist_from_52w  = ((high_52w - close) / high_52w * 100) if high_52w > 0 else float('nan')
    sma_dist_pct   = ((close - trend_line) / trend_line * 100) if trend_line > 0 else float('nan')
    dist_breakout  = ((close - prev_high) / atr) if atr > 0 else float('nan')
    stock_perf     = ((close / df['close'].iloc[-cfg['lookback']]) - 1) * 100 if len(df) >= cfg['lookback'] else float('nan')

    print(f"\n  ── PRICE ──────────────────────────────────────────────")
    chg = close - prev['close']
    chg_pct = chg / prev['close'] * 100 if prev['close'] else 0
    arrow = '▲' if chg >= 0 else '▼'
    print(f"  Close:         ${close:.2f}  {arrow} {chg:+.2f} ({chg_pct:+.2f}%)")
    print(f"  Prev High ({cfg['lookback']}d): ${prev_high:.2f}  {'✅ ABOVE' if close > prev_high else '❌ BELOW'}")
    print(f"  ATR (14):      ${_v(atr)}")
    print(f"  Breakout Dist: {_v(dist_breakout)} ATRs  (need >= {cfg['atr_mult']})")
    print(f"  52w High:      ${_v(high_52w)}  |  52w Low: ${_v(low_52w)}")
    print(f"  Dist from 52w: {_v(dist_from_52w)}%  {'✅ near high' if not np.isnan(dist_from_52w) and dist_from_52w <= 5 else ''}")

    print(f"\n  ── TREND ──────────────────────────────────────────────")
    ttype = cfg['trend_type']
    tper  = cfg.get('trend_period', '')
    print(f"  {ttype} {tper}:       ${_v(trend_line)}  ({'✅ above' if close > trend_line else '❌ below'} trend)")
    print(f"  SMA Dist:      {_v(sma_dist_pct)}%")
    if not np.isnan(ema9):
        print(f"  EMA 9:         ${_v(ema9)}  |  EMA 21: ${_v(ema21)}")
    print(f"  Stock perf:    {_v(stock_perf)}%  (vs SPY over {cfg['lookback']} bars)")

    print(f"\n  ── MOMENTUM ───────────────────────────────────────────")
    rsi_flag = '✅' if not np.isnan(rsi) and 45 <= rsi <= 70 else ('⚠️ ' if not np.isnan(rsi) and rsi > 70 else '❌')
    print(f"  RSI (14):      {_bar(rsi, 0, 100) if not np.isnan(rsi) else '—'}  {rsi_flag}")
    macd_flag = '✅' if not np.isnan(macd_hist) and macd_hist > 0 and (np.isnan(macd_prev) or macd_hist > macd_prev) else '❌'
    print(f"  MACD Hist:     {_v(macd_hist)}  (prev: {_v(macd_prev)})  {macd_flag}")
    print(f"  MACD / Signal: {_v(macd)} / {_v(macd_sig)}")
    adx_flag = '✅' if not np.isnan(adx) and adx > 25 else '❌'
    print(f"  ADX:           {_bar(adx, 0, 60) if not np.isnan(adx) else '—'}  {adx_flag}  (need > 25)")
    mom_flag = '✅' if not np.isnan(momentum) and momentum >= 50 else '❌'
    print(f"  Momentum Score:{_bar(momentum, 0, 100) if not np.isnan(momentum) else '—'}  {mom_flag}  (need >= 50)")
    conv_flag = '✅' if not np.isnan(conviction) and conviction >= 40 else '❌'
    print(f"  Conviction:    {_bar(conviction, 0, 100) if not np.isnan(conviction) else '—'}  {conv_flag}  (need >= 40)")
    if not np.isnan(stoch_k):
        print(f"  StochRSI K/D:  {_v(stoch_k)} / {_v(stoch_d)}")

    print(f"\n  ── VOLATILITY / VOLUME ────────────────────────────────")
    print(f"  BB Upper:      ${_v(bb_upper)}  |  BB Lower: ${_v(bb_lower)}")
    print(f"  BB Width:      {_v(bb_width)}  (trend: {bb_trend})")
    vol_flag = '✅' if not np.isnan(vol_ratio) and vol_ratio >= cfg['vol_thresh'] else '❌'
    print(f"  Vol Ratio:     {_bar(vol_ratio, 0, 5, fmt='.2f') if not np.isnan(vol_ratio) else '—'}  {vol_flag}  (need >= {cfg['vol_thresh']})")

    print(f"\n  ── MINERVINI TEMPLATE ─────────────────────────────────")
    mini_conds, mini_score = calculate_minervini_template(
        df, high_52w=float(high_52w), low_52w=float(low_52w)
    )
    print(f"  Score: {mini_score}/8  {'✅' if mini_score >= 7 else '⚠️ ' if mini_score >= 5 else '❌'}")
    for cond, passed in mini_conds.items():
        icon = '  ✅' if passed else '  ❌'
        print(f"  {icon} {cond}")


def print_signal(symbol: str, signal: dict, df: pd.DataFrame, mode: str, spy_perf: float):
    weights = SCORING_WEIGHTS
    checks  = signal.get('Checks', {})
    max_score = sum(w for w in weights.values())

    # Recalculate score
    earned = 0
    for check, max_pts in weights.items():
        val = checks.get(check, False)
        if isinstance(val, float):
            earned += int(val * max_pts)
        elif val:
            earned += max_pts
    score_pct = (earned / max_score * 100) if max_score else 0

    print(f"\n{'═' * 65}")
    print(f"  {symbol}  [{mode.upper()}]  →  {signal['Quality']}")
    print(f"{'═' * 65}")

    print(f"\n  ── SIGNAL ─────────────────────────────────────────────")
    print(f"  Price:    ${signal['Price']:.2f}")
    print(f"  Stop:     ${signal['Stop']:.2f}   ({(signal['Price'] - signal['Stop']) / signal['Price'] * 100:.1f}% risk)")
    print(f"  Target:   ${signal['Target']:.2f}   ({(signal['Target'] - signal['Price']) / signal['Price'] * 100:.1f}% upside)")
    print(f"  R:R:      {signal['R:R']:.1f}   Grade: {signal.get('RR_Grade', '—')}")
    print(f"  Vol:      {signal['Vol']:.2f}x   RSI: {signal.get('RSI', '—')}   Gap: {signal.get('Gap%', 0):.1f}%")
    print(f"  Type:     {signal.get('Type') or 'Breakout'}   WinProb: {signal.get('WinGrade', '—')}")
    if signal.get('Patterns'):
        print(f"  Patterns: {signal['Patterns']}")
    if signal.get('SR_Resistance'):
        print(f"  S/R Res:  ${signal['SR_Resistance']:.2f}   S/R Sup: ${signal.get('SR_Support') or 0:.2f}")
    if signal.get('SR_InChannel'):
        print(f"  Channel:  {signal.get('SR_Channel_Dir', '')}  width {signal.get('SR_Channel_Width%', '')}%")

    # Indicators
    print_indicators(df, mode)

    # Score breakdown
    bar_filled = int(score_pct / 5)
    bar = '█' * bar_filled + '░' * (20 - bar_filled)
    print(f"\n  ── SCORE BREAKDOWN ────────────────────────────────────")
    print(f"  [{bar}] {score_pct:.0f}%   PREMIUM>={SCORE_THRESHOLDS['PREMIUM']}%  HIGH>={SCORE_THRESHOLDS['HIGH']}%\n")
    print(f"  {'CHECK':<26} {'EARNED':>6}  {'MAX':>4}  STATUS")
    print(f"  {'─' * 52}")

    for check, max_pts in sorted(weights.items(), key=lambda x: -x[1]):
        if max_pts == 0:
            continue
        val = checks.get(check, False)
        if isinstance(val, float):
            pts    = int(val * max_pts)
            status = f"{'✅' if val > 0.5 else '⚠️ '} {val:.2f}"
        else:
            pts    = max_pts if val else 0
            status = '✅' if val else '❌'
        print(f"  {check:<26} {pts:>6}  {max_pts:>4}  {status}")

    print(f"\n  {'TOTAL':<26} {earned:>6}  {max_score:>4}")


def print_rejection(symbol: str, df: pd.DataFrame, mode: str, reasons, spy_perf: float):
    print(f"\n{'═' * 65}")
    print(f"  {symbol}  [{mode.upper()}]  →  REJECTED")
    print(f"{'═' * 65}")

    if isinstance(reasons, list):
        for r in reasons:
            if isinstance(r, dict):
                print(f"  ✗ {r.get('reasons', r)}")
            else:
                print(f"  ✗ {r}")
    else:
        print(f"  ✗ {reasons}")

    # Still show indicators so you can diagnose why
    if df is not None and not df.empty:
        print_indicators(df, mode)


# ── Main ──────────────────────────────────────────────────────────────────

def check_symbol(symbol: str, mode: str, spy_perf: float):
    cfg       = MODES[mode]
    timeframe = cfg.get('default_timeframe', '1 day')
    adapter   = YFinanceAdapter(use_disk_cache=False)

    print(f"\n  Fetching {symbol} ({timeframe}) …", end='', flush=True)
    df = adapter.get_historical_data(symbol, timeframe)
    if df is None or df.empty:
        print(f"\n  ⚠️  No data returned for {symbol}")
        return
    print(f" {len(df)} bars")

    # Pre-calculate indicators so print_indicators always has them
    df = calculate_all_indicators(df, cfg['trend_type'], cfg.get('trend_period'), timeframe)

    detector = BreakoutDetector()

    # 1. Breakout
    signal = detector.detect(df, symbol, mode, timeframe, spy_perf)
    if signal:
        signal['Type'] = signal.get('Type') or 'BREAKOUT'
        print_signal(symbol, signal, df, mode, spy_perf)
        return

    # 2. Bounce
    bounce = detector.detect_bounce(df, symbol, mode, timeframe)
    if bounce:
        bounce['Type'] = 'BOUNCE'
        print_signal(symbol, bounce, df, mode, spy_perf)
        return

    # 3. SMA20 Cross
    sma20 = detector.detect_sma20_cross(df, symbol, mode, timeframe, spy_perf)
    if sma20:
        sma20['Type'] = 'SMA20_CROSS'
        print_signal(symbol, sma20, df, mode, spy_perf)
        return

    # All rejected
    reasons = detector.rejection_reasons or ['No signal / no price break']
    print_rejection(symbol, df, mode, reasons, spy_perf)


def main():
    parser = argparse.ArgumentParser(description='Manual signal checker — runs full scanner pipeline')
    parser.add_argument('symbols', nargs='+', help='Symbol(s) to check')
    parser.add_argument('--mode', default='swing',
                        choices=list(MODES.keys()),
                        help='Trading mode (default: swing)')
    parser.add_argument('--spy-perf', type=float, default=None,
                        help='SPY pct performance override (auto-fetched if omitted)')
    args = parser.parse_args()

    spy_perf = args.spy_perf
    if spy_perf is None:
        lookback = MODES[args.mode].get('lookback', 15)
        print(f"Fetching SPY performance (lookback={lookback})…", end='', flush=True)
        spy_perf = get_spy_perf(lookback)
        print(f" {spy_perf:+.2f}%")

    for symbol in args.symbols:
        check_symbol(symbol.upper(), args.mode, spy_perf)

    print()


if __name__ == '__main__':
    main()
