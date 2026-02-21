#!/bin/bash
# Path: /mnt/c/Users/User/Desktop/Develop/stocksBreakout/run_scanner.sh

# 1. Define Paths
WSL_PATH="/mnt/c/Users/User/Desktop/Develop/stocksBreakout"
if [ -d "$WSL_PATH" ]; then
    PROJECT_ROOT="$WSL_PATH"
else
    # Fallback to Linux home
    PROJECT_ROOT="$HOME/stocksbreakout"
fi

PYTHON_BIN="$PROJECT_ROOT/venv/bin/python3"
HC_BASE="https://hc-ping.com"

# 2. Execute based on arguments passed from Cron
cd "$PROJECT_ROOT" || exit
$PYTHON_BIN "$@"