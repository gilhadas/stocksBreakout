# How to Run the Strategy for Best Results

## Context

V6 strategy is implemented and backtested: 23 pattern detectors with volume confirmation, overextension filter, 52-week high proximity, RSI divergence, sector momentum, GOLD/PREMIUM/HIGH/STANDARD tiers, and parquet disk cache. This guide explains which scripts to run and which config to pick.

## Available Scripts

| Script | Purpose | Runtime |
|--------|---------|---------|
| `enhanced_backtest.py` | V1 vs V2 vs V5 vs V6 vs V6X comparison on 2024-2025 | ~15 min (first run), ~2 min (cached) |
| `backtest_validation.py` | Multi-period validation (bearish + bullish + mixed) | ~40 min (first run), ~5 min (cached) |

Both scripts use the parquet disk cache (`scanner_output/cache/`). First run fetches from Yahoo Finance; subsequent runs hit cache automatically.

## Quick Start

```bash
# Activate the virtual environment
source venv/bin/activate

# Option 1: Full multi-period validation (recommended first run)
PYTHONUNBUFFERED=1 python3 backtest_validation.py

# Option 2: Single-period comparison (faster)
PYTHONUNBUFFERED=1 python3 enhanced_backtest.py

# Clear cache if data seems stale
python3 -c "from yfinance_adapter import YFinanceAdapter; YFinanceAdapter().clear_disk_cache()"
```

## Which Config to Use (Based on Backtest Results)

### Goal: Best Risk-Adjusted Returns (recommended)
**Use: V6X-A) HIGH+, overextension + 10% cap**
- **+129.13%** return, **Sharpe 2.62**, only **-13.56% max DD**
- 60.3% win rate with 1.97:1 W/L ratio
- Beats SPY by +41.45% with 28% less drawdown
- Overextension filter prevents buying stretched stocks

### Goal: Maximum Returns (growth-oriented)
**Use: V1-A) HIGH+, legacy momentum**
- **+141.77%** return, Sharpe 2.45
- Highest raw returns but deeper drawdowns (-22.14%)
- No overextension filter = catches more momentum plays

### Goal: Minimum Risk (capital preservation)
**Use: V6X-A) HIGH+, overextension + 10% cap**
- Same config as risk-adjusted — it's the best on both axes
- -13.56% max DD vs SPY's -18.76%
- Position cap at 10% prevents single-stock concentration

### Goal: Highest Conviction Only
**Use: V6X-B) PREMIUM+, overextension + 10% cap**
- Only trades GOLD and PREMIUM signals (score >= 80)
- Fewer trades but higher quality
- Lower overall return but very high selectivity

## Backtest Results (Jan 2024 - Dec 2025, 40 symbols)

| Config | Return | Sharpe | MaxDD | WinRate | vs SPY |
|--------|--------|--------|-------|---------|--------|
| V1-A HIGH+ baseline | +141.77% | 2.45 | -22.14% | 56.1% | +54.10% |
| V6X-A HIGH+, overextension | +129.13% | 2.62 | -13.56% | 60.3% | +41.45% |
| V6-A HIGH+, 10% cap | +107.59% | 2.15 | -26.85% | 55.5% | +19.91% |
| V6-B PREMIUM+, 10% cap | +60.57% | 1.27 | -19.27% | 55.4% | -27.10% |
| SPY Buy & Hold | +87.67% | 1.45 | -18.76% | N/A | baseline |

**Recommended: V6X-A** — best Sharpe ratio (2.62) and lowest drawdown (-13.56%).

## Quality Tiers

| Tier | Score | Description |
|------|-------|-------------|
| GOLD | 90+ | Elite signals — passes 5 extra hard gates |
| PREMIUM | 80+ | Strong signals — high conviction, volume + trend |
| HIGH | 65+ | Good signals — moderate conviction |
| STANDARD | 60+ | Marginal signals — weakest accepted |

## Customizing the Backtest

### Change the test period
In `backtest_validation.py`, edit the `periods` list (~line 579):
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
2. **MaxDD**: Max drawdown. Lower (closer to 0) = less pain. Strategy typically achieves -13% to -22% vs SPY's -18% to -24%.
3. **Win Rate**: Above 55% with the W/L ratio above 1.5 is healthy.
4. **W/L ratio**: Average win / average loss. Above 1.5 means winners are bigger than losers.
5. **Sharpe**: Risk-adjusted return. Above 2.0 is excellent, above 1.0 is good.

## Output Files

Results are saved to:
```
scanner_output/backtests/v6_validation_multi_period.json
scanner_output/backtests/multi_config_vs_spy_YYYY-MM-DD_YYYY-MM-DD.json
scanner_output/cache/*.parquet  (disk cache for yfinance data)
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

**Portfolio daily report:**
```cron
0 16 * * 1-5 cd /path/to/scanner && venv/bin/python3 breakout_scanner.py dummy --portfolio-report >> scanner_output/logs/cron_portfolio.log 2>&1
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
| `--mock` | Use mock data (no IB connection needed) |
| `--simulate` | Historical simulation mode (uses yfinance + cache) |
| `--portfolio-report` | Send daily portfolio email and exit |
| `--sector-buzz` | Run sector momentum analysis before scan |

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

# Clean stale cache files older than 7 days
10 23 * * 0 find /path/to/scanner/scanner_output/cache -name "*.parquet" -mtime +7 -delete
```
