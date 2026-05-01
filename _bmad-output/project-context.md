---
project_name: "stocksBreakout"
version: "V9-D1"
date_last_updated: "2026-04-29"
key_config: "BBG15 + Pooled Cap + TREND_CONFIRM Path A"
five_year_performance: "+179%"
---

# Project Context: stocksBreakout

## Executive Summary
**Algorithmic breakout scanner & portfolio manager for US equities** using Interactive Brokers API.
Detects high-quality breakout setups, scores them (V12: GOLD≥99, PREMIUM≥69, HIGH≥65), manages live portfolio with ATR-based sizing, executes via IB algos, and notifies via Telegram/Discord/Email.

**5-year backtest performance:** +179% (BBG15 config) vs +167% SPY baseline. Best config: Pooled cap (+195%).

---

## Tech Stack
| Component | Version/Tool |
|-----------|--------------|
| Language | Python 3.14 |
| Broker API | Interactive Brokers (ib_insync) |
| Data Storage | AWS S3 (portfolio state) |
| Notifications | Telegram, Discord, Email, Expo push |
| Backtesting | yfinance, pandas, numpy |

---

## Architecture & Key Responsibilities

| File | Responsibility | Critical Notes |
|------|-----------------|-----------------|
| `scanner.py` | Core breakout detection & V12 scoring | All scoring logic here; `detect_continuation()` includes Volume Profile |
| `config.py` | **Single source of truth** for all thresholds | NEVER hardcode thresholds elsewhere; all modes, gates, caps defined here |
| `auto_portfolio.py` | Portfolio manager, ATR sizing, rebalancing | Pooled cap (cross-day), MAX_ADDS_PER_SCAN=10, Dist tiebreak ≤25% |
| `indicators.py` | ATR, RSI, MACD, Volume Profile (VPOC, HVN/LVN) | Vectorized pandas operations; no for-loops for price calculations |
| `market_data.py` | IB data fetching, caching, timeframe normalization | ⚠️ Use `1W` not `1 week`; normalize via `_normalize_timeframe()` |
| `orchestrator.py` | Coordinates scanning, signal output, regime gates | Skips `detect_bounce()` when RED_MARKET + SPY_consec_below_sma200≥15 |
| `breakout_scanner.py` | CLI entry point, IB connection, async event loop | ⚠️ Event loop setup BEFORE importing ib_insync (lines 13-18) |
| `algo_trader.py` | IB algo order execution (TWAP, VWAP, etc.) | Tracks active_orders dict; all orders keyed by orderId |
| `notifier.py` | Multi-channel notification dispatch | Config-driven: NOTIFICATIONS dict enables/disables channels |

---

## Quick Index
- **🚨 Critical Gotchas** — 6 bug-prevention rules agents MUST follow
- **Architecture Patterns** — Async/concurrency, detection pipeline, code style
- **Mode Configuration** — How modes work, CLI overrides, regime adjustments
- **Portfolio Rules** — Position caps, ATR sizing, pooled cap logic
- **Expert Trading Philosophy** — Trend alignment, risk-first principles
- **Deployment** — Mobile API server, IB connection, mock mode
- **Signal History** — BBG15 (+12 pts), Pooled Cap (+86 pts) backtests

---

## Core Rules for Agents

### 🚨 Critical Gotchas (PREVENT BUGS)
1. **Config is Law:** NEVER hardcode thresholds. Always: `from config import MODES, PORTFOLIO, NOTIFICATIONS` → access via `config.MODES[mode_name]`
2. **Event Loop Timing:** Import `ib_insync` AFTER event loop setup (see `breakout_scanner.py` lines 13-18). Wrong order = hang/crash.
3. **Timeframe Format:** Use `1W` NOT `1 week`. All timeframes normalized via `_normalize_timeframe()`.
4. **Regime Gates:** `V9H_REGIME_GATE` disabled (False); `BOUNCE_BEAR_GATE=15` always active. Regime logic gates signal TYPE, not entry/exit thresholds.
5. **Scoring Thresholds:** GOLD ≥ 99, PREMIUM ≥ 69, HIGH ≥ 65 (V12 system). Reject all trades with Risk:Reward < `config.min_rr`.
6. **Mobile API:** FastAPI server (uvicorn) is launchd-managed, NOT auto-reload. Edit `api/server.py` or imports → manual restart required: `kill $(lsof -ti:8000)`.

