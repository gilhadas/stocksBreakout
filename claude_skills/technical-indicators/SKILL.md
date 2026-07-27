---
name: technical-indicators
description: Expert assistant for technical indicators in Python. Use when the user wants to add, modify, debug, or explain any technical indicator — ATR, RSI, MACD, Bollinger Bands, VWAP, ADX, Aroon, Stochastic RSI, Volume Profile, Minervini Template, or composite scores — in any codebase. Adapts to the project's existing structure.
---

# Technical Indicators Skill

You are a **Senior Quantitative Developer** specializing in vectorized technical analysis. Your job is to help build, modify, debug, and explain technical indicators in any Python project.

## First: Orient to the Project

Before writing any code, check:
1. Where do indicators live? (e.g. `indicators.py`, `ta/`, `features.py`)
2. Is there a master `calculate_all_indicators()` function? New indicators belong there.
3. What column convention? (`close`/`open`/`high`/`low`/`volume` vs `Close`/`Open` etc.)
4. Which libraries are in use? (`pandas`, `numpy`, `pandas-ta`, `TA-Lib`, custom)
5. Is there a single source of truth for indicator periods? (a config file or constants)

> **In stocksBreakout:** indicators live in `indicators.py` AND in `quantkit/indicators.py` (pip-installable package).
> Columns are lowercase. Master function is `calculate_all_indicators()`. Periods from `config.py`. All custom implementations — no external TA libraries.
>
> **Using quantkit in any project:**
> ```bash
> pip install "git+https://github.com/gilhadas/stocksBreakout"
> ```
> ```python
> from quantkit.indicators import (
>     calculate_atr, calculate_rsi, calculate_macd,
>     calculate_bollinger_bands, calculate_vwap, calculate_adx,
>     calculate_aroon, calculate_stochastic_rsi,
>     calculate_momentum_score, calculate_breakout_conviction,
>     calculate_minervini_template, compute_volume_profile,
>     detect_rsi_divergence, calculate_all_indicators,
> )
> ```
> All implementations below are the canonical reference used by `quantkit`.

---

## Universal Implementation Rules

- **Always vectorized** — pandas/numpy operations on entire Series. No `for`-loops over rows for price/indicator calculations.
- **Wilder smoothing** for ATR and RSI: `ewm(alpha=1/period, adjust=False)` — matches TradingView exactly. Never use simple rolling mean for these two.
- **Division by zero guard**: `.replace(0, 1e-10)` before dividing a Series.
- Every new indicator added to the master calculator function so the full pipeline picks it up.
- New DataFrame columns use `snake_case` names (e.g. `df['my_indicator']`).

---

## Indicator Reference

### Volatility

#### ATR — Average True Range
$$ATR = \text{Wilder EMA of True Range}$$
$$TR = \max(H-L,\; |H - C_{prev}|,\; |L - C_{prev}|)$$

```python
def calculate_atr(df, period=14):
    high_low   = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close  = np.abs(df['low']  - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()   # Wilder: alpha=1/n → com=n-1
```

**Outputs:** pd.Series of ATR values.
**TradingView match:** ✅ (`com=period-1` = Wilder smoothing)

---

### Volume

#### Volume Ratio
```python
vol_ma    = df['volume'].rolling(20).mean()
vol_ratio = df['volume'] / vol_ma   # >1.5 = expansion, >2.0 = spike
```

#### VWAP
$$VWAP = \frac{\sum (P_{typical} \times V)}{\sum V}, \quad P_{typical} = \frac{H+L+C}{3}$$

VWAP is **session-anchored**: it must reset at each session boundary, otherwise the
cumulative sums run forever and the line stops tracking price.

```python
def calculate_vwap(df, timeframe):
    df = df.copy()
    df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3

    if hasattr(df.index, 'date'):          # DatetimeIndex → reset per calendar day
        return (df.groupby(df.index.date)
                  .apply(lambda x: (x['typical_price'] * x['volume']).cumsum()
                                   / x['volume'].cumsum(), include_groups=False)
                  .reset_index(level=0, drop=True))
    # No date info (single session / continuous)
    return (df['typical_price'] * df['volume']).cumsum() / df['volume'].cumsum()
```

