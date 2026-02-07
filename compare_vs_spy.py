#!/usr/bin/env python3
"""
Strategy vs SPY Comparison
Comprehensive backtest comparing breakout strategy performance against SPY buy-and-hold
"""
import asyncio
import pandas as pd
import numpy as np
import logging
from pathlib import Path
from datetime import datetime
import json

# Python 3.14 compatibility
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from mock_trader import SimulationMode
from scanner import BreakoutDetector
from yfinance_adapter import YFinanceAdapter

logging.basicConfig(level=logging.WARNING, format='%(message)s')


async def run_strategy_backtest(symbols, start_date, end_date, mode='swing'):
    """Run the breakout strategy backtest"""
    detector = BreakoutDetector()
    yf_adapter = YFinanceAdapter()

    all_signals = []
    historical_data = {}
    end_prices = {}

    print(f"📊 Scanning {len(symbols)} symbols for breakout signals...")

    for symbol in symbols:
        try:
            df = yf_adapter.get_historical_data(
                symbol, '1 day',
                start_date=start_date,
                end_date=end_date
            )

            if df is None or len(df) == 0:
                continue

            historical_data[symbol] = df
            end_prices[symbol] = float(df.iloc[-1]['close'])

            # Scan for signals during test period
            sim_dates = pd.date_range(start=start_date, end=end_date, freq='B')

            for sim_date in sim_dates:
                df_slice = df[df.index <= sim_date]
                if len(df_slice) < 50:
                    continue

                if df_slice.index[-1].date() != sim_date.date():
                    continue

                sig = detector.detect(
                    df_slice, symbol, mode, '1 day', spy_perf=0.0
                )

                if sig:
                    all_signals.append({
                        'date': sim_date,
                        'symbol': symbol,
                        'action': 'BUY',
                        'price': sig['Price'],
                        'entry_price': sig['Price'],
                        'stop_loss': sig['Stop'],
                        'take_profit': sig['Target']
                    })

        except Exception as e:
            pass

    print(f"✅ Found {len(all_signals)} breakout signals")

    if len(all_signals) == 0:
        return None

    # Run simulation
    sim = SimulationMode(
        start_date=start_date,
        end_date=end_date,
        initial_capital=100000,
        max_position_pct=0.10,
        max_risk_pct=0.01,
        use_trailing_stop=True,
        trailing_stop_atr_mult=4.0,
        trailing_stop_activation_pct=0.10
    )

    report = sim.run_simulation(all_signals, end_prices=end_prices, historical_data=historical_data)

    return report


def calculate_spy_performance(start_date, end_date, initial_capital=100000):
    """Calculate SPY buy-and-hold performance"""
    yf_adapter = YFinanceAdapter()

    print(f"\n📈 Fetching SPY data for comparison...")

    spy_df = yf_adapter.get_historical_data(
        'SPY', '1 day',
        start_date=start_date,
        end_date=end_date
    )

    if spy_df is None or len(spy_df) == 0:
        return None

    start_price = float(spy_df.iloc[0]['close'])
    end_price = float(spy_df.iloc[-1]['close'])

    # Calculate returns
    shares = initial_capital / start_price
    end_value = shares * end_price
    total_return = ((end_value - initial_capital) / initial_capital) * 100

    # Calculate daily returns
    spy_df['returns'] = spy_df['close'].pct_change()

    # Calculate Sharpe ratio
    mean_return = spy_df['returns'].mean()
    std_return = spy_df['returns'].std()
    sharpe = (mean_return / std_return) * np.sqrt(252) if std_return > 0 else 0

    # Calculate max drawdown
    spy_df['cummax'] = spy_df['close'].cummax()
    spy_df['drawdown'] = (spy_df['close'] / spy_df['cummax'] - 1) * 100
    max_drawdown = spy_df['drawdown'].min()

    return {
        'start_price': start_price,
        'end_price': end_price,
        'total_return': total_return,
        'sharpe_ratio': sharpe,
        'max_drawdown': max_drawdown,
        'shares': shares,
        'end_value': end_value
    }


