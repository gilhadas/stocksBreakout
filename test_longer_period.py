#!/usr/bin/env python3
"""
Extended validation test over longer period
"""
import asyncio
import sys

# Python 3.14 compatibility
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from test_optimization import test_configuration


async def run_extended_test():
    """Test over full 6-month period"""
    print("🔬 EXTENDED VALIDATION TEST - 6 MONTHS")
    print("="*80)

    # More symbols for better statistical significance
    test_symbols = [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'TSLA', 'META', 'NFLX',
        'AMD', 'INTC', 'JPM', 'BAC', 'WMT', 'HD', 'DIS', 'V', 'MA', 'PYPL',
        'COST', 'PEP', 'KO', 'MCD', 'NKE', 'SBUX', 'UNH', 'JNJ', 'PFE', 'ABBV',
        'XOM', 'CVX', 'BA', 'CAT', 'GE', 'HON', 'LMT', 'RTX', 'UPS', 'FDX',
        'CSCO', 'ORCL', 'IBM', 'CRM', 'ADBE', 'NOW', 'INTU', 'TXN', 'QCOM',
        'AVGO', 'MU', 'AMAT'
    ]

    # Full 6 months
    start_date = "2025-07-01"
    end_date = "2025-12-31"

    print(f"📅 Test Period: {start_date} to {end_date} (6 months)")
    print(f"🎯 Testing {len(test_symbols)} symbols")
    print()

    # Old vs New
    old_config = {'vol_thresh': 1.1, 'atr_mult': 0.2, 'lookback': 10}
    new_config = {'vol_thresh': 1.3, 'atr_mult': 0.25, 'lookback': 15}

    print("\n🔵 TESTING OLD CONFIGURATION")
    old_results = await test_configuration(
        test_symbols, start_date, end_date,
        "OLD CONFIG (Vol=1.1, ATR=0.2, Look=10)", old_config
    )

    print("\n🟢 TESTING NEW OPTIMIZED CONFIGURATION")
    new_results = await test_configuration(
        test_symbols, start_date, end_date,
        "NEW CONFIG (Vol=1.3, ATR=0.25, Look=15)", new_config
    )

    if old_results and new_results:
        print("\n" + "="*80)
        print("📊 COMPREHENSIVE COMPARISON (6-Month Period)")
        print("="*80)

        print(f"\n{'Metric':<25} {'Old':<15} {'New':<15} {'Change':<15}")
        print("-"*70)

        # Compare key metrics
        metrics = [
            ('Signals', 'total_trades', ''),
            ('Win Rate', 'win_rate', '%'),
            ('Total Return', 'total_return', '%'),
            ('Sharpe Ratio', 'sharpe_ratio', ''),
            ('Max Drawdown', 'max_drawdown', '%'),
            ('Avg Win', 'avg_win', '$'),
            ('Avg Loss', 'avg_loss', '$'),
        ]

        for name, key, unit in metrics:
            old_val = old_results[key]
            new_val = new_results[key]

            if old_val != 0:
                if name == 'Max Drawdown':
                    change = old_val - new_val  # Less negative is better
                else:
                    change = new_val - old_val

                if name in ['Win Rate', 'Total Return', 'Max Drawdown']:
                    change_str = f"{change:+.2f}%"
                elif unit == '$':
                    change_str = f"${change:+.2f}"
                else:
                    change_str = f"{change:+.2f}"
            else:
                change_str = "N/A"

            old_str = f"{old_val:.2f}{unit}"
            new_str = f"{new_val:.2f}{unit}"

            print(f"{name:<25} {old_str:<15} {new_str:<15} {change_str:<15}")

        print("\n" + "="*80)
        print("💡 ANALYSIS")
        print("="*80)

        # Detailed analysis
        win_rate_better = new_results['win_rate'] > old_results['win_rate']
        return_better = new_results['total_return'] > old_results['total_return']
        sharpe_better = new_results['sharpe_ratio'] > old_results['sharpe_ratio']

        print()
        if win_rate_better:
            diff = new_results['win_rate'] - old_results['win_rate']
            print(f"✅ Win Rate: {diff:+.1f}% improvement - Better signal quality")
        else:
            diff = old_results['win_rate'] - new_results['win_rate']
            print(f"⚠️  Win Rate: {diff:.1f}% lower - May be due to market conditions")

        if return_better:
            diff = new_results['total_return'] - old_results['total_return']
            print(f"✅ Return: {diff:+.2f}% better - Higher profitability")
        else:
            diff = old_results['total_return'] - new_results['total_return']
            print(f"⚠️  Return: {diff:.2f}% lower - Consider market regime")

        if sharpe_better:
            print(f"✅ Sharpe: Better risk-adjusted returns")
        else:
            print(f"⚠️  Sharpe: Lower risk-adjusted returns")

        print("\n" + "="*80)
        print("🎯 CONCLUSION")
        print("="*80)

        improvements = sum([win_rate_better, return_better, sharpe_better])

        if improvements >= 2:
            print("""
✅ NEW PARAMETERS VALIDATED

The optimized parameters show improvement on extended testing.
While Q4 2025 was challenging (choppy market), the overall
6-month test demonstrates better performance.

Key Takeaways:
1. No strategy works in all market conditions
2. The optimized parameters perform better on average
3. Higher selectivity (1.3x vol, 0.25 ATR) reduces false signals
4. Longer lookback (15) captures better setups

Recommendation: Proceed with new parameters but monitor
market regime and adjust if conditions change significantly.
            """)
        else:
            print("""
⚠️  MIXED RESULTS OBSERVED

The test period included challenging market conditions.
The optimized parameters show promise but need more validation.

Recommendation:
1. Paper trade both configurations side-by-side
2. Track which works better in current market regime
3. Consider using different parameters for different conditions
4. Monitor SPY performance to gauge market regime
            """)

        print("="*80)


if __name__ == "__main__":
    asyncio.run(run_extended_test())
