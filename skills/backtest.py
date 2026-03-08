#!/usr/bin/env python3
"""
Skill: backtest
Run strategy backtests with comparison support.

Usage:
  python skills/backtest.py --period 2024 --compare configs
  python skills/backtest.py --period 2025-01-01:2025-06-30 --mode swing
"""

import sys
import argparse
import subprocess
from datetime import datetime

def parse_period(period_str):
    """Parse period string into start and end dates."""
    if period_str == "2024":
        return "2024-01-01", "2024-12-31"
    elif period_str == "2025":
        return "2025-01-01", "2025-12-31"
    elif period_str == "last-month":
        # Last calendar month
        today = datetime.now()
        first_of_this = today.replace(day=1)
        last_of_last = first_of_this - pd.Timedelta(days=1)
        first_of_last = last_of_last.replace(day=1)
        return first_of_last.strftime("%Y-%m-%d"), last_of_last.strftime("%Y-%m-%d")
    elif ":" in period_str:
        return period_str.split(":")
    else:
        raise ValueError(f"Invalid period: {period_str}")

def main():
    parser = argparse.ArgumentParser(description="Run strategy backtests")

    parser.add_argument(
        "--period",
        default="2024",
        help="Date range (2024, 2025, last-month, YYYY-MM-DD:YYYY-MM-DD)"
    )

    parser.add_argument(
        "--mode",
        choices=["swing", "daytrade", "longterm"],
        default="swing",
        help="Trading mode (default: swing)"
    )

    parser.add_argument(
        "--compare",
        choices=["v1-vs-v2", "configs", "modes"],
        help="What to compare (v1-vs-v2, configs, modes)"
    )

    parser.add_argument(
        "--optimize",
        choices=["rsi_overbought", "atr_mult", "consolidation"],
        help="Parameter to optimize (runs weight_optimizer.py instead)"
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Limit symbols (for faster testing)"
    )

    args = parser.parse_args()

    # Parse period
    try:
        start_date, end_date = parse_period(args.period)
    except Exception as e:
        print(f"Error parsing period: {e}")
        sys.exit(1)

    print(f"📊 Running backtest")
    print(f"Period: {start_date} to {end_date}")
    print(f"Mode: {args.mode}")
    if args.compare:
        print(f"Compare: {args.compare}")
    if args.optimize:
        print(f"Optimize: {args.optimize}")
    print("-" * 70)

    # Route to appropriate script
    if args.optimize:
        # Use weight optimizer
        cmd = [
            "python", "weight_optimizer.py",
            "--trials", "100",  # Reduced from 300 for speed
            "--symbols", "scanner_output/lists/optimizer_watch.txt"
        ]
        print(f"🔧 Running parameter optimization: {args.optimize}")
    else:
        # Use enhanced backtest
        cmd = [
            "python", "enhanced_backtest.py",
            "--start", start_date,
            "--end", end_date,
            "--limit", str(args.limit),
            "--watchlist", "input/ALL.txt"
        ]

        if args.compare:
            if args.compare == "configs":
                cmd.extend(["--versions", "v1,v8,v9,v10,v12"])
            elif args.compare == "modes":
                cmd.extend(["--mode", args.mode])

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
