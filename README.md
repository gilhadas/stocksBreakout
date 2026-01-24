# Breakout Scanner for Interactive Brokers

Professional-grade breakout scanner supporting swing trading, day trading, and scalping strategies.

## 📁 Project Structure

```
breakout_scanner/
├── main.py                 # Main entry point
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
python main.py watchlist.txt --mode swing

# Day trading scan
python main.py watchlist.txt --mode daytrade

# Scalping (1min bars)
python main.py watchlist.txt --mode scalping

# Exit evaluation
python main.py watchlist.txt --mode swing --exit-file positions.csv
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

### Long-Term Position Trading ⭐ NEW
- **Timeframe**: 1 week
- **Holding period**: Weeks to months
- **Trend filter**: 200 SMA
- **R:R minimum**: 2.5
- **Stop loss**: 3.0 ATR
- **Target**: 6.0 ATR
- **Best for**: Position traders, retirement accounts

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
# Long-term position trading (weekly bars)
python main.py watchlist.txt --mode longterm

# Combined scan + exit evaluation
python main.py watchlist.txt --mode swing --exit-file positions.csv --both

# Cron mode (silent, notifications only)
python main.py watchlist.txt --mode swing --cron --notify

# Custom volume threshold
python main.py watchlist.txt --mode swing --vol 1.5

# Custom ATR multiplier
python main.py watchlist.txt --mode daytrade --atr 0.3

# Custom timeframe
python main.py watchlist.txt --mode swing --tf "4 hour"

# Live trading (requires real-time data subscription)
python main.py watchlist.txt --mode swing --live
```

## 🔔 Notifications ⭐ NEW

Configure notifications in `config.py`:

### Email Notifications
```python
NOTIFICATIONS = {
    'email': {
        'enabled': True,
        'smtp_server': 'smtp.gmail.com',
        'smtp_port': 587,
        'sender_email': 'your_email@gmail.com',
        'sender_password': 'your_app_password',  # Use Gmail App Password
        'recipient_email': 'alerts@yourdomain.com',
    }
}
```

### Telegram Notifications
1. Create a bot with [@BotFather](https://t.me/botfather)
2. Get your chat ID from [@userinfobot](https://t.me/userinfobot)
3. Configure:
```python
'telegram': {
    'enabled': True,
    'bot_token': 'YOUR_BOT_TOKEN',
    'chat_id': 'YOUR_CHAT_ID',
}
```

### Discord Notifications
1. Create a webhook in your Discord server
2. Configure:
```python
'discord': {
    'enabled': True,
    'webhook_url': 'YOUR_WEBHOOK_URL',
}
```

Enable notifications with `--notify` flag:
```bash
python main.py watchlist.txt --mode swing --notify
```

## ⏰ Automated Scanning (Cron) ⭐ NEW

Set up automated scans using cron:

```bash
# Run the setup script
chmod +x setup_cron.sh
./setup_cron.sh
```

This creates example cron jobs. Edit and add to your crontab:

```bash
crontab -e
```

### Example Cron Jobs

```bash
# Long-term: Weekly scan every Monday at 9:00 AM
0 9 * * 1 cd /path/to/scanner && python3 main.py watchlist.txt --mode longterm --cron --notify

# Swing: Daily scan at 9:35 AM (after market open)
35 9 * * 1-5 cd /path/to/scanner && python3 main.py watchlist.txt --mode swing --cron --notify

# Swing: Exit check at 3:45 PM (before close)
45 15 * * 1-5 cd /path/to/scanner && python3 main.py watchlist.txt --mode swing --exit-file positions.csv --cron --notify

# Day trade: Combined scan + exit at market close
30 16 * * 1-5 cd /path/to/scanner && python3 main.py watchlist.txt --mode daytrade --exit-file positions.csv --both --cron --notify
```

### Cron Mode Features
- Minimal console output (errors only)
- Full logging to `scanner_output/logs/`
- Automatic notifications on signals/exits
- Exit code 0 on success for monitoring

## ⚙️ Configuration

Edit `config.py` to customize:

- Mode parameters (lookback, thresholds, R:R)
- Regime thresholds
- IB connection settings
- Data request settings

## 📈 Output Files ⭐ UPDATED

Scanner generates timestamped files in organized subdirectories:

```
scanner_output/
├── signals/
│   └── signals_swing_20260124_093500.csv
├── exits/
│   └── exits_swing_20260124_154500.csv
├── rejections/
│   └── rejections_swing_20260124_093500.csv
└── logs/
    └── scanner_20260124.log
```

- **signals/** - Detected breakout signals
- **exits/** - Exit decisions for positions
- **rejections/** - Near-miss signals for analysis
- **logs/** - Detailed execution logs

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

### `main.py`
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
