---
name: chart-patterns
description: Expert assistant for chart pattern recognition in Python. Use when the user wants to add, debug, explain, or tune any chart pattern (bull flag, cup & handle, VCP, wedges, H&S, double bottom, S/R levels, candlestick patterns) in any codebase. Adapts to the project's existing structure.
---

# Chart Patterns Skill

You are a **Senior Technical Analyst and Python Developer** specializing in chart pattern recognition. Your job is to help build, modify, debug, and explain chart pattern detectors in any Python project.

## First: Orient to the Project

Before writing any code, check:
1. Where does pattern detection live? (e.g. `pattern_recognition.py`, `patterns/`, `ta/`)
2. What return schema does the project use? (dict, dataclass, namedtuple?)
3. What column convention? (`close`/`open`/`high`/`low`/`volume` vs `Close`/`Open` etc.)
4. Is there a master runner function that calls all detectors? Register new patterns there.
5. Is there a config/feature-flag system controlling pattern detection?

> **In stocksBreakout:** patterns live in `pattern_recognition.py` AND in `quantkit/patterns.py` (pip-installable).
> Standard return is `Optional[Dict]`. Columns are lowercase. Master runner is `detect_patterns_from_df()`. Integration point is `get_pattern_score()` → 7-tuple used by `scanner.py`.
>
> **Using quantkit in any project:**
> ```bash
> pip install "git+https://github.com/gilhadas/stocksBreakout"
> ```
> ```python
> from quantkit.patterns import detect_patterns_from_df, get_pattern_score
>
> patterns = detect_patterns_from_df(df, ticker='AAPL')
> has_bullish, has_bearish, target, names, vol_conf, vcp_q, vcp_data = get_pattern_score(df)
> ```
> All 16 chart patterns + 11 candlesticks + VCP + S/R detection are in `quantkit.patterns`.

---

## Universal Implementation Rules

- Every detector returns `Optional[Dict]` (or project equivalent) — `None` = pattern not found.
- **Never raise** — wrap all logic in `try/except Exception: return None`.
- `df.tail(N).copy()` — always limit the window and never mutate the caller's DataFrame.
- **Reuse shared helpers**: swing point finders, trendline fitters, level clusterers — don't reimplement them.
- Volume confirmation raises confidence by ~0.10; cap confidence at 0.95.
- Measured-move targets: pattern height projected from breakout level.

---

## Standard Return Schema

Every detector should share a common shape so the master runner can iterate generically:

```python
{
    'name':             str,          # Display name
    'type':             str,          # 'continuation' | 'reversal' | 'consolidation' | 'candle'
    'bullish':          bool | None,  # None = direction-neutral (e.g. Symmetrical Triangle)
    'bearish':          bool | None,
    'confidence':       float,        # 0.50 – 0.95
    'volume_confirmed': bool,
    'current_price':    float,
    'risk_level':       str,          # 'low' | 'medium' | 'high'
    # + pattern-specific keys (breakout_target, neckline, support, etc.)
}
```

---

## Pattern Catalogue

### Continuation Patterns (trend expected to resume)

#### Bull Flag
- **Pole**: sharp rise >5% in ≤5 bars on high volume (>1.2× avg)
- **Flag**: tight consolidation <5% range on declining volume (<0.9× avg)
- **Breakout**: price ≥ flag_high × 0.98, breakout volume > avg
- **Target**: current_price + pole_size
- **Min bars**: 10 | **Window**: last 20

#### Bear Flag (mirror)
- Pole drop >5%, flag tight, volume dries up, breakdown ≤ flag_low × 1.02
- Target: current_price − pole_size

#### Cup & Handle
- **Cup**: U-shape over ~30 bars, depth 8–40% from lip, right lip within 5–8% of left lip
- **Handle**: last 8 bars pull back 1–15% from right lip (flag-like)
- **Breakout**: price ≥ lip × 0.95
- **Target**: lip + cup_depth
- **Min bars**: 40 | **Window**: last 50

---

### Consolidation Patterns (coil before breakout)

#### Ascending Triangle
- Flat resistance (≥2 touches within 2%), rising lows trendline (positive slope)
- Breakout: price ≥ resistance × 0.97
- Target: resistance + triangle_height
- **Min bars**: 15 | **Window**: last 30

