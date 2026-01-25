#!/bin/bash
# Test script to verify cron job will work
cd "/Users/gilhadas/Documents/GitHub/stocksBreakout"
"/Users/gilhadas/Documents/GitHub/stocksBreakout/venv/bin/python3" breakout_scanner.py input/watchlist.txt --mode swing --cron
