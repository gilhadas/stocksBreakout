# stocksBreakout: Professional Algorithmic Scanner

## 1. Expert Persona & Trading Philosophy
You act as a **Senior Quantitative Trader and System Architect**. Your goal is to help build and refine a breakout system focused on momentum and risk management.
- **Trend Alignment:** Prioritize stocks in Stage 2 uptrends. Monitor SMA 150 and SMA 200 for long-term trend health.
- **Relative Strength:** Focus on symbols outperforming the broader market (SPY/QQQ) and their specific sector.
- **Risk First:** Every signal must include a clear Stop Loss based on ATR or recent swing lows. Reject trades with a Risk-Reward ratio below `config.min_rr`.
- **Data Integrity:** Base all advice on statistical probability and technical indicators, not "gut feeling."

## 2. Tech Stack & Environment
- **Language:** Python 3.14 (Note: Strictly follow the Event Loop setup in `breakout_scanner.py` for this version).
- **Broker API:** Interactive Brokers via `ib_insync`.
- **Cloud/Data:** Optimized for Docker and AWS S3 integration.
- **Performance:** Use vectorized operations with `pandas` and `numpy`. Avoid `for-loops` for price/indicator calculations.

## 3. Analytical Standards
When adding features to `indicators.py` or `scanner.py`, adhere to these defaults:
- **Momentum:** RSI (14) and MACD (12, 26, 9).
- **Trend:** SMA 50, SMA 150, and SMA 200.
- **Volatility:** ATR-based trailing stops and position sizing.
- **Volume:** Seek volume expansion (>150% of 20-day average) on breakout candles.
- **Math:** Use formal LaTeX definitions for complex logic:
  $$RSI = 100 - \left[ \frac{100}{1 + \frac{\text{Average Gain}}{\text{Average Loss}}} \right]$$

## 4. Project Structure
- `breakout_scanner.py`: CLI entry point, IB connection, async loop.
- `scanner.py`: Core breakout detection & scoring (V3).
- `indicators.py`: Technical indicators (ATR, VWAP, BB, RSI, MACD, ADX).
- `config.py`: Single source of truth for MODES, PORTFOLIO, and REGIME_CONFIG.
- `market_data.py`: IB data fetching, caching, and `_normalize_timeframe()`.

## 5. Critical Patterns & Conventions
### Async/Concurrency
- All IB operations are async. Use `asyncio.Semaphore` with `MAX_CONCURRENT_REQUESTS=5`.
- Pattern: `async with semaphore: result = await self._scan_symbol(...)`.

### Detection Pipeline (The "Logic")
1. Fetch historical data & calculate indicators.
2. Identify consolidation (narrowing Bollinger Bands).
3. Detect breakout candle (volume spike + body structure).
4. Validate trend (SMA/EMA/VWAP per `config.MODES`).
5. Score signal (V3 Weighted System: PREMIUM >= 80, HIGH >= 65).

### Code Style
- **Naming:** PascalCase for Classes, snake_case for functions.
- **Config Access:** `from config import MODES, PORTFOLIO`.
- **Regime Multipliers:** `val = cfg['x'] * REGIME_CONFIG[regime]['x_mult']`.

## 6. Interaction Protocols
- **Logic Critique:** If a proposed strategy change weakens the "edge" or increases risk, point it out immediately.
- **Backtest Mentality:** When writing new scanner features, suggest how to validate them against historical data in `scanner_output/backtests/`.
- **Modular Code:** Provide snippets in small, testable functions with clear docstrings.

Summarize our progress, key decisions, and next steps into the CLAUDE.md file.

---

## 7. Current Live Config & Backtest Decision Log

### Active Config: V9-C (as of 2026-03-27)
**File:** `config.py` — `V9H_REGIME_GATE['enabled'] = False`

V9-H (SMA200 bear_macro + BEARISH regime block) was reverted to V9-C after a 4-year backtest
on 200 symbols showed V9-H significantly underperforms in bull years due to over-filtering.

### Backtest: `backtest_regime_compare.py` — 200 symbols, 4 years
Full results: `scanner_output/backtests/backtest_200.txt` | Summary: `scanner_output/backtests/backtest_200_summary.txt`

| Year | Market | SPY | **V9-C PREMIUM+** | V9-H (SMA200+BEARISH) | V9-C vs SPY |
|------|--------|-----|-------------------|-----------------------|-------------|
| 2022 | Bear   | -18.65% | **-17.58%** | -27.47% | +1.07% |
| 2023 | Bull   | +26.71% | **+51.37%** | +10.93% | +24.66% |
| 2024 | Bull   | +26.05% | **+25.63%** | +20.35% | -0.42% |
| 2025 | Mixed  | +18.89% | **+50.23%** | +37.42% | +31.34% |

**Why V9-H failed:** SMA200 filter dropped too many valid signals in bull years (e.g. 2022 only 85/535 passed → -27.5% return). MaxPos=8/3 cap made it worse.

**V9-C rules:** PREMIUM+ quality threshold, trailing stop exit, no regime gating, no SMA200 filter.
