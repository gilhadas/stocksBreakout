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
else
    PYTHON_BIN="$BASE_DIR/venv/Scripts/python.exe"
fi

# --- 3. Execution ---
cd "$BASE_DIR" || exit


# Create log directory if it doesn't exist
mkdir -p "scanner_output/logs"

exec $PYTHON_BIN breakout_scanner.py "$@"
# Use the filename (e.g., MAGS) to create the log name
#LOG_FILE="scanner_output/logs/cron_$(basename "$1" .txt).log"

# Run it!
#$PYTHON_BIN breakout_scanner.py "$@" >> "$LOG_FILE" 2>&1