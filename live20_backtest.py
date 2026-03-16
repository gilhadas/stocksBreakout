#!/usr/bin/env python3
"""
live20_backtest.py
==================
Advanced backtest for live20 forecast system.

Uses forecast CSV from a week prior (e.g., live20_7_3_26.csv for 3/7-3/15 forecast)
Replays 1-minute bar data from forecast date to present, simulating:
  - Entry on S/R breakout (price breaks level by >1%)
  - Exit on trailing stop loss (ATR-based) matching breakout_scanner.py logic
  - Portfolio tracking with P&L

Output:
  - CSV report: trades, accuracy, P&L
  - Summary stats: win rate, profit factor, Sharpe ratio
"""

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from live20_monitor import load_forecast, BREAK_TOLERANCE_PCT, NY_TZ

# ── Backtest parameters ──────────────────────────────────────────────────────

POSITION_SIZE_PCT = 0.05  # Risk 5% of capital per trade
ATR_PERIOD = 14
ATR_TRAILING_MULT = 2.0  # 2.0 ATR trailing distance (from config)
INITIAL_CAPITAL = 10000

# Entry filters (more careful entry logic)
ENTRY_CONFIRMATION_BARS = 2  # Require 3 bars above/below threshold
MIN_VOLUME_RATIO = 1.2  # Volume must be 20% above 20-bar average at entry
SKIP_ALREADY_BROKEN = True  # Don't re-enter if already broken on CSV date
ONLY_PREDICTED_TRADES = True  # Only enter if direction matches forecast (test forecast accuracy)

# ── Trade tracking ───────────────────────────────────────────────────────────

class Trade:
    """Tracks a single trade from entry to exit."""
    def __init__(self, ticker: str, entry_time: datetime, entry_price: float,
                 direction: str, is_predicted: bool, sr_level: float):
        self.ticker = ticker
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.direction = direction  # 'UP' or 'DOWN'
        self.is_predicted = is_predicted  # True if matches forecast
        self.sr_level = sr_level

        self.exit_time = None
        self.exit_price = None
        self.exit_reason = None  # 'stop_loss', 'tp', 'eod'

        # ATR-based trailing stop
        self.initial_atr = None
        self.trailing_stop = None
        self.highest_price = entry_price if direction == 'UP' else entry_price
        self.lowest_price = entry_price if direction == 'DOWN' else entry_price

    @property
    def pnl(self) -> float | None:
        if self.exit_price is None:
            return None
        if self.direction == 'UP':
            return (self.exit_price - self.entry_price) * POSITION_SIZE_PCT
        else:  # SHORT
            return (self.entry_price - self.exit_price) * POSITION_SIZE_PCT

    @property
    def pnl_pct(self) -> float | None:
        if self.exit_price is None:
            return None
        if self.direction == 'UP':
            return ((self.exit_price / self.entry_price) - 1) * 100
        else:  # SHORT
            return ((self.entry_price / self.exit_price) - 1) * 100

    def to_dict(self) -> dict:
        return {
            'ticker': self.ticker,
            'entry_time': self.entry_time.strftime('%Y-%m-%d %H:%M:%S'),
            'entry_price': f"${self.entry_price:.2f}",
            'direction': self.direction,
            'predicted': 'YES' if self.is_predicted else 'SURPRISE',
            'exit_time': self.exit_time.strftime('%Y-%m-%d %H:%M:%S') if self.exit_time else '—',
            'exit_price': f"${self.exit_price:.2f}" if self.exit_price else '—',
            'exit_reason': self.exit_reason or '—',
            'P&L': f"${self.pnl:.2f}" if self.pnl is not None else '—',
            'P&L %': f"{self.pnl_pct:.1f}%" if self.pnl_pct is not None else '—',
        }


