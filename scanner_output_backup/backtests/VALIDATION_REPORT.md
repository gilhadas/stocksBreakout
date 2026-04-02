# REGIME-ADAPTIVE CONFIG — VALIDATION REPORT
**Date:** 2026-03-17
**Config tested:** NEW Regime-Adaptive (BOUNCE PREMIUM+ blocked in BEARISH only)
**Baseline:** OLD V9-C (BOUNCE PREMIUM+ all regimes)
**Watchlist:** `input/optimizer_watch.txt` (50 symbols)
**Capital:** $100,000
**Period:** 2022–2025 (4 years)

---

## EXECUTIVE SUMMARY

| Verdict | Rating |
|---------|--------|
| **Overall** | PROCEED WITH CAUTION |
| Bear market protection | FAIL — both configs lose badly in 2022 |
| Bull market edge | PASS — NEW dominates in 2023 (+31pp) and 2024 (+22pp) |
| OOS 2025 (mixed) | CAUTION — V9-C wins by +4.4pp |
| Friction robustness | PASS — 1.7–3.1pp erosion per year, acceptable |
| Optuna regime sizing | FAIL — underperforms flat sizing in both walk-forward folds |
| BEARISH multiplier sensitivity | PASS — robust, 0.25 is appropriate |

---

## TEST 1 — Out-of-Sample 2025 (MIXED market)

**File:** `test1_2025_oos.txt`
**SPY actual:** +18.89%  |  Regime: 41% NORMAL, 35% EXPANSION, 18% RED_MARKET, 5% BEARISH

| Strategy | Return | WR% | Sharpe | MaxDD | vs SPY |
|----------|--------|-----|--------|-------|--------|
| SPY Buy & Hold | +18.89% | — | 1.00 | -18.76% | — |
| OLD V9-C PREMIUM+ TP→Trail | **+16.60%** | 47.9% | 0.84 | -14.62% | -2.29% |
| NEW Regime-Adaptive HIGH+ | +12.16% | 47.0% | 0.61 | -14.69% | -6.73% |
| NEW PREMIUM+ Fixed TP | +2.59% | 49.6% | 0.23 | -15.31% | -16.30% |

**Verdict: CAUTION**
NEW underperforms V9-C by 4.4pp in 2025. The NORMAL regime (41% of 2025) is where NEW breaks down: 55 signals, WR=28.8%, -7.61% return. NORMAL is a choppy sideways grind — breakout signals fail at higher rates. V9-C's coarser filter happens to perform better here because it doesn't restrict signals as aggressively.

---

## TEST 2 — Full 4-Year (2022–2025), No Friction

**File:** `test2_full_4year.txt`

| Year | Market | SPY | V9-C | NEW PREMIUM+ | NEW HIGH+ | NEW edge |
|------|--------|-----|------|-------------|----------|---------|
| 2022 | BEAR | -18.65% | -26.46% | -29.41% | -29.41% | **-2.95pp worse** |
| 2023 | BULL | +26.71% | +89.55% | +120.78% | +119.89% | **+31.23pp better** |
| 2024 | BULL | +26.05% | +16.24% | +35.05% | +38.12% | **+21.88pp better** |
| 2025 | MIXED | +18.89% | +16.60% | +11.65% | +12.16% | **-4.44pp worse** |

**Key observations:**
- **2023/2024 (bull):** NEW is dramatically better. The BOUNCE PREMIUM+ regime gate correctly captures high-quality signals while filtering BEARISH noise.
- **2022 (bear):** Both strategies lose heavily. NEW is ~3pp worse because it generates more trades (288 vs 206 signals) across regimes where win rates are low across the board.
- **2025 (mixed):** V9-C wins. NORMAL regime (41% of days) has low WR for NEW (28.8%) vs acceptable for V9-C.

**Signal count vs quality:**
- 2023: NEW 131 signals (vs 115 V9-C), but much higher quality → 60.7% WR vs 54.0%
- 2024: NEW 139 signals (vs 120 V9-C), same improvement → 46.6% WR vs 39.6%

---

## TEST 3 — Friction Modeling (0.10% slippage + $1/side commission)

**File:** `test3_friction.txt`
**Methodology:** Same simulation with realistic market friction

