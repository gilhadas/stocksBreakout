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

### Active Config: V9-C + BBG15 + Pooled Cap + TREND_CONFIRM Path A (2026-04-26)
**Files:**
- `config.py` — `V9H_REGIME_GATE['enabled'] = False`, `BOUNCE_BEAR_GATE = 15`, `TREND_CONFIRM['enabled'] = True`, `TREND_CONFIRM['enabled_paths'] = ['A']`
- `auto_portfolio.py` — cross-day pooled cap (`MAX_ADDS_PER_SCAN = 10`, date-grouped pooling, Dist tiebreak capped at 25%)

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

---

### Backtest: 200 symbols, 5 years PREMIUM+ TP→Trail (run 2026-04-26)
Three-way comparison: OLD (V9-C+BBG15) vs NEW-no-TC (pooled cap) vs NEW+TC (pooled cap + TREND_CONFIRM A+B).

| Config | 5yr Compound | vs OLD |
|--------|-------------|--------|
| OLD — V9-C + BBG15, per-file cap | **+109%** | baseline |
| NEW — pooled cap + Dist tiebreak, no TREND_CONFIRM | **+195%** | **+86 pts** ✓ |
| NEW — same + TREND_CONFIRM Path A+B | **+171%** | +62 pts |
| TREND_CONFIRM A+B isolated impact | **−24 pts** | destroys edge |

**Key findings:**
- **Pooled cap fix is the real winner (+86 pts):** Cross-day signal pooling (group files by date, rank globally by Quality→WinProb→R:R→Dist≤25%→Vol, apply MAX_ADDS_PER_SCAN=10 globally) eliminates the per-file first-3-wins problem that caused 2026-04-06 backlog to cap-skip INTC/AMD/NVDA.
- **TREND_CONFIRM Path B destroys edge (−24 pts):** Path B (4-of-5 prior bars score ≥6) fires 3.4× more signals in choppy/trending markets (600–900 extra/year in 2023–2025) — turns a sniper into a dragnet. Disabled.
- **TREND_CONFIRM Path A retained (dormant):** Path A (all 7 gates in single bar) fires ~50–100 signals/year — high-conviction only. Net impact untested but likely neutral; kept active for live capture of textbook SMA150+MACD+RSI+vol breakouts.
- **Dist tiebreak capped at 25%:** Prevents YPF-style picks (high prior momentum ≠ forward returns). Vol is secondary tiebreaker.
- **Current best config:** NEW-no-TC (+195%) — if Path A proves noisy, set `TREND_CONFIRM['enabled'] = False`.

---

## 8. Ablation Experiment: Pooled-Cap & Selective-Mode Isolation (2026-05-01)

### Why This Exists
The +195% 5yr compound edge lives almost entirely in **>15-day holds (66% WR)**.
Short holds (≤15d) show ~10% WR and drag performance. Before tightening the cap
or adding signal-type filters, each lever must be tested **independently**.

Daytrade was **never** in the backtest (`modes=['swing','longterm']` hardcoded) —
SELECTIVE_MODE's daytrade exclusion has zero effect on backtest results.
SELECTIVE_MODE itself is not wired into `backtest_regime_compare.py`; it lives
in `auto_portfolio.py` only. The `--pooled-cap` CLI flag is the correct lever.

### Experiment Matrix

| Run | CLI Flags | What It Isolates |
|-----|-----------|-----------------|
| A — Baseline | *(none)* | Current champion; reference anchor |
| B — Cap only | `--pooled-cap 2` | Does tighter cap cut losers without cutting winners? |
| C — Filter only | `--selective --pooled-cap 10` | Does signal-type gating alone improve hold duration? |
| D — Both | `--selective --pooled-cap 2` | Compound effect |

### Commands

```bash
# Prerequisite: regression tests must be green before interpreting results
pytest tests/test_backtest_pooled_cap.py -v

# A — Baseline (current champion)
python backtest_regime_compare.py --no-tc --bounce-bear-gate 15

# B — Cap only
python backtest_regime_compare.py --no-tc --bounce-bear-gate 15 --pooled-cap 2

# C — Filter only
python backtest_regime_compare.py --no-tc --bounce-bear-gate 15 --selective --pooled-cap 10

# D — Both
python backtest_regime_compare.py --no-tc --bounce-bear-gate 15 --selective --pooled-cap 2
```

### Diagnostic Metrics (record per year, per run)

| Metric | Role |
|--------|------|
| Total return % | Primary outcome — north star |
| **Sharpe ratio** | **Key diagnostic** — integrates return and volatility; best predictor of forward total return in this momentum system |
| Max drawdown % | Risk guardrail — not an optimization target |
| Hold ≤15d: count + WR% | Must shrink in B/C/D if the lever is working |
| Hold >15d: count + WR% | Must NOT shrink — this is the entire edge |

### How to Interpret

1. **Sharpe is the arbiter.** WR and return can diverge; Sharpe integrates both.
2. **Hold-duration split is the litmus test.** If Run B raises the >15d share without shrinking >15d count, the cap is effective. If Run B raises WR but total return drops, it is over-filtering.
3. **Regression gate is mandatory.** `tests/test_backtest_pooled_cap.py` must be green before any run result is treated as valid.

### Decision Rules

| Outcome | Action |
|---------|--------|
| D Sharpe > A by ≥ 0.10 | Ship `--selective` + lower default cap |
| D Sharpe within ± 0.05 of A | Keep current champion; close experiment |
| Any run: >15d WR drops vs A | Halt — the edge is being destroyed |

### Results Log
*(To be filled after runs complete)*

| Run | 5yr Return | Sharpe | Max DD | Hold >15d WR% |
|-----|-----------|--------|--------|--------------|
| A — Baseline | +195% | — | — | — |
| B — Cap=2 | TBD | TBD | TBD | TBD |
| C — Filter only | TBD | TBD | TBD | TBD |
| D — Both | TBD | TBD | TBD | TBD |