class VirtualPortfolio:
    """Tracks portfolio with multiple concurrent trades."""
    def __init__(self, initial_capital: float = INITIAL_CAPITAL):
        self.capital = initial_capital
        self.starting_capital = initial_capital
        self.trades = []
        self.closed_trades = []

    def open_trade(self, ticker: str, entry_time: datetime, entry_price: float,
                   direction: str, is_predicted: bool, sr_level: float,
                   atr: float | None = None) -> Trade:
        """Open a new position."""
        trade = Trade(ticker, entry_time, entry_price, direction, is_predicted, sr_level)
        if atr:
            trade.initial_atr = atr
            if direction == 'UP':
                trade.trailing_stop = entry_price - (atr * ATR_TRAILING_MULT)
            else:
                trade.trailing_stop = entry_price + (atr * ATR_TRAILING_MULT)
        self.trades.append(trade)
        return trade

    def close_trade(self, trade: Trade, exit_time: datetime, exit_price: float, reason: str):
        """Close a position."""
        trade.exit_time = exit_time
        trade.exit_price = exit_price
        trade.exit_reason = reason
        self.closed_trades.append(trade)
        self.trades.remove(trade)
        self.capital += trade.pnl if trade.pnl else 0

    def update_trailing_stops(self, ticker: str, current_price: float, atr: float):
        """Update trailing stops for open positions."""
        for trade in self.trades:
            if trade.ticker != ticker:
                continue
            if trade.trailing_stop is None:
                trade.trailing_stop = (
                    current_price - (atr * ATR_TRAILING_MULT)
                    if trade.direction == 'UP'
                    else current_price + (atr * ATR_TRAILING_MULT)
                )
            elif trade.direction == 'UP':
                # Long: trailing stop moves up only
                new_stop = current_price - (atr * ATR_TRAILING_MULT)
                if new_stop > trade.trailing_stop:
                    trade.trailing_stop = new_stop
                trade.highest_price = max(trade.highest_price, current_price)
            else:
                # Short: trailing stop moves down only
                new_stop = current_price + (atr * ATR_TRAILING_MULT)
                if new_stop < trade.trailing_stop:
                    trade.trailing_stop = new_stop
                trade.lowest_price = min(trade.lowest_price, current_price)

    @property
    def total_pnl(self) -> float:
        """Total realized P&L."""
        return sum(t.pnl for t in self.closed_trades if t.pnl is not None)

    @property
    def equity(self) -> float:
        """Current equity = capital + unrealized P&L."""
        unrealized = sum(
            ((t.highest_price - t.entry_price) * POSITION_SIZE_PCT if t.direction == 'UP'
             else (t.entry_price - t.lowest_price) * POSITION_SIZE_PCT)
            for t in self.trades
        )
        return self.capital + unrealized

    @property
    def return_pct(self) -> float:
        """Total return %."""
        return ((self.capital - self.starting_capital) / self.starting_capital) * 100

    @property
    def win_rate(self) -> float:
        """Percent of closed trades that were profitable."""
        if not self.closed_trades:
            return 0.0
        winners = sum(1 for t in self.closed_trades if t.pnl and t.pnl > 0)
        return (winners / len(self.closed_trades)) * 100

    @property
    def profit_factor(self) -> float:
        """Ratio of gross profit to gross loss."""
        gross_profit = sum(t.pnl for t in self.closed_trades if t.pnl and t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.closed_trades if t.pnl and t.pnl < 0))
        if gross_loss == 0:
            return float('inf') if gross_profit > 0 else 0.0
        return gross_profit / gross_loss


def download_bars(tickers: list[str], start_date: str, end_date: str) -> dict[str, pd.DataFrame]:
    """Download 1-minute bars from yfinance."""
    print(f"Downloading 1-min bars for {len(tickers)} tickers ({start_date} to {end_date})...")
    data = {}

    try:
        # Batch download all tickers at once
        df_all = yf.download(
            tickers, start=start_date, end=end_date,
            interval='1m', progress=False, group_by='ticker'
        )

        for ticker in tickers:
            try:
                # Extract single ticker data
                if len(tickers) == 1:
                    df = df_all
                else:
                    df = df_all[ticker] if ticker in df_all.columns.get_level_values(0) else pd.DataFrame()

                if df.empty:
                    continue

                # Flatten multi-level columns if needed
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(-1)

                # Ensure index is timezone-aware (ET)
                if df.index.tz is None:
                    df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')
                else:
                    df.index = df.index.tz_convert('America/New_York')

                # Lowercase columns
                df.columns = [c.lower() for c in df.columns]
                data[ticker] = df
                print(f"  {ticker}: {len(df)} bars")
            except Exception as e:
                print(f"  {ticker}: extract error {e}")

    except Exception as e:
        print(f"Batch download error: {e}")

    return data


def calc_atr(df: pd.DataFrame, period: int = 14) -> float | None:
    """Calculate ATR from last rows."""
    if len(df) < period:
        return None

    try:
        high = df['high'].astype(float)
        low = df['low'].astype(float)
        close = df['close'].astype(float)

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.tail(period).mean()
        return float(atr) if pd.notna(atr) else None
    except Exception:
        return None


