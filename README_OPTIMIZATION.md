# Optimization Strategy Tool

This tool helps you find the best combination of parameters (Volume Threshold, ATR Multiplier, Lookback Period) for your trading strategy.

## Prerequisites

- Python 3.14 (or compatible) environment
- Dependencies installed (`requests`, `pandas`, `yfinance`, `ib_insync`)

## Usage

Run the optimization script:

```bash
python3 optimize_strategy.py
```

## Configuration

You can modify the following parameters in `optimize_strategy.py` (lines 140+):

- `param_grid`: Define ranges for optimization
  - `vol_thresh`: [1.3, 1.5, 2.0]
  - `atr_mult`: [0.5, 1.0, 1.5]
  - `lookback`: [10, 15, 20]
- `watchlist`: Path to watchlist file (default: `input/watchlist2.txt`)
- `start_date` / `end_date`: Simulation period

## Output

The script will:
1. Iterate through all parameter combinations.
2. Run a historical simulation for each combination using `yfinance` data (no IB connection required).
3. Print results in a table showing Return, Win Rate, and Number of Trades.
4. Save the full results to a CSV file (e.g., `optimization_results_YYYYMMDD_HHMMSS.csv`).
5. Highlight the Top 5 configurations by Total Return and by Safety (Max Drawdown).

## Troubleshooting

- If you see `HTTP Error 404` for certain symbols (like `BRK B`), the script will log the error but continue.
- Ensure your internet connection is stable for `yfinance` data fetching.
- The first run for each symbol caches the data, so subsequent iterations are faster.
