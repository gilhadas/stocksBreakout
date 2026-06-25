# stocksBreakout Scanner — System Specification

**Version:** V13 (Tunnel patterns added May 2026)  
**Last Updated:** 2026-05-26  
**Author:** Gil Hadas

---

## 1. System Overview

**stocksBreakout** is a professional algorithmic stock breakout scanner for identifying high-conviction swing/intermediate-term trades. The system combines technical indicators, chart pattern recognition, market regime detection, and sentiment analysis to generate signals with high win rates and favorable risk-reward ratios.

### Key Characteristics
- **Signal Type:** Breakout + Bounce + Tunnel patterns
- **Timeframe:** Swing (5-30 days) to Intermediate (30-90 days)
- **Universe:** S&P 500, large-cap growth stocks
- **Holding Period:** 5-30 days typical; 30-45 days max
- **Risk Management:** ATR-based stops, trailing logic, regime-aware sizing
- **Current Performance:** +136.8% (5-year compound), Sharpe +0.88

---

## 2. Architecture

### 2.1 Core Components

```
breakout_scanner.py (entry point)
    ↓
scanner.py (core detection logic)
    ├→ pattern_recognition.py (chart patterns)
    ├→ indicators.py (technical indicators)
    ├→ regime_detector.py (market regime)
    ├→ finbert_sentiment.py (sentiment analysis)
    └→ exit_evaluator.py (exit logic)
```

### 2.2 Data Pipeline

```
Price Data (yfinance/IB)
    ↓
Normalize (lowercase OHLCV)
    ↓
Calculate Indicators (ATR, RSI, MACD, BB, etc.)
    ↓
Detect Patterns (16 chart + 11 candles + VCP + tunnel)
    ↓
Score Signals (V13: weighted system, 0-100)
    ↓
Filter by Regime (bull/bear/mixed gates)
    ↓
Generate Breakout/Bounce Signals
    ↓
Portfolio Management (position sizing, exits, rebalancing)
```

---

## 3. Signal Types

### 3.1 BREAKOUT (Primary Signal)

**Trigger:** Price breaks above resistance with supporting indicators

**Requirements:**
- Pattern bullish OR momentum strong
- Volume confirmation (>1.2× 20-day avg)
- RSI 50-70 (ideal breakout zone) OR SMA alignment bullish
- Quality PREMIUM+ (75+) or HIGH (65+) depending on regime

**Subtypes:**
1. **Tunnel Breakout** — Price breaks above parallel channel resistance (V13)
2. **Flag Breakout** — Price breaks above flag consolidation
3. **Triangle Breakout** — Price breaks above ascending/symmetrical triangle
4. **Support Breakout** — Price breaks above tested S/R level (with volume)

### 3.2 BOUNCE (Secondary Signal)

**Trigger:** Price bounces off support in uptrend

**Requirements:**
- Prior gain loss recovered (bounce = reversal of prior sell-off)
- SMA50 > SMA150 > SMA200 (uptrend confirmed)
- RSI 35-55 (oversold reset)
- Volume >= 1.0× avg (not critical)
- Quality GOLD+ (85+) only in bear/red-market regimes

**Subtypes:**
1. **Fibonacci Bounce** — Price bounces at fib retracement level (38.2%, 50%, 61.8%)
2. **SMA Bounce** — Price bounces off SMA50/150/200
3. **Support Bounce** — Price bounces off horizontal support

### 3.3 TUNNEL Patterns (Consolidation)

**Trigger:** Price in parallel channel, breaks above/below

**Requirements:**
- Two trendlines with <5% slope difference (parallelism)
- Min 3 touches per side within 1.5% tolerance
- Width 3-15% of current price
- Recent oscillation (both sides touched in last 10 bars)
- Volume spike on breakout (>1.2× avg)

**Signal Outcome:**
- Forming: Low confidence (50%), watch for breakout
- Breakout (Bull): BREAKOUT signal, bullish
- Breakdown (Bear): Signal veto, bearish

