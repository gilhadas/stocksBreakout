#!/usr/bin/env python3
"""
Backtest: V9-H Strategy — Full Transaction Log
===============================================
Simulates the V9-H strategy (regime gate + quality filter) across
bear/mixed/bull years and exports every transaction to CSV:
  date, symbol, entry price, shares, exit price, revenue %, revenue $.

Uses the V9-H hybrid scanner (SPY SMA200 bear_macro + BEARISH regime block)
matching the current live config in orchestrator.py.

Periods:
  2022: Bear  (SPY -18%)    2023: Bull (+26%)    2024: Bull (+23%)
  2025: Mixed               2026: Current (partial)

Usage:
    python backtest_health.py                          # Full 2022-2026 run
    python backtest_health.py --start 2024-01-01       # Custom range
    python backtest_health.py --watchlist input/ALL.txt # Different symbols
"""
import argparse
import asyncio
import csv
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from mock_trader import SimulationMode, MockTrader
from scanner import BreakoutDetector
from enhanced_backtest import load_symbols, fetch_all_data
from backtest_regime_compare import (
    classify_day_regime, is_spy_below_sma200, collect_signals_hybrid,
)


class DateFixedSimulation(SimulationMode):
    """SimulationMode that records correct entry/exit dates (not datetime.now())."""

    def run_simulation(self, signals, end_prices=None, historical_data=None):
        """Override to patch entry_time/exit_time with simulation dates."""
        self.end_prices = end_prices or {}
        self.historical_data = historical_data or {}
        self.trader.historical_data = self.historical_data  # share for ATR sizing

        signals_df = pd.DataFrame(signals)
        signals_df['date'] = pd.to_datetime(signals_df['date'])
        signals_df = signals_df.sort_values('date')

        current_date = self.start_date
        while current_date <= self.end_date:
            # Track open positions before this day
            open_before = set(self.trader.open_positions.keys())

            # Execute signals for this date
            day_signals = signals_df[signals_df['date'].dt.date == current_date.date()]
            for _, signal in day_signals.iterrows():
                quality = signal.get('quality', 'STANDARD')
                win_prob = signal.get('win_probability', 0.50)
                quantity = self.trader.calculate_position_size(
                    price=signal['price'],
                    stop_loss=signal.get('stop_loss', signal['price'] * 0.95),
                    quality=quality, win_probability=win_prob,
                    symbol=signal.get('symbol', ''),
                    as_of_date=current_date,
                )
                if quantity == 0:
                    continue
                trade = self.trader.enter_trade(
                    symbol=signal['symbol'], action=signal['action'],
                    quantity=quantity, price=signal['price'],
                    stop_loss=signal.get('stop_loss', signal['price'] * 0.95),
                    take_profit=signal.get('take_profit', signal['price'] * 1.10),
                    signal_type=signal.get('signal_type', signal.get('type', '')),
                )
                trade.entry_time = current_date.to_pydatetime()
                trade.mode = signal.get('mode', 'swing')
                trade.win_probability = win_prob

            # Check exits
            if self.trader.open_positions:
                self._check_exits(current_date)

            # Patch exit_time for any trades closed today
            new_closed = open_before - set(self.trader.open_positions.keys())
            for tid in new_closed:
                for t in self.trader.trades:
                    if t.trade_id == tid and t.exit_time:
                        t.exit_time = current_date.to_pydatetime()

            # Trailing stops
            if (self.use_trailing_stop or self.tp_as_trail) and self.trader.open_positions:
                self._update_trailing_stops(current_date)

            self.daily_equity.append({
                'date': current_date,
                'equity': self.trader.capital + self._calculate_open_position_value(current_date),
            })
            current_date += timedelta(days=1)

        # Close remaining positions at end
        for t in list(self.trader.open_positions.values()):
            sym = t.symbol
            end_px = self.end_prices.get(sym, t.entry_price)
            self.trader.exit_trade(sym, end_px, 'END_OF_SIM')
            t.exit_time = self.end_date.to_pydatetime()

        return self.generate_report()

