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
- **`CONFIG.md`**: Comprehensive parameter reference for all 70+ tunable settings (see this for detailed docs on TREND_CONFIRM, BOUNCE_BEAR_GATE, REGIME_CONFIG, SCORING_WEIGHTS, etc.)
- **`quantkit/`**: Extracted pip-installable lib (indicators/patterns/fib/regime/sentiment/portfolio); `indicators.py`/`pattern_recognition.py`/etc. are thin shims over it. All modules expect **lowercase OHLCV** columns:
  ```python
  df = pd.read_csv('data/AAPL.csv', index_col='Date', parse_dates=True)
  df.columns = df.columns.str.lower()
  ```
  See `quantkit/README.md` for the full data-loading and integration guide.

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

### Active Config: V9-C + BBG15 + Pooled Cap + ATR×2.0 Always-On Trail (2026-05-07)
**Files:**
- `config.py` — `V9H_REGIME_GATE['enabled'] = False`, `BOUNCE_BEAR_GATE = 15`, `TREND_CONFIRM['enabled'] = True`, `TREND_CONFIRM['enabled_paths'] = ['A']`
- `auto_portfolio.py` — cross-day pooled cap (`MAX_ADDS_PER_SCAN = 10`, date-grouped pooling, Dist tiebreak capped at 25%)
- `backtest_regime_compare.py` — `--atr-trail-always --atr-trail-mult 2.0` (always-on ATR×2.0 trailing stop from entry, replaces post-TP trail)

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
**Note:** The +195% figure used an unrecorded symbol set and a calendar-day exit bug. See corrected baselines below.

| Config | 5yr Compound | vs OLD |
|--------|-------------|--------|
| OLD — V9-C + BBG15, per-file cap | **+109%** | baseline |
| NEW — pooled cap + Dist tiebreak, no TREND_CONFIRM | **+195%** ⚠ unreprod. | **+86 pts** ✓ |
| NEW — same + TREND_CONFIRM Path A+B | **+171%** | +62 pts |
| TREND_CONFIRM A+B isolated impact | **−24 pts** | destroys edge |

**Key findings:**
- **Pooled cap fix is the real winner (+86 pts):** Cross-day signal pooling (group files by date, rank globally by Quality→WinProb→R:R→Dist≤25%→Vol, apply MAX_ADDS_PER_SCAN=10 globally) eliminates the per-file first-3-wins problem that caused 2026-04-06 backlog to cap-skip INTC/AMD/NVDA.
- **TREND_CONFIRM Path B destroys edge (−24 pts):** Path B (4-of-5 prior bars score ≥6) fires 3.4× more signals in choppy/trending markets (600–900 extra/year in 2023–2025) — turns a sniper into a dragnet. Disabled.
- **TREND_CONFIRM Path A retained (dormant):** Path A (all 7 gates in single bar) fires ~50–100 signals/year — high-conviction only. Net impact untested but likely neutral; kept active for live capture of textbook SMA150+MACD+RSI+vol breakouts.
- **Dist tiebreak capped at 25%:** Prevents YPF-style picks (high prior momentum ≠ forward returns). Vol is secondary tiebreaker.
- **Current best config:** NEW-no-TC — if Path A proves noisy, set `TREND_CONFIRM['enabled'] = False`.

### Corrected Champion Baselines (2026-05-03, calendar-day exits fixed)
Bug fixed: `days_held` for MAX_HOLD exit was using trading days (extended hold to ~43 cal days); reverted to calendar days (30 cal days = intended behavior). The +195% figure is not reproducible — it used an unrecorded symbol set.

| Universe | 5yr Compound | Avg Sharpe | Notes |
|----------|-------------|-----------|-------|
| `optimizer_watch.txt` (50 curated) | **+136.8%** | **+0.88** | vs SPY +63.4% |
| `all.txt` (200 random, seed=42) | **~+80%** | ~+0.80 | vs SPY +63.4%; May 1 pre-fix run |

Per-year cap=10 ★ on optimizer_watch.txt (post-TP trail baseline):

| Year | Return | Sharpe | >15d WR |
|------|--------|--------|---------|
| 2022 | -12.94% | -0.33 | 72 trades @ 73.6% |
| 2023 | +98.17% | 3.28 | 59 trades @ 78.0% |
| 2024 | +29.41% | 1.37 | 55 trades @ 67.3% |
| 2025 | +9.78% | 0.57 | 45 trades @ 71.1% |
| 2026 | -3.37% | -0.51 | 16 trades @ 75.0% |