---

## 4. Scoring System (V13)

### 4.1 Quality Tiers

| Tier | Score | Meaning | Position Size |
|------|-------|---------|---|
| **GOLD** | 90+ | Maximum conviction | 10% capital |
| **PREMIUM** | 75-89 | High conviction | 7-8% capital |
| **HIGH** | 65-74 | Moderate-high | 5-6% capital |
| **STANDARD** | 50-64 | Acceptable | 3-4% capital |
| **LOW** | <50 | Rejected | 0% (skip) |

### 4.2 Scoring Components

| Component | Points | Condition |
|-----------|--------|-----------|
| Distance confirm (S/R break) | 24 | Price within 0.5 ATR above resistance |
| At key support | 24 | Price within 1 ATR above support |
| Candle quality | 19 | Close near candle high; body >40% range |
| Near 52-week high | 17 | Within 25% of 52w high |
| Volume confirm | 16 | Vol >1.2× 20-day avg |
| RS okay (vs sector/SPY) | 16 | Relative strength positive |
| Trend alignment | 15 | SMA50 > SMA150 > SMA200 |
| Pattern bullish | 14 | Chart pattern present (flag, triangle, etc.) |
| MACD bullish | 10 | MACD > signal line, histogram >0 |
| ADX strength | 9 | ADX > 25 (strong trend) |
| RSI zone | 8 | RSI 50-70 (ideal), or 30-50 (oversold recovery) |
| **Max Total** | **192** | (capped at 100 via normalization) |

### 4.3 Quality Multipliers (Regime-Aware)

Scores are multiplied based on market regime:

| Regime | Multiplier | Notes |
|--------|-----------|-------|
| BULL | 1.0× | Use signal normally |
| EXPANSION | 1.0× | Use signal normally |
| NORMAL | 0.95× | Slight discount |
| CHOPPY | 0.85× | 15% discount (noisy market) |
| RED_MARKET | 0.75× | 25% discount (weak conditions) |
| BEARISH | 0.60× | 40% discount (require GOLD only) |

---

## 5. Pattern Detection (V13)

### 5.1 Chart Patterns (16)

| Pattern | Type | Bullish | Bearish | Confidence |
|---------|------|---------|---------|-----------|
| Bull Flag | Continuation | ✓ | | 0.80-0.95 |
| Bear Flag | Continuation | | ✓ | 0.80-0.95 |
| Cup & Handle | Continuation | ✓ | | 0.75-0.90 |
| Inverse Cup & Handle | Continuation | | ✓ | 0.75-0.90 |
| Ascending Triangle | Consolidation | ✓ | | 0.70-0.85 |
| Descending Triangle | Consolidation | | ✓ | 0.70-0.85 |
| Symmetrical Triangle | Consolidation | None | None | 0.60-0.75 |
| Rectangle | Consolidation | ✓ | ✓ | 0.65-0.80 |
| **Tunnel (Channel)** | **Consolidation** | **✓** | **✓** | **0.55-0.85** |
| Falling Wedge | Reversal | ✓ | | 0.70-0.85 |
| Rising Wedge | Reversal | | ✓ | 0.70-0.85 |
| Rounding Bottom | Reversal | ✓ | | 0.70-0.85 |
| Inverse H&S | Reversal | ✓ | | 0.75-0.90 |
| Head & Shoulders | Reversal | | ✓ | 0.75-0.90 |
| Double Bottom | Reversal | ✓ | | 0.70-0.85 |
| Double Top | Reversal | | ✓ | 0.70-0.85 |

### 5.2 Candlestick Patterns (11)

Hammer, Inverted Hammer, Hanging Man, Bullish Engulfing, Bearish Engulfing, Bullish Harami, Bearish Harami, Morning Star, Evening Star, Bullish Doji, Bearish Doji

