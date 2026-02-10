# stocksBreakout

Professional algorithmic breakout scanner for Interactive Brokers.

## Tech Stack

- **Language:** Python 3.14
- **Broker API:** Interactive Brokers via `ib_insync`
- **Data:** pandas, numpy, yfinance (fallback)
- **Package Manager:** pip (`.venv/`)

## Project Structure

```
breakout_scanner.py   - CLI entry point, IB connection, async loop
orchestrator.py       - Coordinates scanning, market data, exits
scanner.py            - Core breakout detection & scoring (V3)
config.py             - Single source of truth for ALL parameters
market_data.py        - IB data fetching, caching, rate limiting
indicators.py         - Technical indicators (ATR, VWAP, BB, RSI, MACD, ADX)
exit_evaluator.py     - Position exit signal generation
pattern_recognition.py - Chart pattern detection
notifier.py           - Multi-channel notifications (email, Discord, Telegram)
level2_analyzer.py    - Level 2 market depth analysis
algo_trader.py        - Algo execution (VWAP, TWAP, ICEBERG, ADAPTIVE)
mock_trader.py        - MockIBConnection for offline testing
yfinance_adapter.py   - Fallback data adapter
sentiment.py          - Sentiment & sector tracking
utils.py              - File I/O, regime classification, logging
webhook_server.py     - Flask webhook for automated execution
input/                - Watchlists (txt/csv)
scanner_output/       - Signals, exits, rejections, logs, backtests
```

## Running

```bash
# Live/paper trading
python breakout_scanner.py input/watchlist.txt --mode swing

# Mock mode (no IB needed)
python breakout_scanner.py input/watchlist.txt --mode swing --mock

# CLI overrides
python breakout_scanner.py watchlist.txt --mode daytrade --vol-thresh 1.5 --atr-mult 0.3
```

## Trading Modes

4 modes defined in `config.MODES`:
- **swing** - 1-day bars, SMA trend, days-to-weeks
- **daytrade** - 15-min bars, EMA trend, intraday
- **scalping** - 1-min bars, VWAP trend, seconds-to-minutes
- **longterm** - 1-week bars, SMA trend, weeks-to-months

## Critical Patterns

### Event Loop Setup
Event loop MUST be created BEFORE importing `ib_insync` (Python 3.14 requirement). See `breakout_scanner.py` lines 13-18.

### Async/Concurrency
- All IB operations are async
- Concurrent scanning uses `asyncio.Semaphore` with `MAX_CONCURRENT_REQUESTS=5`
- Pattern: `async with semaphore: result = await self._scan_symbol(...)`

### Timeframe Handling
- Never use `1 week` - IB requires `1W` (normalized via `MarketDataHandler._normalize_timeframe()`)
- Supported: `1 min`, `5 mins`, `15 mins`, `1 hour`, `1 day`, `1W`, `1M`

### Detection Pipeline
1. Fetch historical data, calculate indicators
2. Identify consolidation (min bars, narrowing Bollinger Bands)
3. Detect breakout candle (volume spike, body structure, ATR threshold)
4. Validate trend alignment (SMA/EMA/VWAP per mode)
5. Calculate Risk:Reward ratio (reject if below `min_rr`)
6. Score signal (V3 weighted system: PREMIUM >= 80, HIGH >= 65, STANDARD < 65)

### Market Regime
- Regime detection based on SPY volatility/performance
- Three regimes: CHOPPY, EXPANSION, NORMAL
- Thresholds auto-adjusted via `REGIME_CONFIG` multipliers

## Configuration

- `config.py` is the single source of truth
- `config.MODES` - trading mode parameters
- `config.PORTFOLIO` - capital, risk, position sizing
- `config.REGIME_CONFIG` - regime-based multiplier adjustments
- `config.SCORING_WEIGHTS` - V3 signal scoring weights
- `config.NOTIFICATIONS` - email, Discord, Telegram settings
- IB ports: 7497 (paper), 7496 (live)

## Code Conventions

- **Classes:** PascalCase (`BreakoutDetector`, `ScannerOrchestrator`)
- **Functions:** snake_case (`detect()`, `calculate_atr()`)
- **Constants:** UPPER_SNAKE_CASE
- **Config access:** `from config import MODES, PORTFOLIO, NOTIFICATIONS`
- **Regime pattern:** `cfg = MODES[mode]; adj = REGIME_CONFIG[regime]; val = cfg['x'] * adj['x_mult']`
- **Indentation:** 4 spaces

## Output

```
scanner_output/
├── signals/     - Breakout signals (CSV/JSON)
├── exits/       - Exit evaluation results
├── rejections/  - Near-miss symbols with rejection reasons
├── logs/        - Scan and cron logs
└── backtests/   - Historical backtest results (JSON)
```

## Debugging

- No signals? Check `scanner_output/rejections/` for reasons
- IB connection fails? Use `--mock` or ensure IB Gateway on port 7497
- Wrong timeframe? Check `_normalize_timeframe()` in market_data.py