> ⚠ **The `hasattr(df.index, 'date')` guard does not mean "is intraday".** A
> `DatetimeIndex` *always* has `.date`, daily bars included — so on daily data every
> group holds exactly one row and `cumsum()/cumsum()` collapses to that bar's typical
> price. Daily "VWAP" from this function is just **(H+L+C)/3**, not a volume-weighted
> average at all (verified: `np.allclose(vwap, tp) is True` on 642 AAPL daily bars).
>
> That is dormant rather than wrong in stocksBreakout, because the caller guards it:
> `calculate_all_indicators` sets `df['vwap'] = np.nan` unless the timeframe contains
> `'min'` or `'hour'`, and the only mode using `trend_type='VWAP'` is `scalping`, which
> is intraday. **If you reuse this function in another project, gate it on the
> timeframe yourself — the guard inside it will not do it for you.**

#### Volume Divergence
Price increasing but recent volume < prior volume by >30% → warning signal.

---

### Trend Lines

#### SMA / EMA
```python
sma = df['close'].rolling(period).mean()
ema = df['close'].ewm(span=period, adjust=False).mean()
```

**Standard periods**: SMA/EMA 9, 20, 21, 50, 150, 200.
**TradingView match:** EMA uses `ewm(span=n, adjust=False)` ✅

---

### Bollinger Bands
$$Upper = SMA_{20} + 2\sigma, \quad Lower = SMA_{20} - 2\sigma$$
$$Width = \frac{Upper - Lower}{SMA} \times 100$$

```python
sma   = df['close'].rolling(20).mean()
std   = df['close'].rolling(20).std()
upper = sma + 2 * std
lower = sma - 2 * std
width = (upper - lower) / sma * 100
is_consolidating = width < width.rolling(20).mean() * 0.6   # BB squeeze
```

**BB Position (0–100):** where price sits within the band — ≥80 = bullish, ≤20 = bearish.

---

### Momentum Oscillators

#### RSI — Relative Strength Index
$$RSI = 100 - \left[\frac{100}{1 + \frac{\text{Avg Gain}}{\text{Avg Loss}}}\right]$$

```python
def calculate_rsi(df, period=14):
    delta    = df['close'].diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()   # Wilder EMA
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))
```

**TradingView match:** ✅ (Wilder EMA, `alpha=1/period`)
**Interpretation:** Oversold <30, overbought >70. Ideal breakout zone: 50–70.

#### Stochastic RSI
```python
rsi      = calculate_rsi(df, 14)
rsi_min  = rsi.rolling(14).min()
rsi_max  = rsi.rolling(14).max()
stoch    = (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, 1e-10) * 100
stoch_k  = stoch.rolling(3).mean()    # %K
stoch_d  = stoch_k.rolling(3).mean()  # %D signal
```

**Interpretation:** OB >80, OS <20. Uses RSI's range, not price's.

#### MACD
$$MACD = EMA_{12} - EMA_{26}$$
$$Signal = EMA_9(MACD)$$
$$Histogram = MACD - Signal$$

```python
ema_fast = df['close'].ewm(span=12, adjust=False).mean()
ema_slow = df['close'].ewm(span=26, adjust=False).mean()
macd     = ema_fast - ema_slow
signal   = macd.ewm(span=9, adjust=False).mean()
hist     = macd - signal
```

**TradingView match:** ✅

#### ADX — Average Directional Index (trend strength)
```python
plus_dm  = df['high'].diff()
minus_dm = -df['low'].diff()

# Wilder's directional-exclusivity rule: on any given bar only ONE of
# +DM / -DM may be non-zero — the larger move wins, the other is zeroed.
plus_dm  = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

atr      = calculate_atr(df)
plus_di  = 100 * plus_dm.rolling(14).mean()  / atr.replace(0, 1e-10)
minus_di = 100 * minus_dm.rolling(14).mean() / atr.replace(0, 1e-10)
dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10)
adx      = dx.rolling(14).mean()
```

> ⚠ **Do NOT use `.clip(lower=0)` on the raw diffs.** That lets +DM and −DM both be
> positive on an outside bar (high up *and* low down), inflating +DI and −DI together.
> Since `DX = |+DI − −DI| / (+DI + −DI)`, the numerator barely moves while the
> denominator grows — so ADX reads systematically **low** and the "strong trend" gate
> under-fires. Measured on AAPL 2025-01→2026-07 (363 bars): mean ADX 31.8 vs 33.3
> canonical, max divergence 10.1 pts, and the `ADX > 25` gate **disagrees on 8% of bars**.

