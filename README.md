# Breakout Scanner for Interactive Brokers

Professional-grade breakout scanner supporting swing trading, day trading, and scalping strategies.

## 📁 Project Structure

```
breakout_scanner/
├── breakout_scanner.py    # Main entry point (renamed from main.py)
├── config.py              # Configuration and mode settings
├── orchestrator.py        # Scanner orchestration
├── scanner.py             # Breakout detection logic
├── exit_evaluator.py      # Exit condition evaluation
├── market_data.py         # IB data fetching
├── indicators.py          # Technical indicators
├── level2_analyzer.py     # ⭐ Level 2 market depth analysis
├── algo_trader.py         # ⭐ Algorithmic order execution
├── notifier.py            # Multi-channel notifications
├── utils.py               # Utility functions
├── webhook_server.py      # ⭐ Example webhook for auto-trading
├── setup_cron.sh          # Cron setup helper
├── monitor_cron.sh        # Cron job monitor
├── watchlist.txt          # Your watchlist
├── positions.csv          # Your open positions (for exit eval)
├── WEBHOOK_GUIDE.md       # Webhook integration guide
└── ALGO_TRADING_GUIDE.md  # ⭐ Algorithmic trading guide
```

## 🚀 Quick Start

### Installation

```bash
pip install ib_insync pandas numpy
```

### Basic Usage

```bash
# Swing trading scan
python3 breakout_scanner.py watchlist.txt --mode swing

# Mock trading (no IB connection needed - perfect for testing!)
python3 breakout_scanner.py watchlist.txt --mode swing --mock

# Day trading scan
python3 breakout_scanner.py watchlist.txt --mode daytrade

# Scalping (1min bars)
python3 breakout_scanner.py watchlist.txt --mode scalping

# With Level 2 market depth analysis
python3 breakout_scanner.py watchlist.txt --mode swing --level2

# Historical simulation
python3 breakout_scanner.py watchlist.txt --mode swing --simulate --sim-start 2025-01-01 --sim-end 2025-12-31

# Exit evaluation
python3 breakout_scanner.py watchlist.txt --mode swing --exit-file positions.csv
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
python3 breakout_scanner.py watchlist.txt --mode longterm

# Combined scan + exit evaluation
python3 breakout_scanner.py watchlist.txt --mode swing --exit-file positions.csv --both

# With Level 2 market depth analysis
python3 breakout_scanner.py watchlist.txt --mode swing --level2

# Cron mode (silent, notifications only)
python3 breakout_scanner.py watchlist.txt --mode swing --cron --notify

# Custom volume threshold
python3 breakout_scanner.py watchlist.txt --mode swing --vol 1.5

# Custom ATR multiplier
python3 breakout_scanner.py watchlist.txt --mode daytrade --atr 0.3

# Custom timeframe
python3 breakout_scanner.py watchlist.txt --mode swing --tf "4 hour"

# Live trading (requires real-time data subscription)
python3 breakout_scanner.py watchlist.txt --mode swing --live

# Full-featured example
python3 breakout_scanner.py watchlist.txt --mode swing --level2 --notify --exit-file positions.csv --both
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
python3 breakout_scanner.py watchlist.txt --mode swing --notify
```

### Mac Native Notifications ⭐ NEW
Automatically enabled on macOS - no configuration needed!
- Shows alerts in Notification Center
- Includes sound notification
- Works in background

### Webhook for Automated Trading ⭐ NEW
Send signals to your own server for automated execution:

```python
'webhook': {
    'enabled': True,
    'url': 'http://localhost:5000/webhook',
    'auth_token': 'your_secret_token',
    'default_quantity': 100,
}
```

**Example webhook server provided:** `webhook_server.py`

```bash
# Run the example webhook server
pip install flask
python webhook_server.py
```

The server receives JSON payloads with signals:
```json
{
  "signals": [
    {
      "symbol": "AAPL",
      "action": "BUY",
      "price": 185.50,
      "stop_loss": 180.00,
      "take_profit": 195.00,
      "quantity": 100
    }
  ]
}
```

**⚠️ WARNING:** Auto-trading carries significant risk. Always test thoroughly!


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
0 9 * * 1 cd /path/to/scanner && python3 breakout_scanner.py watchlist.txt --mode longterm --cron --notify

# Swing: Daily scan at 9:35 AM (after market open)
35 9 * * 1-5 cd /path/to/scanner && python3 breakout_scanner.py watchlist.txt --mode swing --cron --notify

# Swing: Exit check at 3:45 PM (before close)
45 15 * * 1-5 cd /path/to/scanner && python3 breakout_scanner.py watchlist.txt --mode swing --exit-file positions.csv --cron --notify