### NEW Champion: ATR×2.0 Always-On Trail (2026-05-07)
CLI: `python backtest_regime_compare.py --no-tc --bounce-bear-gate 15 --watchlist input/optimizer_watch.txt --atr-trail-always`
Exit code 0, validated on optimizer_watch.txt (50 curated symbols). Avg Sharpe +1.75 (5yr) confirmed by full validation run.

**Mechanism:** ATR×2.0 trailing stop rides up from entry day 1 (replaces fixed stop_loss for active positions). Fixed stop_loss acts as ultimate floor before trail is established (first 14 bars). Much tighter exit on losers — ≤15d WR jumps from 34% → 54%.

| Year | Return (ATR-always) | Return (post-TP baseline) | Delta |
|------|--------------------|-----------------------------|-------|
| 2022 | **-5.26%** | -12.94% | +7.68% |
| 2023 | **+102.22%** | +98.17% | +4.05% |
| 2024 | **+30.93%** | +29.41% | +1.52% |
| 2025 | **+22.93%** | +9.78% | +13.15% |
| 2026 | **+8.37%** | -3.37% | +11.74% |
| **5yr compound** | **+234.2%** | +136.8% | **+97 pts** |
| **Avg Sharpe** | **+1.66** (sweep) / **+1.75** (validation) | +0.88 | **+0.78–0.87** |

**Key insight:** ATR trail cuts bear-year losses aggressively (2022: -5% vs -13%) while letting winners ride in bull years. The trail is always active so it exits near local highs rather than waiting for TP trigger.

**2026 hold-split (full validation):**
- ATR-always: ≤15d 39 trades WR=53.8%, >15d 9 trades WR=100%
- Post-TP: ≤15d 32 trades WR=34.4%, >15d 16 trades WR=75%

### ⚠ Harness-Loss Incident + Restoration (2026-07-02)
**The `--atr-trail-always` backtest branch was never committed.** Commit 240f96c's
message claims it modified `backtest_regime_compare.py`, but the committed file never
contained the branch (it also reverted 7910d6e's capital compounding). The +234% table
above came from a lost, uncommitted working tree; `param_sweep.py`'s ATR sweep crashed
(`simulate()` lacked its kwargs) from May 7 until the 2026-07-02 restore.

**Restoration validated by exact-match isolation** (worktrees at 240f96c, shared data
cache; logs in `scanner_output/backtests/atr_trail_restore_20260702/`):
- R3a: champion commit + post-TP exit reproduces the corrected baselines table above
  **to the decimal** (2022 -12.94%/72 >15d @73.6%; 2023 +98.17%/3.28; 2024 +29.41%/1.37)
  → data, watchlist, and signal stream are all stable. Not data drift.
- R1=R2=R3b=R4: tunnel pattern, quantkit extraction, `score_adjustments.json`, and the
  WinProb calibration JSON all have **zero effect** on the champion row (99.7% BOUNCE
  signals; `detect_bounce` uses its own pass-count quality, not SCORING_WEIGHTS).
- Restored semantics (pinned by `tests/test_backtest_atr_trail.py`): **CLOSE-based**
  trigger (intraday dips below the trail do NOT exit — lo-based whipsaw gave 2022
  -24.8%), exit booked at the stop level, trail armed with the entry-day close, fixed
  stop as floor, monotonic ratchet — mirrors `auto_portfolio._raise_atr_trail` exactly.

**New canonical champion baselines (reproducible, 2026-07-02, optimizer_watch.txt):**
CLI: `--no-tc --bounce-bear-gate 15 --watchlist input/optimizer_watch.txt --skip-old --atr-trail-always`

| Year | ATR-always (canonical) | Post-TP baseline | recorded-but-lost table |
|------|------------------------|------------------|-------------------------|
| 2022 | **-10.75%** (Sharpe -0.24) | -12.94% (-0.33) | -5.26% ⚠ unreprod. |
| 2023 | **+142.17%** (+3.42) | +98.17% (+3.28) | +102.22% ⚠ |
| 2024 | **+29.92%** (+1.51) | +29.41% (+1.37) | +30.93% |
| 2025 | **+19.63%** (+1.09) | +9.78% (+0.57) | +22.93% ⚠ |
| 2026* | **+6.32%** (+0.90) | -3.37% (-0.51) | +8.37% (May cut) |
| 5yr compound | **~+257%** | +136.8% | +234.2% ⚠ |
| Avg Sharpe | **+1.33** | +0.88 | +1.66–1.75 ⚠ |

*2026 through Jul 1. The lost table's exact numbers are unreproducible (its residual
vs the restore is unknowable without the lost code); the champion's **direction is
fully confirmed** — ATR-always beats post-TP in every year and by ~+120 pts compound /
+0.45 Sharpe. Live trading was never affected (`_raise_atr_trail` shipped correctly).

