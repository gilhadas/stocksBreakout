---
name: fibonacci-bounce
description: Expert assistant for Fibonacci retracement bounce scoring in Python. Use when the user wants to add, debug, or explain Fibonacci-based bounce detection, swing analysis, and retracement-level scoring in any codebase. Adapts to the project's existing structure.
---

# Fibonacci Bounce Scoring Skill

You are a **Senior Technical Analyst and Quantitative Developer** specializing in Fibonacci retracement patterns and swing-based bounce analysis. Your job is to help build, modify, debug, and explain Fibonacci bounce detectors in any Python project.

## First: Orient to the Project

Before writing any code, check:
1. Does the project detect swing points (highs/lows)? How are they stored?
2. What column convention? (`close`/`open`/`high`/`low`/`volume` vs `Close`/`Open` etc.)
3. Is there a bounce/retracement detection system? How are confidence scores returned?
4. Is regime data (SMA50/150/200) available for Stage 2 confluence?
5. How is volume/RSI used for bounce confirmation?

> **In stocksBreakout:** Fibonacci bounce logic lives in `fib_retracement.py` AND in `quantkit/fib.py` (pip-installable).
> Columns are lowercase. Bounce scores are 0–100 with a component breakdown.
>
> **Using quantkit in any project:**
> ```bash
> pip install "git+https://github.com/gilhadas/stocksBreakout"
> ```
> ```python
> from quantkit.fib import detect_swing, fib_levels, nearest_fib_to_price, score_bounce
>
> swing = detect_swing(df, window=120)   # NOTE: default window is 120 bars, not 20
> if swing:
>     result = score_bounce(df, swing)
>     print(result['bounce_score'], result['nearest_fib'], result['sma_confluence'])
> ```
>
> **Real return contracts** (check these before writing code against them):
>
> ```text
> detect_swing(df)  ->  {'swing_high': float, 'swing_high_date': str,
>                        'swing_low': float,  'swing_low_date': str,
>                        'range': float}          # NO *_idx keys, NO recovery_high
>
> score_bounce(df, swing) -> {'current', 'swing_low', 'swing_high', 'swing_high_date',
>                             'retraced_pct', 'nearest_fib', 'nearest_fib_ratio',
>                             'nearest_fib_price', 'dist_to_fib_pct', 'sma_confluence',
>                             'stage2', 'rsi', 'rsi_reset', 'vol_ratio_3d',
>                             'vol_expansion', 'bounce_score'}
> ```
>
> `detect_swing` finds the **highest high in the window**, then the lowest low *before* it —
> it is not an `argrelextrema` local-extrema scan, and it returns `None` if `len(df) < 30`
> or if the range is flat/inverted. `sma_confluence` is a **tag string** (`'SMA50'` /
> `'SMA150'` / `'SMA200'` / `''`), not a distance.

---

## Universal Implementation Rules

- **Swing detection is foundational** — bad swings → bad targets. Always validate that detected highs/lows are local extrema.
- **Fibonacci levels are probabilities, not certainties** — a score of 75+ suggests confluence; <50 means wait.
- **Volume matters** — 3-day avg vol ≥ 1.2× 20-day avg raises bounce confidence by ~10 points.
- **RSI reset** — an oversold bounce (RSI 35–50) is stronger than a bounce from RSI >60.
- **SMA confluence** — price bouncing off SMA 50/150/200 adds +15–25 points. Measure distance: within 1.5% = full credit.
- All implementations use lowercase OHLCV columns and return `Optional[Dict]` or structured tuple.

---

## Fibonacci Retracement Levels

Classic retracement levels are entered at a **swing low** after a **swing high** (or vice versa for shorts):

| Level | Ratio | Use Case |
|-------|-------|----------|
| 23.6% | Most minor pull, often retested | First bounce zone (weak) |
| 38.2% | Golden zone, common bounce | Strong bounce, measured move |
| 50.0% | Psychological; Dow theory | Mid-swing bounce |
| 61.8% | Golden ratio; Gartley target | Strong support, reversal zone |
| 78.6% | Secondary retest | Late-game retracement |
| 88.6% | Near prior swing | Almost back to start |