# Day trade: Combined scan + exit at market close
30 16 * * 1-5 cd /path/to/scanner && python3 breakout_scanner.py watchlist.txt --mode daytrade --exit-file positions.csv --both --cron --notify
```

### Cron Mode Features
- Minimal console output (errors only)
- Full logging to `scanner_output/logs/`
- Automatic notifications on signals/exits
- Exit code 0 on success for monitoring

## 📊 Level 2 (Market Depth) Analysis ⭐ NEW

Enable advanced order flow analysis:

```bash
python3 breakout_scanner.py watchlist.txt --mode swing --level2
```

### What Level 2 Adds:
- **Bid/Ask Liquidity Analysis** - Measures buying vs selling pressure
- **Order Imbalance Detection** - Identifies institutional accumulation
- **Support/Resistance Strength** - Quantifies level significance
- **Entry Quality Scoring** - EXCELLENT | GOOD | FAIR | POOR
- **Breakout Confirmation** - Validates price action with order flow

### Level 2 Metrics:
- `imbalance`: -100 to +100 (positive = bullish pressure)
- `bid_ask_ratio`: Ratio of bid to ask liquidity
- `support_strength`: Concentration of bids (0-100%)
- `resistance_strength`: Concentration of asks (0-100%)

### Requirements:
- IB Market Depth subscription
- Real-time data (not delayed)
- Works best for liquid stocks

**Signal Enhancement:**
- Signals with EXCELLENT depth are upgraded to PREMIUM
- Signals with POOR depth are rejected
- Level 2 data included in webhook payloads

## 🤖 Algorithmic Order Execution ⭐ NEW

Professional algo execution strategies for better fills:

```python
# Configure in webhook_server.py
CONFIG = {
    'use_algo_trading': True,
    'default_algo': 'VWAP',  # or TWAP, ICEBERG, ADAPTIVE, etc.
    'algo_urgency': 'Normal',  # Patient | Normal | Urgent
}
```

### Available Algorithms:

1. **ADAPTIVE** - IB's smart algo (recommended default)
2. **VWAP** - Volume Weighted Average Price
3. **TWAP** - Time Weighted Average Price  
4. **ICEBERG** - Hide order size
5. **DARK_ICE** - Seek dark pools
6. **ARRIVAL_PRICE** - Minimize slippage
7. **PERCENT_VOL** - Match market volume %

### Benefits:
- ✅ Better fill prices than market orders
- ✅ Minimize market impact
- ✅ Hide large order sizes
- ✅ Access dark pool liquidity
- ✅ Automated execution monitoring

**See [ALGO_TRADING_GUIDE.md](ALGO_TRADING_GUIDE.md) for complete documentation.**

## 🧪 Mock Trading & Simulation ⭐ NEW

Test all features without risking real money or needing IB connection!

### Mock Trading Mode

Perfect for testing strategies, learning the system, or developing new features.

```bash
# Run scanner with mock trading (no IB needed!)
python3 breakout_scanner.py input/watchlist.txt --mode swing --mock

# Test different scenarios
python3 breakout_scanner.py input/watchlist.txt --mode swing --mock --mock-mode realistic
python3 breakout_scanner.py input/watchlist.txt --mode swing --mock --mock-mode optimistic
python3 breakout_scanner.py input/watchlist.txt --mode swing --mock --mock-mode pessimistic
```

**Mock Modes:**
- `realistic` - Simulates real market conditions (default)
- `optimistic` - Best case scenario (0.01% slippage)
- `pessimistic` - Worst case scenario (0.5% slippage)

**Features:**
- ✅ No IB connection required
- ✅ Realistic price data simulation
- ✅ Simulated order fills with slippage
- ✅ Level 2 market depth simulation
- ✅ Full P&L tracking
- ✅ Trade statistics and reports
- ✅ Safe testing environment

### Historical Simulation

Backtest your strategy on historical periods:

```bash
# Run simulation on 2025 data
python3 breakout_scanner.py input/watchlist.txt \
  --mode swing \
  --simulate \
  --sim-start 2025-01-01 \
  --sim-end 2025-12-31
```

**Simulation Features:**
- 📊 Historical performance analysis
- 📈 Win rate calculation
- 💰 P&L tracking
- 📉 Max drawdown measurement
- 🎯 Sharpe ratio calculation
- 📝 Detailed trade log
- 💾 JSON report export

**Output Example:**
```
Simulation Report:
  Total Return: +15.3%
  Win Rate: 62.5%
  Sharpe Ratio: 1.85
  Max Drawdown: -8.2%
  Total Trades: 48
```

### Mock Trading Statistics

```bash
# After running mock trades, check stats
# Report saved to: mock_trading_report.json
```

```json
{
  "stats": {
    "total_trades": 25,
    "win_rate": 64.0,
    "total_pnl": 15340.50,
    "total_return": 15.34,
    "sharpe_ratio": 1.82
  },
  "trades": [...]
}
```

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