def run_backtest(csv_path: Path, end_date: str | None = None):
    """
    Run backtest from forecast CSV date to end_date (default: today).
    """
    # Load forecast
    records = load_forecast(csv_path)
    if not records:
        print("No records in CSV")
        return

    # Extract date from CSV filename (e.g., 'live20_7_3_26.csv' → day=7, month=3, year=26 → 2026-03-07)
    csv_name = csv_path.stem  # 'live20_7_3_26'
    parts = csv_name.replace('live20_', '').split('_')
    if len(parts) != 3:
        print(f"Cannot parse date from {csv_name}")
        return
    d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
    # Format: YYYY-MM-DD
    start_date = f"20{y:02d}-{m:02d}-{d:02d}"

    if end_date is None:
        end_date = (datetime.now(NY_TZ) + timedelta(days=1)).strftime('%Y-%m-%d')

    print(f"\n{'='*70}")
    print(f"Live20 Backtest: {start_date} → {end_date}")
    print(f"Forecast file: {csv_path.name}")
    print(f"Forecast records: {len(records)}")
    print(f"{'='*70}\n")

    # Download bars
    tickers = [r['ticker'] for r in records]
    bars = download_bars(tickers, start_date, end_date)

    if not bars:
        print("No bar data downloaded")
        return

    # Run simulation
    portfolio = VirtualPortfolio(INITIAL_CAPITAL)
    ticker_records = {r['ticker']: r for r in records}

    # Track entry state per ticker: confirmation_bars, previous_bars_for_confirmation
    entry_state = {
        r['ticker']: {
            'confirmation_bars_up': 0,
            'confirmation_bars_down': 0,
            'already_entered': False,  # Prevent re-entry after exit
        }
        for r in records
    }

    # Pre-populate entry state: if already broken on CSV date, mark as "already entered"
    if SKIP_ALREADY_BROKEN:
        for rec in records:
            ticker = rec['ticker']
            price = rec['current_price']
            sr = rec['sr_level']
            if price is None or sr is None:
                continue
            threshold_up = sr * (1 + BREAK_TOLERANCE_PCT / 100)
            threshold_down = sr * (1 - BREAK_TOLERANCE_PCT / 100)
            if price >= threshold_up or price <= threshold_down:
                entry_state[ticker]['already_entered'] = True

    # Get all unique timestamps
    all_timestamps = set()
    for df in bars.values():
        all_timestamps.update(df.index)
    timestamps = sorted(all_timestamps)

    print(f"Replaying {len(timestamps)} bars...\n")

    for ts in timestamps:
        for ticker, df in bars.items():
            # Get price at this timestamp
            bar = df[df.index == ts]
            if bar.empty:
                continue

            close = float(bar['close'].iloc[0])
            volume = float(bar['volume'].iloc[0]) if 'volume' in bar.columns else 0
            rec = ticker_records[ticker]
            sr = rec['sr_level']
            bullish = rec['bullish']
            remarks = rec.get('remarks', '')
            bounce = rec.get('bounce_value')
            wait_direction = rec.get('wait_direction', False)

            if sr is None:
                continue

            # Rule 1: Skip tickers with earnings in Remarks (ER = Earnings Report shortcut)
            if re.search(r'\bER\b', remarks) or \
               any(kw in remarks.lower() for kw in ('earn', 'report', 'eps')):
                continue

            # Check for entry signals
            threshold_up = sr * (1 + BREAK_TOLERANCE_PCT / 100)
            threshold_down = sr * (1 - BREAK_TOLERANCE_PCT / 100)

            # Don't open if already have position
            if any(t.ticker == ticker for t in portfolio.trades):
                continue

            # Don't re-enter if already broken on CSV date
            if entry_state[ticker]['already_entered']:
                continue

            # Rule 2: Bullish=NO only trade on bounce or direction-change, never on fresh breakdown
            if not bullish:
                direction = None
                is_predicted = None

                # Sub-rule A: bounce entry — price reaches the bounce target (going up from support)
                if bounce is not None:
                    bounce_low = bounce * (1 - 0.005)   # within 0.5% of bounce value
                    bounce_high = bounce * (1 + 0.005)
                    if bounce_low <= close <= bounce_high:
                        bars_so_far = df[df.index <= ts]
                        atr = calc_atr(bars_so_far, ATR_PERIOD)
                        vol_avg = bars_so_far['volume'].tail(20).mean() if 'volume' in bars_so_far.columns else 1
                        vol_ratio = volume / vol_avg if vol_avg > 0 else 0
                        if vol_ratio == 0 or vol_ratio >= MIN_VOLUME_RATIO:
                            trade = portfolio.open_trade(ticker, ts, close, 'UP', False, sr, atr)
                            entry_state[ticker]['already_entered'] = True
                            atr_str = f"{atr:.2f}" if atr else "N/A"
                            vol_str = f"{vol_ratio:.1f}x" if vol_ratio > 0 else "N/A"
                            print(f"  [{ts.strftime('%m/%d %H:%M')}] ~ {ticker} BOUNCE @ ${close:.2f} "
                                  f"(target ${bounce:.2f}, ATR {atr_str}, Vol {vol_str})")
                    continue  # Skip the normal breakout logic entirely for Bullish=NO

                # Sub-rule B: direction-change entry (wait_direction=YES, price near SR moving UP)
                if wait_direction:
                    pct_from_sr = (close - sr) / sr * 100
                    # Looking for price to cross above SR after being below (bullish reversal)
                    if 0 < pct_from_sr <= 1.5:
                        bars_so_far = df[df.index <= ts]
                        atr = calc_atr(bars_so_far, ATR_PERIOD)
                        vol_avg = bars_so_far['volume'].tail(20).mean() if 'volume' in bars_so_far.columns else 1
                        vol_ratio = volume / vol_avg if vol_avg > 0 else 0
                        if vol_ratio == 0 or vol_ratio >= MIN_VOLUME_RATIO:
                            trade = portfolio.open_trade(ticker, ts, close, 'UP', False, sr, atr)
                            entry_state[ticker]['already_entered'] = True
                            atr_str = f"{atr:.2f}" if atr else "N/A"
                            vol_str = f"{vol_ratio:.1f}x" if vol_ratio > 0 else "N/A"
                            print(f"  [{ts.strftime('%m/%d %H:%M')}] ~ {ticker} DIR-CHANGE @ ${close:.2f} "
                                  f"(SR ${sr:.2f}, ATR {atr_str}, Vol {vol_str})")
                    continue  # Skip normal logic for Bullish=NO

                continue  # Bullish=NO with no bounce/wait_direction — never enter

            # Bullish=YES — normal breakout entry with confirmation bars
            direction = None
            is_predicted = None

            # Count consecutive bars above/below threshold
            if close >= threshold_up:
                entry_state[ticker]['confirmation_bars_up'] += 1
                entry_state[ticker]['confirmation_bars_down'] = 0
            elif close <= threshold_down:
                entry_state[ticker]['confirmation_bars_down'] += 1
                entry_state[ticker]['confirmation_bars_up'] = 0
            else:
                # Price back in neutral zone - reset counters
                entry_state[ticker]['confirmation_bars_up'] = 0
                entry_state[ticker]['confirmation_bars_down'] = 0

            # Fire entry only after N confirmation bars
            if entry_state[ticker]['confirmation_bars_up'] == ENTRY_CONFIRMATION_BARS:
                direction = 'UP'
                is_predicted = True   # Bullish=YES breaking UP → predicted
            elif entry_state[ticker]['confirmation_bars_down'] == ENTRY_CONFIRMATION_BARS:
                direction = 'DOWN'
                is_predicted = False  # Bullish=YES breaking DOWN → surprise

            if direction:
                # Filter: only enter if direction matches forecast (if enabled)
                if ONLY_PREDICTED_TRADES and not is_predicted:
                    continue

                # Calculate ATR from bars so far
                bars_so_far = df[df.index <= ts]
                atr = calc_atr(bars_so_far, ATR_PERIOD)

                # Volume check (optional)
                vol_avg = bars_so_far['volume'].tail(20).mean() if 'volume' in bars_so_far.columns else 1
                vol_ratio = volume / vol_avg if vol_avg > 0 else 0

                # Only enter if volume is adequate or we don't care (vol_ratio = 0 means no data)
                if vol_ratio == 0 or vol_ratio >= MIN_VOLUME_RATIO:
                    trade = portfolio.open_trade(
                        ticker, ts, close, direction, is_predicted, sr, atr
                    )
                    entry_state[ticker]['already_entered'] = True
                    pred_label = "✓" if is_predicted else "✗"
                    atr_str = f"{atr:.2f}" if atr else "N/A"
                    vol_str = f"{vol_ratio:.1f}x" if vol_ratio > 0 else "N/A"
                    print(f"  [{ts.strftime('%m/%d %H:%M')}] {pred_label} {ticker} {direction:4} @ ${close:.2f} "
                          f"(SR ${sr:.2f}, ATR {atr_str}, Vol {vol_str})")

            # Update trailing stops for open positions
            for trade in portfolio.trades:
                if trade.ticker != ticker:
                    continue
                bars_so_far = df[df.index <= ts]
                atr = calc_atr(bars_so_far, ATR_PERIOD)
                if atr:
                    portfolio.update_trailing_stops(ticker, close, atr)

                    # Check stop loss
                    if trade.trailing_stop:
                        hit_stop = (
                            (trade.direction == 'UP' and close <= trade.trailing_stop)
                            or (trade.direction == 'DOWN' and close >= trade.trailing_stop)
                        )
                        if hit_stop:
                            portfolio.close_trade(trade, ts, close, 'stop_loss')
                            result = "✓ WIN" if trade.pnl > 0 else "✗ LOSS"
                            print(f"    → {result}: {ticker} exit @ ${close:.2f} "
                                  f"P&L ${trade.pnl:.2f} ({trade.pnl_pct:.1f}%)")

    # Close EOD positions (midnight ET)
    for trade in list(portfolio.trades):
        last_bar = bars[trade.ticker].iloc[-1]
        portfolio.close_trade(trade, last_bar.name, float(last_bar['close']), 'eod')

    # Generate report
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"Closed trades: {len(portfolio.closed_trades)}")
    print(f"Winners: {sum(1 for t in portfolio.closed_trades if t.pnl > 0)}")
    print(f"Losers: {sum(1 for t in portfolio.closed_trades if t.pnl < 0)}")
    print(f"Win rate: {portfolio.win_rate:.1f}%")
    print(f"Profit factor: {portfolio.profit_factor:.2f}")
    print(f"\nCapital: ${portfolio.starting_capital:.2f} → ${portfolio.capital:.2f}")
    print(f"P&L: ${portfolio.total_pnl:.2f}")
    print(f"Return: {portfolio.return_pct:.2f}%")
    print()

    # Accuracy breakdown
    if portfolio.closed_trades:
        predicted = sum(1 for t in portfolio.closed_trades if t.is_predicted)
        predicted_correct = sum(
            1 for t in portfolio.closed_trades
            if t.is_predicted and t.pnl > 0
        )
        surprises = len(portfolio.closed_trades) - predicted
        surprises_correct = sum(
            1 for t in portfolio.closed_trades
            if not t.is_predicted and t.pnl > 0
        )

        print(f"Prediction accuracy:")
        print(f"  Predicted ({predicted}): {predicted_correct}/{predicted} correct "
              f"({100*predicted_correct/predicted if predicted else 0:.0f}%)")
        print(f"  Surprises ({surprises}): {surprises_correct}/{surprises} correct "
              f"({100*surprises_correct/surprises if surprises else 0:.0f}%)")
        print()

    # CSV report
    output_file = Path('scanner_output') / 'live20_backtest_report.csv'
    output_file.parent.mkdir(parents=True, exist_ok=True)

    df_trades = pd.DataFrame([t.to_dict() for t in portfolio.closed_trades])
    df_trades.to_csv(output_file, index=False)
    print(f"Trade report: {output_file}")

    # Summary stats file
    stats_file = Path('scanner_output') / 'live20_backtest_stats.txt'
    with open(stats_file, 'w') as f:
        f.write(f"Live20 Backtest: {start_date} → {end_date}\n")
        f.write(f"Forecast: {csv_path.name}\n\n")
        f.write(f"Starting capital: ${portfolio.starting_capital:.2f}\n")
        f.write(f"Ending capital: ${portfolio.capital:.2f}\n")
        f.write(f"Total P&L: ${portfolio.total_pnl:.2f}\n")
        f.write(f"Return: {portfolio.return_pct:.2f}%\n\n")
        f.write(f"Trades closed: {len(portfolio.closed_trades)}\n")
        f.write(f"Winners: {sum(1 for t in portfolio.closed_trades if t.pnl > 0)}\n")
        f.write(f"Losers: {sum(1 for t in portfolio.closed_trades if t.pnl < 0)}\n")
        f.write(f"Win rate: {portfolio.win_rate:.1f}%\n")
        f.write(f"Profit factor: {portfolio.profit_factor:.2f}\n")
    print(f"Stats: {stats_file}\n")


def main():
    parser = argparse.ArgumentParser(description='Live20 advanced backtest with trailing stops')
    parser.add_argument('--file', help='Path to forecast CSV (e.g., input/live20_7_3_26.csv)')
    parser.add_argument('--end-date', help='End date (YYYY-MM-DD, default: today)')
    args = parser.parse_args()

    csv_path = Path(args.file) if args.file else Path('input/live20_7_3_26.csv')

    if not csv_path.exists():
        print(f"Error: {csv_path} not found")
        sys.exit(1)

    run_backtest(csv_path, args.end_date)


if __name__ == '__main__':
    main()
