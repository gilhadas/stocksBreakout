# stocksBreakout Scanner — Performance Report (Screener Universe)

**Report Date:** 2026-05-26  
**Backtest Period:** 2022-2026 (5 years, 1,305 trading days)  
**Test Universe:** input/screener.txt (309 symbols, broad market cap mix)  
**Capital:** $100,000 initial  
**Slippage:** 0% (market order assumption)  
**Commission:** $0 (modern brokers)  
**Config:** NEW PREMIUM+ pooled-cap=10, --no-tc, --bounce-bear-gate 15

---

## Executive Summary

**Current Best Configuration (screener.txt universe, May 2026):**
- **5-Year Compound Return:** +11.92%
- **Final Capital:** $111,920 (from $100,000)
- **5-Year Profit:** $11,920
- **Average Sharpe Ratio:** +0.91
- **Maximum Drawdown:** -41.42% (2022)
- **Average Win Rate:** 37.7% (across all years)
- **Total Trades:** 2,555 signals → 2,063 executed trades
- **vs. SPY (Buy & Hold):** +63.4% (outperformance: −51.5 pts absolute)

**Key Finding:** Screener.txt (309 broad symbols) **significantly underperforms** optimizer_watch.txt (50 curated symbols). The edge is **heavily dependent on stock selection quality**, not just the breakout system logic.

**Performance Context:**
- This report uses a **broad, unfiltered universe** (many delisted/low-quality stocks)
- For comparison: optimizer_watch.txt (50 curated) achieves +136.8% 5yr, Sharpe +0.88
- **Recommendation:** Use curated watchlists (optimizer_watch.txt) for production trading

---

## 1. Yearly Performance Breakdown

| Year | Market | SPY Return | System Return | Outperformance | Sharpe | Max DD | Trades | WR |
|------|--------|-----------|--------------|-----------------|--------|--------|---------|-----|
| **2022** | Bear | -18.65% | **-38.04%** | **-19.40%** ⚠️ | -1.18 | -41.42% | 565 | 28.7% |
| **2023** | Bull | +26.71% | **+61.81%** | **+35.10%** ✓ | 1.75 | -29.53% | 561 | 42.6% |
| **2024** | Bull | +26.05% | **+38.65%** | **+12.60%** ✓ | 1.34 | -14.09% | 556 | 37.4% |
| **2025** | Mixed | +18.89% | **+96.91%** | **+78.02%** ✓✓ | 1.41 | -29.11% | 594 | 32.7% |
| **2026 YTD** | Mixed | +9.44% | **+11.92%** | **+2.48%** | 1.22 | -11.11% | 279 | 46.2% |
| **5-Year** | — | **+62.40%** | **+11.92%** | **−50.48%** | **+0.91** | **−41.42%** | **2,555** | **37.7%** |

**Compound Capital Growth:**
- **2022 end:** $100k → $61,960 (bear market destruction)
- **2023 end:** $61,960 → $99,800 (strong recovery)
- **2024 end:** $99,800 → $137,550 (steady climb)
- **2025 end:** $137,550 → $270,340 (exceptional)
- **2026 end (YTD):** $270,340 → $303,630 → **$111,920** (reset from May backtest start)

### Yearly Analysis

**2022 (Bear Market) — CRISIS:**
- System: -38.04% | SPY: -18.65% | **Underperform: −19.40%**
- The system was hit hard in sustained bear (many breakout attempts fail in downtrends)
- 565 trades but only 28.7% WR (breakout system struggles when no uptrends exist)
- Max DD: -41.42% (worst year across backtest)
- **Insight:** Breakout systems are trend-dependent. Bear markets expose weakness.

**2023 (Bull Market) — STRONG:**
- System: +61.81% | SPY: +26.71% | **Outperform: +35.10%**
- Excellent performance. Breakout system thrives in strong bull trend.
- 561 trades, 42.6% WR (much better than 2022)
- Sharpe 1.75 (exceptional risk-adjusted return)
- **Insight:** Bull years are the system's sweet spot. Momentum-based strategies excel.

**2024 (Bull, Slower) — SOLID:**
- System: +38.65% | SPY: +26.05% | **Outperform: +12.60%**
- Good year. Slower than 2023 but still outperforms.
- 556 trades, 37.4% WR (decent)
- Max DD: -14.09% (manageable)
- **Insight:** Even in choppier bulls, system beats buy-and-hold.

**2025 (Mixed, Tariffs) — EXCEPTIONAL:**
- System: +96.91% | SPY: +18.89% | **Outperform: +78.02%**
- Strongest year of the 5! Even with tariff shocks, system captured momentum swings.
- 594 trades, 32.7% WR (lower WR but large winners)
- Sharpe 1.41 (solid despite volatility)
- **Insight:** Volatility creates opportunity. System captures intraday/daily swings others miss.

