# Breakout Scanner for Interactive Brokers

Professional-grade breakout scanner supporting swing trading, day trading, and scalping strategies.

## 📁 Project Structure

```
breakout_scanner/
├── breakout_scanner.py    # Main entry point
├── config.py              # Configuration and mode settings
├── orchestrator.py        # Scanner orchestration
├── scanner.py             # Breakout detection logic
├── exit_evaluator.py      # Exit condition evaluation
├── market_data.py         # IB data fetching
├── indicators.py          # Technical indicators
├── utils.py               # Utility functions
├── watchlist.txt          # Your watchlist
└── positions.csv          # Your open positions (for exit eval)
```

## 🚀 Quick Start

### Installation

```bash
pip install ib_insync pandas numpy
```

### Basic Usage

```bash
# Swing trading scan
python breakout_scanner.py watchlist.txt --mode swing

# Day trading scan
python breakout_scanner.py watchlist.txt --mode daytrade

# Scalping (1min bars)
python breakout_scanner.py watchlist.txt --mode scalping

# Exit evaluation
python breakout_scanner.py watchlist.txt --mode swing --exit-file positions.csv
```

## 📋 File Formats

### Watchlist Format (`watchlist.txt`)

```
### My Watchlist
AAPL, MSFT, GOOGL
TSLA
AMZN, NVDA

### Comments start with ###
META
```

### Positions Format (`positions.csv`)

```csv
symbol,mode,entry,stop,target,timeframe
AAPL,swing,185.50,180.00,195.00,1 day
NVDA,daytrade,520.30,515.00,530.00,15 mins
TSLA,scalping,245.10,244.85,245.45,1 min
```

## 🎯 Trading Modes

### Swing Trading
- **Timeframe**: 1 day
- **Holding period**: Days to weeks
- **Trend filter**: 200 SMA
- **R:R minimum**: 2.0
- **Stop loss**: 2.0 ATR
- **Target**: 4.0 ATR

### Day Trading
- **Timeframe**: 15 minutes
- **Holding period**: Hours
- **Trend filter**: 9 EMA + VWAP
- **R:R minimum**: 1.5
- **Stop loss**: 1.5 ATR
- **Target**: 3.0 ATR

### Scalping
- **Timeframe**: 1 minute
- **Holding period**: Minutes
- **Trend filter**: VWAP
- **R:R minimum**: 1.0
- **Stop loss**: 0.5 ATR
- **Target**: 1.0 ATR
- **Max spread**: 0.1%
- **Price range**: $5 - $500

## 🔍 Detection Criteria

All modes check for:
- ✅ Price breaking above range high
- ✅ Volume confirmation (mode-specific threshold)
- ✅ Distance from breakout (ATR-based)
- ✅ Trend alignment
- ✅ VWAP position (intraday modes)
- ✅ Consolidation before breakout
- ✅ Liquidity ($5M+ daily volume)
- ✅ Candle structure quality
- ✅ Risk/Reward ratio
- ❌ No volume divergence

## 📊 Market Regime Detection

The scanner automatically adapts thresholds based on market conditions:

### CHOPPY
- **Criteria**: SPY < 1% move, low volatility
- **Adjustment**: 30% stricter thresholds
- **Description**: Low momentum, high noise

### EXPANSION
- **Criteria**: SPY > 5% move, high volatility
- **Adjustment**: 10% looser thresholds
- **Description**: High momentum, trending

### NORMAL
- **Criteria**: Everything else
- **Adjustment**: Standard thresholds

## 🚪 Exit Evaluation

Priority-ordered exit signals:

1. **Hard stop hit** → EXIT_FULL
2. **Trend broken** → EXIT_FULL
3. **SMA150 lost** (swing only) → EXIT_FULL
4. **Reversal candle near target** → EXIT_PARTIAL
5. **Volume divergence** → EXIT_PARTIAL
6. **Choppy + no progress** → EXIT_FULL
7. **Trail stop suggestion** → TRAIL

## 🛠️ Advanced Options

```bash
# Custom volume threshold
python breakout_scanner.py watchlist.txt --mode swing --vol 1.5

# Custom ATR multiplier
python breakout_scanner.py watchlist.txt --mode daytrade --atr 0.3

# Custom timeframe
python breakout_scanner.py watchlist.txt --mode swing --tf "4 hour"

# Live trading (requires real-time data subscription)
python breakout_scanner.py watchlist.txt --mode swing --live
```

## ⚙️ Configuration

Edit `config.py` to customize:

- Mode parameters (lookback, thresholds, R:R)
- Regime thresholds
- IB connection settings
- Data request settings

## 📈 Output Files

Scanner generates timestamped CSV files:

- `signals_{mode}_{timestamp}.csv` - Detected breakout signals
- `rejections_{mode}_{timestamp}.csv` - Near-miss signals for analysis
- `exits_{mode}_{timestamp}.csv` - Exit decisions for positions
- `scanner_{YYYYMMDD}.log` - Detailed log file

## ⚠️ Important Notes

### For Scalping
- Paper trading uses **delayed data** (15min lag) - not suitable for real scalping
- Live scalping requires **real-time data subscription**
- Monitor **spread widening** during volatile periods
- **Close all positions** before market close
- Watch for **news events** that spike volatility

### For IB Connection
1. Make sure TWS or IB Gateway is running
2. Enable API: Configure → Settings → API → Settings
3. Check "Enable ActiveX and Socket Clients"
4. Add `127.0.0.1` to "Trusted IP Addresses"
5. Use correct port:
   - Paper TWS: 7497
   - Live TWS: 7496
   - Paper Gateway: 4002
   - Live Gateway: 4001

### Rate Limits
- IB has strict rate limits (50-100 req/sec)
- Scanner uses concurrent requests with semaphore (default: 5)
- Larger watchlists may take several minutes

## 🐛 Troubleshooting

### "No data" errors
- Check symbol format (use `BRK B` not `BRK.B`)
- Verify you have market data subscription for the symbol
- Some symbols need specific exchange (e.g., `Stock('SPY', 'ARCA', 'USD')`)

### "Connection failed"
- Verify TWS/Gateway is running
- Check API settings are enabled
- Confirm port number matches
- Try restarting TWS/Gateway

### "Spread too wide" (scalping)
- Normal for less liquid stocks
- Scanner automatically filters these out
- Adjust `max_spread_pct` in config if needed

## 📚 Module Documentation

### `breakout_scanner.py`
Entry point. Handles CLI arguments and orchestrates scan/exit workflows.

### `config.py`
All configuration parameters. Modify mode settings here.

### `orchestrator.py`
Coordinates market data fetching, signal detection, and exit evaluation.

### `scanner.py`
Core breakout detection logic. Implements all filters and checks.

### `exit_evaluator.py`
Position management logic. Evaluates when to hold, trail, or exit.

### `market_data.py`
IB data fetching with caching and retry logic.

### `indicators.py`
Technical indicator calculations (ATR, VWAP, BB, etc.).

### `utils.py`
File I/O, regime classification, logging setup.

## 📄 License

MIT License - Use at your own risk. Not financial advice.

## ⚖️ Disclaimer

This tool is for educational purposes. Trading involves substantial risk. Always test thoroughly on paper trading before using live funds. Past performance does not guarantee future results.