**Measured move target** (upside after bounce):
```
Target = Swing High + (Swing High − Swing Low)
```

**Retracement range** (where the bounce can occur):
```
From swing high to swing low — where does price stabilize?
If price bounces at 50%, it's at 50% retracement level.
If price bounces at 61.8%, it's a "golden pocket" — higher conviction.
```

---

## Bounce Score Components (0–100)

| Component | Max | Condition | Notes |
|-----------|-----|-----------|-------|
| Classic fib level | +30 | Price within 2% of 38.2%, 50%, or 61.8% | Golden pocket (50% or 61.8%) adds +5 bonus |
| SMA confluence | +25 | SMA50/150/200 within 1.5% of bounce price | Stage 2 (SMA50 > 150 > 200) = full credit |
| Stage 2 | +15 | SMA50 > SMA150 > SMA200 AND price > SMA200 | Trend alignment boosts conviction |
| RSI reset | +15 | RSI 35–50 at bounce (oversold recovery) | RSI <35 = extreme; RSI 50–65 = milder |
| Volume expansion | +10 | 3-day avg vol ≥ 1.2× 20-day avg | >1.5× = full credit; <1.2× = 0 |
| Golden pocket | +5 | Bounce exactly at 50% or 61.8% | Bonus on top of classic level |

**Threshold interpretation:**
- **75+**: High conviction — multi-confluence, green light for position entry
- **60–74**: Moderate — acceptable setup, watch for breakout
- **45–59**: Marginal — interesting but needs additional confirmation
- **<45**: Weak — wait for better structure

---

## Swing Detection Algorithm

```python
def detect_swing(df, lookback=20, min_height=0.02):
    """
    Find the most recent completed swing high and low.
    
    Returns: dict with 'swing_high_idx', 'swing_low_idx', 'high_price', 'low_price', 'recovery_high'
    or None if no valid swing found.
    """
    try:
        if len(df) < lookback + 10:
            return None
        
        window = df.iloc[-(lookback + 10):].copy()
        
        # Find local highs and lows (order=3 for daily, order=5 for intraday)
        local_highs = argrelextrema(window['high'].values, np.greater, order=3)[0]
        local_lows  = argrelextrema(window['low'].values, np.less, order=3)[0]
        
        if len(local_highs) < 2 or len(local_lows) < 2:
            return None
        
        # Get the 2 most recent highs and lows
        recent_high_idx = local_highs[-1]
        recent_low_idx  = local_lows[-1]
        
        # Ensure there's a swing: a high followed by a low or vice versa
        if recent_high_idx > recent_low_idx:
            # High is more recent; need prior low
            prior_low_idx = local_lows[-2] if len(local_lows) > 1 else None
            if prior_low_idx is None or prior_low_idx > recent_high_idx:
                return None
            swing_high = window['high'].iloc[recent_high_idx]
            swing_low = window['low'].iloc[prior_low_idx]
        else:
            # Low is more recent; need prior high
            prior_high_idx = local_highs[-2] if len(local_highs) > 1 else None
            if prior_high_idx is None or prior_high_idx > recent_low_idx:
                return None
            swing_high = window['high'].iloc[prior_high_idx]
            swing_low = window['low'].iloc[recent_low_idx]
        
        # Validate swing height
        swing_height = (swing_high - swing_low) / swing_low
        if swing_height < min_height:
            return None
        
        # Find recovery high (highest price after swing low)
        recovery_high = window['high'].iloc[recent_low_idx:].max()
        
        return {
            'swing_high_idx': recent_high_idx,
            'swing_low_idx': recent_low_idx,
            'high_price': round(float(swing_high), 2),
            'low_price': round(float(swing_low), 2),
            'recovery_high': round(float(recovery_high), 2),
            'height_pct': round(swing_height * 100, 2),
        }
    
    except Exception:
        return None
```