### 5.3 Volatility Patterns (1)

**VCP** (Volatility Contraction Pattern) — Minervini framework with quality scoring 0.0-1.0

### 5.4 Support/Resistance

- **Horizontal levels** — swing highs/lows clustered within 1.5% tolerance
- **Trendlines** — linear regression through last 4 swing points
- **Channels** — parallel support/resistance (checked for <20% slope difference)
- **Key levels** — within ±20% of current price only

---

## 6. Indicators & Calculations

### 6.1 Trend Indicators

| Indicator | Period | Calculation | Use |
|-----------|--------|-------------|-----|
| SMA | 50, 150, 200 | Simple moving average | Trend direction, alignment |
| EMA | 9, 20, 50 | Exponential moving average | Fast/intermediate trend |
| VWAP | Daily reset (intraday only) | Cumulative (TP×Vol)/Vol | Intraday support/resistance |

### 6.2 Momentum Indicators

| Indicator | Period | Calculation | Use |
|-----------|--------|-------------|-----|
| RSI | 14 | Wilder EMA (alpha=1/14) | Overbought/oversold, divergence |
| MACD | 12,26,9 | (EMA12 - EMA26), Signal=EMA9(MACD) | Momentum direction, crossovers |
| ADX | 14 | DM smoothed / ATR | Trend strength |
| Aroon | 25 | Highest/lowest N-bar position | Uptrend/downtrend power |
| StochRSI | 14 | (RSI-RSImin)/(RSImax-RSImin)×100 | RSI overbought/oversold |

### 6.3 Volatility Indicators

| Indicator | Period | Calculation | Use |
|-----------|--------|-------------|-----|
| ATR | 14 | Wilder EMA (com=13) | Stop-loss distance, trailing |
| Bollinger Bands | 20, 2σ | SMA ± 2×StdDev | Consolidation detection |
| BB Width | 20 | (Upper - Lower) / SMA × 100% | Squeeze detection |
| Volume Profile | 200 bars / 30 buckets | Price × volume distribution | VPOC, Value Area, HVN/LVN |

### 6.4 Composite Scores

**Momentum Score (0-100):**
- RSI in ideal zone (45-65): 30 pts
- MACD histogram >0 and accelerating: 35 pts
- ADX >25 (strong trend): 20 pts
- ROC >0 and ≤10%: 15 pts

**Breakout Conviction (0-100):**
- Close near candle high: 30 pts
- Volume spike (>2× avg): 30 pts
- Gap up (>2%): 20 pts
- Green bars (3 in row): 20 pts

---

## 7. Market Regime Detection

### 7.1 Regime Types

| Regime | Definition | SMA Order | Price vs SMA200 | Characteristic |
|--------|-----------|-----------|-----------------|---|
| **BULL** | Strong uptrend | 50>150>200 | Price > SMA200 | Higher lows, higher highs |
| **EXPANSION** | Early bull | 50>150>200 | Price > SMA200 | Acceleration phase |
| **NORMAL** | No clear trend | Mixed | Oscillating | Choppy, mean-revert bias |
| **CHOPPY** | Mean-reversion zone | Mixed | Oscillating | High chop index |
| **RED_MARKET** | SPY below SMA200 | Mixed | Price < SMA200 | Weak market (not sustained bear) |
| **BEARISH** | Sustained downtrend | 50<150<200 | Price < SMA200 | Lower highs, lower lows |

### 7.2 Parameter Scaling by Regime

| Parameter | BULL | NORMAL | RED_MARKET | BEARISH |
|-----------|------|--------|-----------|---------|
| Quality threshold | 65 | 75 | 75 | 85 |
| TP multiplier | 2.5× ATR | 2.0× | 1.5× | 1.5× |
| Max hold days | 30-45 | 20-30 | 10-20 | 7-14 |
| Position size | 100% | 75% | 50% | 50% |
| Stop buffer (ATR) | 1.5× | 2.0× | 2.0× | 2.5× |
| BOUNCE allowed | YES | GOLD only | NO (gate) | NO (gate) |

