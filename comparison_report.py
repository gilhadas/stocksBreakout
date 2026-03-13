#!/usr/bin/env python3
"""
comparison_report.py
────────────────────
Side-by-side performance comparison of the Breakout Scanner vs MACD/RSI Scanner.
Runs every 2 hours during market hours; included in automated_test_agent emails.

Usage:
  python comparison_report.py               # Full report to stdout
  python comparison_report.py --html        # HTML-formatted output (for email)
  python comparison_report.py --since 09:35 # Only signals from this time onward
"""

import argparse
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

NY_TZ = ZoneInfo('America/New_York')
SIGNALS_DIR = Path('scanner_output/signals')
MACD_DIR    = Path('scanner_output/macd_rsi')

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s',
                    datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────

def today_str(fmt: str) -> str:
    return datetime.now(NY_TZ).strftime(fmt)


def load_breakout_signals(since_time: str | None = None) -> pd.DataFrame:
    """Load all breakout scanner signals from today's CSV files."""
    date_tag = today_str('%Y%m%d')
    csvs = list(SIGNALS_DIR.glob(f'signals_*{date_tag}*.csv'))
    if not csvs:
        return pd.DataFrame()

    frames = []
    for p in csvs:
        try:
            df = pd.read_csv(p)
            # Infer scan time from filename (signals_daytrade_20260313_093512.csv)
            parts = p.stem.split('_')
            if len(parts) >= 4:
                time_part = parts[-1]  # e.g. "093512"
                scan_time = f"{time_part[:2]}:{time_part[2:4]}"
            else:
                scan_time = '?'
            df['Scan_Time'] = scan_time
            df['Scan_Mode'] = parts[1] if len(parts) >= 2 else '?'
            frames.append(df)
        except Exception as e:
            logger.debug(f"Skip {p.name}: {e}")

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=['Symbol', 'Scan_Time'])

    if since_time:
        out = out[out['Scan_Time'] >= since_time]

    return out


def load_macd_portfolio() -> pd.DataFrame:
    """Load today's MACD/RSI paper portfolio."""
    p = MACD_DIR / f'portfolio_{today_str("%Y%m%d")}.csv'
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    # Derive scan time from Entry_Time (e.g. "2026-03-13 09:35 ET")
    if 'Entry_Time' in df.columns:
        df['Scan_Time'] = df['Entry_Time'].str.extract(r'(\d{2}:\d{2})')
    return df


def fetch_current_prices(symbols: list[str]) -> dict[str, float]:
    """Batch fetch latest prices for a list of symbols."""
    if not symbols:
        return {}
    try:
        raw = yf.download(symbols, period='1d', interval='1m',
                          progress=False, auto_adjust=True,
                          group_by='ticker', threads=True)
        prices = {}
        for sym in symbols:
            try:
                # group_by='ticker' always produces a MultiIndex; use it uniformly.
                # Fall back to flat-column access only when MultiIndex is absent.
                if isinstance(raw.columns, pd.MultiIndex):
                    if sym not in raw.columns.get_level_values(0):
                        continue
                    close = raw[sym]['Close'].dropna()
                else:
                    col = 'Close' if 'Close' in raw.columns else 'close'
                    close = raw[col].dropna()
                if len(close) > 0:
                    prices[sym] = round(float(close.iloc[-1]), 2)
            except Exception:
                pass
        return prices
    except Exception as e:
        logger.warning(f"Price fetch failed: {e}")
        return {}


def enrich_breakout(df: pd.DataFrame, prices: dict) -> pd.DataFrame:
    """Add current price and P&L to breakout signals."""
    if df.empty:
        return df
    df = df.copy()
    df['Current_Price'] = df['Symbol'].map(prices)
    # Guard: Price column must exist and be non-zero
    entry = pd.to_numeric(df.get('Price', pd.Series(dtype=float)), errors='coerce')
    df['PnL_Pct'] = ((df['Current_Price'] - entry) / entry.replace(0, float('nan')) * 100).round(2)
    # Target / Stop columns may be absent in some scan modes
    if 'Target' in df.columns and 'Stop' in df.columns:
        df['Hit_Target'] = df['Current_Price'] >= pd.to_numeric(df['Target'], errors='coerce')
        df['Hit_Stop']   = df['Current_Price'] <= pd.to_numeric(df['Stop'],   errors='coerce')
        df['Status'] = df.apply(
            lambda r: 'TARGET' if r['Hit_Target'] else ('STOP' if r['Hit_Stop'] else 'OPEN'), axis=1
        )
    else:
        df['Hit_Target'] = False
        df['Hit_Stop']   = False
        df['Status'] = 'OPEN'
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Report rendering
# ─────────────────────────────────────────────────────────────────────────────