---

## Bounce Score Calculation

```python
def score_bounce(df, swing, lookback_sma=50):
    """
    Score a bounce setup 0–100 based on confluence at recovery_high.
    
    swing: dict from detect_swing()
    lookback_sma: SMA periods (50, 150, 200)
    
    Returns: dict with 'bounce_score', 'nearest_fib', breakdown by component.
    """
    try:
        # Guard on what the SMAs actually need (200), not an arbitrary offset —
        # a 100-bar guard lets SMA200 come back NaN, and every NaN comparison is
        # False, so Stage 2 silently never scores.
        if not swing or len(df) < 200:
            return None

        current_price = df['close'].iloc[-1]
        swing_high = swing['high_price']
        swing_low = swing['low_price']
        height = swing_high - swing_low
        
        # Fib levels
        fib_levels = {
            '23.6%': swing_high - height * 0.236,
            '38.2%': swing_high - height * 0.382,
            '50.0%': swing_high - height * 0.500,
            '61.8%': swing_high - height * 0.618,
            '78.6%': swing_high - height * 0.786,
            '88.6%': swing_high - height * 0.886,
        }
        
        # Find nearest fib level
        distances = {name: abs(current_price - level) for name, level in fib_levels.items()}
        nearest_fib = min(distances, key=distances.get)
        level_distance_pct = (distances[nearest_fib] / current_price) * 100
        
        # Score components
        score = 0
        
        # 1. Classic fib level (+30 max)
        if level_distance_pct <= 2.0:
            fib_score = 30
            if nearest_fib in ['50.0%', '61.8%']:
                fib_score += 5  # Golden pocket bonus
            score += fib_score
        elif level_distance_pct <= 3.5:
            score += 20
        else:
            score += 0
        
        # 2. SMA confluence (+25 max)
        # Roll over the FULL series, then take the last value. Do NOT pre-slice to
        # exactly `period` rows — that leaves a single valid value and silently
        # produces NaN the moment the caller changes the window.
        sma50  = df['close'].rolling(50).mean().iloc[-1]  if len(df) >= 50  else np.nan
        sma150 = df['close'].rolling(150).mean().iloc[-1] if len(df) >= 150 else np.nan
        sma200 = df['close'].rolling(200).mean().iloc[-1] if len(df) >= 200 else np.nan

        # Measure confluence against the FIB LEVEL, not the current price — the
        # question is "does an SMA sit at the retracement level?", and it names the
        # SMA that qualifies so the caller can see which one it was.
        nearest_sma = ''
        sma_dist_pct = None
        for tag, val in (('SMA50', sma50), ('SMA150', sma150), ('SMA200', sma200)):
            if np.isnan(val):
                continue
            d = abs(val - fib_levels[nearest_fib]) / fib_levels[nearest_fib] * 100
            if sma_dist_pct is None or d < sma_dist_pct:
                sma_dist_pct, nearest_sma = d, tag

        if sma_dist_pct is not None and sma_dist_pct <= 1.5:
            score += 25
        elif sma_dist_pct is not None and sma_dist_pct <= 3.0:
            score += 15
        else:
            nearest_sma = ''        # no confluence — don't report a spurious tag

        # 3. Stage 2 check (+15 max) — NaN-safe: an unknown SMA is not a pass
        stage_2 = (
            not any(np.isnan(v) for v in (sma50, sma150, sma200))
            and sma50 > sma150 > sma200
            and current_price > sma200
        )
        if stage_2:
            score += 15

        # 4. RSI reset (+15 max) — Wilder's EMA smoothing, not a simple rolling mean
        rsi = calculate_rsi(df, period=14).iloc[-1]
        if 35 <= rsi <= 50:
            score += 15
        elif rsi < 35:
            score += 10          # extreme oversold: real, but knife-catch risk

        # 5. Volume expansion (+10 max)
        vol_avg_3d  = df['volume'].iloc[-3:].mean()
        vol_avg_20d = df['volume'].iloc[-20:].mean()
        vol_ratio   = vol_avg_3d / vol_avg_20d if vol_avg_20d else 0.0

        if vol_ratio >= 1.5:
            score += 10
        elif vol_ratio >= 1.2:
            score += 8

        return {
            'bounce_score': min(100, score),
            'nearest_fib': nearest_fib,
            'level_price': round(fib_levels[nearest_fib], 2),
            'distance_pct': round(level_distance_pct, 2),
            'sma_confluence': nearest_sma,          # '' when no SMA is in confluence
            'stage_2': stage_2,
            'rsi': round(rsi, 1),
            'vol_ratio': round(vol_ratio, 2),
            'breakout_target': round(swing_high + height, 2),
        }

    except Exception:
        return None
```

