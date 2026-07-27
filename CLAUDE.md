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

## 5a. Mobile API Server (uvicorn) — now on EC2, not the Mac (updated 2026-07-23)
Since the section 9 cutover (2026-07-07), the mobile app hits `api/server.py` running as
the `sb-api` Docker container on the EC2 box, reached via the Cloudflare tunnel
(`gilhadas-stocks.com` / `api.gilhadas-stocks.com`) that runs as the `sb-cloudflared`
container on the **same** box — not the Mac's tunnel. SSH: `ssh -i
~/.ssh/stocksbreakout-key.pem ubuntu@100.68.142.94` (Tailscale; the security group has
zero inbound rules, so this is the only direct path — SSM Run Command and the EC2 Serial
Console are the two independent fallbacks, see section 9).

**Code is baked into the image at build time, not volume-mounted** — a plain restart
reloads the *old* code. Any edit to `api/server.py`, `auto_portfolio.py`, or anything
they import needs a rebuild:
```bash
cd ~/stocksBreakout && git pull --ff-only && docker compose up -d --build api
```
A restart with no code change (e.g. after an `.env` edit) is `docker compose restart api`.

Verify: expect 401/422 (not 404 or connection refused) on an auth-gated endpoint:
```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://api.gilhadas-stocks.com/portfolio/execute-swap \
  -H "Content-Type: application/json" -d '{"close_symbol":"X","open_symbol":"Y"}'
