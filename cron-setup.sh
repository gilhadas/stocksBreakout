#!/bin/bash

# Cron Setup Script for Breakout Scanner
# This script helps you set up automated scanning tasks

echo "=================================="
echo "Breakout Scanner - Cron Setup"
echo "=================================="
echo ""

# Get the script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PYTHON_PATH=$(which python3)

echo "Script location: $SCRIPT_DIR"
echo "Python path: $PYTHON_PATH"
echo ""

# Create cron job examples
CRON_FILE="$SCRIPT_DIR/cron_jobs.txt"

cat > "$CRON_FILE" << 'EOF'
# Breakout Scanner Cron Jobs
# Add these to your crontab with: crontab -e
#
# Note: Make sure to update paths to match your installation
# Format: minute hour day month weekday command

# ============================================
# LONG-TERM POSITION TRADING (Weekly scans)
# ============================================

# Every Monday at 9:00 AM - Long-term breakout scan
0 9 * * 1 cd /path/to/scanner && /usr/bin/python3 breakout_scanner.py watchlist.txt --mode longterm --cron --notify >> scanner_output/logs/cron_longterm.log 2>&1

# Every Monday at 9:15 AM - Long-term exit evaluation
15 9 * * 1 cd /path/to/scanner && /usr/bin/python3 breakout_scanner.py watchlist.txt --mode longterm --exit-file positions_longterm.csv --cron --notify >> scanner_output/logs/cron_longterm_exit.log 2>&1


# ============================================
# SWING TRADING (Daily scans)
# ============================================

# Every weekday at 9:35 AM (after market open) - Swing breakout scan
35 9 * * 1-5 cd /path/to/scanner && /usr/bin/python3 breakout_scanner.py watchlist.txt --mode swing --cron --notify >> scanner_output/logs/cron_swing.log 2>&1

# Every weekday at 3:45 PM (before market close) - Swing exit evaluation
45 15 * * 1-5 cd /path/to/scanner && /usr/bin/python3 breakout_scanner.py watchlist.txt --mode swing --exit-file positions_swing.csv --cron --notify >> scanner_output/logs/cron_swing_exit.log 2>&1

# Every weekday at 4:30 PM (after market close) - Combined swing scan + exit
30 16 * * 1-5 cd /path/to/scanner && /usr/bin/python3 breakout_scanner.py watchlist.txt --mode swing --exit-file positions_swing.csv --both --cron --notify >> scanner_output/logs/cron_swing_combined.log 2>&1


# ============================================
# DAY TRADING (Intraday scans)
# ============================================

# Every weekday at 9:35 AM - Day trade morning scan
35 9 * * 1-5 cd /path/to/scanner && /usr/bin/python3 breakout_scanner.py input/watchlist.txt --mode daytrade --cron --notify >> scanner_output/logs/cron_daytrade_morning.log 2>&1

# Every weekday at 10:00 AM - Day trade mid-morning scan
0 10 * * 1-5 cd /path/to/scanner && /usr/bin/python3 breakout_scanner.py input/watchlist.txt --mode daytrade --cron --notify >> scanner_output/logs/cron_daytrade_mid.log 2>&1

# Every weekday at 2:00 PM - Day trade afternoon scan
0 14 * * 1-5 cd /path/to/scanner && /usr/bin/python3 breakout_scanner.py input/watchlist.txt --mode daytrade --cron --notify >> scanner_output/logs/cron_daytrade_afternoon.log 2>&1

# Every weekday at 3:30 PM - Day trade exit check
30 15 * * 1-5 cd /path/to/scanner && /usr/bin/python3 breakout_scanner.py input/watchlist.txt --mode daytrade --exit-file positions_daytrade.csv --cron --notify >> scanner_output/logs/cron_daytrade_exit.log 2>&1


# ============================================
# SCALPING (Not recommended for cron)
# ============================================
# Scalping requires real-time execution and monitoring
# Not suitable for scheduled cron jobs
# Use manual execution or build a real-time monitoring system


# ============================================
# MAINTENANCE TASKS
# ============================================

# Every Sunday at 11:00 PM - Clean old logs (keep last 30 days)
0 23 * * 0 find /path/to/scanner/scanner_output/logs -name "*.log" -mtime +30 -delete

# Every Sunday at 11:05 PM - Archive old results (keep last 90 days)
5 23 * * 0 find /path/to/scanner/scanner_output/signals -name "*.csv" -mtime +90 -delete
5 23 * * 0 find /path/to/scanner/scanner_output/exits -name "*.csv" -mtime +90 -delete
5 23 * * 0 find /path/to/scanner/scanner_output/rejections -name "*.csv" -mtime +90 -delete

EOF

echo "Created cron job examples in: $CRON_FILE"
echo ""
echo "To set up cron jobs:"
echo "1. Edit $CRON_FILE"
echo "2. Replace '/path/to/scanner' with: $SCRIPT_DIR"
echo "3. Replace '/usr/bin/python3' with: $PYTHON_PATH"
echo "4. Choose which jobs you want to run"
echo "5. Add them to your crontab:"
echo "   crontab -e"
echo ""
echo "Example for swing trading (daily at 9:35 AM):"
echo "35 9 * * 1-5 cd $SCRIPT_DIR && $PYTHON_PATH breakout_scanner.py watchlist.txt --mode swing --cron --notify"
echo ""

# Create a helper script for testing
TEST_SCRIPT="$SCRIPT_DIR/test_cron.sh"
cat > "$TEST_SCRIPT" << EOF
#!/bin/bash
# Test script to verify cron job will work
cd "$SCRIPT_DIR"
"$PYTHON_PATH" breakout_scanner.py watchlist.txt --mode swing --cron
EOF

chmod +x "$TEST_SCRIPT"

echo "Created test script: $TEST_SCRIPT"
echo "Run it to test your cron setup: ./$TEST_SCRIPT"
echo ""
echo "Done!"