def _pnl_str(v) -> str:
    try:
        f = float(v)
        return f'{f:+.2f}%'
    except Exception:
        return 'N/A'


def render_text_report(breakout: pd.DataFrame, macd: pd.DataFrame,
                       prices: dict, now_str: str) -> str:
    W = 72
    lines = []
    lines += [
        '═' * W,
        f'  BREAKOUT vs MACD/RSI — Comparison Report  {now_str}',
        '═' * W,
    ]

    # ── Breakout section ──────────────────────────────────────────────────────
    lines.append('\n  ▶ FLOW 1: BREAKOUT SCANNER')
    lines.append('  ' + '─' * (W - 2))
    if breakout.empty:
        lines.append('  (no signals today)')
    else:
        # Only select columns that actually exist in this run's CSV
        _want_cols = ['Symbol', 'Scan_Time', 'Scan_Mode', 'Quality', 'Type',
                      'Price', 'Current_Price', 'PnL_Pct', 'Status',
                      'RSI', 'R:R', 'Stop', 'Target']
        b = breakout[[c for c in _want_cols if c in breakout.columns]].copy()
        b = b.sort_values('PnL_Pct', ascending=False) if 'PnL_Pct' in b.columns else b

        hdr = f"  {'Symbol':<7} {'Time':<6} {'Mode':<9} {'Quality':<9} {'Type':<14} {'Entry':>7} {'Now':>7} {'P&L':>7} {'Status':<7} {'RSI':>5} {'R:R':>4}"
        lines.append(hdr)
        lines.append('  ' + '─' * (W - 2))
        for _, r in b.iterrows():
            pnl = _pnl_str(r.get('PnL_Pct'))
            rsi = f"{r['RSI']:.0f}" if pd.notna(r.get('RSI')) else '  ?'
            rr  = f"{r['R:R']:.1f}" if pd.notna(r.get('R:R')) else '  ?'
            now = f"{r['Current_Price']:.2f}" if pd.notna(r.get('Current_Price')) else '    ?'
            lines.append(
                f"  {r['Symbol']:<7} {r.get('Scan_Time', '?'):<6} {r.get('Scan_Mode','?'):<9} "
                f"{r.get('Quality','?'):<9} {r.get('Type','?'):<14} "
                f"{float(r['Price']):>7.2f} {now:>7} {pnl:>7} {r.get('Status','?'):<7} "
                f"{rsi:>5} {rr:>4}"
            )

        closed = breakout[breakout['Status'] != 'OPEN']
        open_  = breakout[breakout['Status'] == 'OPEN']
        wins   = closed[closed['PnL_Pct'] > 0]
        wr     = f"{len(wins)/len(closed)*100:.0f}%" if len(closed) else 'N/A'
        avg    = f"{breakout['PnL_Pct'].mean():+.2f}%" if not breakout.empty else 'N/A'
        lines.append(f"\n  Signals: {len(breakout)}  |  Open: {len(open_)}  |  Closed: {len(closed)}  "
                     f"|  Win Rate: {wr}  |  Avg P&L: {avg}")

    # ── MACD/RSI section ──────────────────────────────────────────────────────
    lines.append('\n  ▶ FLOW 2: MACD+RSI SCANNER')
    lines.append('  ' + '─' * (W - 2))
    if macd.empty:
        lines.append('  (no signals today)')
    else:
        m = macd.copy()
        if 'Current_Price' not in m.columns:
            m['Current_Price'] = m['Symbol'].map(prices)
        if 'PnL_Pct' not in m.columns or m['PnL_Pct'].isna().all():
            entry = pd.to_numeric(m.get('Entry_Price', pd.Series(dtype=float)), errors='coerce')
            m['PnL_Pct'] = ((m['Current_Price'] - entry) / entry.replace(0, float('nan')) * 100).round(2)
        m = m.sort_values('PnL_Pct', ascending=False)

        hdr = f"  {'Symbol':<7} {'Time':<6} {'Signal':<12} {'Entry':>7} {'Now':>7} {'P&L':>7} {'Status':<8} {'RSI':>5}"
        lines.append(hdr)
        lines.append('  ' + '─' * (W - 2))
        for _, r in m.iterrows():
            pnl = _pnl_str(r.get('PnL_Pct'))
            now = f"{r['Current_Price']:.2f}" if pd.notna(r.get('Current_Price')) else '    ?'
            lines.append(
                f"  {r['Symbol']:<7} {r.get('Scan_Time', '?'):<6} {r.get('Signal','?'):<12} "
                f"{float(r['Entry_Price']):>7.2f} {now:>7} {pnl:>7} {r.get('Status','?'):<8} "
                f"{r.get('RSI_Entry', '?'):>5}"
            )

        closed = macd[macd['Status'] == 'CLOSED']
        open_  = macd[macd['Status'] == 'OPEN']
        wins   = closed[closed['PnL_Pct'] > 0]
        wr     = f"{len(wins)/len(closed)*100:.0f}%" if len(closed) else 'N/A'
        avg    = f"{macd['PnL_Pct'].mean():+.2f}%" if not macd.empty else 'N/A'
        lines.append(f"\n  Signals: {len(macd)}  |  Open: {len(open_)}  |  Closed: {len(closed)}  "
                     f"|  Win Rate: {wr}  |  Avg P&L: {avg}")

    # ── Overlap analysis ──────────────────────────────────────────────────────
    lines.append('\n  ▶ OVERLAP ANALYSIS')
    lines.append('  ' + '─' * (W - 2))
    b_syms = set(breakout['Symbol']) if not breakout.empty else set()
    m_syms = set(macd['Symbol'])     if not macd.empty     else set()
    both   = b_syms & m_syms
    only_b = b_syms - m_syms
    only_m = m_syms - b_syms

    lines.append(f"  Both scanners agree : {', '.join(sorted(both)) or '(none)'}")
    lines.append(f"  Only BREAKOUT caught: {', '.join(sorted(only_b)) or '(none)'}")
    lines.append(f"  Only MACD/RSI caught: {', '.join(sorted(only_m)) or '(none)'}")

    # ── Head-to-head summary ──────────────────────────────────────────────────
    lines.append('\n  ▶ HEAD-TO-HEAD SUMMARY')
    lines.append('  ' + '─' * (W - 2))
    lines.append(f"  {'Metric':<22} {'BREAKOUT':>12} {'MACD/RSI':>12}")
    lines.append(f"  {'─'*22} {'─'*12} {'─'*12}")

    def stat(df, col='PnL_Pct'):
        if df.empty or col not in df.columns:
            return 'N/A', 'N/A', 'N/A', 'N/A', 'N/A'
        closed = df[df['Status'].isin(['CLOSED', 'STOP', 'TARGET'])]
        # BUG FIX: use closed[col] (not df[col]) as the mask — df may have a
        # different index/length than closed, producing wrong win counts.
        wins   = closed[closed[col] > 0] if not closed.empty else closed
        wr     = f"{len(wins)/len(closed)*100:.0f}%" if len(closed) else 'N/A'
        avg    = f"{df[col].mean():+.2f}%"
        best   = f"{df[col].max():+.2f}%"
        worst  = f"{df[col].min():+.2f}%"
        return str(len(df)), wr, avg, best, worst

    b_n, b_wr, b_avg, b_best, b_worst = stat(breakout)
    m_n, m_wr, m_avg, m_best, m_worst = stat(macd)

    for label, bv, mv in [
        ('Total Signals',   b_n,     m_n),
        ('Win Rate',        b_wr,    m_wr),
        ('Avg P&L',         b_avg,   m_avg),
        ('Best Trade',      b_best,  m_best),
        ('Worst Trade',     b_worst, m_worst),
        ('Overlap %',
         f"{len(both)/len(b_syms)*100:.0f}%" if b_syms else 'N/A',
         f"{len(both)/len(m_syms)*100:.0f}%" if m_syms else 'N/A'),
    ]:
        lines.append(f"  {label:<22} {bv:>12} {mv:>12}")

    lines.append('\n' + '═' * W)
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Breakout vs MACD/RSI comparison report')
    parser.add_argument('--since', default=None, metavar='HH:MM',
                        help='Only include signals from this time onward (e.g. 09:35)')
    parser.add_argument('--html', action='store_true',
                        help='Output HTML for email embedding')
    args = parser.parse_args()

    now_str = datetime.now(NY_TZ).strftime('%Y-%m-%d %H:%M ET')
    logger.info(f"Generating comparison report at {now_str}")

    breakout = load_breakout_signals(since_time=args.since)
    macd     = load_macd_portfolio()

    # Fetch current prices for all unique symbols
    all_syms = list(
        set(breakout['Symbol'].tolist() if not breakout.empty else []) |
        set(macd['Symbol'].tolist()     if not macd.empty     else [])
    )
    logger.info(f"Fetching current prices for {len(all_syms)} symbols...")
    prices = fetch_current_prices(all_syms)

    if not breakout.empty:
        breakout = enrich_breakout(breakout, prices)
    if not macd.empty and 'Current_Price' not in macd.columns:
        macd = macd.copy()
        macd['Current_Price'] = macd['Symbol'].map(prices)

    report = render_text_report(breakout, macd, prices, now_str)
    print(report)


if __name__ == '__main__':
    main()
