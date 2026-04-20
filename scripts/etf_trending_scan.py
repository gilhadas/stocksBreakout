"""
One-shot ETF momentum+volume scanner.

Ranks a curated ETF universe by:
  - price > SMA50 and SMA150 (uptrend)
  - 20-day return vs SPY (relative strength)
  - today's volume vs 20-day average (volume expansion)

Picks the top candidate and adds it to auto portfolio at 20% of total capital.

Usage:
  python -m scripts.etf_trending_scan             # dry run, prints ranking
  python -m scripts.etf_trending_scan --execute   # add top pick to portfolio
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auto_portfolio as ap

ETF_UNIVERSE = [
    # Broad / style
    'SPY', 'QQQ', 'IWM', 'DIA',
    # Sector SPDRs
    'XLK', 'XLF', 'XLE', 'XLV', 'XLI', 'XLY', 'XLP', 'XLU', 'XLRE', 'XLC', 'XLB',
    # Thematic momentum
    'SMH', 'SOXX', 'ARKK', 'IBIT', 'GDX', 'GDXJ', 'TAN', 'URA', 'URNM',
    'ITB', 'KRE', 'IYR', 'XBI', 'IBB', 'XHB', 'XRT', 'JETS',
]

SPY_BENCHMARK = 'SPY'
LOOKBACK_DAYS = 220  # need ≥150 for SMA150


def fetch_history(symbols: list[str]) -> dict[str, pd.DataFrame]:
    df = yf.download(
        symbols,
        period=f'{LOOKBACK_DAYS}d',
        interval='1d',
        auto_adjust=True,
        progress=False,
        group_by='ticker',
        threads=True,
    )
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            sub = df[sym].dropna()
            if len(sub) >= 160:
                out[sym] = sub
        except (KeyError, AttributeError):
            continue
    return out


def score_etf(sub: pd.DataFrame, spy_20d: float) -> dict | None:
    close = sub['Close']
    vol = sub['Volume']
    if len(close) < 160:
        return None

    price = float(close.iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1])
    sma150 = float(close.rolling(150).mean().iloc[-1])
    ret_20d = float(close.iloc[-1] / close.iloc[-21] - 1.0)
    vol_avg20 = float(vol.rolling(20).mean().iloc[-1])
    vol_today = float(vol.iloc[-1])
    vol_ratio = vol_today / vol_avg20 if vol_avg20 > 0 else 0.0

    trending = price > sma50 and price > sma150
    rs_vs_spy = ret_20d - spy_20d
    outperforms = rs_vs_spy > 0
    high_volume = vol_ratio >= 1.5

    # Composite score: weight 20d RS + volume expansion.
    score = (rs_vs_spy * 100) + (vol_ratio * 5)

    return {
        'price': round(price, 2),
        'sma50': round(sma50, 2),
        'sma150': round(sma150, 2),
        'ret_20d_pct': round(ret_20d * 100, 2),
        'rs_vs_spy_pct': round(rs_vs_spy * 100, 2),
        'vol_ratio': round(vol_ratio, 2),
        'trending': trending,
        'outperforms': outperforms,
        'high_volume': high_volume,
        'eligible': trending and outperforms and high_volume,
        'score': round(score, 2),
    }


def atr_stop(sub: pd.DataFrame, mult: float = 2.0) -> float:
    high, low, close = sub['High'], sub['Low'], sub['Close']
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    return round(float(close.iloc[-1]) - mult * atr, 2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true',
                        help='Add top pick to auto portfolio')
    parser.add_argument('--pct', type=float, default=0.20,
                        help='Position size as fraction of total capital (default 0.20)')
    args = parser.parse_args()

    symbols = sorted(set(ETF_UNIVERSE + [SPY_BENCHMARK]))
    hist = fetch_history(symbols)
    if SPY_BENCHMARK not in hist:
        print('ERROR: could not fetch SPY benchmark')
        return 1

    spy_20d = float(hist[SPY_BENCHMARK]['Close'].iloc[-1]
                    / hist[SPY_BENCHMARK]['Close'].iloc[-21] - 1.0)
    print(f'SPY 20d return: {spy_20d*100:+.2f}%\n')

    rows = []
    for sym, sub in hist.items():
        if sym == SPY_BENCHMARK:
            continue
        s = score_etf(sub, spy_20d)
        if s:
            rows.append({'symbol': sym, **s})

    if not rows:
        print('No ETFs returned usable data')
        return 1

    ranked = sorted(rows, key=lambda r: r['score'], reverse=True)
    print(f"{'SYM':<6} {'PRICE':>8} {'20D%':>7} {'RS%':>7} {'VOL×':>6} "
          f"{'TREND':>6} {'RS>SPY':>7} {'VOL≥1.5':>8} {'SCORE':>7}")
    print('-' * 75)
    for r in ranked:
        print(f"{r['symbol']:<6} {r['price']:>8.2f} {r['ret_20d_pct']:>+7.2f} "
              f"{r['rs_vs_spy_pct']:>+7.2f} {r['vol_ratio']:>6.2f} "
              f"{'✓' if r['trending'] else '✗':>6} "
              f"{'✓' if r['outperforms'] else '✗':>7} "
              f"{'✓' if r['high_volume'] else '✗':>8} "
              f"{r['score']:>7.2f}")

    eligible = [r for r in ranked if r['eligible']]
    if not eligible:
        print('\nNo ETFs pass all three filters (trending + RS>SPY + vol≥1.5×)')
        # Fallback: loosen to trending + outperforms
        eligible = [r for r in ranked if r['trending'] and r['outperforms']]
        if not eligible:
            print('Nothing trending with positive RS either. Aborting.')
            return 2
        print(f'Falling back to trending + RS>SPY (ignoring volume gate)')

    pick = eligible[0]
    sym = pick['symbol']
    entry = pick['price']
    stop = atr_stop(hist[sym], mult=2.0)
    # Target: 2R
    risk = entry - stop
    target = round(entry + 2.0 * risk, 2)

    print(f"\n→ Top pick: {sym} @ ${entry:.2f}")
    print(f"   Stop (2×ATR): ${stop:.2f}   Target (2R): ${target:.2f}")
    print(f"   Size: {args.pct*100:.0f}% of total capital")

    if not args.execute:
        print('\n(dry run — pass --execute to add to portfolio)')
        return 0

    result = ap.add_position_direct(
        symbol=sym,
        entry_price=entry,
        stop=stop,
        target=target,
        mode='swing',
        quality='ETF_MOMENTUM',
        vol_ratio=pick['vol_ratio'],
        position_pct=args.pct,
    )
    print(f"\nadd_position_direct result: {result}")
    return 0 if result.get('added') else 3


if __name__ == '__main__':
    sys.exit(main())