logging.basicConfig(level=logging.WARNING, format='%(message)s')
logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path('backtests')


# ── Periods ───────────────────────────────────────────────────────────────────

PERIODS = {
    'bear_2022':  ('2022-01-01', '2022-12-31', 'BEAR'),
    'bull_2023':  ('2023-01-01', '2023-12-31', 'BULL'),
    'bull_2024':  ('2024-01-01', '2024-12-31', 'BULL'),
    'mixed_2025': ('2025-01-01', '2025-12-31', 'MIXED'),
    'current_2026': ('2026-01-01', '2026-12-31', 'MIXED'),
}


# ── Trade Extraction ──────────────────────────────────────────────────────────

def extract_trades(sim: SimulationMode, signals: list[dict]) -> list[dict]:
    """
    Extract all closed trades from a SimulationMode instance into flat dicts.
    Uses signal dates for entry (MockTrader uses datetime.now() internally).
    """
    # Build lookup: symbol → signal dates (sorted) for entry date recovery
    from collections import defaultdict
    sig_dates = defaultdict(list)
    for s in signals:
        d = pd.to_datetime(s['date']).strftime('%Y-%m-%d')
        sig_dates[s['symbol']].append(d)

    # Track which signal date index we've used per symbol
    sig_idx = defaultdict(int)

    rows = []
    for trade in sim.trader.trades:
        if trade.status == 'OPEN':
            continue
        entry_cost = trade.entry_price * trade.quantity
        exit_proceeds = (trade.exit_price or trade.entry_price) * trade.quantity
        pnl_dollar = exit_proceeds - entry_cost
        pnl_pct = (pnl_dollar / entry_cost * 100) if entry_cost > 0 else 0

        # Recover entry date from signal (MockTrader uses datetime.now())
        sym = trade.symbol
        idx = sig_idx[sym]
        dates = sig_dates.get(sym, [])
        date_entry = dates[idx] if idx < len(dates) else ''
        sig_idx[sym] = idx + 1

        # Recover exit date from exit_time (may be datetime.now() for force-close)
        if trade.exit_time and trade.exit_time.year < 2027:
            date_exit = trade.exit_time.strftime('%Y-%m-%d')
        else:
            date_exit = ''

        # Compute hold_days from signal dates
        if date_entry and date_exit:
            try:
                hold = (pd.to_datetime(date_exit) - pd.to_datetime(date_entry)).days
            except Exception:
                hold = trade.hold_days
        else:
            hold = trade.hold_days

        rows.append({
            'period':       '',   # filled later
            'regime':       '',   # filled later
            'date_entry':   date_entry,
            'date_exit':    date_exit,
            'symbol':       sym,
            'mode':         trade.mode,
            'quality':      '',   # filled from signal
            'entry_price':  round(trade.entry_price, 2),
            'exit_price':   round(trade.exit_price, 2) if trade.exit_price else 0,
            'shares':       trade.quantity,
            'cost':         round(entry_cost, 2),
            'proceeds':     round(exit_proceeds, 2),
            'revenue_pct':  round(pnl_pct, 2),
            'revenue_usd':  round(pnl_dollar, 2),
            'hold_days':    hold,
            'exit_reason':  trade.status,
            'signal_type':  trade.signal_type,
        })
    return rows


# ── Run Single Period ─────────────────────────────────────────────────────────