### 7.3 Regime Gates

**BBG15 Rule:** Block BOUNCE+RED_MARKET signals when SPY ≥15 consecutive days below SMA200 (confirmed bear market).

---

## 8. Exit Logic

### 8.1 Exit Modes

| Mode | Trigger | Action | Use Case |
|------|---------|--------|----------|
| **HOLD** | None | Stay in position | Normal operation |
| **TRAIL** | Trailing stop hit | Exit at trail price | Riding winners, protecting gains |
| **EXIT_PARTIAL** | TP hit (partial mode) | Sell 50%, trail 50% | Lock profits, stay for extension |
| **EXIT_FULL** | SL/TP/Time limit | Sell entire position | Cut losses, take profits, exit stale |

### 8.2 Default Configuration

```python
tp_mode = 'trail'                 # Trail on TP hit (vs hold/partial)
tp_mult = 2.5                     # TP = entry + (ATR × 2.5)
trail_enabled = True              # Trailing stop active
trail_mult = 2.0                  # Trail = entry + (ATR × 2.0)
trail_activation = 1.5 ATR        # Activate trail once gain > 1.5×ATR
max_hold_days = 30                # Calendar days
time_exit_on_stale = True         # Exit if max_hold reached and profit < TP×50%
regime_aware = True               # Adjust TP/hold based on regime
bear_reduce_tp_mult = 0.6         # In bear: TP = entry + (ATR × 2.5 × 0.6)
bear_max_hold_days = 10           # In bear: max hold 10 days
max_loss_pct = 3.0                # Hard stop at 3% account loss per position
max_stop_distance_pct = 30        # Stop can't be >30% away
```

### 8.3 Exit Decision Tree

```
Current bar:
├─ Stop-loss hit? → EXIT_FULL
├─ Target hit? → TRAIL or EXIT_PARTIAL (if enabled)
├─ Trailing stop hit? → EXIT_FULL or TRAIL
├─ Max hold days exceeded? → EXIT_FULL
├─ Regime worse than entry? → EXIT_PARTIAL or TRAIL
└─ None → HOLD
```

---

## 9. Portfolio Management

### 9.1 Position Sizing

```
Position Size = (base_allocation% × regime_multiplier × sentiment_conviction × quality_bonus)
```

**Example:**
- Base: 8% per position
- Regime (BULL): 1.0×
- Sentiment (strong bullish): 1.1×
- Quality (PREMIUM): 1.0×
- **Result:** 8.8% allocation

### 9.2 Constraints

| Constraint | Value | Reason |
|-----------|-------|--------|
| Max single position | 10% capital | Concentration risk limit |
| Max total equity usage | 100% | Maintain cash for opportunities |
| Max positions | 20 | Diversification / manageable |
| Cash reserve | 5-20% | Liquidity buffer |
| Max drawdown (stop-loss) | 30% | Risk tolerance guardrail |

### 9.3 Rebalancing

- **Daily:** After market close, evaluate exits and new entries
- **Weekly:** Check portfolio correlation, concentration
- **Monthly:** Assess regime change, adjust parameters
- **Quarterly:** Review sharpe/return vs benchmarks, tune thresholds

---

## 10. Configuration Parameters

### 10.1 Core Config (config.py)

```python
# Modes
MODES = {
    'longterm': {...},   # 30-90 day holds
    'swing': {...},      # 5-30 day holds
    'daytrade': {...},   # <1 day holds
    'scalping': {...},   # <5 min holds
}

# Regime config
REGIME_CONFIG = {
    'bull': {'quality_min': 65, 'tp_mult': 2.5, 'max_hold': 45, ...},
    'bear': {'quality_min': 85, 'tp_mult': 1.5, 'max_hold': 10, ...},
    ...
}

# Scoring thresholds
QUALITY_GOLD = 90
QUALITY_PREMIUM = 75
QUALITY_HIGH = 65
QUALITY_STANDARD = 50

# Gates
BOUNCE_BEAR_GATE = 15  # Days SPY below SMA200 before blocking BOUNCE
TREND_CONFIRM = {
    'enabled': False,    # Disable TREND_CONFIRM Path B (destroys edge)
    'enabled_paths': ['A'],  # Path A only (high conviction)
}
```

