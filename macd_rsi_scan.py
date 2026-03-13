#!/usr/bin/env python3
"""
macd_rsi_scan.py
────────────────
Lightweight parallel scan — MACD + RSI momentum filter.
Runs fast via yfinance (no IB connection required).

With --track-portfolio: maintains a paper portfolio of signals to compare
performance against the main breakout scanner.

Usage:
  python macd_rsi_scan.py                                          # default watchlist
  python macd_rsi_scan.py --watchlist input/optimizer_watch.txt
  python macd_rsi_scan.py --watchlist scanner_output/momentum_watch_daytrade.txt
  python macd_rsi_scan.py --timeframe 15m                         # intraday
  python macd_rsi_scan.py --track-portfolio                       # save + track signals
  python macd_rsi_scan.py --portfolio-report                      # show today's P&L
"""

import argparse
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo('America/New_York')

DEFAULT_WATCHLIST = 'input/optimizer_watch.txt'
PORTFOLIO_DIR = Path('scanner_output/macd_rsi')

# Stop/target by timeframe
RISK_PARAMS = {
    '1d':  {'stop_pct': -3.0, 'target_pct': 6.0},
    '1h':  {'stop_pct': -2.0, 'target_pct': 4.0},
    '15m': {'stop_pct': -1.0, 'target_pct': 2.0},
    '5m':  {'stop_pct': -0.5, 'target_pct': 1.0},
}

# ─────────────────────────────────────────────────────────────────────────────

def load_watchlist(path: str) -> list[str]:
    """Load symbols from watchlist. Supports plain text and TradingView format."""
    skip_exchanges = {'BINANCE', 'BITSTAMP', 'COINBASE', 'CAPITALCOM', 'BSE',
                      'XETR', 'MIL', 'SP', 'FX', 'CRYPTO', 'COMEX', 'NYMEX'}
    p = Path(path)
    if not p.exists():
        logger.warning(f"Watchlist not found: {path}, falling back to {DEFAULT_WATCHLIST}")
        p = Path(DEFAULT_WATCHLIST)

    symbols = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('###'):
                continue
            if ':' in line:
                exchange, sym = line.split(':', 1)
                if exchange.upper() in skip_exchanges:
                    continue
                sym = sym.replace('.', '-')
                symbols.append(sym.upper())
            else:
                symbols.append(line.upper())
    return symbols


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def calculate_macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    sig = macd.ewm(span=signal, adjust=False).mean()
    return macd, sig, macd - sig


def fetch_price(sym: str) -> float | None:
    """Fetch latest price for a symbol (for portfolio updates)."""
    try:
        df = yf.download(sym, period='1d', interval='1m', progress=False, auto_adjust=True)
        if df is not None and len(df) > 0:
            df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
            return float(df['close'].iloc[-1])
    except Exception:
        pass
    return None


TF_MAP = {
    '1d':  ('60d', '1d'),
    '1h':  ('30d', '1h'),
    '15m': ('7d',  '15m'),
    '5m':  ('5d',  '5m'),
}


def batch_scan(symbols: list[str], tf: str, rsi_min: float, rsi_max: float,
               vol_mult: float = 1.5) -> list[dict]:
    """
    Batch download all symbols in one yfinance call, then score each.
    Much faster than per-symbol downloads for large watchlists (e.g. ALL.txt).

    vol_mult: minimum ratio of current bar volume to 20-bar average (0 = disabled).
    """
    period, interval = TF_MAP.get(tf, ('60d', '1d'))
    now_str = datetime.now(NY_TZ).strftime('%Y-%m-%d %H:%M ET')
    results = []

    logger.info(f"Batch downloading {len(symbols)} symbols (period={period}, interval={interval})...")
    try:
        raw = yf.download(
            symbols, period=period, interval=interval,
            progress=False, auto_adjust=True, group_by='ticker',
            threads=True
        )
    except Exception as e:
        logger.error(f"Batch download failed: {e}")
        return results

    for sym in symbols:
        try:
            # Multi-ticker download has a (ticker, field) column MultiIndex.
            # Even for a single symbol, group_by='ticker' keeps the MultiIndex,
            # so always index by ticker name to avoid KeyError.
            if isinstance(raw.columns, pd.MultiIndex):
                if sym not in raw.columns.get_level_values(0):
                    continue
                close  = raw[sym]['Close'].dropna()
                volume = raw[sym]['Volume'].reindex(close.index).fillna(0)
            else:
                # Flat columns — single-symbol download without group_by
                col = 'Close' if 'Close' in raw.columns else 'close'
                close = raw[col].dropna()
                vcol  = 'Volume' if 'Volume' in raw.columns else 'volume'
                volume = raw[vcol].reindex(close.index).fillna(0)

            if len(close) < 35:
                continue

            rsi = calculate_rsi(close)
            macd, sig, hist = calculate_macd(close)

            if len(hist) < 2:
                continue

            rsi_val = float(rsi.iloc[-1])
            hist_cur = float(hist.iloc[-1])
            hist_prev = float(hist.iloc[-2])
            price = float(close.iloc[-1])

            if not (rsi_min <= rsi_val <= rsi_max):
                continue

            macd_cross = hist_prev < 0 < hist_cur
            macd_bull = hist_cur > 0 and hist_cur > hist_prev

            if not (macd_cross or macd_bull):
                continue

            # Volume filter: current bar must be ≥ vol_mult × 20-bar rolling avg
            vol_ratio = 0.0
            if vol_mult > 0 and len(volume) >= 21:
                avg_vol = float(volume.iloc[-21:-1].mean())
                cur_vol = float(volume.iloc[-1])
                vol_ratio = (cur_vol / avg_vol) if avg_vol > 0 else 0.0
                if vol_ratio < vol_mult:
                    continue

            results.append({
                'Symbol': sym,
                'Price': round(price, 2),
                'RSI': round(rsi_val, 1),
                'MACD': round(float(macd.iloc[-1]), 4),
                'MACD_Signal': round(float(sig.iloc[-1]), 4),
                'MACD_Hist': round(hist_cur, 4),
                'MACD_Hist_Prev': round(hist_prev, 4),
                'Vol_Ratio': round(vol_ratio, 2),
                'Signal': 'MACD_CROSS' if macd_cross else 'MACD_BULL',
                'Timeframe': tf,
                'Time': now_str,
            })

        except Exception as e:
            logger.debug(f"{sym}: {e}")
            continue

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio tracking
# ─────────────────────────────────────────────────────────────────────────────

