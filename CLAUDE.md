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

## 15. `swing-close` DOWN — cgroup OOM kills masked by `&& curl … || true` (2026-07-28)

### Symptom
Healthchecks.io reported `swing-close` DOWN: *"success signal did not arrive on time,
grace time passed."* Supercronic, meanwhile, logged **`job succeeded`** for the same run.
Both were telling the truth about different things.

### Root cause — two independent faults compounding
**1. The scan exceeds its memory cap.** `sb-scanner-cron` had `mem_limit: 1024m`
(compose.yaml, added by the §9 reliability pass). The 16:30 ET job loads FinBERT
(~440 MB of weights + torch) on top of the scan and peaks at **anon-rss ≈ 1007–1009 MiB**
— marginally over 1 GiB. The cap was not a safety margin, it was the binding constraint,
and the cgroup OOM killer fired on **four** jobs on 2026-07-27 (host `dmesg`, UTC→ET):

| ET | Job | Pings? |
|----|-----|--------|
| 10:37 | 09:35 swing scan | `HC_UUID_SWING` — also silently unpinged |
| 12:25 | — | |
| 16:52 | **16:30 swing-close** | `HC_UUID_SWING_CLOSE` — the reported outage |
| 20:45 | daytrade evening | no curl tail, so it surfaced honestly as `exit status 137` |

The job log's final line is a bare `Killed`.

**2. The ping shape hid it.** Every healthcheck line was
`cmd >> log 2>&1 && curl ".../${UUID}" … || true`. On a non-zero exit:
- `&&` short-circuits ⇒ **the curl never runs, so no ping is ever sent** — the check
  can only go down later, when the grace window expires;
- `|| true` then forces exit 0 ⇒ **supercronic logs "job succeeded"** for a SIGKILLed job.

So the one job that failed loudly (daytrade, 20:45) was the only one *without* a curl
tail. The pattern inverted the signal: adding a healthcheck made failures less visible.

### Fix (branch `fix/cron-oom-healthcheck-visibility`)
- **All 10 ping lines** → `cmd >> log 2>&1; curl ".../${UUID}/$?" …` — `;` not `&&`, so
  the ping always fires, and the trailing `/$?` reports the exit status (`/0` success,
  non-zero failure; a SIGKILL sends `/137`). Healthchecks fails the check *immediately*
  instead of waiting out the grace window. Verified in POSIX `sh` that a real `kill -9`
  propagates as 137.
- **`mem_limit: 1024m → 1400m`** — ~390 MiB of headroom over measured peak. This spends
  some of the protection the caps exist for: caps now sum to 2424m against 1907 MiB RAM.
  Accepted only because the 4 GB swapfile is in place and near-untouched, and
  cloudflared/tailscale keep `oom_score_adj: -500` so the host killer still avoids the
  two lifelines whose loss caused the 7h outage.
- **`tests/test_crontab_parity.py`** gains two checks (`…not_short_circuited_by_and`,
  `…send_the_exit_status`). Both were mutation-tested: reverting one line to the old
  form fails them; restoring passes. 559 tests green.

### Open / not fixed
- **The box is too small.** t3.small = 1907 MiB usable, below the README's own 4 GB
  minimum, for a scan that legitimately wants ~1 GiB alongside five other containers.
  Raising the cap moves the pressure to the host; a resize is the actual fix.
- **Why the scan needs ~1 GiB** is not diagnosed. `all.txt` is 1300+ symbols and the
  process emits `resource_tracker: There appear to be 1 leaked semaphore objects` at
  shutdown — multiprocessing may not be releasing cleanly.
- `HC_UUID_SWING` almost certainly also failed to ping at 10:37 on 2026-07-27; worth
  checking whether that monitor alerted too.

### Lesson
`|| true` on a cron line buys "no spurious alert" at the cost of "no alert at all."
Any command whose success is reported to an external monitor must separate the report
from the work with `;`, and must forward the exit status — otherwise the monitor is
measuring whether the *shell* ran, not whether the *job* worked.

## 16. Scan concurrency was the OOM cause — serialize, retire daytrade (2026-07-28)

§15 raised `mem_limit` 1024m→1400m and fixed the ping shape that hid the kills. That
treated the symptom. This section is the demand-side fix, and it corrects §15's own
sizing rationale.

### Measurement: FinBERT costs a FLAT ~704 MiB, independent of workload
Measured inside `sb-scanner-cron` (not inferred):

| stage | RSS |
|---|---|
| baseline python | 12.2 MiB |
| `import finbert_sentiment` | 114.9 MiB |
| **first inference (model resident)** | **704.4 MiB** |
| 16-headline batch | 704.4 MiB |
| 64-headline batch | 704.4 MiB |

**Scoring more headlines is free.** Two consequences:
1. The intuitive fix — "only score sentiment on the PREMIUM shortlist" — would have
   saved **~0 MiB**. FinBERT was *already* restricted to HIGH+ signals
   (`breakout_scanner.py:198`, `_quality_sigs`), and the cost is model residency, not
   inference volume. Note also that FinBERT is **not** gated by `--sentiment`; that flag
   only controls the Tavily `check_sentiment` block at line 122. Dropping `--sentiment`
   from cron would not have stopped the model loading.
2. Container peak ≈ **704 MiB × number of scans running concurrently**. The cause was
   concurrency, not any single job.

### The actual concurrency
- `swing` and `daytrade` both fired at **`35 9`** on the same 1375-symbol `all.txt`, and a
  wide scan runs **>60 min** (the 2026-07-27 swing scan was killed 62 min in) — so they
  overlapped for their entire runtime, every weekday.
- On Mondays the `0 9` longterm scan was **still running at 10:00**, making three
  concurrent wide scans in a 1024 MiB cgroup.
- All three wide-scan logs (`cron_swing`, `cron_daytrade_morning`, `cron_longterm`) end
  in `Killed`. **12 cgroup OOM kills** between Jul 23 and Jul 28 — i.e. every trading day
  since the caps were added, not the 4 that §15 recorded.

⚠ **This invalidates §15's headroom claim.** §15 sized 1400m as "~390 MiB over the
measured peak of ~1009 MiB." But 1009 MiB is where the *killer intervened*, not where
demand stopped: kills occurred at anon-rss of 724–1009 MiB against a 1024 MiB cap, so
the cgroup charge (anon + page cache + kernel) hit the limit while anon-rss was far
lower. Peak demand was truncated, never measured. 1400m is adequate for **one** resident
scan; it was never going to be adequate for two.

### Changes (`docker/crontab`)
- **`flock -w 5400 /tmp/sb-heavy-scan.lock`** on all four scan jobs (longterm wide, swing
  wide, 16:30 Phase 2, 19:30 evening) → peak is `max(job)`, not `sum(jobs)`. Verified in
  the container: flock forwards the child's exit status (42→42), still reports **137** on
  SIGKILL so the `/$?` ping keeps working, genuinely blocks the second job, and returns 1
  on timeout so a 90-min wait surfaces as a failure rather than silence.
  **Deliberately NOT applied** to exit / `refresh_prices` / monitor jobs — a stop breach
  must never queue behind a 60-minute discovery scan.
- **Longterm wide scan moved `0 9` → `0 6` Monday.** At `0 9` a >60 min job was still
  resident at 9:35. Behaviourally identical (daily bars, both pre-open ⇒ same last
  complete bar). ⚠ **Requires a manual Healthchecks.io schedule update for
  `HC_UUID_LONGTERM`**, or it reports a missed ping every Monday.