```

Diagnose "mobile action does nothing" the same way as before, just on the box instead of
the Mac: `docker inspect sb-api --format '{{.State.StartedAt}}'` vs `git log -1
--format=%cI -- api/server.py auto_portfolio.py` — if the file changed after the
container started, it's stale (needs the rebuild above, not just a restart). Logs:
`docker compose logs -f api`, or `scanner_output/logs/` inside the `scanner_output`
volume for cron-side issues.

**The Mac's local copy of this service is retired.** `com.stocksbreakout.api.plist`
(launchd, port 8000) was already vestigial — nothing in `~/.cloudflared/config.yml`
routed to it since the section 9 cutover trimmed the stock hostnames out — and was
spawn-looping (`EX_CONFIG`) after a reboot rather than actually serving anything. Moved
to `~/Library/LaunchAgents/disabled/` 2026-07-23. `com.stocksbreakout.tunnel` is a
**different** case — left running, since it still serves
`expenses.gilhadas-stocks.com` (a separate app that never moved off the Mac).

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

⚠ **Sizing caveat (2026-07-22):** these baselines (and every table above) use the
simulator's idealized sizing, which ignores the real capital limit — see §11. Judge
config changes on the `--realistic-sizing` arm, not these headline numbers.

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

**Production is now live on AWS EC2 (instance `i-015657f7d29bb673e`, hostname
`ip-172-31-35-253`), not the Mac.** Public IP is an Elastic IP, **`3.122.167.124`**
(allocated 2026-07-23 — stable across stop/start; earlier IPs in this doc's history
churned on every restart because none was allocated until then). SSH is via Tailscale
only: `ssh -i ~/.ssh/stocksbreakout-key.pem ubuntu@100.68.142.94` — the security group
has **zero inbound rules**, so the public IP cannot be SSH'd to directly regardless of
which address it currently is. Full `deploy/README.md` steps 1–7 completed.
`gilhadas-stocks.com` / `api.gilhadas-stocks.com` now serve from this box;
`expenses.gilhadas-stocks.com` stays on the Mac's original `stocksbreakout` tunnel (trimmed
config, two stock hostnames removed from `~/.cloudflared/config.yml`).

**2026-07-23 reliability pass** (after an ~11h outage — I/O-latency stall, not OOM;
`dmesg`/`journalctl` showed zero oom-kill events and unused swap, but 63% iowait / 0%
idle / ~0% user CPU while every subsystem touching network or disk degraded in
lockstep — root cause of the iowait itself not pinpointed, treat as possible
transient EBS/hypervisor contention): per-container `mem_limit` + `oom_score_adj:
-500` on cloudflared/tailscale in `compose.yaml`, `deploy/setup-swap.sh` (4G
swapfile), two CloudWatch alarms (`stocksbreakout-instance-check-failed-reboot`,
`stocksbreakout-system-check-failed-recover`), detailed (1-min) monitoring enabled,
Elastic IP allocated, and a scoped recovery IAM policy
(`deploy/iam-recovery-policy.json`, attached to `stocks-breakout-s3-user`) so
reboot/stop/start/alarms/EIP/SSM no longer require an AWS console session. Six
healthchecks.io dead-man switches wired into `docker/crontab` (`HC_UUID_*` in
`.env`) — `SWING`/`VALIDATE` split into `_CLOSE`/`_LEARN` variants because the
original single UUID per name covered two cron lines at genuinely different times,
which Healthchecks.io can't express without either nightly false alarms or a grace
window loose enough to miss a real outage. `HC_UUID_DAYTRADE` deliberately left unconfigured — daytrade is no longer in use
(didn't deliver good results; retiring its cron jobs is a pending follow-up, not
yet done as of this writing). Still open: external off-box uptime check, Docker
log-size caps + disk alert, EC2 Serial Console + SSM verification.

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
(Rerun 2026-07-21 on optimizer_watch reproduced this table to the decimal; the broader-universe
NBC confirmation is still open.)

## 11. Realistic Capital Sizing — Methodology Correction (2026-07-21/22)

### The finding: all headline baselines are theoretical, not achievable P&L
Every backtest table above runs `simulate()` with its default sizing, which **ignores the
real capital limit**: position size is computed off *remaining* cash (so it shrinks as capital
gets tied up), and when even the shrunken size is unaffordable it downsizes to as little as
**1 share** rather than skipping. Live (`auto_portfolio.py`) does the opposite: sizes off
stable `data['capital']` (moves only via realized P&L) and **skips outright** when
`cost > available_cash`. Found 2026-07-21 on a 1375-symbol `all.txt` run: **97.5% of trades
ended up sized <50% of target** — the backtest was booking near-worthless fills live would
have skipped. Consequence: theoretical trade counts run **1.5–2.4× what $100k can fund**
(spy_plus 2022: 291 vs 123 real; 2025: 219 vs 112). Capital is a hard constraint and must
not be ignored — **standing rule: every future ablation/config decision must run and be
judged on the `--realistic-sizing` arm (Sharpe + MaxDD), with idealized numbers shown only
as reference.**

### New flags (uncommitted as of 2026-07-22, in `backtest_regime_compare.py`)
- `--realistic-sizing` — adds a REALISTIC A/B pair to the report: (A) live-mirror sizing,
  skip-for-cash, no swaps; (B) same + swap-on-skip mirroring `auto_portfolio.suggest_swaps()`.
  Off by default so all documented reproducible baselines are preserved.
- `--start-date YYYY-MM-DD` — start-of-window override (mirror of `--end-date`); data fetch
  keeps 400 lookback days so SMA150/200 stay valid on short windows.

### Logic validation (2026-07-22) — verified line-by-line vs `auto_portfolio.py`
- **Exact mirrors confirmed:** capital basis (`capital_for_sizing` ≡ `data['capital']`,
  updated `+= pnl` on every close path incl. swaps); skip-not-downsize (`cost > cap` ⇒ skip,
  ≡ `available_cash()` = capital − Σ open costs); `qty = max(1, …)` floor ≡ live
  `shares = max(1, int(...))`; 10%/2% val/risk caps ≡ `max_single_position_pct`; swap gates
  (weak = down ≥2% OR ≤4% from stop; score delta ≥20) ≡ `_SWAP_WEAK_PNL_PCT/_SWAP_STOP_PROXIMITY/
  _SWAP_MIN_SCORE_DELTA`; weakness formula term-for-term ≡ `_position_weakness_score`; implied
  score `(quality, 50.0, held_rr, 1.0)` ≡ `suggest_swaps`; swap exits use the same
  slippage/commission formula as regular closes. Signal dicts carry real `rr`/`win_prob`/`quality`
  keys into `_compute_priority_score` (not silent defaults).
- **Known intentional deviations (fine, documented):** backtest swaps fire same-day (live's
  ≤5-day freshness + positive-momentum filters trivially satisfied; target-sanity checks not
  replicated); backtest auto-executes every qualifying swap (live only *suggests* ≤3, human
  confirms) — that's the point of the A/B; backtest adds an affordability guard (won't liquidate
  a weak position unless the replacement is then affordable). Backtest sizing omits live's
  quality/ATR/event/balance multiplier stack — it mirrors the capital basis + skip policy,
  not the full live formula ("mirrors exactly" in the docstring overclaims slightly).
- **Cosmetic quirk:** a successfully swapped signal is still counted in "Skipped for cash"
  (append happens before the swap attempt) — display-only, no logic impact.
- **Regression:** `tests/test_backtest_pooled_cap.py` + `tests/test_backtest_atr_trail.py`
  all 20 pass — the default (idealized) path is byte-identical, baselines unaffected.

### Results so far
**A/B on live-like universe** (`all.txt` 1333 syms, 2026-05-21→07-21, SPY +0.17%): champion
idealized +11.13%/Sharpe 4.78 (325 trades); REALISTIC no-swap **+15.63%/Sharpe 5.14** on just
32 trades (304 skipped for cash); swap-on-skip +0.96%/Sharpe 0.45 (5 swaps) — **swaps −4.68
Sharpe, keep no-swap**. (An earlier same-evening run showing −1.33 had a swap-accounting bug;
the fix strengthened the verdict.)

**5-year realistic vs theoretical** (`spy_plus.txt` 500 syms, `--no-tc --bounce-bear-gate 15
--atr-trail-always --skip-old --realistic-sizing`; 2026 row pending — run in flight 2026-07-22.
**2026 will be YTD-through-today (~Jul 21), not a full year** — no `--end-date` was passed, but
yfinance has no bars past the real current date so the sim naturally truncates there, same as
every other 2026 row in this doc (§10 confirmed untruncated vs `--end-date`-truncated runs are
byte-identical). Don't read the 2026 row against the 2022–2025 full-year rows as like-for-like,
and don't fold it into a "5yr compound" figure without labeling it partial.):

| Year | Theoretical cap=10: ret/Sharpe/MaxDD | REALISTIC no-swap: ret/Sharpe/MaxDD | Trades T→R (skipped) |
|------|--------------------------------------|--------------------------------------|----------------------|
| 2022 (full) | −2.10% / 0.02 / −23.4% | −4.09% / −0.01 / −25.8% | 291→123 (180) |
| 2023 (full) | +54.13% / 2.90 / −6.9% | **+77.46% / 2.92** / −8.5% | 178→99 (84) |
| 2024 (full) | +19.61% / 1.58 / −4.2% | +28.02% / 1.52 / −7.9% | 152→108 (45) |
| 2025 (full) | +23.56% / 1.17 / −12.6% | **+34.81% / 1.28** / −14.6% | 219→112 (110) |
| 2026 (YTD thru ~Jul21, SPY +10.11%) | +21.43% / **2.49** / −6.2% | +16.74% / 1.53 / −6.4% | 214→86 (132) |
| Avg Sharpe (4 full yrs) | **1.42** | **1.43** | — |

**2026 YTD note:** unlike the four full years, theoretical *beats* realistic here (Sharpe 2.49 vs
1.53) and by the widest margin of any year — 132/214 trades (62%) skipped for cash, the highest
skip rate observed. Don't blend this partial year into a "5yr avg Sharpe" (the script's own
auto-printed 5yr average — champion 1.63, realistic-A 1.45 — is YTD-tainted for this reason; the
4-full-year figures above are the fair comparison). Swap-on-skip fired 0 swaps in all 5 spy_plus
years including 2026 (B≡A) — confirms swaps are rare/universe-dependent, not a 2026-only effect.

**Interpretation:** the edge survives realism on a risk-adjusted basis (Sharpe wash, 1.43 vs
1.42) — but the shape changes: forced concentration (fewer, larger, top-ranked positions)
*raises* raw return in every up-year (+8 to +23 pts) at a *deeper MaxDD every single year*,
and is worse in the 2022 bear. So idealized tables misrepresent the real trade set and risk
profile rather than uniformly overstating return. Swap-on-skip fired **0 swaps** in all four
spy_plus years (B ≡ A) — swaps are a rare, universe-dependent event; the only measured
instance (all.txt 2026) was strongly negative. **Keep no-swap.** Note this A/B measures the
backtest's swap *policy*; live `suggest_swaps()` remains suggestion-only with human confirm.

### `run_scan()` fictional-day padding bug (found + fixed 2026-07-22)
While validating a `plus.txt` (82-symbol curated trending-stocks list) YTD run, found `run_scan()`
(line ~313) built its signal-scan day loop from a raw `pd.date_range(..., freq='B')` through the
requested `end_date` (default `f"{year}-12-31"`) **regardless of real data availability** — for a
`--years 2026` run with no `--end-date`, this looped 261 business days when only 144 were real
(yfinance stops at 2026-07-21). The exact-date-match guard inside the loop prevents any phantom
*trades* (verified: `simulate()`'s own `trading_days`, derived from `spy.index`, was already
correctly bounded to 137 real NYSE days — the P&L/Sharpe/trade-count numbers were never wrong).
But the diagnostic "Regime distribution" summary **was** corrupted: it re-classified the same
stale end-of-data regime for all 117 fictional days, inflating whichever bucket matched
conditions as of the last real bar. Proof: before the fix, `plus.txt` 2026 printed
`NORMAL: 161 days (62%)`; after bounding `sim_dates` to `min(end_date, spy_df.index.max())`,
it correctly prints `NORMAL: 44 days (31%)` — exactly the predicted 117-day gap, with every
other number (signals/trades/Return/Sharpe) byte-identical. 20/20 regression tests still pass.

### `plus.txt` results — 82 curated "trending stocks", 2026 YTD (thru Jul 21, SPY +10.11%/Sharpe 1.36)
CLI: `--no-tc --bounce-bear-gate 15 --atr-trail-always --skip-old --realistic-sizing --years 2026
--watchlist input/plus.txt`

| Strategy | Trades | Return | Sharpe | MaxDD |
|---|---|---|---|---|
| Theoretical (pooled-cap=10) | 98 | +34.62% | 2.32 | −11.56% |
| **Realistic no-swap** | 69 | **+55.88%** | **2.51** | −17.26% |

Skipped for cash: 33/104 (32%) — much lower than spy_plus.txt's 62% skip rate (fewer same-day
correlated signals competing for capital on a smaller curated list). Swap-on-skip: 0 swaps (B≡A).

**Notable reversal vs the 500-symbol spy_plus.txt 2026 YTD row above:** there, theoretical beat
realistic (Sharpe 2.49 vs 1.53) — capital constraints hurt on the broad mechanical universe. Here,
realistic sizing **improves** risk-adjusted return (2.51 vs 2.32), not just raw return. Suggests
concentration only helps when what you're forced to concentrate into is itself high-quality —
`plus.txt` reads as a better-curated signal source than `S&P_500.txt` + `screener.txt` merged.

### `plus.txt` full 5-year confirmation (2026-07-22)
Full 5yr run (74/82 symbols with sufficient 2022-history; `--no-tc --bounce-bear-gate 15
--atr-trail-always --skip-old --realistic-sizing --watchlist input/plus.txt`, no `--years`):

| Year | Regime | Theoretical: ret/Sharpe/MaxDD | Realistic no-swap: ret/Sharpe/MaxDD | Skipped |
|------|--------|-------------------------------|--------------------------------------|---------|
| 2022 | Bear | −2.60% / −0.02 / −19.1% | −12.27% / **−0.36** / −25.0% | 56/272 |
| 2023 | Bull | +114.28% / 3.63 / −9.0% | +171.04% / 3.60 / −10.7% | 6/93 |
| 2024 | Bull | +67.37% / 2.70 / −8.5% | +113.93% / 2.82 / −11.6% | 25/139 |
| 2025 | Mixed | +46.05% / 1.52 / −17.9% | +60.48% / 1.44 / −21.5% | 30/163 |
| 2026 | YTD | +34.62% / 2.32 / −11.6% | +55.88% / 2.51 / −17.3% | 33/104 |
| **Avg Sharpe (4 full yrs)** | | **1.96** | **1.88** | |

Swap-on-skip: 0 swaps fired in all 5 years (B≡A everywhere), same as every other universe tested.

**Headline: this curated list has a materially stronger edge than the mechanical merge.**
4-full-year avg Sharpe 1.96 (theoretical) / 1.88 (realistic) vs. `spy_plus.txt`'s 1.42 / 1.43 —
roughly **+0.5 Sharpe** on both bases, same years, same config. Curation (liquid, trending,
thematically-relevant names) appears to matter more than universe breadth for this strategy.

**Caveat — 2022 is this list's weak point.** Realistic Sharpe −0.36 (vs spy_plus's −0.01) with
deeper losses (−12.3% vs −4.1%). `plus.txt` is thematically concentrated (crypto/space/nuclear/
chips baskets) — the same correlated-cluster risk already diagnosed in the Feb-2026 NORMAL-regime
dig (§10) likely compounds in a genuine sustained bear. Sample sizes are still modest per year
(72–140 trades) — directionally strong across 4 full years, not yet a basis for a live-config
decision on its own, but a promising candidate for a production watchlist swap/addition pending
a root-cause look at the 2022 drawdown.

### 2022 root-cause dig: adding 29 defensive stocks made it *worse*, but not for the reason expected (2026-07-22)
User added 29 blue-chip/defensive names to `plus.txt` (utilities DUK/NEE/SO, staples KO/PEP/PG/PM,
REITs EQIX/O/PLD/SPG, healthcare ABT/MDT/TMO, financials AXP/BAC/BLK/GS, industrials CAT/UNP/UPS/GE,
etc. — 82→111 symbols) hoping to cushion the bear year. Rerunning 2022 alone made both bases worse:
theoretical Sharpe −0.02→−0.10, **realistic Sharpe −0.36→−0.53** (return −12.27%→−16.56%, MaxDD
−25.0%→−26.3%). Per-trade CSV (`--trades-log`) attribution overturned the obvious read:

| Cohort | Trades | Total P&L | WR% |
|---|---|---|---|
| **NEW-stable (29 added)** | 9 | **+$418** | 55.6% |
| Original-82 | 105 | −$16,979 | 33.3% |

**The new stocks were net profitable** (BLK +$724, ECL +$988, CAT +$355, only small losses on
BKNG/HD/one NKE trade) — they are not the cause. The degradation is a **crowding-out artifact**:
the original-82 cohort performed measurably worse *inside the expanded run* (−$16,979/105 trades)
than standalone (−$12,270/110 trades) — same symbols/signals, but pooled-cap slot + cash
competition from the 29 new candidates changed which original-82 trades actually executed. The
arithmetic reconciles almost exactly (−$4,709 worse reshuffle + $418 new-stock gain ≈ −$4,291 net,
matching the observed −$16,561 vs −$12,270 delta). **Lesson: in a capital-constrained (realistic-
sizing) backtest, adding net-positive candidates can still make the aggregate number worse purely
via ranking/sequencing displacement — always check per-trade attribution before concluding an
addition "hurt," don't just read the headline delta.**

The real root cause, found by then splitting the original-82 cohort by regime:

| Regime | Trades | P&L | WR% |
|---|---|---|---|
| **EXPANSION** | 42 | **−$10,566** | 35.7% |
| RED_MARKET | 56 | −$5,806 | 30.4% |

EXPANSION (relief-rally: SPY up ≥2% over the lookback) is the *larger* loss bucket, bigger than the
sustained-bear RED_MARKET regime. Biggest single losers: **IREN (3 separate losing trades)**, COIN
−$1,735, MSTR −$1,225, HOOD −$1,373, TMC (×2), RDW, RKLB, TSLA, MRVL, VRT, APLD, PL — correlated
crypto-adjacent/space/speculative-growth names all firing BOUNCE together on a dead-cat relief
rally within the 2022 downtrend, then getting hit again when the rally failed. **This is the same
correlated-cluster-fires-together, no-cross-sectional-check mechanism already diagnosed in the
Feb-2026 NORMAL-regime dig above** — same gap, different regime label (EXPANSION here vs NORMAL
in 2026), same thematic concentration in `plus.txt` as the trigger. Adding defensive stocks doesn't
fix this because it doesn't touch the mechanism; the `--normal-bounce-cap` lever (or an
EXPANSION-regime analog) is the more promising fix to test next, not further universe changes.

## 12. Code-vs-Design Audit + Research Review + Improvement Plan (2026-07-22)

### Audit: all design pillars verified in code, ONE mismatch found
Verified ✓: V9-H disabled (config.py:485), BBG15=15 (config.py:218) + gate condition
(orchestrator.py:530-534), TREND_CONFIRM Path A only (config.py:228), Tension/Supertrend dormant,
ATR_TRAIL_MULT=2.0 + 14-bar floor (config.py:152), live `_raise_atr_trail` ≡ backtest formula
(simple TR-mean over 15 bars, monotonic, fixed-stop floor), WinProb calibration wired but JSON
deliberately absent from scanner_output/ (inert as designed), pooled cap 10/day date-grouped with
Quality→WinProb→R:R→Dist(≤25)→Vol + symbol dedup (auto_portfolio.py:280-330), max position 10%,
stop-distance guard 30%.

**⚠ MISMATCH — live exits are not close-based.** Champion exit was validated strictly CLOSE-based
(intraday dips below trail must NOT exit; low-based gave 2022 −24.8%; pinned by
tests/test_backtest_atr_trail.py). But cron runs `refresh_prices()` at **10:00 AND 15:45 ET**
(cron_jobs.txt:79,82) and it closes on `current <= stop` using the intraday price at that moment
(auto_portfolio.py:~1175) — a 10 AM dip below the trail exits live even if the day closes back
above. The 10 AM run also raises the trail using an intraday price as a pseudo-close. 15:45 run is
a fair close proxy; the 10:00 run is the deviation. → Task 1 below.

> **🔴 CORRECTION (2026-07-27) — the premise of the paragraph above was false, and this
> is the single most expensive documentation error in this file.**
>
> "cron runs `refresh_prices()` at 10:00 AND 15:45 ET" cited **`cron_jobs.txt`** — the
> **retired Mac** schedule, which had not been production since the §9 cutover on
> 2026-07-07. `docker/crontab`, the file supercronic actually runs on EC2, had **never**
> contained `refresh_prices` in any commit (`git log -S` on that path is empty; the file
> was branched 2026-02-19, a month before `75a638f` introduced the call).
>
> So the audit was analysing a schedule that was not running, and Task 1's fix
> (`dc3e252`, `_close_basis_history`) shipped **into a function cron never called.**
> Consequences observed in S3 production state on 2026-07-27: `current_price ==
> entry_price` on all 25 open positions across both live books, **zero** positions
> closed ever, three sitting 27–30% below their stops since April, and AMD at +145%
> with its stop still at the original entry-level value. The champion's largest measured
> edge (+97 pts compound / +0.45 Sharpe) had never run in production.
>
> Fixed 2026-07-27 (`d20c2a5`, deployed): `refresh_prices_all_users()` added — bare
> `refresh_prices()` only touches the default book, not the per-user portfolios — and
> wired into `docker/crontab` at 10:00 and 15:45 ET. Guarded by
> `tests/test_crontab_parity.py`, whose SEMANTIC_FLAGS check would have caught this the
> moment `42f4817` landed.
>
> **Standing rule: reason about production from `docker/crontab` only.** `cron_jobs.txt`
> is retired and now carries a banner saying so. Task 1's close-basis logic is correct
> and is only now actually executing.

### Research review (sources in git history / session log) mapped to measured weaknesses
1. **Daniel & Moskowitz "Momentum Crashes"** — momentum crashes happen in panic states (post-
   decline, high vol, during rebounds) because losers' beta >3 → snap back together. This IS the
   Feb-2026 NORMAL cluster and 2022 EXPANSION bucket (−$10.6k). Their dynamic vol/mean-forecast
   sizing ~doubles Sharpe → Task 4 (panic-rebound throttle).
2. **Moreira & Muir volatility-managed portfolios** — scale exposure by inverse realized variance;
   +25% Sharpe on market factor. Caveat: 103-strategy replication (Cederburg et al.) finds it does
   NOT generalize universally → ablate, don't assume. System has per-stock ATR sizing but NO
   portfolio-level market-vol throttle.
3. **Blitz residual/idiosyncratic momentum** — rank by return unexplained by market beta; ~2×
   Sharpe, much lower crash risk/left-skew. Pooled-cap Dist tiebreak currently ranks RAW prior
   return = exactly what picks 10 correlated high-beta names on a rebound day → Task 3
   (beta-adjusted Dist).
4. **Kaminski & Lo stop-loss theory** — stops add value under momentum, destroy value under mean
   reversion. Our >15d holds (75-95% WR) = momentum → trail helps (matches +97pts measured). BOUNCE
   entries' first days = mean reversion → close-based trigger (tolerant of intraday noise) is
   load-bearing; never tighten to intraday. Reinforces Task 1.
5. **Connors-style dip-buying conditioning** — practitioner consensus: only buy dips in stocks
   ABOVE their own 200-day SMA; below it, dips chain into falling knives (IREN's 3 consecutive
   2022 stop-outs). BBG15 gates on SPY's SMA200 only, not the stock's own → Task 2.

### Task plan (execute in order; judge every ablation on realistic-sizing Sharpe+DD per §11 rule;
>15d WR must never shrink)
1. **Fix live close-based exit** — refresh_prices: before ~15:30 ET use last COMPLETED daily bar
   for both exit check and trail raise (catches yesterday's close-breach at the 10 AM run =
   close-based catch-up); at/after 15:30 keep live price as close proxy (current behavior).
   Unit tests for the trim helper + semantics.
2. **`--bounce-sma200-gate`** — backtest flag: skip BOUNCE signals when stock close < its own
   SMA200 (pass if <200 bars history). Validate 2022 + 5yr plus.txt + optimizer_watch.
3. **`--residual-dist`** — pooled-cap tiebreak on beta-adjusted Dist (stock return − β×SPY return,
   rolling β) instead of raw Dist. Validate same.
4. **`--panic-throttle`** — halve size / tighten BOUNCE admission when SPY realized vol high AND
   market rebounding off a decline (panic-state definition per D&M). Validate same.

### §12 Task results log
**Task 1 — live close-based exit: SHIPPED (2026-07-22, commit dc3e252 — confirmed committed
and live on the EC2 production image as of 2026-07-23).** `_close_basis_history()`
in auto_portfolio.py: before 15:30 ET, exit checks + trail raises use the last COMPLETED daily
bar (10 AM cron now only catches yesterday's close-breach, never an intraday dip); ≥15:30 keeps
the near-close proxy (15:45 cron unchanged); ≥16:00 bar is final. UI `current_price` still live.
6 tests (tests/test_close_basis_history.py). Local API server restarted; EC2 needs commit+deploy.

**Task 2 — unconditional `--bounce-sma200-gate`: REJECTED (2026-07-22).** 5yr plus.txt (111
symbols) realistic A/B: avg Sharpe 1.87→1.64 (−0.23), >15d WR shrinks in 4/5 years → both §12
halt criteria hit. BUT the split is structural, not noise: 2022 bear +0.83 Sharpe (−16.56%→
+3.14%, MaxDD −26.3%→−11.2%, 270/296 signals gated) and 2026 +0.27 (DD −17.5%→−10.6%); all of
2023/24/25 negative because post-bottom V-recovery entries are ALSO below their SMA200s (2023:
+173%→+42% realistic, only 47 signals gated did that damage — they were the year's best trades).
→ spawned Task 2b: `--bounce-sma200-bear-only` (gate active only on SPY<SMA200 days), in the
combined validation run with Tasks 3+4.

**Task 2b — `--bounce-sma200-bear-only`: REJECTED (2026-07-22).** Conditioning the per-stock gate
on SPY<SMA200 kept most of the 2022 rescue (realistic −3.47%/−0.20 vs champion −16.56%/−0.53) but
STILL gave back 2023 (−0.69 Sharpe: +95.5% vs +173.1%) — SPY itself sat below its SMA200 through
much of H1-2023, so the "recovery entries fire while everything is below trend" problem survives
the conditioning. Avg realistic Sharpe 1.75 vs champion 1.87 (−0.12). Dormant.

**Task 3 — `--residual-dist`: NULL (2026-07-22).** Avg realistic Sharpe 1.88 vs 1.87 (+0.01);
2023/2025 picks literally identical, 2024 −0.04, 2026 +0.06 (DD −17.5→−14.2), 2022 ≈unchanged.
Same structural reason as the WinProb-cal no-op: within-day pooled-cap ties are rarely decided by
the tiebreak on this signal mix. Dormant; joins the null-lever list (Tension, Supertrend,
Breakeven, WinProb-cal, Daytrade A/B).

**Task 4 — `--panic-throttle`: BEST LEVER OF THE CYCLE, narrowly below ship bar (2026-07-22).**
Avg realistic Sharpe **1.95 vs 1.87 (+0.08 < +0.10 bar)**. Profile is the strongest seen since the
ATR trail: 2022 **+0.51 Sharpe** (−16.56%→−3.72%, MaxDD −26.3→−20.1; half-sizing freed cash → 137
trades vs 114, so it also diversified); 2023/2024/2026 **byte-identical** (panic days don't occur
in healthy years — zero cost); only 2025 −0.12 (April tariff dip briefly put SPY<SMA200+high vol,
throttled entries that worked). **>15d WR never shrinks** (2022 76.7→76.7 on more trades, 2025
75.0→75.4, rest identical). Dormant for now. **Obvious 4b variant if pursued: require SUSTAINED
bear (SPY<SMA200 ≥15 consec days, mirroring BBG15's validated distinction) in the panic
definition — the April-2025 dip was 9–14d and would be excluded, likely erasing the only negative
year while keeping 2022.** Note: 2026 rows in all §12 runs are YTD (thru ~Jul 21), not full-year.

## 13. Repeating Exit Notifications — root cause + docker/crontab drift (2026-07-23)

### Symptom
Daily Telegram/Discord "Exit evaluation completed — N positions require action" naming the same
tickers every day. CSWC/ABT/RUSHA/SSB/EXPD/LH/SMG/CLH fired `EXIT_FULL` on Jul 20, 21, 22 **and**
23 — positions entered Jul 16–17 that had long since stopped out.

### Root cause: append-only mock CSVs, read by the exit evaluator
`positions_swing_mock.csv` / `positions_daytrade_mock.csv` are the **original (2026-02-11, f962f77)
exit mechanism**: Phase 1 appends PREMIUM/GOLD signals via `--auto-positions`
(`utils.append_signals_to_positions`), the exit evaluator reads them back via `--exit-file`.
"mock" = mock *portfolio* (paper signal notes), **not** test fixtures — README.md:924 documents
them as a system deliberately independent of `auto_portfolio.json`.

`append_signals_to_positions()` is **append-only with dedup and has no remover anywhere in the
codebase** — README states it outright: *"Positions are NOT automatically removed from this file
when an exit signal fires — you must remove them manually."* So a stopped-out position stays in
the file forever and re-fires `EXIT_FULL` every day. `.exit_history.json` dedups only *within* a
calendar day (`if hist.get('date') == today`), so every midnight the same corpses re-notify.

### The real defect: docker/crontab was never migrated (Feb wiring still live on EC2)
| Date | Event |
|------|-------|
| 2026-02-11 `f962f77` | `--auto-positions` + mock CSVs introduced as the exit mechanism |
| 2026-02-19 `d647601` | `docker/crontab` created as a copy of the then-current `cron_jobs.txt` |
| 2026-03-19 `75a638f` | **`cron_jobs.txt` migrated** to `--exit-from-portfolio` (commit note: *"auto-portfolio positions only"*) |
| — | `docker/crontab` **never** updated: `git log -S"exit-from-portfolio" -- docker/crontab` is empty |
| 2026-07-07 | §9 cutover — production moved to EC2, which runs `docker/crontab` = the **pre-migration** wiring |

The March fix was silently reverted in production by the July cutover. `cron_jobs.txt` (Mac,
retired) and `docker/crontab` (EC2, live) had drifted on both the exit **and** monitor paths.

### Fix applied (2026-07-23)
- `docker/crontab` exits: `--exit-file input/positions_*_mock.csv` → `--exit-from-portfolio`
  (3:45 PM swing, 4:30 PM combined, 3:30 PM daytrade). Reads `portfolio.json` +
  every user's `auto_portfolio.json` (breakout_scanner.py:~1852).
- `docker/crontab` monitor (9:45 AM, */15 10–15, 4:00 PM): `--monitor input/positions_*_mock.csv`
  → `--monitor-portfolio --monitor-auto-portfolio`. **This was the second, separate notification
  stream** (`Notifier.send_monitor_alert`) — fixing only the exit path would have left 15-minute
  alerts firing on the same corpses.
- Pruned closed rows on EC2 (backups at `scanner_output/positions_*_mock.bak.csv`):
  swing 19→8 rows, daytrade 11→0.
- `breakout_scanner.py` exit-history write is now `fcntl`-locked with merge-on-write, mirroring
  `Notifier._save_cache()`. **Defensive only** — the 15:30 and 15:45 jobs are 14 min apart and
  each runs ~10 s, so no race was actually occurring; this was not the bug.

### Consequence: the mock CSVs now have ZERO readers on the live box
After this change `grep positions_.*_mock docker/crontab` shows **only `--auto-positions` writes
(7 jobs)** — nothing reads them. Note the watchlist role they hold in `cron_jobs.txt` does **not**
apply here: `docker/crontab`'s Phase 2/Evening scans use `input/premium_swing.txt` (from
`--export-premium`), not the mock CSV. Their only remaining consumer is the Streamlit "Watch
Lists" display (pages/portfolio_page.py:1363). Writes were left in place so that tab stays
populated. **Open decision:** retire the `--auto-positions` flags entirely, or keep the files as a
display-only signal log.

### Lesson
`cron_jobs.txt` and `docker/crontab` are two hand-maintained copies of the same schedule with no
test or CI check binding them. Only `docker/crontab` runs in production. Any schedule change must
be applied to both, and drift between them is invisible until it produces a symptom like this.

### ⚠ Follow-up (2026-07-27): this fix was a PARTIAL PORT, and the rest bit four days later
`42f4817` ported the `--exit-from-portfolio` half of commit `75a638f` and rewrote those exact cron
lines — **without** the `&& refresh_prices()` tail that the same commit had added to the same lines
in `cron_jobs.txt`. `refresh_prices` is the only path that raises the ATR trail and auto-closes on
a stop breach, so production held every position forever regardless of stops (see the 🔴 CORRECTION
in §12 for the measured damage). Third instance of this drift, same root commit as the first two.

Three consequences, all now in place:
1. **`tests/test_crontab_parity.py`** — the binding check whose absence the Lesson above named as
   root cause, but which was never actually written at the time. Its `SEMANTIC_FLAGS` test asserts
   every behaviour-defining flag used in `cron_jobs.txt` also appears in `docker/crontab`; it fails
   on the exact state production was in.
2. **`cron_jobs.txt` now opens with a RETIRED banner** listing all three incidents. It is kept
   solely as the reference the parity test diffs against.
3. **When porting a commit between the two files, port the whole line, not the flag you came for.**
   All three incidents were one commit touching one line and only part of it being carried across.

## 13. Tiebreak / Sleeve / Panic-4b / NBC Validation Cycle (2026-07-23/24)

Closes out the four open research items left dangling by the §12 cycle and the infra detour.
**Outcome: five runs, five negatives — no live config change.** All levers left dormant.
Logs: `scanner_output/backtests/tiebreak_validation_20260723/`.

Code shipped (all dormant/off by default, tests green):
- `d1b19ee` — `--live-tiebreak` + `--sleeve-watchlist`/`--sleeve-slots` (9 tests)
- `0c09540` — `--panic-throttle-bear-only` (§12 Task 4b variant, 5 tests)
- `179f0e7` — `/portfolio/suggest-swaps` NaN crash fix (6 tests; 46/46 regression green)

### 13.1 `--live-tiebreak` — live Dist ranking vs validated backtest ranking: WASH, no change
`auto_portfolio.py` ranks the pooled-cap Dist tiebreak **descending** (most-extended first,
clip-25, +Vol); the validated backtest ranks **ascending** (closest-to-trend, >25 back-bucket).
§12's audit flagged the divergence qualitatively; this quantifies it.

**⚠ A short-window run is actively misleading here.** `daytrade_admission_ab.py
--compare-tiebreak` (live S3 backlog, Apr 1–Jun 30 2026, 41–46 trades, ONE regime) reported
**backtest asc winning by +2.38 Sharpe** (live 2.10 vs backtest 4.48) and printed
"fix LIVE to match". It also showed the divergence is real and not inert: cap bound on 42 days,
the admitted set **differed on 31 (73.8%)**, avg **4.48 symbols swapped/day**. The 5yr run
overturns the P&L verdict completely — including its sign.

5yr `spx_plus.txt` (548 syms), realistic-sizing arm (`4b_live_tiebreak_spx_plus.log`):

| Year | Champion (asc) | LiveTiebreak (desc) | Δ | >15d WR champ → live |
|------|---------------|---------------------|-----|---------------------|
| 2022 bear | 0.02 | **0.06** | +0.04 | 80.0% → 78.0% ↓ |
| 2023 bull | 3.18 | **3.39** | +0.21 | 83.9% → 84.1% ↑ |
| 2024 bull | **2.60** | 2.30 | −0.30 | 79.1% → 78.5% ↓ |
| 2025 mixed | **2.02** | 1.90 | −0.12 | 83.6% → 82.4% ↓ |
| **4 full yrs** | **1.955** | **1.913** | **−0.04** | — |
| 2026 YTD | 1.34 | **2.03** | +0.69 | 93.3% → 90.3% ↓ |

**Verdict: keep live as-is (descending).** Sign flips year to year — no stable direction.
The script's own 5yr line (`+1.93` live vs `+1.83` champion) **is YTD-tainted**: the entire
lead comes from the partial 2026 row, exactly the contamination §11 warns against. On four
full years it is −0.04, a wash. Secondary: live's >15d WR is *lower* in 4 of 5 years (0.6–3.0
pts) — mild, but pointing the wrong way on §12's halt metric. **Lesson: a single-regime,
~40-trade window can invert a 5-year verdict by >2 Sharpe. The `--compare-tiebreak` harness's
own footer says the 5yr run is the arbiter — believe it, don't act on the short window.**

### 13.2 `--sleeve-slots` — reserve 3/10 daily cap slots for a curated sleeve: WASH, dormant
Core `spx_plus.txt` (548) + sleeve `plus.txt` (111); 322 of 1078 PREMIUM+ signals sleeve-tagged
in 2022, so the mechanism had ample opportunity to bite (`4c_sleeve_slots.log`).

| Year | Champion | +Sleeve(3/10) | Δ | MaxDD champ → sleeve |
|------|----------|---------------|-----|---------------------|
| 2022 bear | 0.02 | −0.07 | **−0.09** | −24.58 → −25.42 deeper |
| 2023 bull | 3.18 | **3.34** | +0.16 | −14.69 → −14.69 flat |
| 2024 bull | 2.60 | **2.61** | +0.01 | −9.08 → −11.72 deeper |
| 2025 mixed | **2.02** | 1.89 | −0.13 | −20.73 → −18.65 shallower |
| **4 full yrs** | **1.955** | **1.943** | **−0.01** | mostly deeper |
| 2026 YTD | 1.22 | **1.57** | +0.35 | −8.70 → −11.87 deeper |

**Verdict: dormant.** Same YTD trap as 13.1 — the script's 5yr line shows sleeve ahead
(1.87 vs 1.81) but that lead is *entirely* the partial 2026 row; full years are −0.01.
Buys bull-year gains at the cost of a worse bear (2022 −0.09) and deeper drawdown in 3 of 5
years — reserving guaranteed seats for thematically-concentrated `plus.txt` imports its 2022
weakness (§11: −0.36 realistic standalone). >15d WR held flat (80.0%, 40 trades) so it is not
destructive, just not additive.

### 13.3 §12 Task 4b `--panic-throttle-bear-only` — REJECTED; promotion path closed
Thesis (from §12): base panic-throttle's only negative year was 2025 (April tariff dip, 9–14
consecutive days below SMA200); requiring a SUSTAINED bear (≥15 consec, mirroring BBG15) should
exclude it and push the lever over the +0.10 bar.

**⚠ First run used the wrong universe.** Ran on `optimizer_watch.txt` on the mistaken belief it
matched §12 Task 4. **It does not — §12 Task 4 was measured on `plus.txt` (111 syms)**; its
2022 champion return of −16.56% is `plus.txt`'s signature (`optimizer_watch` is −8.59%).
The `optimizer_watch` run (`4d1_panic_bear_only.log`) is retained as a robustness note: 5yr
realistic champion 1.32, base panic 1.25, bear-only 1.30 — **both variants net-negative there**,
because that curated 50-symbol universe has no 2022 correlated-cluster disaster to rescue
(champion 2022 is a benign −0.03), so throttling merely trims exposure that recovers.

Correct comparison, `plus.txt`, realistic-sizing (`4d1b_panic_bear_only_plus.log`):

| Year | Champion | +PanicThrottle (base) | +PanicBearOnly (4b) |
|------|----------|----------------------|---------------------|
| 2022 | −0.46 (−16.5%, DD −31.9) | **−0.17** (−8.6%, DD −26.9) | −0.17 (−8.7%, DD −27.0) |
| 2023 | 3.55 | 3.55 | 3.55 identical |
| 2024 | 3.44 | 3.44 | 3.44 identical |
| 2025 | **1.73** | 1.72 | 1.63 |
| 2026 YTD | 1.81 | 1.81 | 1.81 identical |
| **4 full yrs** | **2.065** | **2.135 (+0.07)** | **2.113 (+0.047)** |

**Verdict: 4b variant REJECTED; base panic-throttle stays dormant.** The refinement is *worse*
than the lever it was meant to improve (+0.047 vs +0.07), **because its premise did not
reproduce**: on this fresh run base-panic 2025 is 1.72 vs champion 1.73 — essentially flat, not
the −0.12 §12 logged. With no April-2025 cost to recover, the stricter gate only discarded base
panic's 2025 cash-freeing diversification (135→125 trades), netting −0.10. Base panic itself
reproduces its §12 profile directionally (2022 rescue +0.29 here vs +0.51 logged; 2023/24/26
byte-identical — zero cost in healthy years) at **+0.07, still below the +0.10 bar.**
**The §12 Task 4b promotion path is now closed — the specific refinement was tested and lost.**

### 13.4 §10 `--normal-bounce-cap 2` broader-universe confirmation — NOT CONFIRMED, dormant
§10 measured +0.07 (5yr 1.30→1.37) on the 50-symbol `optimizer_watch.txt` and flagged a
broader-universe run as the missing step before promotion. Run on `spx_plus.txt` (548 syms,
`4d2_normal_bounce_cap_spx.log`):

| Year | Champion ★ | +NBC=2 | Δ | >15d WR (n) champ → NBC |
|------|-----------|--------|-----|------------------------|
| 2022 | −0.11 | **−0.02** | +0.09 | 79.7% (133) → 78.6% (126) ↓ |
| 2023 | 3.34 | 3.34 | 0.00 | 90.0% (150) → 89.2% (130) ↓ |
| 2024 | 3.15 | **3.21** | +0.06 | 80.0% (145) → 80.4% (143) ↑ |
| 2025 | **1.71** | 1.57 | −0.14 | 72.9% (144) → 71.6% (134) ↓ |
| 2026 YTD | **2.26** | 2.11 | −0.15 | 85.0% (107) → 84.8% (99) ↓ |
| **4 full yrs** | **2.0225** | **2.025** | **+0.003** | — |

**Verdict: not confirmed → dormant.** (a) Dead wash on full years (+0.003) vs §10's +0.07.
(b) **The finding reverses in the very year that produced it** — §10's case rested on 2026
(+0.71→+1.04, the Feb-6 cluster fix); here 2026 is NBC's *worst* year (−0.15). The Feb-2026
correlated cluster was an `optimizer_watch`-specific event; on a broad universe the cap mostly
removes trades that were fine. (c) **>15d WR shrinks in 4 of 5 years** — §12's explicit halt
criterion — by cutting long-hold trade *count* materially (2023: 150→130). It trims the edge,
not the drag.

**⚠ Known limitation:** `--normal-bounce-cap` only emits an **idealized** pooled-cap row — the
flag is never wired into the REALISTIC arm, so this table violates §11's judge-on-realistic
standing rule. Given the result is a wash *and* trips the >15d halt criterion, the verdict
stands; but **wiring NBC into the realistic arm is a prerequisite if it is ever revisited.**

### 13.5 Cross-cutting lessons
1. **Never decide on a partial-year-blended average.** Three of the five runs (13.1, 13.2, and
   the script's own auto-printed 5yr line generally) show the arm winning *only* via the YTD
   2026 row. Always recompute the 4-full-year average by hand before reading a verdict.
2. **Short single-regime windows can invert a multi-year verdict by >2 Sharpe** (13.1).
3. **Universe choice can flip a lever's sign** (13.3, 13.4) — a lever validated on a 50-symbol
   curated list may be measuring one idiosyncratic event. Always confirm on a broad universe
   before promotion, and record which watchlist a result came from.
4. **Meta-finding reconfirmed** (extends §8/§12): the champion (ATR×2.0 always-on close-based
   trail + pooled-cap=10 + BBG15 + no TREND_CONFIRM) is **well-tuned**. Levers now returning
   null/negative: Tension Index, Supertrend, Breakeven, WinProb-cal, Daytrade A/B, SMA200 gates
   (both), residual-dist, live-tiebreak, sleeve-slots, panic-throttle (+4b), normal-bounce-cap.
   Future effort is better spent on **signal generation** (the ≤15d hold bucket, WR 4–29%, is
   the consistent drag across every universe) than on further admission/ranking tweaks.

## 14. Live Signal Panel + Multi-Day Research Agents (2026-07-24)

Branch `research/auto-agents` (commits `113841c`, `8a12993`). A standing research system whose
substrate is the **live daily signal archive**, not historical config sweeps.

### Why the substrate changed
§13 closed 11 consecutive null levers, all measured by re-mining the same 5 years of history —
with a measured run-to-run noise floor of **0.25 Sharpe** (two identical `spx_plus` runs gave
4yr avg 1.955 vs 2.20, because yfinance fetch failures vary the loaded universe by ~11 symbols).
Every lever tested was ±0.01–0.09, i.e. **below that noise**. Repeatedly re-mining a fixed
history at sub-noise resolution is the most likely explanation for the null streak.

The live scanner emits signal CSVs every trading day (**833 files, 52 dates, Apr 1 → Jul 23**,
via `utils.list_files('scanner_output/signals', …)`), already carrying `Vol, Dist, SMA_Dist%,
R:R, Gap%, RSI, TC_Score, Quality, Type, Sector, FinBERT_*`. Joining those to what each stock
*actually did* is **measurement, not simulation** — no counterfactual, and fresh days are never
re-mined, so there is no overfitting-by-repetition.

### ⚠ Finding: live and every documented champion baseline trade near-disjoint populations
Live runs `TREND_CONFIRM['enabled']=True` Path A (config.py:226-228). **Every** champion run in
§7/§11/§13 used `--no-tc`, which disables it.

| | Live archive (episode-deduped) | Champion backtest |
|---|---|---|
| TREND_CONFIRM | **1459 (87%)** | 0 — disabled |
| BOUNCE | 134 (8%) | ~99.7% |
| CONTINUATION | 62 | ~0 |

Consequences: §7's *"WinProb calibration is inert — 490 trades collapse to one BOUNCE|PREMIUM
bucket"* is a **backtest artifact**, not a property of live; and §13's 11 null ranking levers were
measured on a signal population live barely produces. Treat §7–§13 conclusions as **hypotheses to
re-test on the panel**, not settled facts. Tracked as H4.

### ⚠ HZ1: the scanner writes prices that never traded (archival bug, NOT a trading bug)
**~32% of archived signal rows** carry a `Price` outside the signal-day bar range — 100% of
2026-07-21, ~80% of May–June, 15% of April, ~0 on other July dates (swing 43% vs longterm 11%).
On unambiguous mega-caps: PLTR 197.20 (traded 131.23–134.68), PANW 163.66 (334.03–352.00),
MRNA 164.80 (58.61–60.83), OXY 131.80 (55.36–56.50).

**Ruled out:** CSV misparse (rows internally consistent — Stop/Target correctly derived from the
bogus Price); split adjustment (yfinance reports **zero** splits on affected names since
2026-01-01); intra-file row shuffle (signal-price multiset ≠ actual-price multiset); the current
data path (yfinance returns correct prices for all of them today). **Root cause unknown.** The
concentration in specific runs suggests bad scan *invocations*, not a continuously broken pipeline.

**Blast radius — contained, no live-money impact:**
- `auto_portfolio` fetches its own entry and stop. The 9 positions opened 2026-07-21 (user
  cf699841) all have correct entries (RKLB 69.12 = the real close) and sane stops at −3% to −5%;
  none inverted.
- `daytrade_admission_ab.py` uses `avail['close'].iloc[0]` and guards the stop
  (`stop >= entry or >30% away → entry*0.95`), so §7's A/B is unaffected.
- Only the research panel was affected; it now **mirrors live** (entry = signal-day bar close +
  the same stop guard) rather than filtering around the bug.

**Still worth fixing at source** — the scanner corrupts its own output, which matters for the UI
and any analysis trusting `Price`/`Stop`/`Target`. Not urgent, not a trading risk.

### The panel
`research/panel/build_panel.py` (full build) + `update_panel.py` (idempotent daily increment:
append new files, advance rows still accruing forward bars; a row **freezes** at 30 forward bars —
9524 frozen vs 338 open on first increment).

Per row: signal features verbatim + `entry_used` (bar close) + measured `mae_pct`, `mfe_pct`,
`bars_to_mae`, `ret_1/3/5/10/20/30d`, `hit_stop`, `hit_target`, `episode_id`.
**9862 rows → 1675 independent episodes with a full 30-bar forward window**, 971 symbols, 41 dates.

Three traps found while building, all now handled and documented in the guardrails:
- **yfinance returns NOTHING for a fetch window ending in the future** — the first build silently
  got bars for only 23/994 symbols. Fetch end is clamped to today.
- **`mae_pct > 0` is legitimate** (gap-up that never retraces); `mfe_pct < 0` likewise. The naive
  `mae<=0<=mfe` invariant is wrong — the real one is `mae_pct <= mfe_pct`. `mae_pct_floored` is
  the conventional clamped version.
- **Tri-state flags must be nullable `boolean`**, not object dtype — `~df['price_in_bar_range']`
  silently does *bitwise* negation on object columns (True → −2).

### First measured result (TREND_CONFIRM, n=1459, 59% winners at 30d)
| | MAE |
|---|---|
| Winners p50 / p75 / **p90** | −3.09% / −6.04% / **−10.71%** |
| Losers p50 | **−12.28%** |

Winner-p90 and loser-median nearly coincide — that overlap *is* the difficulty a stop must
resolve, now measured rather than assumed. Live applies a uniform ATR×2.0 to every position
regardless of cohort; whether that is right per cohort is H1.

### Agent system
- `research/runner.py` — **single-shot tick** (launchd `StartInterval` 1800s), not a KeepAlive
  daemon: a wedged daemon is indistinguishable from an idle one, a tick fails loudly.
  Detects new signal files → runs `update_panel.py` → invokes a worker via `claude -p`.
- `research/prompts/` — `_shared_guardrails.md` + `lead.md` + `worker_stops.md` +
  `worker_picking.md`. Guardrails encode: panel-is-measurement, the §11 realistic-sizing rule,
  the §13 `>15d` WR halt criterion, `n>=30` after episode dedup, walk-forward, HZ1/HZ2/HZ3.
- `research/confirm_backtest.py` — **mandatory 2022 gate**: the panel window (Apr–Jul 2026) has
  **no sustained bear**, and stops matter most in one. Runs candidate vs baseline ATR multiplier
  in a paired comparison.
- `research/ledger/` — `hypotheses.md` (H1–H4, HZ1–HZ3), `decisions.md`, `results.jsonl`,
  `budget.json` (invocation cap + end date; runner refuses past either).
- **Live config is propose-only by construction.** Agents may commit to `research/auto-agents`;
  they may never touch `config.py`, `docker/crontab`, `cron_jobs.txt`, or EC2.
- `research/launchd/install.sh {install|uninstall|status}` — **not installed**; starting the
  unattended run is a deliberate human decision.

### §14.1 First unattended run — post-mortem and hardening (2026-07-25)

The overnight run of 2026-07-24 ran **27 ticks and produced 2 pieces of research.** Ticks 3–14
were consecutive session-limit rejections, each retried on the next 30-minute tick and each
first paying for a full 131-symbol yfinance panel refresh. Fixes below; `tests/
test_research_runner.py` (14 tests) pins them.

**"Propose-only by construction" was not, in fact, by construction.** It was prompt text.
`claude -p` is non-interactive, so anything not pre-approved is silently *denied* — which also
meant the agents never had `Write`/`Edit` at all and did every ledger write through `python -c`
one-liners (only `Bash(python:*)`/`git add`/`git commit` happened to be allowlisted in
`.claude/settings.local.json`). Now `research/agent_settings.json`, passed via
`claude --settings`, grants the research tools *and* denies `Edit`/`Write` on `config.py`,
`auto_portfolio.py`, `orchestrator.py`, `scanner.py`, `breakout_scanner.py`, `cron_jobs.txt`,
`docker/crontab`, `.env`, plus `ssh`/`aws`/`docker`/`git push`/`git checkout`. Deny beats allow.
Kept separate from `.claude/settings.local.json` so interactive sessions are unaffected.
⚠ **Verified empirically, and that verification is not optional:** `-p` mode *silently ignores a
settings file that fails validation*, so a schema slip leaves the deny rules looking present but
inert. Probe result: `Write(config.py)` blocked, `Write(research/…)` succeeded, hash of
`config.py` unchanged.

**Other fixes:** geometric backoff 30m→8h on failed invocations (was: retry every tick) and
self-parking after 8; an `flock` tick lock (`runner`/`lead` are *different launchd labels running
the same script* — two concurrent `update_panel.py` runs do read→mutate→overwrite and silently
lose a day's ingest); worker rotation moved to its own `last_worker_role` key (`last_role` also
records lead runs, so the daily lead reset every following tick to `stops` and starved
`picking`); the tail of `decisions.md` + the role's own past results are now injected into the
task prompt (a worker re-read the same `hypotheses.md` "Next tasks" list every tick, and only the
lead may update it — which had never run); `runner.py --status` for one-command health.

**Prompt contradictions corrected.** `worker_stops.md` instructed filtering on
`price_in_bar_range == True` and `lead.md`'s audit checklist required it — both **forbidden** by
the guardrails since the HZ1 fix (`8a12993`), which made it a diagnostic, not a validity gate.
The lead would have rejected correct work. Stale `~92%/~5%` type-mix figures corrected to the
measured **87% TREND_CONFIRM / 8% BOUNCE / 4% CONTINUATION** (n=1675 episodes).

### §14.2 Promotion-gate and panel-plumbing fixes (2026-07-25)

**The promotion gate's population is now an explicit choice.** `confirm_backtest.py` originally
hardcoded `--no-tc`. ⚠ **That was deliberate, not a slip** — its first docstring said it "runs the
champion config" and `BASE_ARGS` was a verbatim copy of the canonical champion CLI in §7. Using
the documented champion as the reference makes gate numbers comparable to every §7–§13 baseline,
which is a defensible reason.

What was never reconciled: `--no-tc` *disables* TREND_CONFIRM, so the gate ran a ~99.7%-BOUNCE
stream while live is 87% TREND_CONFIRM — meaning it could not confirm a panel-derived rule on the
population live actually trades. H4 (the divergence) had been logged **that same morning, five
hours before this script was written**, yet nothing in the docstring, the guardrails, or the
ledger records the tradeoff being weighed. Two decisions made the same day, never checked against
each other.

Now explicit rather than implicit: `--population live` (default, TC Path A as production runs it)
or `--population champion` (`--no-tc`, reproducing documented baselines). Both arms of any run use
the same population, so the ship bar (candidate vs *its own* baseline, ≥ +0.10 Sharpe) stays
internally valid either way — what changes is which population the answer is about. Never mix
numbers across the two.

⚠ **A structural limit that flags cannot fix, and that the gate now surfaces rather than
hides.** TREND_CONFIRM is blocked in `RED_MARKET`/`BEARISH`
(`backtest_regime_compare.py`: `if _TC_CFG.get('enabled') and regime not in
('RED_MARKET','BEARISH')`). 2022 is a sustained bear, so **the 2022 bear gate cannot exercise
live's dominant signal type — because live does not emit that type in a bear.** That is a
property of the strategy, not a bug. Consequence: 2022 is a genuine *downside* check but is
**not** a test of a TREND_CONFIRM-derived rule. The gate now runs with `--trades-log` and prints
the **realized signal-type mix** of every arm, warning loudly when TREND_CONFIRM is <10% of the
trades; default years are `2022,2024` so a bull year where TC actually fires is always included.
This is the difference between a gate that passes silently on zero relevant trades and one that
tells you it had none.

**Worker B now has a promotion path.** `backtest_regime_compare.py` gained
`--rank-scores FILE` (CSV `date,symbol,score`), forwarded by `confirm_backtest.py
--rank-scores`. The score orders signals **within** the quality tier, never over it —
deliberately, because the panel showed GOLD>PREMIUM is the one robust thing the current ranking
does (+8.3pp at 20d, significant every month) while the order *within* PREMIUM is inert. Unscored
signals sort behind every scored one, so a partial model degrades gracefully. Default path
(`rank_scores=None`) is byte-identical — pinned by a test, since every documented baseline
depends on it.

**Panel plumbing.** Panel freshness is now decoupled from agent invocation: the panel refreshes
on new data *or* on its own ~daily cadence (`PANEL_REFRESH_HOURS = 20`), because `update_panel`'s
second job — advancing forward metrics on rows still accruing bars — previously never ran on a
quiet day. And "new file" now comes from an explicit record (`research/panel/ingested_files.json`,
written after every successful update, including files deliberately skipped as unusable) instead
of being inferred by differencing disk against `panel.source_file`. The old inference meant any
CSV that `load_signal_rows` silently drops (empty, or no `Symbol` column) read as new **forever** —
the same class of bug as the daytrade CSVs that burned 12 invocations, which had been patched by
special-casing modes rather than by fixing the mechanism.

`tests/test_research_runner.py` is now 21 tests covering all of the above.