def portfolio_path() -> Path:
    today = datetime.now(NY_TZ).strftime('%Y%m%d')
    PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)
    return PORTFOLIO_DIR / f'portfolio_{today}.csv'


PORTFOLIO_COLS = [
    'Symbol', 'Entry_Price', 'Entry_Time', 'Signal', 'Timeframe',
    'RSI_Entry', 'MACD_Hist_Entry', 'Status', 'Current_Price',
    'PnL_Pct', 'Exit_Price', 'Exit_Time', 'Exit_Reason',
]


def load_portfolio() -> pd.DataFrame:
    p = portfolio_path()
    if p.exists():
        return pd.read_csv(p)
    return pd.DataFrame(columns=PORTFOLIO_COLS)


def save_portfolio(df: pd.DataFrame):
    df.to_csv(portfolio_path(), index=False)


def update_open_positions(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Check open positions against current price, apply stop/target rules."""
    if df.empty:
        return df

    params = RISK_PARAMS.get(tf, RISK_PARAMS['1h'])
    stop_pct = params['stop_pct']
    target_pct = params['target_pct']
    now_str = datetime.now(NY_TZ).strftime('%Y-%m-%d %H:%M ET')
    now_et = datetime.now(NY_TZ)

    # EOD force-close after 15:55 ET
    eod = now_et.hour > 15 or (now_et.hour == 15 and now_et.minute >= 55)

    for idx, row in df[df['Status'] == 'OPEN'].iterrows():
        price = fetch_price(row['Symbol'])
        if price is None:
            continue

        entry = float(row['Entry_Price'])
        pnl_pct = (price - entry) / entry * 100

        df.at[idx, 'Current_Price'] = round(price, 2)
        df.at[idx, 'PnL_Pct'] = round(pnl_pct, 2)

        if eod:
            df.at[idx, 'Status'] = 'CLOSED'
            df.at[idx, 'Exit_Price'] = price
            df.at[idx, 'Exit_Time'] = now_str
            df.at[idx, 'Exit_Reason'] = 'EOD'
        elif pnl_pct <= stop_pct:
            df.at[idx, 'Status'] = 'CLOSED'
            df.at[idx, 'Exit_Price'] = price
            df.at[idx, 'Exit_Time'] = now_str
            df.at[idx, 'Exit_Reason'] = 'STOP'
        elif pnl_pct >= target_pct:
            df.at[idx, 'Status'] = 'CLOSED'
            df.at[idx, 'Exit_Price'] = price
            df.at[idx, 'Exit_Time'] = now_str
            df.at[idx, 'Exit_Reason'] = 'TARGET'

    return df


def add_new_signals(portfolio: pd.DataFrame, signals: list[dict]) -> pd.DataFrame:
    """Add new signals to portfolio, skipping symbols already open."""
    if portfolio.empty or 'Status' not in portfolio.columns:
        open_syms: set = set()
    else:
        open_syms = set(portfolio[portfolio['Status'] == 'OPEN']['Symbol'])
    new_rows = []
    for sig in signals:
        if sig['Symbol'] in open_syms:
            continue
        new_rows.append({
            'Symbol': sig['Symbol'],
            'Entry_Price': sig['Price'],
            'Entry_Time': sig['Time'],
            'Signal': sig['Signal'],
            'Timeframe': sig['Timeframe'],
            'RSI_Entry': sig['RSI'],
            'MACD_Hist_Entry': sig['MACD_Hist'],
            'Status': 'OPEN',
            'Current_Price': sig['Price'],
            'PnL_Pct': 0.0,
            'Exit_Price': None,
            'Exit_Time': None,
            'Exit_Reason': None,
        })
    if new_rows:
        portfolio = pd.concat([portfolio, pd.DataFrame(new_rows)], ignore_index=True)
    return portfolio


def print_portfolio_report(df: pd.DataFrame):
    """Print today's portfolio performance summary."""
    if df.empty:
        print("No MACD/RSI portfolio positions today.")
        return

    closed = df[df['Status'] == 'CLOSED']
    open_pos = df[df['Status'] == 'OPEN']

    print(f"\n{'═'*60}")
    print(f"  MACD+RSI Paper Portfolio — {datetime.now(NY_TZ).strftime('%Y-%m-%d')}")
    print(f"{'═'*60}")
    print(f"  Total signals: {len(df)}  |  Open: {len(open_pos)}  |  Closed: {len(closed)}")

    if not closed.empty:
        wins = closed[closed['PnL_Pct'] > 0]
        losses = closed[closed['PnL_Pct'] <= 0]
        avg_pnl = closed['PnL_Pct'].mean()
        win_rate = len(wins) / len(closed) * 100
        print(f"\n  Closed positions:")
        print(f"    Win rate : {win_rate:.0f}%  ({len(wins)}W / {len(losses)}L)")
        print(f"    Avg P&L  : {avg_pnl:+.2f}%")
        print(f"    Best     : {closed['PnL_Pct'].max():+.2f}%  ({closed.loc[closed['PnL_Pct'].idxmax(), 'Symbol']})")
        print(f"    Worst    : {closed['PnL_Pct'].min():+.2f}%  ({closed.loc[closed['PnL_Pct'].idxmin(), 'Symbol']})")
        print(f"\n  {'Symbol':<8} {'Entry':>8} {'Exit':>8} {'P&L':>7} {'Reason':<8} Signal")
        print(f"  {'─'*55}")
        for _, r in closed.sort_values('PnL_Pct', ascending=False).iterrows():
            exit_price = r['Exit_Price']
            exit_str = f"{float(exit_price):>8.2f}" if pd.notna(exit_price) else '     N/A'
            print(f"  {r['Symbol']:<8} {r['Entry_Price']:>8.2f} {exit_str} "
                  f"{r['PnL_Pct']:>+6.2f}% {r['Exit_Reason']:<8} {r['Signal']}")

    if not open_pos.empty:
        print(f"\n  Open positions:")
        print(f"  {'Symbol':<8} {'Entry':>8} {'Current':>8} {'P&L':>7} Signal")
        print(f"  {'─'*45}")
        for _, r in open_pos.iterrows():
            print(f"  {r['Symbol']:<8} {r['Entry_Price']:>8.2f} {r['Current_Price']:>8.2f} "
                  f"{r['PnL_Pct']:>+6.2f}% {r['Signal']}")

    print(f"{'═'*60}\n")


# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Lightweight MACD+RSI parallel scan')
    parser.add_argument('--watchlist', default=DEFAULT_WATCHLIST)
    parser.add_argument('--timeframe', default='1d', choices=['1d', '1h', '15m', '5m'])
    parser.add_argument('--rsi-min', type=float, default=40.0)
    parser.add_argument('--rsi-max', type=float, default=72.0)
    parser.add_argument('--vol-mult', type=float, default=1.2,
                        help='Min volume ratio vs 20-bar avg (0 = disabled, default 1.5)')
    parser.add_argument('--track-portfolio', action='store_true',
                        help='Save signals and track P&L (paper portfolio)')
    parser.add_argument('--portfolio-report', action='store_true',
                        help='Show today portfolio P&L report and exit')
    args = parser.parse_args()

    if args.portfolio_report:
        df = load_portfolio()
        print_portfolio_report(df)
        return

    symbols = load_watchlist(args.watchlist)
    vol_str = f"Vol≥{args.vol_mult}x" if args.vol_mult > 0 else "Vol=off"
    logger.info(f"MACD+RSI scan | {len(symbols)} symbols | TF={args.timeframe} | RSI {args.rsi_min}-{args.rsi_max} | {vol_str}")

    results = batch_scan(symbols, args.timeframe, args.rsi_min, args.rsi_max, args.vol_mult)
    for sig in results:
        flag = '🔀' if sig['Signal'] == 'MACD_CROSS' else '📈'
        logger.info(f"  {flag} {sig['Symbol']:6s}  RSI={sig['RSI']:.1f}  MACD_Hist={sig['MACD_Hist']:+.4f}  [{sig['Signal']}]")

    logger.info(f"Found {len(results)} signals out of {len(symbols)} symbols")

    if results:
        df_out = pd.DataFrame(results)
        df_out['_sort'] = df_out['Signal'].map({'MACD_CROSS': 0, 'MACD_BULL': 1})
        df_out = df_out.sort_values(['_sort', 'RSI']).drop(columns='_sort')
        print("\n" + df_out.to_string(index=False))
    else:
        print("(no MACD+RSI signals)")

    if args.track_portfolio:
        portfolio = load_portfolio()
        portfolio = update_open_positions(portfolio, args.timeframe)
        portfolio = add_new_signals(portfolio, results)
        save_portfolio(portfolio)
        logger.info(f"Portfolio updated → {portfolio_path()}")
        print_portfolio_report(portfolio)


if __name__ == '__main__':
    main()
