# How to Run the Strategy for Best Results

## Context

All V3 code is implemented and backtested across 3 market regimes (bearish 2022, bullish 2023-24, mixed 2024-25). This guide explains which scripts to run and which config to pick based on your goals.

## Available Scripts

| Script | Purpose | Runtime |
|--------|---------|---------|
| `enhanced_backtest.py` | V1 vs V2 vs V3 comparison on 2024-2025 | ~15 min |
| `backtest_validation.py` | Multi-period validation (bearish + bullish + mixed) | ~40 min |

## Quick Start

```bash
# Activate the virtual environment
source venv/bin/activate

# Option 1: Full multi-period validation (recommended first run)
python3 backtest_validation.py

# Option 2: Single-period comparison (faster)
python3 enhanced_backtest.py
```

## Which Config to Use (Based on Backtest Results)

### Goal: Maximum Returns (growth-oriented)
**Use: V3-C) PREMIUM only, 50% SPY hedge**
- Best average return: **+20.07%** across all periods
- 50% of capital in SPY captures bull market upside
- Only takes PREMIUM signals (highest conviction breakouts)
- Tradeoff: higher drawdowns due to SPY exposure (-18.60% in mixed period)

### Goal: Minimum Risk (capital preservation)
**Use: V3-A) HIGH+, BB filter, patterns (no hedge)**
- Best max drawdown: **-7.77%** in mixed period (vs SPY -18.76%)
- Consistently 2-2.5x lower drawdowns than SPY
- Beats SPY in bear markets (+10.60% outperformance in 2022)
- Tradeoff: trails SPY in strong bull markets

### Goal: Balanced (good returns + controlled risk)
**Use: V3-D) ALL quality, 40% SPY hedge**
- Average return: **+19.27%** (close to V3-C)
- More diversified signal pool (takes all quality levels)
- Moderate drawdowns (-14.74% in mixed period)
- Good middle ground between pure breakout and pure SPY

## Backtest Results Summary

```
                                          Bearish 2022   Bullish 2023-24   Mixed 2024-25   Average
SPY Buy & Hold                              -18.65%         +46.00%          +48.95%       +25.43%
V3-A) HIGH+, no hedge (risk-first)           -8.05%         +18.57%          +31.39%       +13.97%
V3-C) PREMIUM, 50% SPY (growth)             -14.43%         +36.91%          +37.74%       +20.07%
V3-D) ALL, 40% SPY (balanced)               -12.80%         +33.55%          +37.07%       +19.27%
```

## Customizing the Backtest

### Change the test period
In `backtest_validation.py`, edit the `periods` list (~line 578):
```python
periods = [
    ("BEARISH 2022", "2022-01-01", "2022-12-31"),
    ("BULLISH 2023-24", "2023-01-01", "2024-06-30"),
    ("MIXED 2024-25", "2024-01-01", "2025-12-31"),
]
```

### Change the SPY hedge percentage
In the `configs` list, modify `spy_alloc`:
```python
{'spy_hedge': True, 'spy_alloc': 0.40}  # 40% to SPY
```

### Change the watchlist
Edit `input/watchlist3.txt` — one ticker per line (or comma-separated). The scripts load up to 100 symbols.

### Change capital
Set `initial_capital` in the script (default: $100,000).

## Key Metrics to Watch in Output

1. **Return vs SPY**: The `vs SPY` column shows outperformance. Positive = you're winning.
2. **MaxDD**: Max drawdown. Lower (closer to 0) = less pain. Strategy typically achieves -7% to -9% vs SPY's -18% to -24%.
3. **Win Rate**: Above 45% with the W/L ratio above 1.5 is healthy.
4. **W/L ratio**: Average win / average loss. Above 1.5 means winners are bigger than losers.
5. **Sharpe**: Risk-adjusted return. Above 1.0 is good.

## Output Files

Results are saved to:
```
scanner_output/backtests/v3_validation_multi_period.json
scanner_output/backtests/multi_config_vs_spy_YYYY-MM-DD_YYYY-MM-DD.json
```

## Scheduling Automated Scans (Cron)

### Setup

Run the setup script to generate cron job templates with your local paths:
```bash
bash cron-setup.sh
```
This creates `cron_jobs.txt` with ready-to-use entries and `test_cron.sh` to verify your setup.

### Recommended Schedules

**Swing trading (daily)** — scan after market open, evaluate exits before close:
```cron
35 9 * * 1-5  cd /path/to/scanner && venv/bin/python3 breakout_scanner.py input/watchlist3.txt --mode swing --cron --notify >> scanner_output/logs/cron_swing.log 2>&1
45 15 * * 1-5 cd /path/to/scanner && venv/bin/python3 breakout_scanner.py input/watchlist3.txt --mode swing --exit-file input/positions.csv --cron --notify >> scanner_output/logs/cron_swing_exit.log 2>&1
```

**Long-term (weekly)** — scan Monday morning:
```cron
0 9 * * 1 cd /path/to/scanner && venv/bin/python3 breakout_scanner.py input/watchlist3.txt --mode longterm --cron --notify >> scanner_output/logs/cron_longterm.log 2>&1
```

**Day trading (multiple intraday):**
```cron
35 9  * * 1-5 cd /path/to/scanner && venv/bin/python3 breakout_scanner.py input/watchlist3.txt --mode daytrade --cron --notify >> scanner_output/logs/cron_daytrade.log 2>&1
0  10 * * 1-5 cd /path/to/scanner && venv/bin/python3 breakout_scanner.py input/watchlist3.txt --mode daytrade --cron --notify >> scanner_output/logs/cron_daytrade.log 2>&1
0  14 * * 1-5 cd /path/to/scanner && venv/bin/python3 breakout_scanner.py input/watchlist3.txt --mode daytrade --cron --notify >> scanner_output/logs/cron_daytrade.log 2>&1
```

Replace `/path/to/scanner` with your repo path.

### Key Flags

| Flag | Purpose |
|------|---------|
| `--cron` | Minimal output, no interactive prompts |
| `--notify` | Send alerts via email/Discord/Telegram (configure in `config.py`) |
| `--exit-file <csv>` | Evaluate exit conditions for open positions |
| `--both` | Run breakout scan + exit evaluation in one call |
| `--mode <mode>` | `swing`, `longterm`, `daytrade`, or `scalping` |

### Installing Cron Jobs

```bash
# Edit your crontab
crontab -e

# Paste the lines from cron_jobs.txt (with paths updated)
# Save and exit
```

### Monitoring

Check recent cron executions and errors:
```bash
bash monitor-cron.sh
```

### Maintenance (auto-cleanup)

Add to crontab to prevent log/result files from growing indefinitely:
```cron
# Clean logs older than 30 days (every Sunday 11 PM)
0 23 * * 0 find /path/to/scanner/scanner_output/logs -name "*.log" -mtime +30 -delete

# Clean old signal/exit CSVs older than 90 days
5 23 * * 0 find /path/to/scanner/scanner_output/signals -name "*.csv" -mtime +90 -delete
5 23 * * 0 find /path/to/scanner/scanner_output/exits -name "*.csv" -mtime +90 -delete
```