- **Daytrade retired** (5 jobs, commented not deleted). This was already a decided-but-
  unexecuted follow-up (§9). Not a no-op for trading: `--cron` calls
  `scan_and_add_all_users()`, which admits any GOLD/PREMIUM signal *regardless of mode*,
  so these lines did reach the live book. The project's own A/B (§7,
  `daytrade_admission_ab.py`) measured removal at **B−A = +0.09 Sharpe** (favourable, just
  under the +0.10 bar) with only 2 of 41 control-arm trades being daytrade.
- **Momentum-watch monitor retired** (3 jobs) — a *consequence* of the above, not a
  separate decision. `monitor_watch.py` defaults to `--mode daytrade` and reads
  `momentum_watch_daytrade.txt`, whose only writer was the daytrade 9:35 job's
  `--export-momentum-watch`. Leaving it enabled would have re-created §13's bug exactly:
  a never-refreshed list re-alerting every 15 minutes. `--mode swing` is not a drop-in
  fix — nothing exports `momentum_watch_swing.txt`.

Active cron lines: **24 → 16**. Five new tests in `tests/test_crontab_parity.py`, each
mutation-tested (revert one line ⇒ that one test fails). 564 pass.

### Open
- **`MarketData.clear_cache()` never clears `series_cache`** (`market_data.py:406-408`) —
  an unbounded `(symbol, timeframe) → DataFrame` dict. Currently harmless: its only
  callers (`orchestrator.py:369-374`) are gated on `TENSION_CONFIG['enabled']`, which is
  `False`. It becomes a real leak the moment Tension Index is re-enabled.
