#!/bin/bash
# Test script to verify cron job will work

# Define the WSL path to your Windows Desktop folder
WSL_PATH="/mnt/c/Users/User/Desktop/Develop/stocksBreakout"

# Check if the WSL path exists; if not, fall back to the original logic
if [ -d "$WSL_PATH" ]; then
    PROJECT_ROOT="$WSL_PATH"
else
    PROJECT_ROOT=$( [ -d "$HOME/documents/github/stocksbreakout" ] && echo "$HOME/documents/github/stocksbreakout" || echo "$HOME/stocksbreakout" )
fi

PYTHON_BIN=$PROJECT_ROOT/venv/bin/python3

# Change directory and run
cd "$PROJECT_ROOT" && "$PYTHON_BIN" breakout_scanner.py input/mags.txt --mode swing --mock --cron