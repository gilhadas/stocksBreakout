#!/usr/bin/env python3
"""
Skill: scan
Run breakout signal scans with intelligent defaults.

Usage:
  python skills/scan.py [watchlist] --mode swing --notify
  python skills/scan.py input/optimizer_watch.txt --mode daytrade --sentiment
"""

import sys
import argparse
import subprocess
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Run breakout signal scans")

    # Positional: watchlist
    parser.add_argument(
        "watchlist",
        nargs="?",
        default="input/S&P_500.txt",
        help="Watchlist file path (default: input/S&P_500.txt)"
    )

    # Options
    parser.add_argument(
        "--mode",
        choices=["swing", "daytrade", "longterm", "scalping"],
        default="swing",
        help="Trading mode (default: swing)"
    )

    parser.add_argument(
        "--premium",
        action="store_true",
        help="Export only PREMIUM/GOLD signals"
    )

    parser.add_argument(
        "--sentiment",
        action="store_true",
        help="Enrich with FinBERT sentiment data"
    )

    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send Discord/Email/Telegram notifications"
    )

    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use yfinance (mock mode, no IB needed)"
    )

    args = parser.parse_args()

    # Build breakout_scanner.py command
    cmd = ["python", "breakout_scanner.py", args.watchlist, "--mode", args.mode]

    if args.mock:
        cmd.append("--mock")
    if args.sentiment:
        cmd.append("--sentiment")
    if args.notify:
        cmd.append("--notify")
    if args.premium:
        cmd.append("--export-premium")
        cmd.append(f"scanner_output/lists/premium_{args.mode}_export.txt")

    print(f"🚀 Running scan: {' '.join(cmd)}")
    print(f"Mode: {args.mode} | Watchlist: {args.watchlist}")
    print(f"Sentiment: {args.sentiment} | Notify: {args.notify} | Premium-only: {args.premium}")
    print("-" * 70)

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
