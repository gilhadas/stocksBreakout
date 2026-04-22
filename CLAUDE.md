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

## 5a. Mobile API Server (uvicorn)
The mobile app hits the local FastAPI server at `api/server.py` via the Cloudflare tunnel (`gilhadas-stocks.com`). The service is launchd-managed (`~/Library/LaunchAgents/com.stocksbreakout.api.plist`, `KeepAlive=true`) and is **not** started with `--reload`, so any edit to `api/server.py`, `auto_portfolio.py`, or anything they import requires a manual restart — otherwise new endpoints return 404 and new functions raise AttributeError on the running process.

Restart procedure (launchd auto-respawns within ~5s):
```bash
# 1. Kill the running uvicorn — launchd restarts it automatically
kill $(lsof -ti:8000)

# 2. Verify the new process is up — expect 401 (not 404) on an auth-gated endpoint
sleep 3
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/portfolio/execute-swap \
  -H "Content-Type: application/json" -d '{"close_symbol":"X","open_symbol":"Y"}'
```

Diagnose "mobile action does nothing" by comparing server start time to file mtime: `ps -o lstart= -p $(lsof -ti:8000)` vs `stat -f %Sm api/server.py auto_portfolio.py` — if the files are newer than the process, the server is stale. Logs: `scanner_output/api_server.log` and `scanner_output/api_server.err`.

## 6. Interaction Protocols
- **Logic Critique:** If a proposed strategy change weakens the "edge" or increases risk, point it out immediately.
- **Backtest Mentality:** When writing new scanner features, suggest how to validate them against historical data in `scanner_output/backtests/`.
- **Modular Code:** Provide snippets in small, testable functions with clear docstrings.

Summarize our progress, key decisions, and next steps into the CLAUDE.md file.

---

## 7. Current Live Config & Backtest Decision Log

### Active Config: V9-C + BOUNCE_BEAR_GATE=15 (added 2026-04-22)
**Files:** `config.py` — `V9H_REGIME_GATE['enabled'] = False`, `BOUNCE_BEAR_GATE = 15`

### Backtest: `backtest_regime_compare.py` — 200 symbols, 5 years (run 2026-04-22)
Includes RSI Wilder's EMA fix + regime fix + bounce_bear_gate=15.

| Year | Market | SPY | **V9-C PREMIUM+** | **BBG15 V9-C** | BBG15 vs Baseline |
|------|--------|-----|-------------------|----------------|-------------------|
| 2022 | Bear   | -18.65% | -17.32% | **-12.82%** | +4.50% |
| 2023 | Bull   | +26.71% | +56.76% | **+56.08%** | -0.68% |
| 2024 | Bull   | +26.05% | +24.05% | **+24.05%** | 0%     |
| 2025 | Mixed  | +18.89% | +52.53% | **+52.02%** | -0.51% |
| 2026 | Mixed  | +3.34%  | +8.75%  | **+8.75%**  | 0%     |

**5-year compound:** Baseline +167% → BBG15 **+179%** (+12%).

**Why BBG15:** Blocks BOUNCE+RED_MARKET only when SPY has been below SMA200 for ≥15 consecutive days. Distinguishes 2022 sustained bear (57% of days ≥15d) from brief corrections (2023 dips: max ~6d, April 2025 tariff: 9–14d, April 2026 tariff: <15d). BBG10 rejected — kills 2023 -30.7% vs baseline.

**Active rules:**
- `BOUNCE_BEAR_GATE = 15` in config.py (always active, independent of V9-H)
- `market_data.get_spy_consec_below_sma200()` — cached per session
- `orchestrator._scan_symbol()` skips `detect_bounce()` when `RED_MARKET + consec ≥ 15`
- BOUNCE requires GOLD quality only. No BEARISH block. V9-H gate disabled.