> **⚠ The `try/except Exception: return None` wrapper hides `NameError`.** The version of
> this function that shipped previously referenced an undefined `nearest_sma`, so *every*
> call returned `None` and the detector looked like "no bounces found" rather than a crash.
> When a detector silently finds nothing, temporarily remove the `except` and re-run before
> assuming the market simply has no setups. Requires `import numpy as np` and a
> `calculate_rsi` (see the `technical-indicators` skill — RSI must use Wilder's
> `ewm(alpha=1/period)`, not `rolling(period).mean()`).

---

## Template: Add a Bounce Modifier

```python
def add_bounce_modifier(df, swing, modifier_type='vcp'):
    """
    Apply volatility/pattern modifiers to a base bounce score.
    
    modifier_type: 'vcp' | 'bollinger_squeeze' | 'divergence' | 'trendline_confluence'
    
    Returns: score adjustment (−10 to +20).
    """
    try:
        adjustment = 0          # initialise BEFORE the branches — an unset name here
                                # raises NameError, which the `except` below swallows
                                # into a silent 0 for every modifier type.

        if modifier_type == 'vcp':
            # VCP reduces volume and range on each pullback
            # Strong VCP setup = +15 to bounce score
            adjustment = 0      # TODO: call the project's VCP detector
        elif modifier_type == 'bollinger_squeeze':
            # Narrow BB width at bounce = +10
            adjustment = 0
        elif modifier_type == 'divergence':
            # RSI lower low while price makes lower low = +10 (reversal signal)
            adjustment = 0
        elif modifier_type == 'trendline_confluence':
            # Price bouncing off major trendline = +5 to +15
            adjustment = 0

        return max(-10, min(20, adjustment))
    except Exception:
        return 0
```

---

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Swing detection finds phantom swings (too recent) | Ensure local extrema are ≥3–5 bars from current price |
| Fib levels too tight (all bounce setups score 50+) | Set tolerance to 2–3%, not 1%. Use tier-based scoring. |
| RSI oversold threshold too wide (15–65 all count) | Constrain to 35–50 for true reset; <35 is extreme |
| SMA confluence distance not normalized | Divide by current price, not SMA price |
| Volume expansion baseline wrong (uses 5-day, not 20-day) | Always 20-day rolling avg for "normal" volume |
| No check for prior swing structure | Ensure swing_high > swing_low before calling score |
| Score capped at 95, not 100 | Cap at 100 for max confluence; don't artificially suppress |

---

## Instructions

**Explain Fibonacci bounce**: describe the swing structure, the retracement levels, confluence (SMA/RSI/vol), and target.

**Add bounce detection**: ask what swing timeframe (daily/intraday), then produce a ready-to-paste detector + optional modifiers. Match the project's column convention.

**Debug a bounce**: ask for the symptom (not detecting valid bounces / false signals / wrong target). Walk through swing detection → fib level calc → confluence check.

**Tune thresholds**: propose a change (SMA tolerance, RSI range, vol ratio), explain the trade-off, and recommend backtesting on historical pullback data.

**Integrate with quantkit**: if the project uses `quantkit.fib`, show how to call `detect_swing()` and `score_bounce()` with custom modifiers wrapped on top.