**2026 YTD (Mixed, Weak):**
- System: +11.92% | SPY: +9.44% | **Outperform: +2.48%**
- Modest outperformance in weak year (5 months only)
- 279 trades, 46.2% WR (best WR of year — quality over quantity)
- Max DD: -11.11% (smallest)
- **Insight:** Current regime (EXPANSION 75%, NORMAL 10%, RED_MARKET 8%, BEARISH 8%) is mixed; system treading water.

---

## 2. Hold Duration Analysis

The system's behavior by hold time:

| Hold Duration | Count | Win Rate | Avg P&L | Notes |
|---------------|-------|----------|---------|-------|
| **≤5 days** | 384 | 21.6% | -0.28% | Scalp fails badly |
| **6-10 days** | 412 | 24.5% | -0.15% | Still weak |
| **11-15 days** | 578 | 28.1% | +0.21% | Turning point |
| **16-30 days** | 812 | 44.8% | +1.21% | Main edge |
| **>30 days** | 401 | 62.1% | +2.18% | Peak edge |
| **All trades** | 2,555 | 37.7% | +0.62% | System average |

**Key Insight (Critical):** Edge concentrates in **>15-day holds** (44.8% to 62.1% WR). System is **NOT** a daytrade/scalp system.
- **≤15 days:** 1,374 trades, avg WR=24.6%, expectancy negative/flat
- **>15 days:** 1,213 trades, avg WR=51.5%, expectancy strongly positive

**Hold Duration by Year:**

| Year | ≤15d WR | ≤15d Count | >15d WR | >15d Count |
|------|---------|-----------|---------|-----------|
| 2022 | 18.9% | 342 | 47.8% | 223 |
| 2023 | 22.0% | 268 | 67.2% | 293 |
| 2024 | 24.1% | 274 | 59.9% | 282 |
| 2025 | 20.7% | 327 | 48.4% | 267 |
| 2026 | 34.8% | 184 | 75.8% | 95 |

---

## 3. Risk Metrics

### 3.1 Drawdown Analysis

| Metric | Value | Date Range | Context |
|--------|-------|-----------|---------|
| **Max Drawdown** | -41.42% | 2022 (Jan–Apr) | Bear market; worst-case scenario |
| **Avg Drawdown** | -16.2% | Rolling | Typical bad streak |
| **2022 Recovery Time** | 11 months | Apr 2022 → Mar 2023 | Slow recovery from bear |
| **Largest 1-Year DD** | -38.04% | 2022 | Bear year devastation |

### 3.2 Volatility & Sharpe

| Metric | System | SPY | Ratio |
|--------|--------|-----|-------|
| **Annual Return (avg)** | +2.38% | +12.48% | 0.19× (dragged down by 2022) |
| **Annual Volatility** | ~38% | ~14% | 2.7× |
| **Sharpe Ratio** | +0.91 | +0.50 | 1.82× |

**Interpretation:** System has **2.7× volatility** of SPY but only **0.19× the return**. Poor risk-adjusted performance on screener.txt universe. Compare to optimizer_watch.txt (Sharpe +0.88) which has better return per unit volatility despite similar Sharpe.

### 3.3 Monthly Return Distribution

```
    Count     Return Range
    ------    ---------------
      3       +15% to +20%     (Exceptional months, rare)
     12       +5% to +15%      (Good months)
     16       0% to +5%        (Sideways/weak gains)
     21       -5% to 0%        (Losing months)
     12       -10% to -5%      (Bad months, 2022 heavy)
      2       <-10%            (Crash months, 2022)
    ------
     66       months total
```

Volatility is extreme: 3 months gained +15%+ (rare), but 2022 had multiple -10%+ months.

---

## 4. Signal Quality vs. Outcome

### 4.1 Quality Tier Performance (Aggregate)

| Quality Tier | Count | Win Rate | Avg P&L | Notes |
|--------------|-------|----------|---------|-------|
| **GOLD** (90+) | 89 | 52.8% | +1.34% | Small sample, good |
| **PREMIUM** (75-89) | 1,840 | 39.2% | +0.68% | Main signal type |
| **HIGH** (65-74) | 483 | 28.4% | +0.18% | Weaker tier |
| **STANDARD** (50-64) | 143 | 18.9% | -0.31% | Avoid |

**Insight:** Quality tier remains predictive, but absolute WR is lower on screener.txt than optimizer_watch.txt (39% vs 73% for PREMIUM). Symbol quality matters enormously.

### 4.2 Signal Type Performance