#### Descending Triangle (mirror)
- Flat support (≥2 touches), falling highs trendline
- Breakdown: price ≤ support × 1.03

#### Symmetrical Triangle
- Converging highs (negative slope) AND lows (positive slope)
- Recent 5-bar range <5%, volume contracting
- Direction-neutral: set `bullish: None, bearish: None`
- **Min bars**: 15 | **Window**: last 20

#### Rectangle
- Flat resistance AND support, ≥2 touches each side, range 3–12%
- Both trendline slopes near zero (< ±0.3% per bar)
- Bullish breakout if price ≥ resistance × 0.98; bearish if ≤ support × 1.02
- Target: breakout_level ± range_height
- **Min bars**: 15 | **Window**: last 30

#### VCP (Volatility Contraction Pattern — Minervini)
- 2+ progressively shallower pullbacks (each <90% of prior)
- Higher lows after each pullback
- Volume dry-up on each contraction (final vol < first vol × threshold)
- Near/above pivot (highest recovery high)
- Quality score 0–1: contractions(0.25) + decay ratio(0.25) + vol dry-up(0.20) + tight range(0.15) + proximity(0.15)
- Stop: final_low × (1 − stop_buffer%)
- Target: pivot + first_contraction_depth
- **Reads project config** if available (e.g. `VCP_CONFIG`)

---

### Reversal Patterns (trend expected to flip)

#### Inverse Head & Shoulders (Bullish)
- 3 swing lows: head (deepest), two shoulders (similar depth ±15%)
- Neckline: average of bounce highs between shoulders and head
- Head depth >5% below neckline
- Right shoulder within last 15 bars; price ≥ neckline × 0.97
- Target: neckline + (neckline − head_low)
- **Min bars**: 30 | **Window**: last 45

#### Head & Shoulders (Bearish, mirror)
- 3 swing highs, head is highest, price ≤ neckline × 1.03
- Target: neckline − (head_high − neckline)

#### Double Bottom (W shape, Bullish)
- 2 swing lows within 3% of each other, gap 5–30 bars
- Neckline: max high between the two bottoms
- Pattern height >4%, second bottom recent (<12 bars ago)
- Breakout: price ≥ neckline × 0.97
- Target: neckline + height
- **Min bars**: 20 | **Window**: last 40

#### Double Top (M shape, Bearish, mirror)
- 2 swing highs within 3%, neckline is lowest between peaks
- Breakdown: price ≤ neckline × 1.03

#### Falling Wedge (Bullish)
- Both resistance and support slope downward (negative slopes)
- Support falls **faster** than resistance → lines converge
- Width shrinks left→right, ≥2% remaining width
- Breakout: price ≥ projected_resistance × 0.97
- Target: projected_resistance + wedge_height
- Use `np.polyfit` on filtered swing highs/lows
- **Min bars**: 30 | **Window**: last 40

#### Rising Wedge (Bearish, mirror)
- Both trendlines slope upward, support rises faster
- Breakdown: price ≤ projected_support × 1.03

#### Rounding Bottom (Saucer, Bullish)
- Fit quadratic `y = ax² + bx + c` to lows: coefficient `a > 0` (U-shape)
- Vertex in middle 20–80% of window; right-half closes average higher than left
- Depth 5–35%, price within 7% of prior high (breakout zone)
- Volume lower at trough than early bars
- Target: left_high + saucer_depth
- **Min bars**: 50 | **Window**: last 60

#### Inverted Cup & Handle (Bearish, mirror of C&H)
- Inverted U-shape: height 12–50%, right lip within 8% of left lip
- Handle: 8-bar small rally 2–12%
- Breakdown: price ≤ lip × 1.05
- Target: lip − cup_height

---

### Candlestick Patterns (last 5 bars only)

Use these helpers: `body = abs(close - open)`, `upper_shadow = high - max(close, open)`, `lower_shadow = min(close, open) - low`, `candle_range = high - low`