> ⚠⚠ **This ADX is NOT TradingView's `ta.adx`, and the difference is large.**
> Wilder (and TradingView) smooth the DI values *and* the final ADX average with RMA
> — `ewm(alpha=1/14)`. The implementation above uses `rolling(14).mean()` (a simple
> moving average) for both. Measured on AAPL, 616 bars:
>
> | | this implementation | Wilder / TradingView |
> |---|---|---|
> | mean ADX | **34.8** | 24.7 |
> | bars reading > 25 | **75%** | 46% |
> | | reads *higher* on 85% of bars; mean |Δ| 11.3 pts, max 48.5 |
>
> So the textbook interpretation below is on the **wrong scale** for these values —
> ">25 = strong trend" selects three quarters of all bars here, not roughly half.
>
> **Do not "fix" this in stocksBreakout without re-optimising what consumes it.**
> The thresholds were tuned *against this implementation*: `scanner.py:378/383` gates
> `adx_trending` at `> 20` (V1) / `> 25` (V2), `SCORING_WEIGHTS['adx_trending'] = 5`,
> and the Momentum Score maps `(ADX−15)/25×20` — all fitted by Optuna walk-forward
> with these numbers as input, and every backtest baseline in CLAUDE.md §7–§13 was
> produced with them. Swapping in RMA smoothing would silently tighten the gate
> (ADX-component saturation drops 31% → 7% of bars) and invalidate those baselines.
> Treat implementation-and-threshold as one calibrated pair.

**Interpretation (textbook / Wilder scale):** >25 = strong trend, >40 = very strong.
Doesn't indicate direction. See the warning above before applying these cutoffs here.

#### Aroon (25)
```python
aroon_up   = df['high'].rolling(26).apply(lambda x: x.argmax() / 25 * 100, raw=True)
aroon_down = df['low'].rolling(26).apply(lambda x: x.argmin() / 25 * 100, raw=True)
aroon_osc  = aroon_up - aroon_down   # Osc >+50 = strong uptrend
```

#### Rate of Change (ROC)
```python
roc = ((df['close'] - df['close'].shift(10)) / df['close'].shift(10).replace(0, 1e-10)) * 100
```

---

### Composite Scores (0–100)

#### Momentum Score
Replaces 3 binary checks with a continuous signal:

| Component | Max | Condition |
|-----------|-----|-----------|
| RSI | 30 | Ideal 45–65: full 30pts; linear decay outside |
| MACD | 35 | Hist >0 and accelerating=35, >0 decelerating=20, recovering=10 |
| ADX | 20 | Scaled: (ADX−15)/(40−15)×20, clipped 0–20 |
| ROC | 15 | >0 and ≤10%=15, ≤20%=10, >20%=5 |

#### Breakout Conviction Score
Measures *how* the breakout happened:

| Component | Max | Condition |
|-----------|-----|-----------|
| Close position | 30 | (close−low)/(high−low)×30 — near high of bar |
| Volume surge | 30 | ≥2×avg=30, ≥1.5×=20, ≥1.2×=10 |
| Gap up | 20 | ≥2%=20, ≥1%=15, ≥0.5%=10 |
| Green streak | 20 | 3 green bars=20, 2=13, 1=7 |

---

### Volume Profile (VPOC / Value Area)

Bins the price range into N buckets, distributes each bar's volume across overlapping bins, then identifies:
- **VPOC** (Point of Control): bin with highest volume — strongest S/R
- **Value Area**: 70% of volume, expanding outward from VPOC
- **HVN** (High Volume Nodes): bins >1.5× median — price congestion zones
- **LVN** (Low Volume Nodes): bins <0.5× median — price moves fast through these

```python
# Vectorize: for each bar spread volume across bins its range overlaps
bin_edges   = np.linspace(price_min, price_max, num_bins + 1)
bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
# ... distribute volume per bar ...
vpoc = bin_centers[np.argmax(bin_volumes)]
```

---

### Minervini Stage 2 Template (8 Conditions)

| # | Condition |
|---|-----------|
| C1 | Price > SMA150 AND Price > SMA200 |
| C2 | SMA150 > SMA200 |
| C3 | SMA200 slope positive (today vs 20 bars ago) |
| C4 | SMA50 > SMA150 AND SMA50 > SMA200 |
| C5 | Price > SMA50 |
| C6 | Price ≥ 25% above 52-week low |
| C7 | Price within 25% of 52-week high |
| C8 | Stock up on the year (252-day return > 0) |

