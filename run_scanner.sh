#!/bin/bash

# --- 1. Path Detection ---
# Check if the WSL/Windows path exists
if [[ -d "/mnt/c/Users/User/Desktop/Develop/stocksBreakout" ]]; then
    BASE_DIR="/mnt/c/Users/User/Desktop/Develop/stocksBreakout"
else
    # Default to Mac Path
    BASE_DIR="$HOME/documents/github/stocksbreakout"
fi

# --- 2. Venv Detection ---
# WSL/Mac usually use 'bin/python3', Windows-created venvs use 'Scripts/python.exe'
if [[ -f "$BASE_DIR/venv/bin/python3" ]]; then
    PYTHON_BIN="$BASE_DIR/venv/bin/python3"
elif [[ -f "$BASE_DIR/venv/Scripts/python.exe" ]]; then
    PYTHON_BIN="$BASE_DIR/venv/Scripts/python.exe"
else
    PYTHON_BIN="python3"
fi

# --- 3. Setup ---
cd "$BASE_DIR" || exit
mkdir -p "$BASE_DIR/scanner_output/logs"

# --- 4. Parse --logName (shell-only flag, stripped before passing to Python)
# Usage: run_scanner.sh <script_or_watchlist> [args...] --logName cron_swing
# Builds absolute log path: $BASE_DIR/scanner_output/logs/<logName>.log
# Falls back to cron_run.log if --logName is omitted.
LOG_NAME=""
FILTERED_ARGS=()
skip_next=false
for arg in "$@"; do
    if $skip_next; then
        LOG_NAME="$arg"
        skip_next=false
    elif [[ "$arg" == "--logName" ]]; then
        skip_next=true
    else
        FILTERED_ARGS+=("$arg")
    fi
done

if [[ -n "$LOG_NAME" ]]; then
    LOG_FILE="$BASE_DIR/scanner_output/logs/${LOG_NAME}.log"
else
    LOG_FILE="$BASE_DIR/scanner_output/logs/cron_run.log"
fi

# If first arg ends in .py → run it directly (monitor_watch.py, validate_signals.py, …)
# Otherwise → pass all args to breakout_scanner.py (watchlist scans)
FIRST_ARG="${FILTERED_ARGS[0]:-}"
if [[ "$FIRST_ARG" == *.py ]]; then
    $PYTHON_BIN "${FILTERED_ARGS[@]}" >> "$LOG_FILE" 2>&1
else
    $PYTHON_BIN breakout_scanner.py "${FILTERED_ARGS[@]}" >> "$LOG_FILE" 2>&1
fi
