#!/usr/bin/env python3
"""
Skill: monitor
Monitor open positions for price drops and stop loss alerts.

Usage:
  python skills/monitor.py --once
  python skills/monitor.py --interval 5 --once false
  python skills/monitor.py --positions scanner_output/lists/positions_swing_mock.csv
"""

import sys
import argparse
import subprocess

def main():
    parser = argparse.ArgumentParser(description="Monitor open positions")

    parser.add_argument(
        "--positions",
        default="portfolio",
        help="Position source: 'portfolio' (auto_portfolio.json) or CSV file path(s)"
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=15,
        help="Check interval in minutes (default: 15)"
    )

    parser.add_argument(
        "--once",
        type=lambda x: x.lower() == "true",
        default=True,
        help="Run once (true) or continuous (false) — default: true"
    )

    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send Discord alerts on status changes"
    )

    args = parser.parse_args()

    # Build monitor_watch.py command
    cmd = ["python", "monitor_watch.py"]

    if args.positions != "portfolio":
        cmd.extend(["--file", args.positions])

    if not args.once:
        cmd.extend(["--interval", str(args.interval)])

    if args.notify:
        cmd.append("--notify")

    print(f"📍 Monitoring positions")
    print(f"Source: {args.positions}")
    print(f"Interval: {args.interval} min (ignored if --once=true)")
    print(f"Run mode: {'Once' if args.once else 'Continuous'}")
    print(f"Notifications: {args.notify}")
    print("-" * 70)

    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