**Score 0–8.** Stage 2 = C1+C2+C3+C4+C5 all true, ideally score ≥7.

---

## RSI Divergence (Vectorized)

```python
# Bullish: price undercuts N-bar low but RSI holds above its N-bar low
roll_price_min = close.shift(1).rolling(lookback).min()
roll_rsi_min   = rsi.shift(1).rolling(lookback).min()
bullish = (close < roll_price_min) & (rsi > roll_rsi_min)

# Bearish: price exceeds N-bar high but RSI stays below its N-bar high
roll_price_max = close.shift(1).rolling(lookback).max()
roll_rsi_max   = rsi.shift(1).rolling(lookback).max()
bearish = (close > roll_price_max) & (rsi < roll_rsi_max)
```

`shift(1)` excludes the current bar from the lookback window.

---

## Template: Add a New Indicator

```python
def calculate_my_indicator(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    One-line description.

    Math: ...
    Range: 0–100 (or describe).
    TradingView equivalent: ...
    """
    # Vectorized — no row loops
    result = df['close'].rolling(period).apply(lambda x: ..., raw=True)
    return result.fillna(0)

# Then add to the master calculator:
df['My_Indicator'] = calculate_my_indicator(df)
```

---

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Row-level loop: `df.apply(lambda row: ..., axis=1)` | Use `np.where`, `.shift()`, `.rolling()`, `.ewm()` |
| SMA smoothing for ATR or RSI | ATR: `ewm(com=period-1, adjust=False)` · RSI: `ewm(alpha=1/period, adjust=False)` |
| Simple rolling mean for EMA | `ewm(span=period, adjust=False)` |
| Division by zero | `.replace(0, 1e-10)` before dividing |
| New indicator not in master calculator | Always add to the single master function |
| VWAP on daily bars without daily reset | Reset logic conditional on `hasattr(df.index, 'date')` |
| RSI divergence includes current bar | `shift(1)` before rolling min/max |

---

## TradingView Parity Cheatsheet

| Indicator | TradingView Pine | Python Equivalent | Parity |
|-----------|-----------------|-------------------|--------|
| RSI | `ta.rsi(close, 14)` | `ewm(alpha=1/14, adjust=False)` Wilder | ✅ exact |
| ATR | `ta.atr(14)` | `ewm(com=13, adjust=False)` Wilder | ✅ exact |
| EMA | `ta.ema(close, n)` | `ewm(span=n, adjust=False)` | ✅ exact |
| SMA | `ta.sma(close, n)` | `rolling(n).mean()` | ✅ exact |
| MACD | `ta.macd(close, 12, 26, 9)` | `ewm(span=12/26, adjust=False)` difference | ✅ exact |
| BB | `ta.bb(close, 20, 2)` | `rolling(20).mean() ± 2×rolling(20).std(ddof=0)` | ⚠ **needs `ddof=0`** |
| ADX | `ta.adx(14)` | `ewm(alpha=1/14)` on DI **and** ADX | ❌ **not equivalent here** |

**⚠ Bollinger Bands — `ddof` is the whole story.** pandas `.std()` defaults to
`ddof=1` (*sample* standard deviation); TradingView's `ta.stdev` uses the *population*
form, `ddof=0`. The ratio is `sqrt(20/19) = 1.0260`, so a default-pandas band is
**2.60% wider** than TradingView's — mean $0.34, max $0.91 on AAPL daily.

quantkit uses the pandas default, so its absolute band levels and BB Position do not
match TradingView. **The BB *squeeze* verdict is unaffected**: `is_consolidating`
compares width to its own 20-bar mean, and a constant 2.6% factor cancels in that
ratio — measured, `is_consolidating` disagrees on **0 of 642 bars**. Only reach for
`ddof=0` when you need band levels to line up with a chart.

**❌ ADX is not TradingView-equivalent** — different smoothing, ~10 points higher on
average. See the warning in the ADX section; do not change it without re-optimising
its thresholds.

---

## Instructions

**Explain an indicator**: state what it measures, the formula, typical range, and interpretation. Note any smoothing subtleties (Wilder vs SMA).

**Add to project**: read the existing indicator file first to match style, then write the function + master-calculator diff.

**Debug mismatch vs TradingView**: check smoothing method first (SMA vs Wilder EMA), then period alignment and `shift()` direction.

**Tune periods/thresholds**: propose the change, explain sensitivity trade-off, suggest backtesting.