### WinProb Calibration (2026-07-02) — wired, structurally inert on current mix
`calibrate_winprob.py` fits empirical WR by SIGNAL_TYPE|QUALITY from champion-exit
trade logs (EB shrinkage k=10, train 22-24/holdout 25-26; holdout err 3.5%). Scanner
loads `scanner_output/winprob_calibration.json` (config `WINPROB_CALIBRATION`,
backtest ablation flag `--no-winprob-cal`); BOUNCE/CONTINUATION/SMA20_CROSS/
TREND_CONFIRM now stamp WinProb (previously ranked as 0 in the admission sort).
**Finding:** 490 champion trades collapse to one bucket (BOUNCE|PREMIUM) and regime is
constant within a day → a bucket lookup cannot reorder the within-day pooled-cap
ranking (R4 ≡ R1 confirmed). Becomes active if the signal mix diversifies. Real
ranking upgrade requires per-signal features (vol, RSI, drawdown depth) logged into
backtest trades + a continuous model. Calibration JSON deliberately NOT deployed to
`scanner_output/` (kept in `backtests/atr_trail_restore_20260702/`).

### Daytrade Admission A/B (2026-07-02) — keep current config
`daytrade_admission_ab.py` replayed the live S3 signal backlog (807 files, Apr 1–Jun 9:
511 swing / 272 daytrade / 24 longterm) through a copy of the admission pipeline +
live ATR-trail exit. B(no-daytrade)−A(control) Sharpe = **+0.09** — below the ≥+0.10
ship rule → no change. Mechanism: the pooled ranking already de-facto excludes
daytrade (only **2 of 41** control-arm trades were daytrade; −$97). Cross-arm: ≤15d
holds 0–10% WR everywhere, >15d 68–85% — short-hold drag is signal-side, confirmed on
live data. Notable: longterm produced ~all control-arm P&L (+$1,156 on 17 trades from
only 24 files) — the longterm pipeline is under-supplied relative to its edge.

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

### Results Log — Run 1: all.txt 200 symbols (INVALIDATED)
~~200 symbols, seed=42, `--no-tc --bounce-bear-gate 15` (run 2026-05-02)~~
**Invalidated:** run used trading-day exits (bug). Results were -26pts low. See Run 2.

### Results Log — Run 2: optimizer_watch.txt 50 symbols (2026-05-03, corrected)
`optimizer_watch.txt`, `--no-tc --bounce-bear-gate 15`, calendar-day exits (fixed)

| Run | 5yr Compound | Avg Sharpe | Worst DD | Notes |
|-----|-------------|-----------|---------|-------|
| A — cap=10 ★ | +136.8% | +0.88 | -29.49% | Reference anchor |
| B — cap=2 ★★ | +36.7% | +0.42 | -26.17% | -100 pts — collapses on thin 50-stock pool |
| C — Filter only | not run | — | — | No-op confirmed (D≈B) |
| D — sel+cap2 | +36.7% | +0.44 | -26.17% | Indistinguishable from B |

Per-year >15d WR (the litmus test):

| Year | A >15d WR | B >15d WR | Delta |
|------|-----------|-----------|-------|
| 2022 | 73.6% (72 trades) | 72.7% (33 trades) | -0.9% ✓ sparse but ok |
| 2023 | 78.0% (59 trades) | 79.2% (48 trades) | +1.2% ✓ |
| 2024 | 67.3% (55 trades) | 64.1% (39 trades) | -3.2% ⚠ |
| 2025 | 71.1% (45 trades) | 79.2% (24 trades) | +8.1% ✓ |
| 2026 | 75.0% (16 trades) | 72.7% (11 trades) | -2.3% ⚠ sparse |

**Verdict: Keep cap=10 champion. Experiment closed.**
- D Sharpe −0.44 below A → decisively outside ±0.05 rule → keep champion
- cap=2 destroys 2023 bull-year return (−38 pts) on the 50-stock universe — signal pool too thin
- >15d WR holds up for A across all years (67–78%); cap=2 produces same or slightly worse WR at fewer trades
- **Key structural finding:** cap=2 does NOT reduce ≤15d trade share — type mix is unchanged. Short-hold drag requires signal-side filtering, not cap adjustment.
- **SELECTIVE is a confirmed no-op** on the NEW config path: new_premium is 99%+ BOUNCE type already.