- Whether one wide scan alone fits in 1400m is still unconfirmed — the first clean
  single-scan run is the datapoint. If it OOMs again, the box genuinely is too small
  (t3.small = 1907 MiB usable vs the README's own 4 GB minimum) and t3.medium is the fix.

## 17. t3.small is definitively too small — resize required (2026-07-28)

§16's open question ("does one wide scan alone fit in 1400m?") is answered: **no.**

### Evidence
Manual re-run of the OOM-killed Monday longterm job, nothing else on the box, §16
serialization active (mutex verified HELD, so genuinely a solo scan):

- 07:40:17 ET → `rc=137` (SIGKILL) at **08:52:55 ET — 72 min in**
- memory 162 MiB → 1.143 GiB → **1.361 GiB of the 1.367 GiB cap = 99.55%**
- host: 108 MiB available of 1907; cgroup OOM kill **#13**

Composition at peak: ~704 MiB FinBERT resident + **~690 MiB held by the scan itself**.
So FinBERT is only half the problem, and serialization — while necessary, it removed a
2–3× concurrency multiplier — was never going to be sufficient.

⚠ This retires §15's headroom claim. "1400m leaves ~390 MiB over the measured peak of
1.01 GiB" was wrong because 1.01 GiB was where the *killer intervened*, not where demand
stopped. Every OOM measurement in §15/§16 is truncated for the same reason; treat any
"peak" taken from a killed process as a lower bound only.

### Action: resize to t3.medium AND raise the cap — both, or nothing changes
`mem_limit: 1400m` binds regardless of host RAM, so a resize on its own leaves the scan
dying at exactly the same point. `compose.yaml` now carries **2500m**, which **must not be
applied while the box is still t3.small** (2500m > 1907 MiB ⇒ the cgroup can never bind and
the *host* killer fires instead — the §9 failure mode the caps exist to prevent).

Order: resize first → `git pull` → `docker compose up -d scanner-cron`. A stop/start alone
brings containers back with their OLD limits.

t3.medium (2 vCPU / 4 GiB, ~$35/mo vs t3.small's ~$17.52 in eu-central-1) is same-arch, so
no rebuild; the Elastic IP holds the address and every container has `restart:
unless-stopped`. Note it has the **same CPU baseline and credit rate** as t3.small — it only
doubles RAM. Scans will not get faster; the 72-min duration is network-bound on 1375
yfinance calls. Changing instance type needs `ec2:ModifyInstanceAttribute`, which is **not**
in `deploy/iam-recovery-policy.json` — use the AWS console.

### Software levers measured, and why neither is the answer yet
- **Free the model after use — no.** `del pipeline` + `gc.collect()` returns only **132 MiB
  of 742 (18%)**; the rest is torch/transformers import overhead plus allocator arenas that
  never return to the OS. It is also the wrong timing — FinBERT loads at the *end* of the
  scan, so freeing it afterwards cannot lower peak.
- **int8 dynamic quantization — unsafe as a drop-in.** Labels agreed 8/8 on a sample, but
  confidence moved up to **0.32** ("Board approves a $2bn buyback" 0.865 → 0.648; "Guidance
  cut" 0.762 → 0.442). `FINBERT_PROMOTION` gates on `min_score` **0.80** (HIGH→PREMIUM) and
  **0.88** (PREMIUM→GOLD), and promotions feed `scan_and_add_all_users()` — so this would
  silently change which positions are opened. Would need threshold recalibration plus a
  backtest. (Measured on macOS/qnnpack; the container has fbgemm and needs its own run.)
- **Process isolation — structurally right, blocked on the above.** Since ~610 MiB never
  returns in-process, running FinBERT in a short-lived subprocess reclaims 100% on exit. But
  the cgroup counts all processes, so it only lowers peak once the parent stops holding
  ~690 MiB at that moment.

### Open
**Profile what the scan still holds after detection completes.** By the FinBERT stage the
scan is finished and `results` is just a list of signal dicts, yet ~690 MiB is resident. If
that were released before enrichment, peak would fall to ~700–800 MiB and would fit even on
t3.small. This is the cheapest and largest remaining win, and it is not yet investigated.

### Silver lining: §15's ping fix is proven in a real failure
The kill produced `/137` and Healthchecks recorded `last_ping` **one second** after the
exit, flipping the check to `down` immediately instead of waiting out its 3 h grace. Under
the old `cmd && curl … || true` no ping would have been sent at all and supercronic would
have logged "job succeeded" — the exact blindness that let the Monday failure sit unnoticed
since Jul 23. First correctly-reported kill.

## 18. The real OOM cause: scan_and_add replays the whole S3 archive (2026-07-28)

§17 concluded "t3.small is too small, resize." That was **wrong** — or rather, it fixed a
real but secondary problem. The resize happened, the cap went to 2500m, and the very next
wide scan died anyway.

### The datapoint that exposed it
| cap | died at |
|---|---|
| 1400m | ~1.37 GiB |
| 2500m | **~2.45 GiB** |

The process **expands to fill whatever ceiling it is given**. That is a leak signature, not
a sizing problem, and it means every "measured peak" in §15/§16/§17 was just the cap.
**Standing lesson: if peak == cap on every observation, suspect unbounded growth, not
under-provisioning.**

### Root cause
`scan_and_add()`'s file loop was bounded **only** by the book's `processed_files` set, and
`list_files()` reads **S3**. So a book whose `processed_files` was empty or far behind
re-downloaded and re-parsed the entire archive on every scan.

On 2026-07-28: **858** signal CSVs on S3 back to 2026-04-06, against per-book backlogs of
858 / 858 / **848** / 61 — roughly **2,600 S3 downloads + pandas loads per run**, plus a
yfinance quote per candidate.

Evidence chain:
- all three killed runs died **40+ min after** writing their signals CSV (detection itself
  takes ~26 min and completed fine every time)
- the last log lines before each kill were yfinance 404s on **CTRA**/**OS** — delisted
  tickers from *old archived* signals, not among that day's signals
- memory tracked files consumed, so it never plateaued

**Why the existing guard missed it:** `scan_and_add_all_users()` (auto_portfolio.py:1119)
fires only when `processed_files` **and** `positions` are *both* empty. The book doing the
most damage had 8 positions and 10 processed files — it fell straight through. The guard
is also caller-side, so it cannot protect the loop.

### Fix
`SIGNAL_MAX_AGE_DAYS = 7`, enforced **inside** the loop, so it holds regardless of caller.
Stale files are *retired* into `processed_files` without ever being loaded — no S3 read —
which also lets a far-behind book self-heal in a single pass. An explicit `min_date=` still
overrides, so deliberate backfills are unaffected.

This is a **correctness** fix as much as a performance one: auto_portfolio prices entries at
TODAY's price (it deliberately distrusts the CSV's `Price` column — HZ1), so admitting a
weeks-old signal opens a position on a setup that already played out.

`tests/test_scan_add_stale_window.py` — 5 tests, mutation-verified (removing the bound fails
3, including the production shape: positions present + processed_files far behind).

⚠ **`tests/test_exit_from_portfolio.py::TestV9HFilter` had to change too**, and the reason
is worth remembering: its 6 fixtures used a hardcoded `20260318` filename. Three of them
broke outright; the other three — the *rejection* tests — kept passing **for the wrong
reason**, blocked by the new age bound before the V9-H filter they exist to test ever ran.
Dates are now derived from today. Verified non-vacuous by re-running with
`SIGNAL_MAX_AGE_DAYS = 365`: still green, so the rejections are filter-driven.

### Status of §17's resize
Not wasted — t3.small was genuinely below the README's own 4 GB minimum and the legitimate
working set is ~700 MiB–1 GiB — but it was **not** the fix, and the 2500m cap is now far
larger than needed. Once this fix is confirmed in production, consider whether the cap can
come back down.

## 19. The leak under §18: every S3 read builds a new botocore client (2026-07-29)

§18 fixed a real defect — replaying 858 archive files is wrong on correctness grounds
alone — but it fixed the *symptom*. The per-file cost that made the replay fatal is a
separate, still-live bug one layer down, and it is now measured rather than inferred.

### Tooling built for this (all new, all off by default)
- **`memory_trace.py`** — stdlib-only tracer (no psutil; the container is rebuilt from
  `requirements.txt` and a debug tool that needs a new dependency is useless during an
  incident). Inert unless `SB_MEM_TRACE=1`. RSS via `/proc` on Linux and `task_info` on
  macOS; **also reports the cgroup charge**, which is what the OOM killer enforces and
  which §17 showed diverges sharply from anon-RSS. `tracemalloc` is *separately* gated
  behind `SB_MEM_TRACE_ALLOC=1` because it roughly doubles the footprint it measures.
- **Marks wired into the OOM path** — `orchestrator.scan_watchlist` (per-symbol tick,
  detection/earnings/missed-movers boundaries), `breakout_scanner.run_scan_mode` (stage
  brackets around detection / enrichment / save / scan_and_add), and
  `auto_portfolio.scan_and_add` (per-file tick reporting the four accumulators that grow
  with files×rows). All no-ops when the env var is unset.
- **`debug_memory_scan.py`** — three modes: `replay` (archive A/B, bounded vs unbounded,
  **one process per arm** so one arm's unreleased arenas cannot inflate the other's
  baseline), `scan` (real detection, subsampled), `finbert` (residency isolation).
- **`tests/test_memory_trace.py`** — 18 tests, each mutation-verified. 587 pass overall.

⚠ **The harness's first draft wrote a probe portfolio into the production S3 bucket**
(`save_json` mirrors to S3 whenever AWS creds are present) and then read it back on the
next arm, so arm A saw arm B's `processed_files` and loaded zero files — a silently
invalid A/B. Deleted from the bucket; the sandbox is now pinned by a test.

### The measurement that isolates it
Same real signal CSV read 20 times from S3, macOS local:

| arm | per-read growth | 20 reads |
|---|---|---|
| `utils._s3_fs()` per read (**current behaviour**) | **+12.8 MB, perfectly linear** | +256 MB |
| one filesystem constructed once, reused | +13.4 MB once, then **+0.0 MB** | +13.4 MB |

**Root cause: `utils.py:189`, `skip_instance_cache=True`.** That flag defeats fsspec's
instance cache, so every S3 operation constructs a fresh `S3FileSystem` → a fresh
aiobotocore/botocore client → botocore re-parses its S3 service-model JSON, and none of it
is released. `tracemalloc` attribution on a 40-file replay names it outright:
**`json/decoder.py:361  +410 MB across 5.5M blocks`**, with `botocore/loaders.py:307` and
`botocore/model.py:777` immediately behind it. The flag was added deliberately (commit
`4007cd0`) to dodge an AioSession kwarg bug in s3fs ≥2025 + aiobotocore ≥3.x — so the fix
is to memoize the constructed filesystem, **not** to simply drop the flag.

### Why this explains every earlier observation
- **"Expands to fill whatever ceiling it is given."** 858 files × ~12.8 MB ≈ 11 GB of
  demand. The process hits a 1400m cap ~107 files in and a 2500m cap ~191 files in — so
  peak always equals the cap and never the demand. §15/§16/§17 each measured a ceiling.
- **Kills landed 40+ min *after* the signals CSV was written** — that is the replay loop.
- **The synthetic replay arm grew only +13.7 MB over 400 files**, because stubbing
  `load_data` removes exactly this layer. The real-archive arm grew **+957 MB over 150
  files**. That gap *is* the bug.
- **§17's unexplained "~690 MiB held by the scan itself"** is very likely the same thing:
  a normal scan still makes ~10–20 S3 calls (`list_files`, `save_data`, per-user portfolio
  load/save), at ~12.8 MB each.

### What is NOT the leak (measured, so stop suspecting these)
- **Detection.** 400 symbols traced end to end: peak 150 MB, **net −55 MB**. Flat per
  symbol. The missed-movers second data pass is also flat.
- **FinBERT.** Reproduces §16 to the megabyte: +81.8 MB on import, **+695.5 MB on first
  inference**, and **+0.0 MB for a 4× larger batch**. A large but *bounded, one-time*
  residency — and confirmation that "score fewer headlines" saves nothing.
- **The pipeline's own data structures.** `rejection_reasons`, the pooled DataFrames, and
  the entry/split/price caches together account for ~14 MB per 400 files.

### ⚠ §18's age bound does NOT make this safe on its own
Counted against the real archive: the 7-day window still holds **31 files** (up to 10/day
— several modes × several scans). A book with an empty or far-behind `processed_files` set
therefore still makes **33 `_s3_fs()` calls ≈ 422 MB**, and five such books ≈ **2.06 GB —
over the current 2500m cap**. Steady state (book current, ~5-10 new files/day) is ~40-65
calls ≈ 510-830 MB per scan. So "confirm §18 in production" was never sufficient; the
per-call term had to go too.

### Fix APPLIED (2026-07-29, `utils.py`)
`_s3_fs()` now memoizes one `S3FileSystem` per `(key, secret, region)` at module scope,
under a lock. **`skip_instance_cache=True` is preserved** — the memo wraps *around*
fsspec's instance cache rather than re-enabling it, so the `4007cd0` AioSession workaround
still holds. All 7 call sites go through a new `_s3_call(op)` which, on any error, discards
the cached filesystem, rebuilds, and retries **once** before letting the caller's existing
local-disk fallback take over. Every op passed to it is a whole-object read, whole-object
overwrite, or listing — all idempotent, so the retry is safe.

`list_files` already called `invalidate_cache(prefix)` before every `ls`; that pre-existing
call is exactly what makes reuse safe, since listings are the one thing fsspec caches
across calls. It is now pinned by a test.

Measured before → after:

| measurement | before | after |
|---|---|---|
| 20 reads of one S3 object | +255.7 MB (12.8 MB each, linear) | **+0.7 MB total** |
| …wall clock | 7.1 s | **1.7 s** |
| `replay --real-archive --files 150` | **+957.3 MB**, verdict CLIMBING | **−48.0 MB**, verdict working set |

Risk was tested, not assumed: survives repeated `asyncio.run()` boundaries (s3fs uses its
own process-global IO loop, not the caller's), 8-16 concurrent threads share one instance
with zero errors, object reads through the production shape (`fs.open` → `read_csv`) show
no staleness across an overwrite of different size, the Streamlit branch is untouched, and
a deliberately poisoned cached filesystem self-heals on the next call. 9 new tests
(`tests/test_s3_fs_reuse.py`), each mutation-verified; **596 pass**.

~~**Still to do:** re-verify on the box~~ → **CONFIRMED ON THE BOX 2026-07-30**, see §21.

## 20. Signal ingestion, Phase 0: bound it (2026-07-29)

§18 and §19 each fixed a symptom of one design problem: the S3 **file archive is used as a
work queue**. This is the first of a planned two-step correction. Phase 0 adds no new
components — it bounds what production reads. (Phase 1+, a derived SQLite index, is
planned but deliberately **not** started until these numbers are confirmed in production.)

### Measured, real archive (860 files, 2.1 MB)

| Lever | Before | After |
|---|---|---|
| **S3 calls per `scan_and_add_all_users`** (3 books = production today) | 99 | **37** |
| …5 books / 8 books | 165 / 264 | **41 / 47** |
| **`processed_files` persisted per book** | 860 entries | **53** (30d window) |
| **`rebuild_skipped_cash` archive reads** | **1,720** (walked twice) | **638** (90d, single pass) |

Cost is now O(files + books) instead of O(files × books).

### What changed
- **`_prune_processed()`** — `processed_files` is persisted only for the window in which it
  can still do work. Safe *because* the age bound is the real guard: a pruned entry is by
  construction older than `stale_cutoff`, so the next run retires it at
  `auto_portfolio.py:246-255` **without a read**. `PROCESSED_PRUNE_MARGIN_DAYS = 23`
  (7+23=30, matching `cleanup_outputs.py`) — a margin, not an exact match, because the
  guarantee **inverts if anyone raises `SIGNAL_MAX_AGE_DAYS`**: previously-pruned files
  become loadable again and weeks-old signals get admitted at today's price. Pinned by
  `test_prune_window_exceeds_load_window`.
- **`_SCAN_FILE_CACHE`** — scoped to one `scan_and_add_all_users()` call, cleared in a
  `finally`. ⚠ Hands out `.copy()`: `scan_and_add` rewrites `df_raw.columns` **in place**,
  so a shared frame would let one book's edits reach another — cross-user corruption, not a
  perf detail. Deliberately not module-lifetime: signal files are appended to during the
  day, and a surviving frame would make a book miss that day's signals.
- **`rebuild_skipped_cash`** — the unfixed §18 twin, reachable from a Streamlit button in
  the **320 MiB** dashboard container. Now bounded (`MISSED_TRADE_MAX_AGE_DAYS = 90`,
  `max_age_days=0` restores the old unbounded behaviour) and single-pass: the old first
  walk existed only to collect `signal_syms`, a subset of what the second walk already
  read.
- **`_v9h_mask(df, *, type_bypass)`** — one filter replacing two hand-written copies that
  had silently drifted. `scan_and_add` passes `True` (BOUNCE/CONTINUATION/SMA20_CROSS/
  TREND_CONFIRM skip the Minervini gate — those detectors never compute a score);
  `rebuild_skipped_cash` passes `False`, so "missed trades" is computed **stricter than
  admission**. That asymmetry is preserved, not fixed — changing it changes what the UI
  reports, which is a product decision. It is now visible in one place.
- Removed a stray `processed.add(fname)` that sat outside the file loop on a leaked loop
  variable — dead, and a landmine for exactly this refactor.

### Retention decision: keep everything
Archive growth is **7.3 files/day → 6.6 MB/year → 66 MB per decade**. Storage is not the
cost; *walking* it is. Deleting history is irreversible and destroys the research
substrate. **Do not prune S3.** Bound each consumer's read window instead — and note the
repo currently has four inconsistent local windows (`SIGNAL_MAX_AGE_DAYS=7`,
`scan_feedback_agent._KEEP_DAYS=7`, `cleanup_outputs.py=30`, `cron-setup.sh=90`) and no S3
pruning at all.

⚠ **`SIGNAL_MAX_AGE_DAYS` is a correctness bound, not a performance knob.** Entries are
priced at *today's* price (HZ1), so widening it admits setups that already played out.

### Traps found while measuring, worth remembering
- **A sandbox that maps every user to one book hides this entire class of bug.** The first
  A/B reported *zero* improvement because books 2..N read book 1's freshly-saved
  `processed_files` and skipped everything. Per-user book paths are mandatory when
  measuring multi-book behaviour.
- **`rebuild_skipped_cash` fires a live 5-day yfinance fetch per symbol** whenever
  `entry_price == price` (it reads that as "yfinance fell back to the CSV value"). A test
  stub returning `(price, price)` turns a hermetic unit test into a network test — 31 s
  vs 0.02 s.

### Tests
`tests/test_scan_file_cache.py` (7), `tests/test_rebuild_skipped_cash_bound.py` (4), and
`tests/test_scan_add_stale_window.py` extended to 7. Five mutations verified (no prune, no
`.copy()`, cache never cleared, rebuild unbounded, stale files loaded) — each fails only
its own tests. **609 pass.**

### Standing lesson
**If peak == cap on every observation, you are measuring the ceiling, not the demand.**
The only way out is a probe that reports growth *per unit of work consumed* while the
process is still alive — which is what `SB_MEM_TRACE=1` now provides. Note also that the
first verdict heuristic in `debug_memory_scan.py` called +575 MB then +232 MB a "plateau"
because the ratio halved; a process still paying 3 MB per file has not stopped growing.
Judge the per-item *rate*, not the ratio between halves.

## 21. §19 + §20 deployed and confirmed on the box (2026-07-30)

Closes the gate that §19 ("re-verify on the box") and §20 ("Phase 1 … deliberately not
started until these numbers are confirmed in production") both blocked on. Deployed via
PR #2 → `14ecc2a`; box rebuilt with `docker compose up -d --build` (all app services, not
just `scanner-cron` — `utils.py` is shared, and `sb-api` had been up 42 h holding exactly
the kind of idle pool §19's memoization introduces).

### The replay A/B, run inside `sb-scanner-cron` on Linux

| | pre-fix (recorded §19) | **measured on the box 2026-07-30** |
|---|---|---|
| `replay --real-archive --files 150` | +957.3 MB, **CLIMBING** | **+40.1 MB, "working set"** |
| per-item rate, 1st half | ~6.4 MB/item | **+0.01 MB/item** |
| per-item rate, 2nd half | still climbing | **+0.00 MB/item** |

~600× reduction in the per-file cost. The +40 MB is one-time working set (111 entry/split
cache entries plus pandas), not growth. Arm A loads 0 files, which is correct rather than a
null result: the harness replays the *oldest* files, all long past the 7-day bound, so they
are retired without a read — §18's age bound doing its job.

**Judged on the per-item rate, not the totals.** The macOS totals were unstable run to run
(−44.9 MB, then +5.8 MB, for the identical command) because RSS totals move with allocator
behaviour. The rate is the stable, decisive figure — §20's own standing lesson, and the
reason the earlier ratio-based verdict heuristic was wrong.

### ⚠ The harness reported a broken measurement as a benign result (`076e1dd`)

Running §19's own verification locally produced `files loaded 0` in **both** arms and
printed *"The age bound avoided 0 file loads and −2.6MB of growth"* — then exited 0.

Root cause: `debug_memory_scan.py` never called `load_dotenv()`. It imports
`auto_portfolio` and `utils`, neither of which imports `config` (where `load_dotenv()`
normally happens), so locally `utils._is_cloud()` was False, `list_files` fell back to an
empty local dir, and nothing was measured. Invisible in the container, where compose's
`env_file: .env` supplies the credentials as real env vars — which is precisely why it
survived: the tool works where it is deployed and lies where it is developed.

Fixed both halves: `load_dotenv()` at import, and the unbounded arm loading 0 files now
exits 1 with a diagnostic. That arm exists to load everything, so zero is definitionally an
environment fault, never a finding.

⚠ **Trap for the mutation-testing method itself.** Verifying that guard means flipping
`return 1` → `return 0` — **byte-length-identical**, so the `.pyc` validity check
(mtime-second, size) does not trip and a stale bytecode cache silently serves the old
code. It masked the *restore* here and briefly looked like the guard was broken. **`touch`
the file after every mutation and after the restore**, or the mutation test can pass or
fail for reasons unrelated to the change.

### `orchestrator`'s signal-CSV upload never self-healed (`6bfa62f`)

`save_results` mirrored the signals CSV to S3 via a bare `_s3_fs().put()` — the only
`_s3_fs()` call site outside `utils.py`, so the only S3 access that did **not** go through
`_s3_call`'s drop-rebuild-retry after §19. Worse than the count suggests: `scanner-cron`
runs for days, it sits on the write path for the signal CSVs every consumer reads, and its
`except` only logs a warning — so a stale pool means the day's signals silently never reach
S3 while the scan reports success. Now wrapped; `put()` overwrites a whole object, so the
retry is idempotent like every other op `_s3_call` takes. Test covers both the behavioural
half (one stale-pool failure survived, dead filesystem discarded) and the structural half
(no `_s3_fs().put` anywhere in the file).

### The in-situ half of the gate — PASSED (2026-07-30 09:35 ET scan)

One instrumented wide `all.txt` scan (1363 symbols, `SB_MEM_TRACE=1`), completed cleanly:

| probe | pre-fix | measured |
|---|---|---|
| `to_load=` (files pulled per book) | 858 | **1** |
| files read for 3 books | ~2,600 loads | **1, read once for 3 books** |
| `STAGE 4-scan_and_add_all_users` | 40+ min, climbing → SIGKILL | **12.6 s, Δ +27.3 MB** |
| `STAGE 1-detection` | — | +18.3 MB in 1459 s |
| peak RSS | filled whatever cap existed | **1013.6 MB** |
| **cgroup peak vs cap** | **~98% of cap, 13 times** | **912.2 MB / 2500 MB = 36.5%** |
| outcome | rc=137 | **completed, 26 min** |

The last row is the proof. Peak had always equalled the cap (1.37 GiB under 1400m, 2.45 GiB
under 2500m) — the leak signature. It now sits at **36.5%** and finishes. Composition is a
real working set: ~704 MB flat FinBERT residency + ~300 MB scan.

⚠ **Read the probes from `scanner_output/logs/scanner_YYYYMMDD.log`, not `cron_*.log`.**
`breakout_scanner.py:1715` sets the *stderr* handler to `ERROR`, so INFO — which is every
`[MEM]` line — goes only to the dated FileHandler. The cron log captures errors alone. A
watcher pointed at `cron_swing.log` reported "no probes found" while 501 `[MEM]` lines sat
in the other file, i.e. a successful run looked like a failed measurement.

### Cap lowered 2500m → 1500m (`61d80cb`)
Sized from the untruncated peak above: ~64% headroom over 912 MB. Caps now sum to **2524m**
against ~3900 MB usable, down from 3524m. `compose.yaml`'s self-contradictory comments are
fixed too — the header had still described the box as a t3.small, and the cap rationale
still named "the ~690 MiB the scan holds after detection" as the open lead, which §19
identified as the same s3fs per-call leak. `SB_MEM_TRACE` removed from the box's `.env`
(restored from `.env.bak.20260730`), tracing verified off.

### Still open
- **Phase 1** — recommendation is now **defer, and build Parquet not SQLite if ever**:
  Phase 0 already took the hot path to ~1–2 CSV GETs of ~11 steady-state S3 ops; the index
  would be 6–10 MB over a 2.1 MB corpus; and expressing `_v9h_mask` in SQL would re-fork the
  filter §20 just unified — where a missing `MinerviniScore` column means *no gate at all*
  (`auto_portfolio.py:246-247`), so getting it wrong changes live admissions. Cheaper
  follow-ups instead: hoist the per-book archive LIST into `_SCAN_FILE_CACHE` (the only fix
  whose benefit grows with the archive), batch `rebuild_skipped_cash`'s yfinance calls (the
  real bottleneck there, not S3), and close the `min_date` hole at
  `auto_portfolio.py:346-348`, which bypasses **both** the `processed` bookmark and
  `stale_cutoff` — a correctness gap, since entries price at today's price.

## 22. Three production bugs found while shipping §21 (2026-07-30)

All three were found by *using* the system rather than reading it, and all three share a
shape: **a check or a config that fails silently, so the broken state looks like a normal
one.**

### 22.1 The verification harness lied where it was developed (`076e1dd`)
`debug_memory_scan.py --real-archive` needs AWS credentials for `utils._is_cloud()`, but
nothing on its import path loads `.env` — it imports `auto_portfolio` and `utils`, neither
of which imports `config`, which is where `load_dotenv()` normally happens. Locally that
made `_is_cloud()` False, `list_files` fall back to an empty local dir, and **both** A/B
arms load zero files. It then printed *"The age bound avoided 0 file loads and −2.6MB of
growth"* and **exited 0**. Invisible in the container, where compose's `env_file: .env`
supplies credentials as real env vars — the tool worked where it was deployed and lied
where it was developed. Fixed: `load_dotenv()` at import, plus the unbounded arm loading 0
files now exits 1 loudly (that arm exists to load everything; zero is an environment fault,
never a finding).

⚠ **Trap for the mutation-testing method itself.** Verifying that guard means flipping
`return 1` → `return 0` — **byte-length-identical**, so the `.pyc` validity check
(mtime-second, size) does not trip and stale bytecode serves the old code. It masked the
*restore* and briefly made a working guard look broken. **`touch` the file after every
mutation and after the restore.**

### 22.2 `orchestrator`'s signal-CSV upload never self-healed (`6bfa62f`)
`save_results` mirrored the day's signals to S3 via a bare `_s3_fs().put()` — the only
`_s3_fs()` call site outside `utils.py`, so the only S3 access that did **not** go through
§19's `_s3_call` drop-rebuild-retry. `scanner-cron` runs for days (a stale idle pool is
exactly what reuse introduces), it sits on the write path every consumer reads, and its
`except` only logs a warning — so the failure mode is *the day's signals silently never
reaching S3 while the scan reports success*.

### 22.3 `portfolio.json` positions always reported `days_held=0` (`575b000`)
Found from a daily Telegram exit alert naming 24 positions. 14 of them live in
`portfolio.json`, opened 2026-05-07 (84 days), yet every run reported `DaysHeld 0` — so
"Max hold period reached" could never fire for that book. The tell was an asymmetry in one
log: auto_portfolio positions in the *same* evaluation correctly showed 92/113/99 days.
Cause: both books feed one exit run but their dicts are built in different places —
`breakout_scanner.py` sets `entry_date` explicitly, `Portfolio.get_positions_as_exit_format()`
omitted it. `orchestrator.evaluate_exits` does `pos.get('entry_date', '')` and leaves
`days_held` at 0, so the omission produced no error, no warning — just a rule that never
fired for half its input.

**Two related things NOT fixed, both needing a human decision:** `portfolio.json` is never
touched by `refresh_prices` (only `auto_portfolio.json` books are), so its stops never trail
and its breaches never auto-close — which is why those 14 re-alert daily, several genuinely
deep below their stops (ASTS −18%, AMZN −11%). And all 14 carry `target: 0`, which is the
`TP: $0.0` and the meaningless negative R:R in the notification.

### 22.4 Streamlit Cloud login: settings were read from the environment only (`ef8ea65`, `ec3d938`)
Reported as "streamlit stopped working with google auth"; the pasted error was
`HTTPConnectionPool(host='127.0.0.1', port=8000) … Errno 111`.

**`Errno 111` is Linux's ECONNREFUSED — macOS is 61.** That single digit ruled out the Mac
and pointed at a Linux host with no `API_BASE_URL`.

Two distinct defects, and the first fix was **not sufficient**:
1. `app.py` used one URL for two consumers. `api_base` is server-side (in the container,
   `http://api:8000` over the compose network) but the Google button renders a **link the
   browser follows**, and `http://api:8000` is a Docker-internal name no browser resolves.
   The old code papered over this with
   `api_base.replace('127.0.0.1:8000', 'gilhadas-stocks.com')`, which only fires when
   `api_base` is the *default* — it worked on the retired Mac deployment and became a silent
   no-op the moment `API_BASE_URL` was set, i.e. from the §9 cutover onward. Its result was
   assigned to a `redirect_uri` local that was never read. → added `PUBLIC_API_BASE_URL`.
2. **The actual cause:** every setting was read with `os.getenv()`. **Streamlit Cloud has no
   way to set an OS environment variable** — its config lives in `st.secrets`, and `.env`
   does not exist in the Cloud checkout. So setting the secret could not have helped; the
   code was not looking there. `GOOGLE_CLIENT_ID` had the same defect with a quieter
   symptom: it resolved to `''`, so the Google button rendered "not configured yet" rather
   than failing visibly. → added `_setting()`: `st.secrets` → `os.getenv` → default,
   mirroring `utils._is_cloud()`'s precedence (including the try/except, since `st.secrets`
   raises when no secrets file exists — the normal case in the container).

Three deployments now resolve correctly, each pinned by a test: container (env), Streamlit
Cloud (secret), secret-beats-env, and neither → default.
**Streamlit Cloud still needs `API_BASE_URL = "https://api.gilhadas-stocks.com"` set in its
own Secrets** — a console change no commit can make.

## 23. Deleting a user orphaned their book; and two manual-scan traps (2026-08-04)

Started from an ordinary question — "I ran a manual longterm scan from Streamlit, how do I
use it in auto portfolio?" — and turned up three unrelated defects, each of the same shape
as §22's: **state that looks live but isn't, and nothing that says so.**

### 23.1 Portfolios are files keyed by path; the DB has no portfolio table
The users DB is a **single `users` table** (`trading_api_kit/models.py`; `api/models.py` is
one of the shadow duplicates — `api/server.py:18` imports from the kit). A portfolio lives
*only* as JSON at `scanner_output/portfolio/<user_id>/`, keyed by user id in the **path**,
so `db.delete(user)` cascades to nothing.

`delete_user` (`trading_api_kit/admin_routes.py`) was, in full, `db.delete(user);
db.commit()`. Every deletion therefore left a book behind that is indistinguishable from a
live one. Two such orphans — **16 and 17 open positions, ~$99k deployed each** — survived
long enough to be read back mid-investigation and reported as "the live books." The data
was fine; what it *meant* was wrong. The user's actual book was empty, which is what
Streamlit was correctly showing all along.

**Fixed:** the kit gained a `register_user_delete_hook` registry — it owns identity and
must not know about `scanner_output/`. Hooks run **before** `db.delete(user)` and a failure
aborts with a 500, because once the id is gone nothing ties the files to a person and
cleanup becomes archaeology (exactly how the two orphans became unattributable).
`api/server.py` registers `auto_portfolio.archive_user_portfolio()`, which moves each file
to `portfolio/_deleted/<user_id>/` and removes the original **only after reading the copy
back** — `save_json` swallows S3 errors, so a successful-looking write is not proof of a
copy. Archive, never delete: a book is the only record a user ever traded (§20's rule).
`utils.delete_file()` added as the missing half of the local+S3 pair; its existence check
sits *inside* the `_s3_call` op so the rebuild-and-retry cannot turn "already gone" into an
error, and unlike `save_json` it does **not** swallow S3 failures.

Backfilled the same day: 5 directories archived (2 orphans, avivss's book, and the
`__test_recalc_fix__` / `__test_scan_add_isolation__` probes that had leaked into the
production bucket — the §19 trap again), positions verified 16/16, 17/17, 16/16 after the
move. Empty leftovers removed from the box (`0c6edf84…/`, `_prerefresh_backup_/`) and the
Mac. `tests/test_user_delete_cleanup.py` — 11 tests, each mutation-verified.

⚠ **One of those mutations caught a bug in the test, not the code.** The "host app
registers the hook" test grepped `inspect.getsource(api.server)` for
`register_user_delete_hook` and **passed with the call deleted**, because the *import*
line contains the same string. Rewritten to import the module and assert the registered
hook actually reaches `archive_user_portfolio`. A substring assertion cannot distinguish
using a name from importing it.

**Identity facts worth not re-deriving:** production has exactly **two** users
(`cf699841…` = gil.hadas@gmail.com, `6cf6c4a5…` = gil.hadas+1@gmail.com). `users.db` is
**per-box and never synced to S3** (`api/database.py`), so the repo's local copy is a stale
Mac artifact — always read the box's. There is **no default
`scanner_output/portfolio/auto_portfolio.json`** in production; `scan_and_add_all_users()`
writes per-user books only, so an unauthenticated Streamlit session (`user_id=None`) loads
an empty legacy book and correctly reports "No open positions."

### 23.2 A manual Streamlit scan is not a small cron scan
`pages/scan_page.py::_run_scan` calls `orchestrator.scan_watchlist` + `save_results`
**directly**, so the signals CSV lands in S3 like any other and `scan_and_add` — which is
mode-agnostic (`signals_*.csv`) — consumes it on the next `--cron` run. Nothing needs
importing. But the manual path skips two things that only exist in the CLI:

- **`MAX_SIGNALS_PER_SCAN = 20`** and the same-day symbol dedup live in
  `breakout_scanner.py:497`. Measured 2026-08-04: manual longterm = **187 rows / 109
  GOLD+PREMIUM**, vs a cron file's 20 rows / ≤19. Since the pooled cap groups by *date*, an
  ad-hoc scan's GOLD rows outrank the day's swing signals for the same 10 slots.

- ⚠ **Never admit premarket.** `_fetch_entry_and_current` fetches **daily bars**; before
  the open today's bar does not exist, `hist` is empty, and it returns
  `(csv_price, csv_price)` — falling back to the exact column the system deliberately
  distrusts (HZ1: ~32% of scanner rows carry impossible prices). Clicking "Scan Signals"
  premarket, or adding a premarket cron line, removes the book's price insulation. This is
  a correctness regression, not a timing preference. To admit earlier, add a standalone
  `scan_and_add_all_users()` cron line at ~9:40 ET (today's earliest is ~10:05, because the
  call is a *tail* of the 9:35 scan and that scan takes ~26 min).

### 23.3 The pooled ranking trusts an R:R a later guard can invalidate — issue #3
`scan_and_add`'s priority sort ranks on the CSV's `R:R` column, but the stop-distance guard
(`auto_portfolio.py:562`) can rewrite the stop afterwards. CAPR on 2026-08-04: csv price
$4.20 / stop $2.47 / target $23.73 → **R:R 11.27**, which won it slot 9 of 10 ahead of
every other PREMIUM. Real close was $3.85, putting the stop **35.8%** below entry — over
the 30% guard — so the stop becomes `entry × 0.95` and the admitted position's R:R is
nothing like 11.27. **Systematic, not a one-off:** the guard fires precisely on the
widest-stop signals, which are exactly the ones a raw `(target−entry)/(entry−stop)`
flatters most, so the ranking preferentially promotes the candidates whose R:R is most
fictitious — deep-dip BOUNCE rows (Dist −80%+, RSI < 20), the falling-knife shape §12
already flagged. Filed as issue #3; the fix changes admission order, so per §11 it must be
judged on the `--realistic-sizing` arm with the >15d WR halt criterion.

### 23.4 A correlated-cluster warning that did not survive checking
Initial read of the manual longterm file was that its 8 GOLD rows would fill a fresh $100k
book with a §10-style correlated cohort. Checking the sectors overturned it: Energy,
Healthcare, Technology ×2, Finance ×2 (+ STEL, banking), Industrial — RSI 60–72, Dist +2%
to +18% (all inside the 25% tiebreak cap), volume 1.8–4.5×. Textbook Stage-2 continuation
across six sectors, i.e. the *opposite* of the Feb-2026 crypto-beta cluster the concern was
extrapolated from. The two genuinely weak rows (CAPR, LII) turned out self-limiting: both
end up on tight stops (CAPR's rewritten to 5% by the guard above, LII's 2.4% away), bounding
combined downside near ~$500 on a $100k book. **Verdict: no action** — blocking the file
would have discarded 8 good signals to avoid 2 bounded ones. Lesson: §10's correlated-cluster
finding is about *thematic concentration*, not about filling the cap; check the sector spread
before invoking it.

⚠ Note `QUALITY_SIZING` is **2.0× for GOLD *and* PREMIUM**, so the 5% base becomes 10% per
position — right at `max_single_position_pct`. Ten slots is therefore the **entire** book,
not half of it: a fresh account goes 0 → fully deployed in one morning, making one day's
prices its whole cost basis. That is designed behaviour and matches §11's realistic-sizing
arm, but the backtests spread entries over many days and never concentrate this way.

### Still open
- The Oracle migration (2026-08-02) still has no section of its own; §9 describes the
  retired EC2 deployment. Section numbering here does not reserve a slot for it.
- Issue #3 (R:R ranking) unfixed by design — needs a validated backtest arm.
- Issue #4 (chart NaN serialization) — fixed same day, `cb40ab1`, deployed.

## 24. `monitor` healthcheck was the SWING/VALIDATE bug a third time (2026-08-05)

Found while working the standing todo list, not from a page. Healthchecks.io reported
`monitor` down daily after 16:00 ET while supercronic logged every run as succeeded — the
same disagreement §15 exists to explain, but §15's fix (the `/$?` ping shape) was already
correctly in place here. This was a different bug wearing the same symptom.

**Root cause:** one UUID (`HC_UUID_MONITOR`) fed three differently-timed crontab lines —
a single 9:45 ping, a `*/15 10-15` block (10:00–15:45), and a single 16:00 ping — but the
Healthchecks schedule was `*/15 9-16 * * 1-5`, which expects a *uniform* 15-min cadence
across the whole 9:00–16:45 range. No 5-field cron expression can say "boundary hours get
one minute, middle hours get four," so the schedule was structurally unable to match
reality: it wanted phantom pings at 9:00/9:15/9:30 (before any line fires) and
16:15/16:30/16:45 (after the last line fires), and flipped down ~2h after every 16:00
ping, every single day.

This is §9's SWING/VALIDATE bug for the third time — "a shared UUID across genuinely
different times can't be expressed without either false alarms or a grace window loose
enough to miss a real outage" — just with three time-shapes sharing one UUID instead of
two. `HC_UUID_MONITOR` was the one name from §9's original list that was never audited
for this when the others were split.

**Fix:** same pattern as SWING/SWING_CLOSE and VALIDATE/VALIDATE_LEARN. Split into
`MONITOR_OPEN` (45 9 * * 1-5), `MONITOR` (retargeted to `*/15 10-15 * * 1-5` — the one
block that already was a clean single expression), and `MONITOR_CLOSE` (0 16 * * 1-5).
New UUIDs created via the Healthchecks API, added to the box's `.env` (not committed —
secrets convention), `docker/crontab` updated, committed (`f12494e`), and deployed via
`docker compose up -d --build scanner-cron` (env vars and the crontab are both baked into
the image, so a bare restart would not have picked either up). Verified inside the
running container, not just from the build log: `env | grep HC_UUID_MONITOR` shows all
three, and `grep HC_UUID_MONITOR /app/docker/crontab` shows each line pointing at its own
UUID. `tests/test_crontab_parity.py` has no hardcoded UUID names, so nothing there needed
updating.

**Lesson:** a fix applied to two instances of a bug does not imply the third instance was
checked. §9 split SWING and VALIDATE by name because those were the ones that had already
alarmed; MONITOR shared the identical structural flaw (three cron lines, one UUID) from
day one and simply hadn't been caught yet. When a bug shape is identified, grep for every
other instance of the shape, not just the ones already reported.

## 25. Production migrated off EC2 onto the Oracle box (2026-08-02)

Written 2026-08-05 to close the gap MEMORY.md has flagged since the cutover — §9 above
still describes the retired EC2 deployment in the present tense. This section documents
the box actually running production today, verified directly rather than recalled.

### What changed
Production moved from the AWS EC2 instance (§9, `i-015657f7d29bb673e`) to the **Oracle
Cloud VM already referenced — and explicitly called "unrelated" — throughout §9's own
text**: `82.70.210.194` (`il-jerusalem-1`, hostname `instance-20260615-1424`), which since
2026-06-15 has independently run the separate `daytrade` engine/web/IB-Gateway/Caddy
stack. That box now runs **both** systems side by side as two independent
`docker compose` projects:

```
NAME             STATUS         CONFIG FILES
daytrade         running(3)     /opt/daytrade/docker-compose.yml
stocksbreakout   running(6)     /home/ubuntu/stocksBreakout/compose.yaml
```

`stocksbreakout`'s six containers: `sb-scanner-cron`, `sb-api`, `sb-cloudflared`,
`sb-tailscale`, `sb-journal` (Trade Journal SPA), and — new since the cutover —
`sb-dashboard` (the Streamlit admin/scan UI, previously loopback-only via SSH tunnel, now
also public at `dashboard.gilhadas-stocks.com`, commit `6f631cd`). `deploy/README.md` and
`deploy/OPERATIONS.md` still describe the old three-container EC2 layout and its IP —
**both are stale and unrewritten**; treat this section and direct box inspection as
authoritative until they're updated.

**The EC2 box is stopped, not terminated** — `t3.medium`, `eu-central-1b`, confirmed via
`aws ec2 describe-instances`. Its `StateTransitionReason` shows a brief manual
start-then-stop on 2026-08-04 (09:27→09:30 GMT); cause not recorded. Its two CloudWatch
alarms (`stocksbreakout-instance-check-failed-reboot`,
`stocksbreakout-system-check-failed-recover`) are both sitting in `ALARM` state, which is
expected and harmless — their actions (`ec2:reboot`, `ec2:recover`) are no-ops on a
stopped instance, they do not start it — but it means the AWS console will show red for
this instance indefinitely, which reads as an active incident to anyone who doesn't know
the history. Worth an explicit disable or a note if that alarm noise ever gets confusing.
MEMORY.md's `project_server_deployment_cutover_jul2026` describes the T+7-soak-then-decide
plan but the follow-through (terminate, or downgrade the instance type, or keep as a cold
fallback) isn't recorded as done.

### Why Oracle, not just fixing EC2
Not stated in any commit message, so this is inference, not a documented decision: EC2 had
just come through three consecutive memory firefighting rounds (§15 OOM-killed cron jobs,
§16 concurrency, §17 resize to t3.medium) and the Oracle box was already paid for, already
running, and — per `free -h` on 2026-08-05 — sitting on 11 GiB RAM with under 2 GiB in use,
roughly 3× the headroom t3.medium ever had. If the actual reason was something else, it
isn't written down anywhere this session found.

### Architecture is a bigger change than "same containers, new host"
Oracle's VM is **aarch64**, EC2's was **x86_64** — this was a cross-architecture rebuild,
not a redeploy. Chain of build fixes, all in git:
- `eb83fcd` — snapshotted EC2's exact 133-package dependency set
  (`deploy/constraints-ec2-20260802.txt`) so the ARM rebuild wasn't *also* a 6-week
  dependency upgrade — two confounded variables at once otherwise.
- `d63347f` / `26f4264` — wired the constraints into the Dockerfile, then fixed it: the
  first pass fed the full constraint file to the `torch` install step, whose
  `--index-url` is scoped to `download.pytorch.org` and can't serve `fsspec` (a torch
  dependency) at all — `ResolutionImpossible`. Fixed by extracting just the torch line as
  an explicit target; the full constraint still applies to the normal-PyPI-index step.
- `5a1bd70` — macOS `tar` had been embedding `._*` AppleDouble sidecars (the
  `com.apple.provenance` xattr) into the transferred mobile web build; 40 files became
  105. `.dockerignore` covered `.DS_Store` but not `._*`; they would have been baked into
  the image and served by `StaticFiles`. Found only because the file count looked wrong.
- `c8d7140` — the scanner memory cap needed its own re-measurement rather than reusing
  x86's number, and the result reads as a warning about trusting "peak" under a tight
  cap (§18–§21's own standing lesson, reconfirmed here): the first ARM run at a 1500m cap
  measured 1231.8 MB peak; raising the cap to 2048m dropped the *measured* peak to
  1002.2 MB — the process wasn't using more under pressure, it was thrashing against the
  ceiling. Real aarch64 working set is within 1.1% of x86's (§21: 1013.6 MB). FinBERT
  residency is architecture-sensitive on its own (+218.7 MB vs x86, isolated
  independently across a 256× headline-batch range and flat at every size) but that delta
  doesn't propagate to whole-scan peak, because peak is a high-water mark, not a sum.
  Cap landed at 2048m — ~104% headroom over measured peak, still 17% of the box's 11 GiB
  so the cgroup killer still wins the race against the host killer.

### Access differs from the EC2 playbook — do not carry that guidance over
§9's EC2 access notes (Tailscale-only, zero-inbound security group, `stocksbreakout-key.pem`)
**do not apply here**. This session connected all day via plain
`ssh -i ~/.ssh/daytrade_oracle ubuntu@82.70.210.194` on the public IP — Oracle Cloud's
security list is not configured zero-inbound the way EC2's was. `sb-tailscale` is present
and running on the box regardless; unclear whether it's load-bearing for anything now that
direct SSH works, or a carried-over lifeline from the compose file. Two independent SSH
keys now exist for what is, from the shell's perspective, one machine:
`~/.ssh/daytrade_oracle` (used throughout this session) and possibly others provisioned
for the original daytrade deployment — didn't enumerate further, not this task's scope.

### Operational state, verified 2026-08-05
- **Tunnel**: `deploy/cloudflared/config.yml` on the box routes `gilhadas-stocks.com`,
  `api.gilhadas-stocks.com`, `journal.gilhadas-stocks.com`, and
  `dashboard.gilhadas-stocks.com` to their respective compose services over the internal
  Docker network. A cloudflared footgun is documented inline in `6f631cd`'s commit body:
  `route dns <name> <hostname>` can silently match the wrong tunnel via a loose prefix
  match against other tunnels in the account (`stocksbreakout-oracle` matched
  `stocksbreakout`) — always pass the full UUID, never the name.
- **Disk alerting exists and was missed by earlier "still open" notes** — §9 and §17 both
  listed "Docker log-size caps + disk alert" as outstanding. `deploy/disk-alert.sh` (host
  cron, hourly, `HC_UUID_DISK`) already covers the disk half — confirmed both by the
  script's existence and a live `up` status on Healthchecks (`disk-space`, last ping
  2026-08-05T10:00). Docker log-size caps specifically are still unconfirmed.
- **Swap**: 2 GiB `/swapfile`, created 2026-08-02 — smaller than EC2's `setup-swap.sh` 4 GiB,
  proportionate to Oracle's much larger base RAM.
- **Headroom**: 11 GiB RAM (1.6 GiB used), 45 GB disk (17 GB used, 37%) — both far looser
  than any constraint that drove §15–§21's tuning on EC2. Don't assume that tuning
  transfers; it was sized for a box with 4–5× less to work with.
- All Healthchecks monitors for the scanner schedule are green as of this write-up
  (confirmed while fixing §24) except transient "new" status on the just-created
  MONITOR_OPEN/MONITOR_CLOSE checks, expected until their first daily ping.

### Still open
- `deploy/README.md` and `deploy/OPERATIONS.md` describe the retired EC2 layout — need a
  rewrite for the Oracle box, the two-project coexistence with daytrade, and the six (not
  three) stocksBreakout containers.
- EC2's disposition (terminate / downsize / keep as cold fallback) — not decided in
  writing anywhere this session found.
- Whether `sb-tailscale` still does anything on Oracle, given direct SSH already works.
- Docker log-size caps, specifically — disk *space* is covered, log growth is not
  confirmed either way.
