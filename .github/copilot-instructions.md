# AI Copilot Instructions for stocksBreakout

## Project Overview
**stocksBreakout** is a professional algorithmic trading scanner for Interactive Brokers that detects breakout signals across multiple trading modes (swing, day trading, scalping, position trading). It integrates real-time market data, technical indicators, and order execution.

## Architecture & Data Flow

### Component Architecture
```
breakout_scanner.py (entry point)
    ↓
orchestrator.py (ScannerOrchestrator) - Coordinates all scanning operations
    ├── market_data.py (MarketDataHandler) - Fetches bars from IB
    ├── scanner.py (BreakoutDetector) - Applies detection logic
    ├── exit_evaluator.py - Evaluates open positions for exits
    ├── level2_analyzer.py - Analyzes Level 2 market depth (optional)
    └── algo_trader.py - Executes orders via IB algo strategies
```

### Key Data Structures
- **Signal Dict**: `{symbol, timeframe, price, stop_loss, take_profit, mode, atr, reason, timestamp}`
- **Trade Modes**: Defined in `config.MODES` - each has `lookback`, `atr_mult`, `tp_mult`, `sl_mult`, `trend_period`, `min_rr`
- **Market Data**: DataFrame with `[open, high, low, close, volume]` indexed by datetime

### Mode Configuration System
- **config.py** is the single source of truth for ALL mode parameters
- Each mode has defaults but can be overridden via CLI: `--vol-thresh 1.5 --atr-mult 0.3`
- **Regime adjustments** apply multipliers based on SPY performance/volatility (CHOPPY/EXPANSION/NORMAL)
- **Trend Types**: SMA (swing/longterm), EMA (daytrade), VWAP (scalping)

## Critical Patterns & Conventions

### Async/Concurrency Patterns
- **Event loop setup** must happen BEFORE importing `ib_insync` (see line 13-18 in breakout_scanner.py)
- Concurrent symbol scanning uses `asyncio.Semaphore` with `MAX_CONCURRENT_REQUESTS=5` (IB rate limit)
- All IB operations are async: `await ib.connectAsync()`, `await market_data.get_historical_data()`

### Timeframe Handling ⚠️
- **Never use `1 week`** - IB uses `1W`, normalized via `MarketDataHandler._normalize_timeframe()`
- Supported: `1 min`, `5 mins`, `15 mins`, `1 hour`, `1 day` (and `1W`, `1M`)
- Each mode has `default_timeframe` in config; duration in `DATA_DURATION` dict

### Indicator Calculation
- **All indicators** live in [indicators.py](indicators.py) - ATR, VWAP, Bollinger Bands, Volume Ratio
- **Consolidation detection**: checks min bars at resistance/support with low volatility
- **Volume divergence**: compares current vol to 20-bar MA
- **Candle structure**: validates breakout candles (not too much wick, body top position)

### Detection Logic Flow
1. Fetch historical data, calculate indicators
2. Identify consolidation (min_consolidation_bars, narrowing Bollinger Bands)
3. Detect breakout candle (volume spike, body structure, ATR threshold)
4. Validate trend alignment (price vs SMA/EMA/VWAP depending on mode)
5. Calculate Risk:Reward ratio - reject if below `min_rr`
6. Return signal dict or rejection reason (logged to `scanner_output/rejections/`)

### Testing & Simulation
- **Mock mode**: `--mock` flag uses `MockIBConnection` (realistic/random modes)
- **Backtesting**: `optimizer.py` runs symbol screening on historical yfinance data
- **No need to connect to real IB**: Use `--mock` for all development

### Configuration Loading Pattern
```python
from config import MODES, PORTFOLIO, NOTIFICATIONS, OUTPUT_DIR
mode_config = MODES[mode_name]  # Returns dict with all parameters
regime_adjustments = REGIME_CONFIG.get(regime, REGIME_CONFIG['NORMAL'])
```

## Common Development Tasks

### Adding a New Trading Mode
1. Add entry to `config.MODES` dict with parameters: `lookback`, `vol_thresh`, `atr_mult`, `trend_type`, `trend_period`, `sl_mult`, `tp_mult`, `min_rr`, etc.
2. Update timeframe handling in `market_data.py` if new bar size needed
3. Mode-specific validation happens in `scanner.py` - apply regime multipliers there

### Testing a Signal
```bash
python breakout_scanner.py watchlist.txt --mode swing --mock
```
This runs offline with simulated market data.

### Extending Detection Logic
- Detection happens in [scanner.py](scanner.py) `BreakoutDetector.detect()` method
- Add new rejection reasons to existing checks or create new check functions
- Rejection details are logged to `scanner_output/rejections/{symbol}_{timestamp}.json`

### Understanding Order Execution
- [algo_trader.py](algo_trader.py) contains AlgoType enum: TWAP, VWAP, ICEBERG, ADAPTIVE, etc.
- `AlgoTrader.execute_with_algo()` creates IB algo orders with urgency levels
- All orders tracked in `self.active_orders` dict keyed by orderId

## Integration Points & Dependencies

### Interactive Brokers Integration
- **Connection**: Port 7497 (paper) or 7496 (live), localhost only
- **Data**: Uses `ib_insync` for qualified contracts and bar data
- **Orders**: Places orders via IB's algo execution system
- **Market Data Types**: 3=delayed (paper), 1=real-time (live)

### External Data
- **Fallback**: [yfinance_adapter.py](yfinance_adapter.py) for backtesting when IB unavailable
- **Webhook**: [webhook_server.py](webhook_server.py) receives signals for auto-trading (see webhook-readme.md)

### Notifications
- **Multi-channel**: Email (SMTP), Discord (webhook), Telegram (bot)
- All enabled/disabled in `config.NOTIFICATIONS` dict
- Sentvia [notifier.py](notifier.py) after signal generation

## Key Files Reference

| File | Purpose |
|------|---------|
| [breakout_scanner.py](breakout_scanner.py) | CLI entry point, IB connection, main loop |
| [config.py](config.py) | All parameters - **edit here for tweaks** |
| [orchestrator.py](orchestrator.py) | Coordinates scanning, output writing |
| [scanner.py](scanner.py) | Core detection logic, rejection evaluation |
| [market_data.py](market_data.py) | IB data fetching, timeframe normalization |
| [indicators.py](indicators.py) | All technical indicator calculations |
| [mock_trader.py](mock_trader.py) | Offline testing without IB connection |
| [optimizer.py](optimizer.py) | Backtesting optimization over historical periods |
| [exit_evaluator.py](exit_evaluator.py) | Position exit signal generation |

## Debugging Tips

- **Signals not generated?** Check `scanner_output/rejections/` for reasons
- **IB connection fails?** Ensure IB Gateway running on port 7497 (paper) or use `--mock`
- **Wrong timeframe behavior?** Verify `_normalize_timeframe()` in market_data.py
- **Indicator calculations off?** Check `df.index` is datetime, validate data shape in indicators.py
- **Performance slow?** Reduce `MAX_CONCURRENT_REQUESTS` or increase `SCAN_DELAY`

## Output Structure
```
scanner_output/
├── signals/      → Generated breakout signals (JSON)
├── exits/        → Exit evaluation results
├── rejections/   → Symbols rejected with reasons
├── logs/         → Detailed scan logs
└── optimization/ → Backtesting results
```

---
Last updated: 2026-02-06