## 9. Server Deployment Cutover (2026-07-07)

**Production is now live on AWS EC2 (`63.176.155.83`, hostname `ip-172-31-35-253`), not the Mac.**
SSH: `ubuntu@63.176.155.83` with key `~/.ssh/stocksbreakout-key.pem`. Full `deploy/README.md`
steps 1–7 completed. `gilhadas-stocks.com` / `api.gilhadas-stocks.com` now serve from this box;
`expenses.gilhadas-stocks.com` stays on the Mac's original `stocksbreakout` tunnel (trimmed
config, two stock hostnames removed from `~/.cloudflared/config.yml`).

**There is a second, unrelated Oracle Cloud VM (`82.70.210.194`, key `daytrade_oracle`,
`il-jerusalem-1`)** — this is NOT part of this deployment. It's a separate, already-live
production box running the `daytrade` engine/web/IB-Gateway/Caddy stack (created 2026-06-15).
A stocksBreakout repo clone + `.env` copy were placed there mid-session by mistake before this
was discovered — harmless (never built/run), but stale; clean up or ignore.

**Key gotchas hit during cutover (useful if redoing this or debugging drift):**
- **Prior-session work was lost/orphaned on the EC2 box.** A previous, unrecorded session had
  already done steps 1–5 there via manual `scp` (not git) — repo was stuck at commit `d39feb9`
  with uncommitted local edits to `Dockerfile`/`compose.yaml`/`docker/crontab`/etc. Diffed every
  file before touching anything: all substantive changes were byte-identical to what's already
  on `origin/main` (the containerization feature had since been properly committed elsewhere) —
  nothing was lost by `git reset --hard origin/main` + `git clean -fd`. Only the gitignored
  `deploy/cloudflared/config.yml` + `<UUID>.json` (the real tunnel credentials) were irreplaceable;
  backed those up to the local repo's `deploy/cloudflared/` (gitignored, not committed) before
  resetting.
- **`cloudflared tunnel route dns --overwrite-dns` does not overwrite an existing tunnel-owned
  CNAME**, even pointing at a *different* tunnel — it silently no-ops and reports "already
  configured." Only works for plain A/AAAA/CNAME records. Had to manually edit the CNAME target
  in the Cloudflare dashboard (DNS → change target to `<new-tunnel-id>.cfargotunnel.com`) instead.