| Year | Strategy | No Friction | With Friction | Friction Cost |
|------|----------|-------------|---------------|---------------|
| 2022 | V9-C | -26.46% | -28.12% | **-1.66pp** |
| 2022 | NEW HIGH+ | -29.41% | -31.34% | -1.93pp |
| 2023 | V9-C | +89.55% | +86.67% | **-2.88pp** |
| 2023 | NEW HIGH+ | +119.89% | +116.83% | **-3.06pp** |
| 2024 | V9-C | +16.24% | +14.43% | **-1.81pp** |
| 2024 | NEW HIGH+ | +38.12% | +35.59% | **-2.53pp** |
| 2025 | V9-C | +16.60% | +14.87% | **-1.73pp** |
| 2025 | NEW HIGH+ | +12.16% | +10.30% | **-1.86pp** |

**Verdict: PASS**
Friction erodes 1.7–3.1pp per year. Higher in bull years (more trades, more exits) but proportionally small relative to returns. The NEW config trades ~10–20% more than V9-C, so slightly higher friction cost, but the edge remains intact in bull years.

---

## TEST 4 — Broader Watchlist (all.txt)

**File:** `test4_allsymbols.txt`
**Verdict: INCONCLUSIVE**
The all.txt watchlist contains many post-2022 IPOs (IBIT, RDDT, RBRK, CRWV, etc.) that had no price history before 2023. The test loaded only ~20–30 symbols for 2023/2024 — insufficient sample for reliable conclusions. The optimizer_watch.txt (50 symbols, established 2019+) remains the primary validation set.

---

## TEST 5 — Walk-Forward Optuna Regime Sizing

### 5a: Train 2022 → Validate 2023

**File:** `test5a_walkforward_2023.txt`
Best train Sharpe: **-0.209** (2022 bear year is extremely hard to optimize on)

| Strategy | Validate 2023 Return | Sharpe | vs Baseline |
|----------|---------------------|--------|-------------|
| V9-C baseline (no sizing) | +89.55% | 2.796 | — |
| V9-C + Optuna regime sizing | +74.84% | 2.469 | **-14.71pp** |

Optuna learned BEARISH=0.04x, RED_MARKET=0.49x from 2022's heavy losses. In 2023, RED_MARKET was actually excellent (+55% P&L share) — the model correctly destroyed value by under-sizing it.

### 5b: Train 2023 → Validate 2024

**File:** `test5b_walkforward_2024.txt`
Best train Sharpe: **3.151** (2023 was excellent training data)

| Strategy | Validate 2024 Return | Sharpe | vs Baseline |
|----------|---------------------|--------|-------------|
| V9-C baseline (no sizing) | +16.24% | 0.853 | — |
| V9-C + Optuna regime sizing | +12.97% | 0.825 | **-3.27pp** |

Optimal sizing from 2023: RED_MARKET=0.50x, NORMAL=0.63x. In 2024, NORMAL was 35% of days with decent WR — under-sizing it cost returns.

**Verdict: FAIL — DO NOT USE OPTUNA REGIME SIZING**
In both walk-forward folds, regime sizing underperforms flat (1.0x) sizing by 3–15pp. The regime categories (EXPANSION, NORMAL, RED_MARKET) have **non-stationary win rates** — what works in a bear year fails in a bull year. Applying regime sizing is over-fitting to historical regime P&L patterns.

---

## TEST 6 — BEARISH Multiplier Sensitivity

**File:** `test6_bearish_sensitivity.txt`
Methodology: V9-C (OLD config) with varying BEARISH position-size multiplier, scan once / simulate 6×

| Mult | 2022 Return | 2023 Return | 2024 Return | 2023 Sharpe |
|------|------------|------------|------------|-------------|
| 0.00 (block all) | -27.17% | **+103.08%** | **+17.65%** | 3.208 |
| 0.10 | -27.08% | +101.71% | +17.56% | 3.170 |
| **0.25 (current)** | -26.97% | +99.53% | +17.23% | 3.108 |
| 0.50 | -26.88% | +96.06% | +16.80% | 3.004 |
| 0.75 | -26.72% | +92.74% | +16.48% | 2.900 |
| 1.00 (no filter) | -26.46% | +89.55% | +16.24% | 2.796 |

**Verdict: PASS**
The parameter is robust and monotonic. Lower BEARISH multiplier consistently improves bull-year performance (+13.5pp in 2023 at 0.0 vs 1.0). Cost in bear 2022 is minimal (-0.71pp between 0.0 and 1.0).

The sweet spot is **0.0–0.25** — full blocking to 25% size. Current production value of 0.25 is appropriate: captures most of the benefit (-0.71pp vs full block) while allowing rare high-conviction BEARISH trades some exposure.

---

## REGIME DIAGNOSTICS (from Phase 1 investigation)

Based on 393 V9-C trades (2022–2024):

