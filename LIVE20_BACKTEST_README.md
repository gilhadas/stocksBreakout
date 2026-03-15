# Live20 Backtest System

Advanced backtest for the Live20 forecast system with trailing stop loss simulation.

## Overview

The Live20 backtest system replicates real trading using:
- **Historical forecast CSV** (e.g., `live20_7_3_26.csv` from March 7, 2026)
- **1-minute bar data** from forecast date to present (5 days of history)
- **Entry signals** on S/R level breakouts (±1% tolerance)
- **Exit signals** on ATR-based trailing stops (2.0 ATR multiplier, matching `breakout_scanner.py`)
- **Position sizing** at 5% per trade for portfolio simulation

## Features

### Trade Simulation
- ✅ **Long positions**: Enter when price breaks above S/R level
- ✅ **Short positions**: Enter when price breaks below S/R level  
- ✅ **Prediction labels**: Marks trades as "PREDICTED" (matches forecast) or "SURPRISE" (opposite direction)
- ✅ **Trailing stops**: ATR-based (14-period), 2.0x multiplier
- ✅ **Auto-exit**: EOD close-out for any open positions

### Reporting
- **CSV report**: `scanner_output/live20_backtest_report.csv`
  - Per-trade metrics: entry/exit times, prices, direction, predicted/surprise, P&L, %
- **Stats file**: `scanner_output/live20_backtest_stats.txt`
  - Portfolio P&L, win rate, profit factor, prediction accuracy breakdown

## Usage

```bash
# Run backtest for 3/7-3/15 forecast
python live20_backtest.py --file input/live20_7_3_26.csv

# Specify end date (default: today)
python live20_backtest.py --file input/live20_7_3_26.csv --end-date 2026-03-20
```

## Output Example

```
======================================================================
Live20 Backtest: 2026-03-07 → 2026-03-15
Forecast file: live20_7_3_26.csv
Forecast records: 40
======================================================================

[Entry signals on 3/9 at 09:30]
  [03/09 09:30] ✓ RKT DOWN @ $14.33 (SR $14.48, ATR N/A)  ← PREDICTED (forecast was bearish)
  [03/09 10:11] ✗ SPY DOWN @ $662.58 (SR $669.38, ATR 0.87) ← SURPRISE (forecast was bullish, but went down)

======================================================================
SUMMARY
======================================================================
Closed trades: 40
Winners: 20
Losers: 20
Win rate: 50.0%
Profit factor: 0.69

Capital: $10000.00 → $9998.00
P&L: $-2.00
Return: -0.02%

Prediction accuracy:
  Predicted (2): 1/2 correct (50%)
  Surprises (38): 19/38 correct (50%)
```

## Parameters

Edit in `live20_backtest.py`:
- `POSITION_SIZE_PCT = 0.05` — Risk per trade (5% of capital)
- `ATR_PERIOD = 14` — Period for ATR calculation
- `ATR_TRAILING_MULT = 2.0` — Trailing stop distance (2.0 ATR)
- `INITIAL_CAPITAL = 10000` — Starting portfolio equity

## How Entry Signals Work

1. **Entry threshold**: Price must break S/R level by >1.0%
   - Bullish forecast + price >= SR * 1.01 → LONG entry → "PREDICTED" if price goes UP, else "SURPRISE"
   - Bearish forecast + price <= SR * 0.99 → SHORT entry → "PREDICTED" if price goes DOWN, else "SURPRISE"

2. **Trailing stop**: ATR-based distance updated each bar
   - Initial stop: entry_price ± (ATR * 2.0)
   - Moves UP for longs, DOWN for shorts (never tightens)
   - Closes position when hit

3. **Exit reasons**:
   - `stop_loss`: Hit ATR-based trailing stop
   - `eod`: Closed at end-of-day

## Accuracy Breakdown

Reports accuracy separately for:
- **Predicted trades**: Matches forecast direction (bullish=UP, bearish=DOWN)
- **Surprise trades**: Opposite to forecast (bullish forecast but goes DOWN, etc.)

This helps identify:
- Forecast quality (% correct when prediction matches)
- Fade potential (% correct when prediction is wrong — edge in reversals?)

## CSV Report Columns

| Column | Example | Notes |
|--------|---------|-------|
| ticker | ACHR | Stock symbol |
| entry_time | 2026-03-09 09:30:00 | When entry signal fired |
| entry_price | $6.16 | Price at entry |
| direction | DOWN | UP (long) or DOWN (short) |
| predicted | SURPRISE | "PREDICTED" or "SURPRISE" |
| exit_time | 2026-03-13 15:59:00 | When position closed |
| exit_price | $6.03 | Exit price |
| exit_reason | eod | Why: stop_loss, tp, or eod |
| P&L | $0.01 | Dollar profit/loss |
| P&L % | 2.2% | Percentage return |