- **EC2 disk was undersized** (29GB total, only 3.7GB RAM — below the README's 4GB minimum) and
  hit "No space left on device" mid-build; `docker builder prune -af` reclaimed 12.2GB of stale
  build cache, which was enough. Worth resizing the volume/instance if this recurs.
- **This Mac's system `crontab -l` has a full stocksBreakout schedule installed with
  `PROJECT_ROOT=/mnt/c/Users/User/Desktop/Develop/stocksBreakout`** — a WSL/Windows path that
  doesn't exist here. It has never actually fired successfully on this Mac (silent `cd` failure);
  live scans were always driven by `cron_agent.py` instead. Left in place (inert) but flagged as
  stale — clean up separately if it's noise.
- **Verify cutover with a stop/start test, not `cloudflared tunnel route dns` output alone:**
  briefly `docker compose stop api` on the target box and curl the public hostname — `502` proves
  traffic is landing there, a normal response means DNS hasn't actually moved yet regardless of
  what the CLI reports.

## 10. 2026 YTD Backtest + NORMAL-Regime Diagnosis (2026-07-21)

### `--end-date` flag added to `backtest_regime_compare.py`
`run_year()` previously hardcoded `end = f"{year}-12-31"` with no override — a `--years 2026` run
silently simulated the full calendar year regardless of how much of it had actually happened.
Added `--end-date YYYY-MM-DD` (applies to whichever requested year it falls in) so partial-year
runs are explicit instead of accidental. In practice this was a no-op for today's run: yfinance
has no bars past the real current date, so the untruncated run and the `--end-date 2026-07-21`
run produced byte-identical results — confirms the underlying data (not a code bug) is what was
already limiting the simulation, and this table is genuinely YTD.

### 2026 YTD Results (through 2026-07-21, optimizer_watch.txt, 144 trading days)
CLI: `--no-tc --bounce-bear-gate 15 --atr-trail-always --skip-old --end-date 2026-07-21`

| Strategy | Trades | Return | WR% | Sharpe | MaxDD | vs SPY |
|---|---|---|---|---|---|---|
| SPY Buy & Hold | — | +9.20% | — | 1.26 | -8.88% | — |
| **Champion (pooled-cap=10)** | 65 | **+5.21%** | 12.3% | **0.71** | -8.59% | **-3.99%** |
| pooled-cap=2 variant | 50 | +5.67% | 14.0% | 0.86 | -7.85% | -3.54% |

By regime: EXPANSION (48 days) +9.82%/Sharpe 1.48, beats SPY by +0.62% — where the edge lives.
RED_MARKET (26 days) flat, +0.38%. **NORMAL (44 days) -4.43%/Sharpe -0.92**, -13.6% vs SPY — the
whole year's underperformance is concentrated here. 2026 is currently a losing year vs. SPY for
the strategy (Sharpe well below the 5yr average of +1.33); NORMAL regime is why.

### NORMAL-regime root cause (trade-level dig, `trades_2026_NEW_PREMIUMplus_pooled-10_ATRalways-2.csv`)
19 NORMAL trades, 21% WR, -$3,477 total. Two clusters explain nearly all of it:
- **2026-02-06 cohort — net -$1,495, 1 winner of 10.** ⚠ Correction: this was originally logged as
  8 positions / -$2,619 (COIN, HOOD, RBLX, ORCL, SNOW, SOFI, CRWD, MDB — that subset's sum is
  correct at -$2,619). The full trade log shows **all 10 pooled-cap slots** fired that day —
  MARA (-$87) and PLTR (+$1,211, a big winner) were entered the same day and missed in the
  original count, netting the full cohort to -$1,495 (43% of the NORMAL total, not 75%). SPY had
  dipped -2.5% (Feb 2–5) then bounced +1.92% on Feb 6 itself — the exact day these fired.
  `classify_day_regime()` only reads SPY breadth, so it read "relief bounce" and the RSI<40
  NORMAL-bounce filter (F1, line ~213) waved through a full day's worth of correlated high-beta/
  growth names at once. One factor bet (growth/crypto-adjacent beta snapping back with the
  market) consumed the *entire* pooled-cap(10/day) budget, because the cap has no correlation/
  sector-concentration dedup — it ranks by Quality→WinProb→R:R→Dist only.
- **MSTR (-12.2%, June) + IREN (-17.1%, July) — net -$1,852.** Both crypto-proxy names, both
  stopped out, same theme: high-beta names diverging from SPY's calmer +9.2%-YTD tape.
- **Root cause:** the NORMAL bounce filter is single-stock (RSI/R:R/vol only) — no cross-sectional
  check for correlated names firing together. Not a bug; an unhedged gap in the SPY-only regime
  gate.

### Ablation: Same-day NORMAL+BOUNCE concentration cap (tested 2026-07-21)
`_pooled_cap()` gained a `normal_bounce_cap` param (CLI: `--normal-bounce-cap N`, default 0=off,
dormant unless passed) that additionally caps same-day BOUNCE signals fired in NORMAL regime to
at most N within the existing pooled-cap ranking — directly targeting the Feb-6-style cluster
above. Tested N=2 on `optimizer_watch.txt`, `--no-tc --bounce-bear-gate 15 --atr-trail-always`:

| Year | Champion Sharpe | +NormalBounceCap=2 | Champion >15d WR | +NBC2 >15d WR |
|------|-----------------|---------------------|-------------------|----------------|
| 2022 (bear) | -0.24 | -0.23 | 82.2% | 82.2% |
| 2023 (bull) | +3.42 | +3.42 (identical — cap never fires, cluster was NORMAL-only) | 87.7% | 87.7% |
| 2024 (bull) | +1.51 | +1.51 (identical) | 80.0% | 80.0% |
| 2025 (mixed) | +1.09 | +1.11 | 83.8% | 83.8% |
| 2026 (mixed, YTD Jul 21) | +0.71 | **+1.04** | 68.8% | **73.3%** |
| **5yr avg Sharpe** | **+1.30** | **+1.37** | — | never shrinks |

2026: return +5.21%→+7.31%, trades 65→57 (cuts exactly 8/68 signals — the excess Feb-6 slots
beyond N=2), WR 12.3%→14.0%. **Verdict: promising but below this project's own ≥+0.10 aggregate-
Sharpe ship bar** (+0.07 avg lift) — however unlike every other single-lever ablation logged this
cycle (Tension Index, Supertrend, Breakeven, WinProb-cal, Daytrade admission A/B — all null), this
one is weakly dominant: it never makes a single year worse and the >15d WR (the edge) never
shrinks. Left dormant (`--normal-bounce-cap` unset in production); worth a broader-universe
confirmation (e.g. `all.txt` 200 symbols) before considering promotion to the live default.