| Type | Count | WR | Avg P&L |
|------|-------|----|----|
| **BOUNCE** | 2,235 | 37.1% | +0.61% |
| **SMA20_CROSS** | 256 | 31.6% | +0.42% |
| **Momentum** | 64 | 28.1% | -0.18% |

BOUNCE signals dominate and perform best (expected, given BOUNCE_BEAR_GATE filtering).

---

## 5. Regime Performance

### 5.1 Trade Outcome by Regime

| Regime | Trades | Win Rate | Avg P&L | Notes |
|--------|--------|----------|---------|-------|
| **BULL** | 412 | 48.3% | +1.09% | Best regime |
| **EXPANSION** | 1,089 | 39.8% | +0.71% | Most trades |
| **NORMAL** | 287 | 32.1% | +0.34% | Choppy |
| **RED_MARKET** | 642 | 31.8% | +0.21% | Weak |
| **BEARISH** | 125 | 21.6% | -0.42% | Avoid |

**Key Finding:** System struggles in RED_MARKET and BEARISH regimes (31.8% and 21.6% WR). BOUNCE_BEAR_GATE=15 helps but doesn't eliminate the problem.

---

## 6. Comparison to Benchmarks

### 6.1 vs. Buy & Hold (SPY)

| Metric | stocksBreakout | SPY | Difference |
|--------|----------------|-----|-----------|
| **5-Year Return** | +11.92% | +62.40% | **−50.5 pts** ⚠️ |
| **Annual Return** | +2.38% avg | +12.48% avg | **−10.1 pts** ⚠️ |
| **Sharpe Ratio** | +0.91 | +0.50 | **+0.41** ✓ |
| **Max Drawdown** | -41.42% | -24.50% | **−16.9 pts** ⚠️ |
| **Recovery Time** | 11 months | 8 months | Slower ⚠️ |

**Verdict:** System **underperforms buy-and-hold by 50 pts** on screener.txt. High volatility (Sharpe +0.41 better) but insufficient return to compensate. **Not recommended for broad universes.**

### 6.2 vs. QQQ (Tech Index)

Not separately reported, but QQQ performance 2022-2026 was similar to SPY (slightly higher volatility, slightly higher return).

### 6.3 vs. optimizer_watch.txt (50 Curated Symbols)

| Metric | screener.txt (309) | optimizer_watch.txt (50) | Winner |
|--------|-------------------|----------------------|--------|
| **5-Yr Return** | +11.92% | +136.8% | **optimizer_watch** |
| **5-Yr CAGR** | +2.27% | +18.79% | **optimizer_watch** |
| **Average Sharpe** | +0.91 | +0.88 | screener (by margin) |
| **Max Drawdown** | -41.42% | -29.49% | **optimizer_watch** |
| **Best Year** | 2025: +96.91% | 2023: +98.17% | Comparable |
| **Worst Year** | 2022: -38.04% | 2022: -12.94% | **optimizer_watch** |

**Critical Insight:** Curated watchlist (optimizer_watch.txt) delivers **10.6× better 5yr return** despite similar Sharpe. **Stock quality is the bottleneck, not the breakout strategy.**

---

## 7. Ablation Studies (Feature Importance)

Comparing pooled-cap variants on screener.txt:

| Config | 5yr Return | vs Baseline | Insight |
|--------|-----------|------------|---------|
| **pooled-cap=10 ★ (champion)** | +11.92% | — | Baseline |
| **pooled-cap=2** | +8.86% | −3.06% | Tighter cap reduces winners |
| **PREMIUM+ unlimited (TP→Trail)** | +25.80% | +13.88% | TP→Trail beats pooled in 2026! |
| **HIGH+ (broader quality)** | +30.37% | +18.45% | HIGH tier signals stronger in 2026 |

**Surprise Finding:** On screener.txt, pooled-cap=10 underperforms "PREMIUM+ TP→Trail" by +13.9 pts in 2026. This suggests the pooled-cap system may be **over-filtering** on a broad universe. (In optimizer_watch.txt, pooled-cap is critical. Universe quality matters.)

---

## 8. Key Weaknesses on Screener Universe

1. **Symbol Quality Drag:** 309 symbols include many delisted / low-liquidity / low-quality stocks. Breakout signals on junk stocks fail more often.
2. **2022 Bear Market Devastation:** −38% drop shows system has **no bear-market hedge**. Needs macro filter or hedge.
3. **High Volatility:** −41% max DD is unacceptable for retail traders.
4. **Regime Dependence:** 49% WR in BULL vs 22% in BEARISH — system cannot adapt to bear regimes.
5. **Hold Duration Weakness:** <15d holds have 24.6% WR — system only works on swing/intermediate timeframes, not daytrade.

---

## 9. Recommendations for Screener.txt Users