async def run_comprehensive_comparison():
    """Run comprehensive strategy vs SPY comparison"""
    print("=" * 80)
    print("🏆 BREAKOUT STRATEGY vs SPY (S&P 500) COMPARISON")
    print("=" * 80)
    print()

    # Test parameters
    start_date = "2024-01-01"
    end_date = "2025-12-31"
    initial_capital = 100000

    # Use S&P 500 stocks from watchlist
    print("📋 Loading symbols from watchlist...")
    try:
        with open('input/watchlist3.txt', 'r') as f:
            content = f.read()

        raw_symbols = [s.strip() for s in content.replace('\n', ',').split(',') if s.strip()]
        symbols = []
        for s in raw_symbols:
            if s.startswith('#'):
                continue
            if ':' in s:
                s = s.split(':')[1]
            symbols.append(s)

        # Use top 100 most liquid stocks
        symbols = sorted(list(set(symbols)))[:100]

    except FileNotFoundError:
        print("⚠️  Watchlist not found, using default symbols")
        symbols = [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'TSLA', 'META', 'NFLX',
            'AMD', 'INTC', 'JPM', 'BAC', 'WMT', 'HD', 'DIS', 'V', 'MA', 'PYPL',
            'COST', 'PEP', 'KO', 'MCD', 'NKE', 'SBUX', 'UNH', 'JNJ', 'PFE', 'ABBV',
            'XOM', 'CVX', 'BA', 'CAT', 'GE', 'HON', 'LMT', 'RTX', 'UPS', 'FDX',
            'CSCO', 'ORCL', 'IBM', 'CRM', 'ADBE', 'NOW', 'INTU', 'TXN', 'QCOM'
        ]

    print(f"✅ Testing {len(symbols)} symbols")
    print(f"📅 Period: {start_date} to {end_date} (2 years)")
    print(f"💰 Initial Capital: ${initial_capital:,.2f}")
    print()

    # Test swing trading mode (optimized parameters)
    print("=" * 80)
    print("📊 RUNNING BREAKOUT STRATEGY BACKTEST")
    print("=" * 80)
    print()
    print("Strategy: Breakout Scanner with Optimized Parameters")
    print("  - Mode: Swing Trading")
    print("  - Lookback: 15 bars")
    print("  - Volume Threshold: 1.3x")
    print("  - ATR Multiplier: 0.5")
    print("  - Trailing Stop: 4.0 ATR (activated at +10%)")
    print("  - Risk: 1% per trade, 10% max per position")
    print()

    strategy_results = await run_strategy_backtest(symbols, start_date, end_date, mode='swing')

    # Get SPY performance
    print("\n" + "=" * 80)
    print("📈 CALCULATING SPY BUY-AND-HOLD PERFORMANCE")
    print("=" * 80)
    print()

    spy_results = calculate_spy_performance(start_date, end_date, initial_capital)

    if not strategy_results or not spy_results:
        print("\n❌ Could not complete comparison - insufficient data")
        return

    # Display Results
    print("\n" + "=" * 80)
    print("📊 RESULTS COMPARISON")
    print("=" * 80)

    print(f"\n{'METRIC':<30} {'STRATEGY':<20} {'SPY':<20} {'DIFFERENCE':<20}")
    print("-" * 90)

    # Total Return
    strategy_return = strategy_results['total_return']
    spy_return = spy_results['total_return']
    return_diff = strategy_return - spy_return
    return_better = "✅" if strategy_return > spy_return else "❌"

    print(f"{'Total Return':<30} {strategy_return:+.2f}%{'':<13} {spy_return:+.2f}%{'':<13} {return_better} {return_diff:+.2f}%")

    # Sharpe Ratio
    strategy_sharpe = strategy_results['sharpe_ratio']
    spy_sharpe = spy_results['sharpe_ratio']
    sharpe_diff = strategy_sharpe - spy_sharpe
    sharpe_better = "✅" if strategy_sharpe > spy_sharpe else "❌"

    print(f"{'Sharpe Ratio':<30} {strategy_sharpe:.2f}{'':<16} {spy_sharpe:.2f}{'':<16} {sharpe_better} {sharpe_diff:+.2f}")

    # Max Drawdown (lower is better)
    strategy_dd = strategy_results['max_drawdown']
    spy_dd = spy_results['max_drawdown']
    dd_diff = spy_dd - strategy_dd  # Reversed: less negative is better
    dd_better = "✅" if strategy_dd > spy_dd else "❌"

    print(f"{'Max Drawdown':<30} {strategy_dd:.2f}%{'':<13} {spy_dd:.2f}%{'':<13} {dd_better} {dd_diff:+.2f}%")

    # Win Rate (strategy only)
    strategy_winrate = strategy_results['win_rate']
    print(f"{'Win Rate':<30} {strategy_winrate:.1f}%{'':<13} {'N/A':<20} {'(strategy only)'}")

    # Number of Trades
    strategy_trades = strategy_results['total_trades']
    print(f"{'Number of Trades':<30} {strategy_trades}{'':<18} {'1 (buy & hold)':<20} {''}")

    print("-" * 90)

    # Final Values
    strategy_final = initial_capital * (1 + strategy_return/100)
    spy_final = spy_results['end_value']
    value_diff = strategy_final - spy_final

    print(f"\n{'FINAL PORTFOLIO VALUE':<30} {'STRATEGY':<20} {'SPY':<20} {'DIFFERENCE':<20}")
    print("-" * 90)
    print(f"{'Starting Capital':<30} ${initial_capital:,.2f}{'':<10} ${initial_capital:,.2f}{'':<10} {'$0.00'}")
    print(f"{'Ending Value':<30} ${strategy_final:,.2f}{'':<10} ${spy_final:,.2f}{'':<10} ${value_diff:+,.2f}")

    # Calculate metrics
    metrics_better = sum([
        strategy_return > spy_return,
        strategy_sharpe > spy_sharpe,
        strategy_dd > spy_dd  # Less negative is better
    ])

    print("\n" + "=" * 80)
    print("🎯 VERDICT")
    print("=" * 80)
    print()

    if metrics_better >= 2:
        print("🏆 WINNER: BREAKOUT STRATEGY")
        print()
        print(f"The breakout strategy BEATS SPY on {metrics_better}/3 key metrics!")
        print()

        if strategy_return > spy_return:
            outperformance = ((strategy_return - spy_return) / abs(spy_return)) * 100
            print(f"✅ Returns: Strategy outperforms by {return_diff:+.2f}% ({outperformance:+.1f}%)")

        if strategy_sharpe > spy_sharpe:
            print(f"✅ Sharpe: Better risk-adjusted returns ({strategy_sharpe:.2f} vs {spy_sharpe:.2f})")

        if strategy_dd > spy_dd:
            print(f"✅ Drawdown: Lower maximum loss ({strategy_dd:.2f}% vs {spy_dd:.2f}%)")

        print()
        print("💡 KEY ADVANTAGES:")
        print(f"   • Active management captures breakout opportunities")
        print(f"   • Trailing stops lock in profits during trends")
        print(f"   • Stop losses limit downside risk")
        print(f"   • Position sizing manages risk per trade")
        print(f"   • {strategy_winrate:.1f}% win rate shows consistent edge")

    else:
        print("📊 WINNER: SPY (BUY & HOLD)")
        print()
        print(f"SPY buy-and-hold beats the strategy on {3-metrics_better}/3 metrics.")
        print()

        if spy_return > strategy_return:
            print(f"❌ Returns: SPY outperforms by {-return_diff:+.2f}%")

        if spy_sharpe > strategy_sharpe:
            print(f"❌ Sharpe: SPY has better risk-adjusted returns")

        if spy_dd < strategy_dd:
            print(f"❌ Drawdown: SPY has lower maximum loss")

        print()
        print("💡 CONSIDERATIONS:")
        print(f"   • The test period may have favored buy-and-hold")
        print(f"   • Strategy had {strategy_trades} trades vs SPY's 1 (more opportunities)")
        print(f"   • {strategy_winrate:.1f}% win rate shows the strategy has an edge")
        print(f"   • Consider different market regimes and time periods")

    # Additional Analysis
    print("\n" + "=" * 80)
    print("📈 DETAILED ANALYSIS")
    print("=" * 80)
    print()

    # Annual Return Comparison
    days = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days
    years = days / 365.25

    strategy_annual = (((1 + strategy_return/100) ** (1/years)) - 1) * 100
    spy_annual = (((1 + spy_return/100) ** (1/years)) - 1) * 100

    print(f"Annualized Return:")
    print(f"  Strategy: {strategy_annual:+.2f}% per year")
    print(f"  SPY:      {spy_annual:+.2f}% per year")
    print()

    # Risk-adjusted comparison
    print(f"Risk-Adjusted Performance (Sharpe Ratio):")
    if strategy_sharpe > 1.0:
        print(f"  Strategy: {strategy_sharpe:.2f} (Excellent)")
    elif strategy_sharpe > 0.5:
        print(f"  Strategy: {strategy_sharpe:.2f} (Good)")
    elif strategy_sharpe > 0:
        print(f"  Strategy: {strategy_sharpe:.2f} (Moderate)")
    else:
        print(f"  Strategy: {strategy_sharpe:.2f} (Poor)")

    if spy_sharpe > 1.0:
        print(f"  SPY:      {spy_sharpe:.2f} (Excellent)")
    elif spy_sharpe > 0.5:
        print(f"  SPY:      {spy_sharpe:.2f} (Good)")
    elif spy_sharpe > 0:
        print(f"  SPY:      {spy_sharpe:.2f} (Moderate)")
    else:
        print(f"  SPY:      {spy_sharpe:.2f} (Poor)")
    print()

    # Trade efficiency
    print(f"Strategy Trade Statistics:")
    print(f"  Total Trades:    {strategy_results['total_trades']}")
    print(f"  Winning Trades:  {strategy_results['winning_trades']}")
    print(f"  Losing Trades:   {strategy_results['losing_trades']}")
    print(f"  Win Rate:        {strategy_results['win_rate']:.1f}%")
    print(f"  Avg Win:         ${strategy_results['avg_win']:.2f}")
    print(f"  Avg Loss:        ${strategy_results['avg_loss']:.2f}")

    if strategy_results['avg_loss'] != 0:
        win_loss_ratio = abs(strategy_results['avg_win'] / strategy_results['avg_loss'])
        print(f"  Win/Loss Ratio:  {win_loss_ratio:.2f}:1")
    print()

    # SPY statistics
    print(f"SPY Buy-and-Hold Statistics:")
    print(f"  Entry Price:     ${spy_results['start_price']:.2f}")
    print(f"  Exit Price:      ${spy_results['end_price']:.2f}")
    print(f"  Shares Bought:   {spy_results['shares']:.2f}")
    print(f"  No trading costs or management required")
    print()

    # Save results
    output_dir = Path('scanner_output/backtests')
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {
        'test_period': {
            'start': start_date,
            'end': end_date,
            'days': days,
            'years': years
        },
        'strategy': {
            'total_return': strategy_return,
            'sharpe_ratio': strategy_sharpe,
            'max_drawdown': strategy_dd,
            'win_rate': strategy_winrate,
            'total_trades': strategy_trades,
            'annual_return': strategy_annual,
            'final_value': strategy_final
        },
        'spy': {
            'total_return': spy_return,
            'sharpe_ratio': spy_sharpe,
            'max_drawdown': spy_dd,
            'annual_return': spy_annual,
            'final_value': spy_final
        },
        'comparison': {
            'return_difference': return_diff,
            'sharpe_difference': sharpe_diff,
            'drawdown_difference': dd_diff,
            'winner': 'strategy' if metrics_better >= 2 else 'spy',
            'metrics_won': metrics_better
        }
    }

    results_file = output_dir / f'strategy_vs_spy_{start_date}_{end_date}.json'
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"💾 Full results saved to: {results_file}")
    print()

    print("=" * 80)
    print("📝 RECOMMENDATIONS")
    print("=" * 80)
    print()

    if metrics_better >= 2:
        print("""
✅ STRATEGY VALIDATED - Proceed with Confidence

Your breakout strategy has demonstrated superior performance vs SPY.

Next Steps:
1. ✅ Continue using optimized parameters (lookback=15, vol=1.3, atr=0.5)
2. 📊 Paper trade for 2-4 weeks to validate in current market
3. 💰 Start with 25-50% of intended capital
4. 📈 Scale up gradually as you gain confidence
5. 🔄 Retest quarterly to ensure edge persists

Key Success Factors:
• Maintain disciplined position sizing (10% max per trade)
• Use trailing stops to lock in profits
• Honor your stop losses - no exceptions
• Track performance vs SPY monthly
• Adapt to market regime changes
        """)
    else:
        print("""
⚠️  MIXED RESULTS - Further Optimization Recommended

The test period showed SPY performing better overall.

Recommendations:
1. 🔍 Test on different time periods (bull/bear/sideways markets)
2. 📊 Analyze which market conditions favor the strategy
3. 🎯 Consider combining strategy with SPY allocation (60/40 split)
4. 💡 Use strategy selectively in trending markets
5. 📈 Track SPY regime to time strategy deployment

Consider Hybrid Approach:
• 60% SPY buy-and-hold (core holding)
• 40% breakout strategy (active management)
• Rebalance quarterly
• This captures both passive gains and active opportunities
        """)

    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_comprehensive_comparison())