### 10.2 Scanner Defaults (scanner.py)

```python
MAX_CONCURRENT_REQUESTS = 5          # Async semaphore for IB
LOOKBACK_BARS = 200                  # Historical data window
CONSOLIDATION_MIN_DAYS = 5           # Min consolidation before breakout
BREAKOUT_MIN_VOL_RATIO = 1.2         # Min volume expansion
RSI_OVERSOLD = 35                    # RSI <35 = extreme oversold
RSI_IDEAL_LOW = 45                   # Ideal RSI breakout low
RSI_IDEAL_HIGH = 70                  # Ideal RSI breakout high
SMA_ALIGN_TOLERANCE = 0.02           # 2% tolerance for SMA ordering
```

---

## 11. Key Metrics & Definitions

| Metric | Definition | Target |
|--------|-----------|--------|
| **Win Rate** | % of trades with profit > 0 | 65-75% |
| **Avg Winner** | Average profit per winning trade | 2.0-2.5% |
| **Avg Loser** | Average loss per losing trade | 0.8-1.0% |
| **Profit Factor** | (Gross Profit) / (Gross Loss) | >1.5 |
| **Sharpe Ratio** | Return / Volatility | >0.8 (good), >1.5 (excellent) |
| **Max Drawdown** | Largest peak-to-trough decline | <30% |
| **Risk-Reward Ratio** | Expected return / Max risk | >1.5:1 |
| **Hold Duration** | Average days in trade | 10-15 days (swing mode) |
| **Signal Accuracy** | Quality score vs actual return correlation | >0.50 |

---

## 12. Data Requirements

### 12.1 Inputs

- **Price Data:** Open, High, Low, Close, Volume (OHLCV)
- **Timeframe:** Daily bars (intraday on request)
- **History:** Min 200 days (10 months); 2+ years preferred
- **Sources:** yfinance, Interactive Brokers, Alpaca
- **Frequency:** Update end-of-day; live during market hours

### 12.2 Outputs

- **Signals CSV:** Date, Symbol, Type, Quality, Entry, Stop, Target, Reason
- **Trades Log:** Entry, Exit, P&L %, Days Held, Win/Loss, Reason
- **Portfolio Snapshot:** Current positions, allocation, drawdown, sharpe
- **Performance Report:** Daily/Monthly/Yearly returns, metrics, regime breakdown

---

## 13. Comparison to Competitors

This specification enables side-by-side comparison with other breakout systems:

**Key Differentiators:**
1. **Dual signal types** (BREAKOUT + BOUNCE) — Most systems use breakout only
2. **Regime-aware gating** — Adapts to market condition (bear/bull/choppy)
3. **V13 Tunnel patterns** — Parallel channel detection (rare feature)
4. **Composite scoring** — Weighted multi-component system vs binary gates
5. **ATR-based exits** — Dynamic stops + trailing logic vs fixed percentages
6. **Sentiment integration** — FinBERT + Finnhub for quality boosting
7. **Fibonacci bounces** — Retracement-level scoring (high win rate on bounces)

**Benchmark Comparison Targets:**
- Win Rate: 65%+ (most: 50-60%)
- Sharpe: 0.8+ (most: 0.3-0.6)
- Risk-Reward: 1.5:1+ (most: 1.0-1.3:1)
- Max DD: <30% (most: 30-50%)
- 5-year return: +136%+ (most: +50-100%)

---

**End of System Specification**