| Regime | Trades | WR% | Avg P&L% | P&L Share | Action |
|--------|--------|-----|----------|-----------|--------|
| EXPANSION | 111 | 41.4% | +3.28% | +43.2% | KEEP full size |
| NORMAL | 60 | 48.3% | +2.60% | +14.4% | KEEP full size |
| RED_MARKET | 195 | 43.1% | +1.99% | **+55.8%** | KEEP full size — largest P&L source |
| BEARISH | 27 | 22.2% | -3.28% | **-13.4%** | REDUCE/BLOCK |

**Critical insight:** RED_MARKET is the engine of returns (55.8% of total P&L). The initial "NEW config" mistake was blocking RED_MARKET alongside BEARISH — eliminating 55.8% of alpha. The fixed version blocks only BEARISH.

---

## YEAR-BY-YEAR REGIME CONTEXT

| Year | Dominant Regimes | Market | Strategy advantage |
|------|-----------------|--------|-------------------|
| 2022 | RED_MARKET 50%, EXPANSION 33% | Bear -18.65% | Neither — both lose |
| 2023 | EXPANSION 46%, NORMAL 27% | Bull +26.71% | NEW wins big (+31pp) |
| 2024 | EXPANSION 46%, NORMAL 35% | Bull +26.05% | NEW wins big (+22pp) |
| 2025 | NORMAL 41%, EXPANSION 35% | Mixed +18.89% | V9-C wins (+4.4pp) |

---

## FINAL VERDICTS BY QUESTION

| # | Question | Result | Rating |
|---|----------|--------|--------|
| 1 | Does edge survive OOS 2025? | NEW -4.4pp vs V9-C in mixed year | CAUTION |
| 2 | Does it hold in bear 2022? | Both lose -26% to -29%; NEW slightly worse | FAIL |
| 3 | How much does friction erode? | 1.7–3.1pp/year, edge survives | PASS |
| 4 | Does it hold on broader watchlist? | Too many delisted/IPO symbols, inconclusive | N/A |
| 5 | Is Optuna regime sizing consistent? | Underperforms in both folds by 3–15pp | FAIL |
| 6 | Is BEARISH=0.25 robust? | Monotonic improvement to 0.0; 0.25 is fine | PASS |
| 7 | Is there a clear deployable edge? | Yes — in bull/trending markets specifically | CONDITIONAL |

---

## DEPLOYMENT RECOMMENDATION

### PROCEED WITH CAUTION — Deploy NEW config with the following conditions:

**When to use NEW Regime-Adaptive:**
- Trending bull market (EXPANSION regime dominant, SPY in uptrend)
- Expected to significantly outperform V9-C (+20–30pp in strong bull years)
- Use PREMIUM+ signals with TP→Trail

**When V9-C is safer:**
- Choppy/sideways market (NORMAL regime >35% of days)
- High-uncertainty periods (post-crash recovery, early bear reversals)
- V9-C has slightly lower MaxDD in mixed conditions (-14.62% vs -14.69%)

**Do NOT deploy:**
- Optuna regime sizing (consistently over-fits)
- Fixed TP variant in any regime (worst risk-adjusted returns across all years)
- BEARISH multiplier > 0.25 (no upside, degrades bull-year performance)

### Configuration to deploy:
```python
# BOUNCE gate: allow PREMIUM+ in all regimes EXCEPT BEARISH
# BEARISH multiplier: 0.25 (or 0.0 for maximum filtering)
# Regime sizing: FLAT (1.0x all regimes) — no Optuna sizing
# Exit: TP→Trail (not Fixed TP)
```

---

## SUMMARY TABLE — ALL STRATEGIES, ALL YEARS (no friction)

| Strategy | 2022 | 2023 | 2024 | 2025 | 4yr CAGR* |
|----------|------|------|------|------|-----------|
| SPY | -18.65% | +26.71% | +26.05% | +18.89% | — |
| V9-C PREMIUM+ | -26.46% | +89.55% | +16.24% | +16.60% | — |
| NEW PREMIUM+ | -29.41% | +120.78% | +35.05% | +11.65% | — |
| **NEW HIGH+** | **-29.41%** | **+119.89%** | **+38.12%** | **+12.16%** | — |

*Compound 4-year returns are not directly comparable across strategies since each year starts at $100k. The NEW HIGH+ configuration is net superior across the 4-year period in pure return terms, with the caveat that 2022 and 2025 underperform V9-C.

---

*Report generated: 2026-03-17 | Tests: 6 | Symbols: 50 (optimizer_watch.txt) | Capital: $100,000*