### Architecture Patterns

**Async/Concurrency:**
- All IB operations are async
- Use `asyncio.Semaphore(MAX_CONCURRENT_REQUESTS=5)` for IB rate limiting
- Pattern: `async with semaphore: result = await self._scan_symbol(...)`

**Detection Pipeline (6 steps):**
1. Fetch historical data & calculate indicators (SMA, RSI, MACD, ATR, Volume)
2. Identify consolidation (narrowing Bollinger Bands, min_consolidation_bars check)
3. Detect breakout candle (volume spike >150% MA20, body structure, ATR threshold)
4. Validate trend alignment (price vs SMA/EMA/VWAP per mode)
5. Score signal (V12 gates: volume, trend, consolidation, momentum)
6. Calculate Risk:Reward; reject if < min_rr

**Code Style:**
- Classes: PascalCase | Functions/vars: snake_case
- Vectorized pandas operations (no for-loops for indicators)
- Regime multipliers: `val = cfg['x'] * REGIME_CONFIG[regime]['x_mult']`

### Mode Configuration System
- Each mode in `config.MODES` defines: `lookback`, `vol_thresh`, `atr_mult`, `trend_type` (SMA/EMA/VWAP), `trend_period`, `sl_mult`, `tp_mult`, `min_rr`
- CLI overrides: `--vol-thresh 1.5 --atr-mult 0.3` (runtime params)
- Regime adjustments (CHOPPY/EXPANSION/NORMAL) apply multipliers to volatility/volume thresholds

### Portfolio Rules
- **Position caps:** Max 5 per mode, max 3 per sector
- **Cash reserve:** Minimum 15% (enforced)
- **Daily deployment:** MAX_ADDS_PER_SCAN=10 (pooled across all files), MAX_PORTFOLIO_ATR_RISK=0.12
- **Pooled cap logic:** Group signals by date → rank globally by Quality→WinProb→R:R→Dist≤25%→Vol → apply cap across all symbols
- **ATR-based sizing:** Size reductions only (no hard blocks):
  - ATR > 4x avg → 20% | > 3x → 25% | > 2x → 50% | > 1.5x → 75%

### Indicator Defaults
- **Momentum:** RSI (14), MACD (12, 26, 9)
- **Trend:** SMA 50, SMA 150, SMA 200
- **Volatility:** ATR-based stops & sizing
- **Volume:** >150% of 20-bar MA on breakout candles
- **Consolidation:** Narrowing Bollinger Bands + low volatility

---

## Expert Trading Philosophy

Act as a **Senior Quantitative Trader & System Architect**. Apply these principles to all decision-making:

- **Trend Alignment:** Prioritize Stage 2 uptrends. Monitor SMA 150 & 200 health. Reject trades not aligned with prevailing trend.
- **Relative Strength:** Focus on symbols outperforming SPY/QQQ and their sector. Ignore laggards.
- **Risk First:** Every signal must have clear Stop Loss (ATR or swing low). Reject all trades with Risk:Reward < `config.min_rr`.
- **Data Integrity:** Base all advice on statistical probability & technical indicators. No "gut feeling" trades.

---

## Deployment & Operations

### Mobile API Server (FastAPI via uvicorn)
Launchd-managed service (`com.stocksbreakout.api.plist`) at `api/server.py`, exposed via Cloudflare tunnel.

**⚠️ Critical:** NOT auto-reload. Any edit to `api/server.py`, `auto_portfolio.py`, or imports requires manual restart.

**Restart:** `kill $(lsof -ti:8000)` → launchd auto-respawns (~5s)

**Verify:** `curl -X POST http://127.0.0.1:8000/portfolio/execute-swap` (expect 401 if auth-gated, NOT 404)