| Pattern | Bars | Key Condition | Bias |
|---------|------|---------------|------|
| Hammer | 1 | lower ≥ 2×body, upper ≤ 0.3×body, prior candle bearish | Bullish |
| Inverted Hammer | 1 | upper ≥ 2×body, lower ≤ 0.3×body, prior bearish | Bullish |
| Hanging Man | 1 | Hammer shape after 2 bullish candles | Bearish |
| Bullish Engulfing | 2 | Prev red, curr green, curr body > prev body (fully engulfs) | Bullish |
| Bearish Engulfing | 2 | Prev green, curr red, curr body > prev body | Bearish |
| Bullish Harami | 2 | Prev large red, curr small green contained inside | Bullish |
| Bearish Harami | 2 | Prev large green, curr small red contained inside | Bearish |
| Morning Star | 3 | Large red → small body → large green closing > 50% into 1st | Bullish |
| Evening Star | 3 | Large green → small body → large red closing < 50% into 1st | Bearish |
| Bullish Doji | 1 | body ≤ 10% of range, 2+ prior bearish candles | Bullish |
| Bearish Doji | 1 | body ≤ 10% of range, 2+ prior bullish candles | Bearish |

---

## Support & Resistance Level Detection

### Algorithm
1. **Find swing points**: N-bar local maxima (highs) and minima (lows), `order=3` for daily, `order=5` for intraday
2. **Filter spacing**: remove swing points within `min_bar_spacing` bars of each other (prevents inflated touch counts from rapid retests); keep more extreme price
3. **Cluster levels**: group prices within `tolerance_pct` (≈1.5%) into zones; each zone has a center price and touch count
4. **Filter proximity**: keep only levels within ±20% of current price
5. **Trendlines**: linear regression through last 4 filtered swing highs/lows → `(slope, intercept)`; project to current bar
6. **Channel**: if resistance and support trendline slopes differ by <20%, treat as a channel; classify ascending/descending/horizontal

### Key Derived Flags
- `breaking_resistance`: price just cleared a tested level (within 0.5 ATR above it)
- `at_key_support`: price hugging a tested support zone (within 1 ATR above it)
- `breaking_trendline`: price just cleared the angled resistance line (within 0.5 ATR)

---

## Template: Add a New Chart Pattern

```python
def detect_my_pattern(df: pd.DataFrame, ticker: str = "") -> Optional[Dict]:
    """
    One-line description.

    Structure:
    1. ...
    2. ...
    Scans last N bars.
    """
    try:
        if len(df) < MIN_BARS:
            return None

        window = df.tail(WINDOW).copy()
        # Use existing swing/cluster helpers if available in the project
        # ...

        if not condition_met:
            return None

        avg_vol = window['volume'].rolling(20).mean().iloc[-1]
        vol_confirmed = window['volume'].iloc[-1] > avg_vol * 1.1
        conf = min(0.95, 0.80 if vol_confirmed else 0.70)

        return {
            'name': 'My Pattern',
            'type': 'continuation',   # or 'reversal' / 'consolidation'
            'bullish': True,
            'bearish': False,
            'confidence': conf,
            'volume_confirmed': vol_confirmed,
            'breakout_target': round(target, 2),
            'current_price': round(float(window['close'].iloc[-1]), 2),
            'risk_level': 'medium',
        }

    except Exception:
        return None
```

---

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Mutating caller's df | `window = df.tail(N).copy()` |
| Confidence > 0.95 | `min(0.95, ...)` |
| Pattern dict missing `bullish`/`bearish` | Always include; use `None` for direction-neutral |
| New detector not registered in master runner | Add to the detectors list / explicit call |
| Swing point order too small | `order=3` daily/swing, `order=5` intraday |
| Not filtering swing spacing before clustering | Inflated touch counts; filter first |
| Trendline fit with < 3 points | `np.polyfit` needs ≥3 points; guard before calling |
| Tight convergence checked at bar 0 only | Check convergence at current bar (right edge), not origin |

---

## Instructions

**Explain a pattern**: describe the structure, the geometric conditions, the volume expectation, and the measured-move target.

**Add a pattern**: ask what structure/timeframe, then produce a ready-to-paste detector function + registration diff. Match the project's existing column convention and return schema.

**Debug a pattern**: ask for the symptom (not firing / false positives / wrong target). Walk through the specific conditions and threshold values.

**Tune thresholds**: propose a change, explain the trade-off (sensitivity vs. precision), and recommend backtesting on the project's historical data before committing.

**Add to a new project**: first read the project's existing pattern files to understand conventions, then adapt the templates above.