def _scan_v9h(historical: dict, start: str, end: str,
              modes: list[str] = None) -> list[dict]:
    """
    V9-H signal scanner — replicates the live regime gate logic:
      - bear_macro (SPY < SMA200): GOLD breakouts only, no BOUNCE/SMA20_CROSS
      - BEARISH regime: block BOUNCE/SMA20_CROSS, PREMIUM+ breakouts OK
      - All other regimes: trade normally
    """
    if modes is None:
        modes = ['swing', 'longterm']

    detector = BreakoutDetector()
    spy_df = historical.get('SPY')
    symbols = [s for s in historical if s != 'SPY']
    cooldowns = {}
    signals = []
    regime_counts = {}

    sim_dates = pd.date_range(start=start, end=end, freq='B')

    print(f"  [V9-H] Scanning {len(symbols)} symbols x {len(sim_dates)} days x {len(modes)} modes...")

    for day_idx, sim_date in enumerate(sim_dates):
        # Day-level regime classification
        regime, spy_pct, vol_pct = ('NORMAL', 0.0, 0.5) if spy_df is None \
            else classify_day_regime(spy_df, sim_date)
        regime_counts[regime] = regime_counts.get(regime, 0) + 1

        spy_perf_frac = spy_pct / 100.0

        # Compute bear_macro (SPY < SMA200) once per day
        bear_macro = is_spy_below_sma200(spy_df, sim_date) if spy_df is not None else False

        for symbol in symbols:
            # 10-day cooldown
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
                sig = collect_signals_hybrid(
                    detector, df_slice, symbol, mode, spy_perf_frac, regime, bear_macro
                )
                if sig:
                    quality = sig.get('Quality', 'STANDARD')
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
                        'signal_type': sig.get('Type', 'BREAKOUT'),
                        'regime': regime,
                        'bear_macro': bear_macro,
                        'minervini_score': sig.get('MinerviniScore', 0) or 0,
                        'win_probability': sig.get('WinProb', 0.50),
                    })
                    cooldowns[symbol] = sim_date
                    break  # one signal per symbol per day

        if (day_idx + 1) % 60 == 0:
            print(f"    Day {day_idx+1}/{len(sim_dates)}: {len(signals)} signals")

    # Print regime distribution
    total_days = sum(regime_counts.values())
    print(f"\n  Regime distribution ({total_days} days):")
    for r, n in sorted(regime_counts.items()):
        print(f"    {r:<14}: {n:>3} days ({n/total_days*100:.0f}%)")
    print(f"  V9-H signals: {len(signals)}")

    return signals