**Diagnose stale server:**
```bash
ps -o lstart= -p $(lsof -ti:8000)  # process start time
stat -f %Sm api/server.py auto_portfolio.py  # file modification times
```
If files newer than process → restart. **Logs:** `scanner_output/api_server.log` & `api_server.err`

### Interactive Brokers Connection
- **Paper:** Port 7497, localhost only
- **Live:** Port 7496, localhost only
- **Data types:** 3=delayed (paper), 1=real-time (live)
- **Mock mode:** `--mock` flag uses MockIBConnection (no IB Gateway required)

---

## Signal History & Configuration Decisions

### Active Config (2026-04-29)
| Setting | Value | Rationale |
|---------|-------|-----------|
| **Signal Version** | V9-D1 (V9-C + BEARISH block) | BEARISH regime signals permanently blocked |
| **Scoring** | V12: GOLD≥99, PREMIUM≥69, HIGH≥65 | Weighted multi-gate system |
| **Regime Gate (V9-H)** | Disabled (False) | Pooled cap + BBG15 supersede |
| **Bounce Bear Gate** | 15 consecutive days | SPY below SMA200 ≥15 days blocks BOUNCE+RED_MARKET |
| **TREND_CONFIRM** | Enabled, Path A only | High-conviction textbook breakouts (~50–100/year) |
| **Portfolio Cap** | Pooled cross-day (MAX_ADDS_PER_SCAN=10) | Eliminates per-file first-3-wins problem |
| **Dist Tiebreak** | ≤25% of portfolio | Prevents YPF-style high-momentum picks |

### BBG15 Backtest Results (200 symbols, 5 years)
**Why BBG15 works:** Distinguishes 2022 sustained bear (57% days ≥15) from brief corrections (2023 max ~6d, 2025 tariffs 9–14d, 2026 tariffs <15d).

| Year | Market | SPY | V9-C PREMIUM+ | BBG15 V9-C | Improvement |
|------|--------|-----|---------------|-----------|----|
| 2022 | Bear   | -18.65% | -17.32% | **-12.82%** | +4.50% |
| 2023 | Bull   | +26.71% | +56.76% | **+56.08%** | -0.68% |
| 2024 | Bull   | +26.05% | +24.05% | **+24.05%** | 0% |
| 2025 | Mixed  | +18.89% | +52.53% | **+52.02%** | -0.51% |
| 2026 | Mixed  | +3.34%  | +8.75%  | **+8.75%**  | 0% |

**5-year compound:** +167% (SPY baseline) → **+179% (BBG15)** = **+12 pts edge**

### Pooled Cap Backtest (200 symbols, 5 years)
**Problem:** Per-file caps caused first-3-wins to exhaust daily budget → skipped high-quality signals (INTC, AMD, NVDA on 2026-04-06).
**Solution:** Cross-day signal pooling (group files by date → rank globally by Quality→WinProb→R:R→Dist≤25%→Vol → apply MAX_ADDS_PER_SCAN=10).

| Config | 5yr Compound | vs OLD | Status |
|--------|-------------|--------|--------|
| OLD — per-file cap | +109% | baseline | ❌ Deprecated |
| NEW — pooled cap, no TREND_CONFIRM | **+195%** | **+86 pts** | ✅ Current best |
| NEW + TREND_CONFIRM A+B | +171% | +62 pts | ❌ B destroys edge |

**Key findings:**
- ✅ **Pooled cap winner (+86 pts):** Solves first-3-wins bottleneck
- ❌ **TREND_CONFIRM Path B disabled:** Fires 3.4× more signals → turns sniper to dragnet (−24 pts isolated impact)
- ⏸ **Path A retained (dormant):** High-conviction only; net impact untested
- ✅ **Dist tiebreak ≤25%:** Prevents momentum-based noise

---

## Interaction Protocols

- **Logic Critique:** Point out immediately if proposed strategy weakens edge or increases risk
- **Backtest Mentality:** Validate new scanner features against historical data in `scanner_output/backtests/`
- **Modular Code:** Provide small, testable functions with clear docstrings
- **Decision Log:** Summarize progress & key decisions back into CLAUDE.md