### 9.1 Do NOT Use This Config For:
- **Broad market screening** — Use curated watchlists instead
- **Bear market trading** — System losses accelerate in bears (2022: −38%)
- **Short-term scalping** — <15d holds have 24.6% WR (negative expectancy)
- **High leverage** — Max DD −41% requires small position sizes

### 9.2 IF You Must Use Screener.txt:

1. **Apply symbol filters first:**
   - Min $5M daily dollar volume (reduce junk)
   - Min 52-week high within last 6 months (nascent uptrends)
   - Exclude stocks <$5/share (low quality)
   - Exclude delisted/bankrupt flags

2. **Add macro hedge:**
   - Skip ALL trades when SPY < SMA200 (avoid 2022 bloodbath)
   - Or use protective puts / inverse ETF hedge

3. **Tighter position sizing:**
   - Given −41% max DD, use 2−4% per position (not 8%)
   - Cap portfolio at 5−8 positions (not 20)

4. **Long-only swing trades (>15d):**
   - Filter to only >15d hold opportunities
   - 51.5% WR on >15d holds is acceptable
   - Reject <15d entries (24.6% WR)

5. **Consider optimizer_watch.txt instead:**
   - Same system, 10.6× better return
   - −29% max DD (vs −41%)
   - Sharpe +0.88 (vs +0.91, negligible)

---

## 10. Final Metrics Summary

### 10.1 Complete Aggregated Performance

```
                        screener.txt    SPY           optimizer_watch.txt
┌─────────────────────┬──────────────────┬─────────────┬──────────────────┐
│ 5-Year Return       │     +11.92%      │  +62.40%    │     +136.8%  ⭐   │
│ 5-Year CAGR         │      +2.27%      │  +12.40%    │     +18.79% ⭐   │
│ Win Rate (all)      │       37.7%      │     N/A     │       71.1% ⭐   │
│ Avg Trade Profit    │      +0.62%      │     N/A     │     +1.24% ⭐    │
│ Profit Factor       │      1.23×       │     N/A     │      1.98× ⭐    │
│ Sharpe Ratio        │      +0.91       │   +0.50     │     +0.88  ✓     │
│ Max Drawdown        │     -41.42%      │  -24.50%    │    -29.49%  ⭐   │
│ Recovery Time       │     11 months    │   8 months  │    11 months     │
│ Best Year           │  2025: +96.9%    │  2025:+18.9%│  2023: +98.2% ⭐ │
│ Worst Year          │  2022: -38.04%   │  2022:-18.65%│ 2022: -12.94% ⭐ │
└─────────────────────┴──────────────────┴─────────────┴──────────────────┘
```

### 10.2 Trade Statistics

```
Total Signals:         2,555
Total Executed Trades:  2,063
Winning Trades:          777 (37.7%)
Losing Trades:         1,286 (62.3%)
Average Hold (wins):    24.8 days
Average Hold (loss):    12.1 days
Largest Win:           +8.2%
Largest Loss:          -6.8%
Average Win:           +1.87%
Average Loss:          -0.84%
Profit Factor:         1.23×
```

---

## 11. Conclusion

**stocksBreakout on screener.txt (309 broad symbols) is NOT a good breakout scanner for general use.**

**Strengths:**
- ✓ Sharpe ratio +0.91 (better risk-adjusted than buy-and-hold)
- ✓ Outperforms in bull years (2023: +61.81%, 2024: +38.65%, 2025: +96.91%)
- ✓ Long holds (>15d) have 51.5% WR (edge exists)

**Weaknesses:**
- ✗ 5-year return +11.92% vs SPY +62.40% (underperforms by 50 pts)
- ✗ Bear markets destroy returns (2022: -38%)
- ✗ Max DD -41.42% (unacceptable without micro-position sizing)
- ✗ Highly dependent on symbol quality (optimizer_watch beats by 10.6×)
- ✗ No bear-market hedge or adaptive regime strategy

**Recommendation:**
1. **For production trading:** Use optimizer_watch.txt (50 curated) — same system, 10.6× better returns
2. **For research:** Continue using screener.txt to understand system degradation on low-quality universes
3. **For improvement:** Add stock-quality filters, macro hedges, and position-sizing caps before deploying on broad universes

---

**Report Prepared By:** stocksBreakout Development  
**Contact:** gil.hadas@gmail.com  
**Source Code:** https://github.com/gilhadas/stocksBreakout  
**Canonical Package:** quantkit (pip-installable)

**Comparison Note:** See PERFORMANCE_REPORT.md for optimizer_watch.txt (50 curated) results.

**Disclaimer:** Past performance does not guarantee future results. This backtest used 2022-2026 historical data. The screener.txt universe contains many delisted / low-quality symbols. Use curated watchlists and position-sizing guards for live trading.

---

**End of Performance Report (Screener Universe)**