def run_period(period_name: str, start: str, end: str, regime: str,
               historical: dict, end_prices: dict,
               initial_capital: float) -> dict:
    """
    Run V9-H simulation for one period. Returns summary + trade list.
    """
    print(f"\n{'='*70}")
    print(f"  {period_name} ({start} → {end})  Regime: {regime}")
    print(f"{'='*70}")

    # Generate signals using V9-H hybrid scanner (regime gate)
    v9h_signals = _scan_v9h(historical, start, end)

    if not v9h_signals:
        print(f"  No V9-H signals found for this period.")
        return {
            'period': period_name, 'regime': regime,
            'start': start, 'end': end,
            'total_signals': 0, 'total_trades': 0,
            'total_return': 0, 'win_rate': 0,
            'sharpe': 0, 'max_dd': 0,
            'trades': [],
        }

    # Run simulation with V9-H config (matches live settings)
    sim = DateFixedSimulation(
        start_date=start,
        end_date=end,
        initial_capital=initial_capital,
        max_position_pct=0.10,     # 10% per position
        max_risk_pct=0.02,         # 2% risk per trade
        use_trailing_stop=False,
        tp_as_trail=True,          # V9: TP activates trailing stop
        tp_trail_atr_mult=2.0,     # V9: 2.0 ATR trail after TP
    )

    report = sim.run_simulation(
        v9h_signals,
        end_prices=end_prices,
        historical_data=historical,
    )

    # Extract trades (pass signals for correct date recovery)
    trades = extract_trades(sim, v9h_signals)

    # Enrich with signal quality + regime
    sig_quality_map = {
        (s['symbol'], pd.to_datetime(s['date']).strftime('%Y-%m-%d')): {
            'quality': s.get('quality', ''),
            'regime': s.get('regime', ''),
        }
        for s in v9h_signals
    }
    for t in trades:
        key = (t['symbol'], t['date_entry'])
        meta = sig_quality_map.get(key, {})
        t['quality'] = meta.get('quality', '')
        t['period'] = period_name
        t['regime'] = meta.get('regime', regime)

    # Summary
    total_trades = len(trades)
    wins = sum(1 for t in trades if t['revenue_usd'] > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades * 100) if total_trades else 0
    total_pnl = sum(t['revenue_usd'] for t in trades)
    total_return = (total_pnl / initial_capital * 100) if initial_capital else 0

    # Sharpe & drawdown from report
    sharpe = report.get('sharpe_ratio', 0) if report else 0
    max_dd = report.get('max_drawdown', 0) if report else 0

    print(f"  Trades: {total_trades}  Win/Loss: {wins}/{losses}  WR: {win_rate:.1f}%")
    print(f"  Return: {total_return:+.2f}%  (${total_pnl:+,.0f})  Sharpe: {sharpe:.2f}  MaxDD: {max_dd:+.2f}%")

    return {
        'period':        period_name,
        'regime':        regime,
        'start':         start,
        'end':           end,
        'total_signals': len(v9h_signals),
        'total_trades':  total_trades,
        'wins':          wins,
        'losses':        losses,
        'win_rate':      win_rate,
        'total_pnl':     total_pnl,
        'total_return':  total_return,
        'sharpe':        sharpe,
        'max_dd':        max_dd,
        'final_capital': initial_capital + total_pnl,
        'trades':        trades,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description='Backtest: Position Health & Cash Management')
    parser.add_argument('--watchlist', default='input/optimizer_watch.txt',
                        help='Watchlist file (default: input/optimizer_watch.txt)')
    parser.add_argument('--start', default='2022-01-01', help='Overall start date')
    parser.add_argument('--end', default=None, help='Overall end date (default: today)')
    parser.add_argument('--capital', type=int, default=100_000, help='Initial capital')
    parser.add_argument('--limit', type=int, default=0, help='Cap number of symbols (0=all)')
    parser.add_argument('--periods', default='all',
                        help='Comma-separated period names or "all" (default: all)')
    args = parser.parse_args()

    if args.end is None:
        args.end = datetime.now().strftime('%Y-%m-%d')

    # Load symbols
    symbols = load_symbols(args.watchlist, args.limit)
    print(f"\nLoaded {len(symbols)} symbols from {args.watchlist}")

    # Determine which periods to run
    if args.periods == 'all':
        selected_periods = {k: v for k, v in PERIODS.items()
                           if v[0] >= args.start and v[0] <= args.end}
    else:
        selected_periods = {k: PERIODS[k] for k in args.periods.split(',') if k in PERIODS}

    if not selected_periods:
        # Single custom range
        selected_periods = {'custom': (args.start, args.end, 'CUSTOM')}

    print(f"Periods: {', '.join(selected_periods.keys())}")

    # Fetch all data (full range with lookback)
    overall_start = min(v[0] for v in selected_periods.values())
    overall_end = max(v[1] for v in selected_periods.values())
    # Clamp end to today
    today = datetime.now().strftime('%Y-%m-%d')
    if overall_end > today:
        overall_end = today

    print(f"\nFetching data: {overall_start} → {overall_end}...")
    historical, end_prices = fetch_all_data(symbols, overall_start, overall_end)

    # Run each period
    all_trades = []
    summaries = []

    for period_name, (start, end, regime) in selected_periods.items():
        # Clamp end to today
        if end > today:
            end = today
        result = run_period(
            period_name, start, end, regime,
            historical, end_prices, args.capital
        )
        summaries.append(result)
        all_trades.extend(result['trades'])

    # ── Export all transactions to CSV ────────────────────────────────────
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = _OUTPUT_DIR / 'health_backtest_transactions.csv'
    csv_columns = [
        'period', 'regime', 'date_entry', 'date_exit', 'symbol', 'mode',
        'quality', 'entry_price', 'exit_price', 'shares', 'cost', 'proceeds',
        'revenue_pct', 'revenue_usd', 'hold_days', 'exit_reason', 'signal_type',
    ]

    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=csv_columns)
        writer.writeheader()
        for t in all_trades:
            writer.writerow({k: t.get(k, '') for k in csv_columns})

    print(f"\n{'='*70}")
    print(f"  TRANSACTIONS SAVED: {csv_path}")
    print(f"  Total trades: {len(all_trades)}")
    print(f"{'='*70}")

    # ── Summary table ─────────────────────────────────────────────────────
    print(f"\n{'='*100}")
    print(f"  BACKTEST SUMMARY — V9-H (regime gate) across market regimes")
    print(f"{'='*100}")
    header = (f"{'Period':<18} {'Regime':<8} {'Signals':>8} {'Trades':>7} "
              f"{'Win%':>6} {'Return':>10} {'P&L $':>12} {'Sharpe':>7} {'MaxDD':>8}")
    print(header)
    print('-' * 100)

    total_pnl_all = 0
    for s in summaries:
        total_pnl_all += s['total_pnl']
        print(f"{s['period']:<18} {s['regime']:<8} {s['total_signals']:>8} {s['total_trades']:>7} "
              f"{s['win_rate']:>5.1f}% {s['total_return']:>+9.2f}% "
              f"${s['total_pnl']:>+11,.0f} {s['sharpe']:>7.2f} {s['max_dd']:>+7.2f}%")

    print('-' * 100)
    total_return = total_pnl_all / args.capital * 100
    print(f"{'TOTAL':<18} {'':8} {'':>8} {len(all_trades):>7} "
          f"{'':>6} {total_return:>+9.2f}% "
          f"${total_pnl_all:>+11,.0f}")
    print()

    # ── Per-period CSVs ───────────────────────────────────────────────────
    for s in summaries:
        period_csv = _OUTPUT_DIR / f"health_backtest_{s['period']}.csv"
        period_trades = [t for t in all_trades if t['period'] == s['period']]
        with open(period_csv, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=csv_columns)
            writer.writeheader()
            for t in period_trades:
                writer.writerow({k: t.get(k, '') for k in csv_columns})
        print(f"  {s['period']:18s} → {period_csv}  ({len(period_trades)} trades)")

    # ── Risk analysis per period ──────────────────────────────────────────
    print(f"\n{'='*100}")
    print(f"  RISK ANALYSIS — Winners vs Losers by regime")
    print(f"{'='*100}")
    for s in summaries:
        trades = s['trades']
        if not trades:
            continue
        winners = [t for t in trades if t['revenue_usd'] > 0]
        losers  = [t for t in trades if t['revenue_usd'] <= 0]
        avg_win  = sum(t['revenue_usd'] for t in winners) / len(winners) if winners else 0
        avg_loss = sum(t['revenue_usd'] for t in losers)  / len(losers)  if losers else 0
        avg_win_pct = sum(t['revenue_pct'] for t in winners) / len(winners) if winners else 0
        avg_loss_pct = sum(t['revenue_pct'] for t in losers) / len(losers) if losers else 0
        avg_hold_w = sum(t['hold_days'] for t in winners) / len(winners) if winners else 0
        avg_hold_l = sum(t['hold_days'] for t in losers) / len(losers) if losers else 0
        expectancy = (avg_win * len(winners) + avg_loss * len(losers)) / len(trades)

        print(f"\n  {s['period']} ({s['regime']}):")
        print(f"    Avg Win:  ${avg_win:+,.0f} ({avg_win_pct:+.1f}%)  held {avg_hold_w:.0f}d")
        print(f"    Avg Loss: ${avg_loss:+,.0f} ({avg_loss_pct:+.1f}%)  held {avg_hold_l:.0f}d")
        print(f"    Expectancy per trade: ${expectancy:+,.0f}")
        print(f"    Win/Loss ratio: {abs(avg_win/avg_loss):.2f}" if avg_loss != 0 else "")

    print()


if __name__ == '__main__':
    asyncio.run(main())
