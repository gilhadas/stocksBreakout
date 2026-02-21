#!/bin/bash
# Test script to verify cron job will work
PROJECT_ROOT=$( [ -d "$HOME/documents/github/stocksbreakout" ] && echo "$HOME/documents/github/stocksbreakout" || echo "$HOME/stocksbreakout" )
PYTHON_BIN=$PROJECT_ROOT/venv/bin/python3

cd $PROJECT_ROOT && $PYTHON_BIN breakout_scanner.py input/mags.txt --mode swing --mock --cron
