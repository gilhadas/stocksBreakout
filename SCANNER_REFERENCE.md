# Breakout Scanner Reference Guide

## Table of Contents
1. [Output Columns Explained](#output-columns-explained)
2. [Command Line Parameters](#command-line-parameters)
3. [Trading Modes](#trading-modes)
4. [Signal Quality Levels](#signal-quality-levels)
5. [Examples](#examples)

---

## Output Columns Explained

When the scanner finds signals, it displays a table with these columns:

| Column | Description | Range/Values | What to Look For |
|--------|-------------|--------------|------------------|
| **Symbol** | Stock ticker | Any valid ticker | — |
| **Price** | Current/entry price | $ amount | Entry point for the trade |
| **Vol** | Volume ratio vs 20-day average | 0.5x - 10x+ | **>1.5x** = strong institutional interest |
| **Dist** | Distance above breakout level in ATR units | 0 - 3.0 | **0.5 - 1.5** = ideal (not too extended) |
| **Stop** | Suggested stop-loss price | $ amount | Place your stop-loss here |
| **Target** | Suggested take-profit price | $ amount | First profit target |
| **R:R** | Risk-to-Reward ratio | 1.0 - 5.0+ | **>2.0** = good, **>3.0** = excellent |
| **Gap%** | Gap percentage from previous close | -5% to +20% | Positive = gap-up (bullish) |
| **Mode** | Trading mode used | swing/daytrade/scalping/longterm | Determines timeframe |
| **Quality** | Signal quality rating | STANDARD/HIGH/PREMIUM | Higher = more conviction |
| **RR_Grade** | Risk-Reward grade | A/B/C/D | **A** = best R:R, **D** = poor R:R |
| **WinProb** | Estimated win probability | 0.30 - 0.80 | Based on technical confluence |
| **WinGrade** | Win probability tier | LOW/MEDIUM/HIGH/VERY_HIGH | Quick reference for WinProb |
| **Patterns** | Detected chart patterns | Pattern names | E.g., "Bull Flag, Ascending Triangle" |

### Additional Columns for Bounce Signals

When using `--bounce` flag, additional bounce-specific signals appear:

| Column | Description |
|--------|-------------|
| **Type** | Signal type: `BREAKOUT` or `BOUNCE` |
| **RSI** | Current RSI value (for bounce signals) |
| **Dist** | For bounces: drawdown % from 20-day high (e.g., -34.2 = down 34%) |

---

## Command Line Parameters

### Basic Usage

```bash
python3 breakout_scanner.py <watchlist_file> [options]
```

### Required Arguments

| Argument | Description | Example |
|----------|-------------|---------|
| `file` | Path to watchlist file (one ticker per line or comma-separated) | `input/watchlist.txt` |

### Mode Selection

| Flag | Values | Default | Description |
|------|--------|---------|-------------|
| `--mode` | `longterm`, `swing`, `daytrade`, `scalping` | `swing` | Trading mode (see Trading Modes section) |
| `--tf` | `1 day`, `1 hour`, `15 mins`, `1 min`, etc. | Mode default | Override timeframe |

### Parameter Overrides

| Flag | Type | Description |
|------|------|-------------|
| `--vol` | float | Volume threshold override (e.g., `--vol 1.5` for 1.5x average) |
| `--atr` | float | ATR multiplier for entry distance (e.g., `--atr 0.5`) |
| `--lookback` | int | Lookback period for breakout level (e.g., `--lookback 15`) |

### Connection & Data

| Flag | Description |
|------|-------------|
| `--live` | Connect to live IB account (default: paper account) |
| `--mock` | Use mock mode with yfinance data (no IB connection needed) |
| `--mock-mode` | Mock simulation type: `realistic`, `optimistic`, `pessimistic` |

### Signal Detection

| Flag | Description |
|------|-------------|
| `--bounce` | Also detect bounce/recovery signals (oversold stocks with strong recoveries) |
| `--level2` | Enable Level 2 (Market Depth) analysis for entry quality |

### Exit Evaluation

| Flag | Description |
|------|-------------|
| `--exit-file <csv>` | CSV file with open positions to evaluate for exits |
| `--both` | Run both breakout scan AND exit evaluation |

### Simulation Mode

| Flag | Description |
|------|-------------|
| `--simulate` | Run historical simulation (requires `--sim-start` and `--sim-end`) |
| `--sim-start` | Simulation start date (YYYY-MM-DD) |
| `--sim-end` | Simulation end date (YYYY-MM-DD) |
| `--sim-data-source` | Data source: `auto`, `ib`, `yfinance`, `mock` |
| `--sim-mock` | Shorthand for `--sim-data-source mock` |

### Output & Notifications

| Flag | Description |
|------|-------------|
| `--cron` | Cron mode: minimal output, no interactive prompts |
| `--notify` | Send notifications via email/Telegram/Discord (configure in `config.py`) |

---

## Trading Modes

| Mode | Default Timeframe | Lookback | Vol Threshold | Best For |
|------|-------------------|----------|---------------|----------|
| `longterm` | 1 week | 20 bars | 1.2x | Position trades (weeks to months) |
| `swing` | 1 day | 15 bars | 1.3x | Swing trades (days to weeks) |
| `daytrade` | 15 mins | 15 bars | 1.5x | Intraday trades (hours) |
| `scalping` | 1 min | 5 bars | 2.0x | Scalp trades (seconds to minutes) |

### Mode-Specific Behavior

**Longterm & Swing:**
- Use daily/weekly data
- Check relative strength vs SPY
- Require consolidation before breakout
- Use SMA 150/50 as trend filter

**Daytrade:**
- Use 15-minute intraday data
- Faster stop-loss (1.5x ATR)
- Require VWAP confirmation

**Scalping:**
- Use 1-minute data
- Requires live IB connection for accurate spreads
- Very tight stops (0.5x ATR)
- ⚠️ **Warning**: Not recommended with `--mock` (limited data quality)

---

## Signal Quality Levels

| Quality | Description | Criteria |
|---------|-------------|----------|
| **PREMIUM** | Highest conviction | Gap-up + excellent depth/patterns, or 90%+ score |
| **HIGH** | Strong signals | 70-89% score, good technical confluence |
| **STANDARD** | Acceptable signals | 50-69% score, basic criteria met |
| **REJECT** | Filtered out | Below minimum thresholds (not shown) |

### RR_Grade Thresholds

| Grade | R:R Range | Recommendation |
|-------|-----------|----------------|
| **A** | ≥ 3.0 | Excellent - prioritize these |
| **B** | 2.0 - 2.99 | Good - standard trades |
| **C** | 1.5 - 1.99 | Acceptable - use discretion |
| **D** | < 1.5 | Poor - typically rejected |

### WinProb & WinGrade

Estimated probability that the trade reaches target before stop:

| WinGrade | WinProb Range | Interpretation |
|----------|---------------|----------------|
| **VERY_HIGH** | > 65% | Multiple confirming factors |
| **HIGH** | 55-65% | Strong technical setup |
| **MEDIUM** | 45-55% | Average probability |
| **LOW** | < 45% | Weaker setup, higher risk |

---

## Examples

### Basic swing scan with mock data
```bash
python3 breakout_scanner.py input/watchlist.txt --mode swing --mock
```

### Live scalping with IB connection
```bash
python3 breakout_scanner.py input/watchlist.txt --mode scalping --live
```

### Swing + bounce detection with notifications
```bash
python3 breakout_scanner.py input/watchlist.txt --mode swing --live --bounce --notify
```

### Evaluate exits for open positions
```bash
python3 breakout_scanner.py input/watchlist.txt --mode swing --live --exit-file input/positions.csv
```

### Both scan and exit evaluation
```bash
python3 breakout_scanner.py input/watchlist.txt --mode swing --live --both --exit-file input/positions.csv
```

### Historical simulation
```bash
python3 breakout_scanner.py input/watchlist.txt --mode swing --simulate --sim-start 2025-01-01 --sim-end 2025-12-31
```

### Cron job (silent, notifications only)
```bash
python3 breakout_scanner.py input/watchlist.txt --mode swing --cron --notify --mock
```

### Custom parameters
```bash
python3 breakout_scanner.py input/watchlist.txt --mode swing --vol 1.5 --atr 0.3 --lookback 20
```

---

## Output Files

Results are automatically saved to:

| Directory | Contents |
|-----------|----------|
| `scanner_output/signals/` | Signal CSVs (signals_mode_YYYYMMDD_HHMMSS.csv) |
| `scanner_output/exits/` | Exit evaluation CSVs |
| `scanner_output/rejections/` | Near-miss signals (for analysis) |
| `scanner_output/logs/` | Cron and scan logs |

---

## Configuration

Edit `config.py` to customize:

- **Trading mode parameters** (lookback, thresholds, stops)
- **SPY hedge settings**
- **Notification settings** (email, Telegram, Discord)
- **Scoring weights and quality thresholds**

See `config.py` for detailed comments on each setting.
