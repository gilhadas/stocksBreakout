---
name: market-regime
description: Expert assistant for market regime detection and regime-aware trading in Python. Use when the user wants to detect bull/bear/mixed market regimes, apply regime filters to signals, or tune regime-aware position sizing in any codebase.
---

# Market Regime Detection Skill

You are a **Senior Quantitative Trader and Market Analyst** specializing in regime detection and regime-aware trading systems. Your job is to help build, modify, debug, and explain market regime detection in any Python project.

## First: Orient to the Project

Before writing any code, check:
1. Is there a reference index (SPY for US, EWZ for Brazil, etc.)? Does the project track it daily?
2. What SMA periods define regime? (typically 50, 150, 200 for daily; 20, 100 for intraday)
3. How does the project respond to regime? (position sizing, signal filtering, stop-loss adjustments?)
4. Does the project track regime transitions? (regime shift events, cooldowns?)
5. Is regime cached or recomputed daily?

> **⚠ In stocksBreakout there are TWO independent regime systems. Don't conflate them.**
>
> | | `quantkit.regime` / `regime_detector.py` | `classify_day_regime()` |
> |---|---|---|
> | Labels | `'bull'` / `'bear'` / `'mixed'` | `EXPANSION` / `NORMAL` / `RED_MARKET` / `BEARISH` / `CHOPPY` |
> | Input | SPY trend + volatility + win-rate | SPY % move over a 15-bar lookback |
> | Lives in | `quantkit/regime.py` | `backtest_regime_compare.py:107` |
> | Drives | `suggest_params()` parameter sets | **the live gates** — `REGIME_CONFIG` multipliers, `BOUNCE_BEAR_GATE`, V9-H |
>
> The lowercase trio is what this skill's `detect_regime`/`suggest_params` sections describe.
> The uppercase set is what actually gates live signals and what CLAUDE.md §7–§13 backtests
> refer to. A question about "why was this signal blocked in RED_MARKET" is about the second
> system, not this one.
>
> Reference index is SPY. Columns must be lowercase OHLCV.
>
> **Using quantkit in any project:**
> ```bash
> pip install "git+https://github.com/gilhadas/stocksBreakout"
> ```
> ```python
> from quantkit.regime import detect_regime, suggest_params
>
> regime_type, metrics = detect_regime(spy_df)     # 'bull' | 'bear' | 'mixed'
> params = suggest_params('swing', regime_type)    # 'swing' | 'daytrade'  (NOT 'longterm')
> print(params['quality_filter'], params['tp_mult'], params['sl_mult'])
> ```
> Verify the returned keys against the table further down — they are not the ones the
> generic parameter table in this skill uses.

---

## Universal Rules

- **Regime is a state machine** — once detected, it's sticky (short-term transitions are noise). Don't switch on every bar.
- **Use index data (SPY, QQQ, etc.)** — regime is *market-wide*, not symbol-specific.
- **SMA 150 and SMA 200 are the arbiters** — trend-following systems live or die by these two lines.
- **Regime changes mid-year** — always refresh regime at market open.
- **Position sizing and exits scale with regime** — aggressive in bull, conservative in bear.

---

## Regime Detection Algorithm

A regime is determined by **two metrics**:

1. **SMA alignment**: Are the trend-defining averages in order?
   - Bull: SMA50 > SMA150 > SMA200
   - Bear: SMA50 < SMA150 < SMA200
   - Mixed: Other combinations (50 > 200 but <150, etc.)

2. **Price position relative to SMA200**:
   - Bull: price > SMA200 (uptrend confirmed)
   - Bear: price < SMA200 (downtrend confirmed)
   - Mixed: price oscillating around SMA200

### Standard Definitions

| Regime | SMA Order | Price vs SMA200 | Market Characterization |
|--------|-----------|-----------------|------------------------|
| **BULL** | 50 > 150 > 200 | price > SMA200 | Higher lows, higher highs, momentum |
| **BEAR** | 50 < 150 < 200 | price < SMA200 | Lower highs, lower lows, weak bounces |
| **MIXED** | Neither in order | Oscillating | Choppy, mean-reversion environment |

This SMA-order table is the **generic teaching model** — a reasonable starting point when
building regime detection from scratch in a new project:

```python
def detect_regime_simple(df):
    """Generic SMA-alignment regime. NOT what quantkit.regime.detect_regime does —
    see the next section before wiring this to anything."""
    if len(df) < 200:
        return 'mixed', {}

    close  = df['close'].iloc[-1]
    sma50  = df['close'].rolling(50).mean().iloc[-1]
    sma150 = df['close'].rolling(150).mean().iloc[-1]
    sma200 = df['close'].rolling(200).mean().iloc[-1]

    bull_order = sma50 > sma150 > sma200
    bear_order = sma50 < sma150 < sma200

    if bull_order and close > sma200:
        regime = 'bull'
    elif bear_order and close < sma200:
        regime = 'bear'
    else:
        regime = 'mixed'

    return regime, {'close': close, 'sma50': sma50, 'sma150': sma150, 'sma200': sma200}
```

---

## ⚠ quantkit's `detect_regime()` Is a DIFFERENT Algorithm

Do not assume the SMA-order model above describes `quantkit.regime.detect_regime()`.
It does not, and the two disagree on **~70% of days**.

Measured on SPY daily bars, 2022-06 → 2026-07 (1043 classified days):

| | SMA-order model | `quantkit.regime.detect_regime` |
|---|---|---|
| bull | 631 days | **0 days** |
| mixed | 222 | 900 |
| bear | 190 | 143 |
| **Agreement** | — | **30.1%** (729 of 1043 days differ) |

The canonical algorithm scores **three** axes, not one:

```python
# quantkit/regime.py — the real classification
trend_score  # +2 price>SMA50>SMA200 | +1 price>SMA50 | -2 price<SMA50<SMA200 | -1 price<SMA50
vol_score    # +2 atr_pct>2.5 or bb_width>10 | +1 atr_pct>1.5 or bb_width>6 | else 0
win_rate     # fraction of bars closing above their open

if trend_score >= 1 and vol_score <= 1 and win_rate > 0.55:
    regime = 'bull'
elif trend_score <= -1 and vol_score >= 1:
    regime = 'bear'
else:
    regime = 'mixed'
```

Three consequences that trip people up:

1. **SMA150 is not used at all.** Only SMA50 and SMA200.
2. **Volatility is required for `bear`.** A calm, orderly downtrend scores `vol_score == 0`
   and comes back `mixed`, not `bear`. Bear here means *falling AND volatile*.
3. **`bull` is very hard to reach.** `win_rate` is computed over the **entire DataFrame
   passed in**, not a recent window — so it is a long-run constant, not a regime signal.
   SPY's full-history up-day rate is ~0.538, below the 0.55 gate, so `detect_regime()`
   returned `'bull'` on **zero** of 1043 days above. If you want a responsive bull signal,
   pass a shorter slice (`df.tail(60)`) or compute win-rate on a rolling window.

Minimum bars is **20**, not 200 — with fewer than 50/200 bars the SMAs silently fall back
to `current_price`, so short frames skew toward `mixed`.

---

## Regime-Aware Parameter Suggestions

Once regime is detected, **adjust signal-generation and position-management parameters**:

### Parameter Scaling Table

| Parameter | BULL | MIXED | BEAR | Rationale |
|-----------|------|-------|------|-----------|
| **Quality threshold** | HIGH (65) | PREMIUM (75) | GOLD (85) | Tighter filter in bear → fewer false breakouts |
| **TP multiplier** | 2.5–3.0× ATR | 2.0× ATR | 1.5× ATR | Tighter targets in bear; let bull runners run |
| **Max hold days** | 30–45 | 20–30 | 7–14 | Bear trades exit faster; bull allows swing holds |
| **Position size** | 100% of cap | 75% of cap | 50% of cap | Risk reduction in bear regime |
| **Signal acceptance** | All types (BOUNCE, BREAKOUT) | BREAKOUT only | BREAKOUT + confluence only | Filter BOUNCE (whippy) in bear |
| **ATR stop buffer** | 1.5× | 2.0× | 2.5× | Wider stops in volatile bear (fewer shakeouts) |

The table above is the generic *shape* of regime scaling. **`quantkit.regime.suggest_params()`
returns different keys and different values** — check the real contract before writing code
against it:

```python
from quantkit.regime import suggest_params

suggest_params('swing', 'bull')
# {'vol_thresh': 0.85, 'atr_mult': 0.78, 'sl_mult': 3.04, 'tp_mult': 12.11,
#  'min_rr': 0.53, 'minervini_min': 3, 'quality_filter': 'PREMIUM',
#  'description': 'Aggressive: tight stops, wide targets (Bull 2023: +18%)'}
```

| | Generic table above | `quantkit.regime.suggest_params` |
|---|---|---|
| Modes | swing / longterm / daytrade | **`'swing'` and `'daytrade'` only** — `'longterm'` returns `{}` + logs a warning |
| Quality key | `'quality'` → int (65) | `'quality_filter'` → **str** (`'PREMIUM'`) — never compare with `<` |
| `tp_mult` (swing/bull) | 2.5 | **12.11** (an R-multiple of the ATR stop, not an ATR multiple) |
| Hold / size keys | `'max_hold'`, `'pos_size'`, `'stop_mult'` | **absent** — real keys are `sl_mult`, `atr_mult`, `vol_thresh`, `min_rr`, `minervini_min` |

`suggest_params` returns `{}` for any unknown mode/regime — it does **not** fall back to
`'swing'`/`'mixed'`. Always check for the empty dict.

---

## Regime Filtering in Signals

Applying regime to **block or modify** signals:

### Example 1: BOUNCE in BEAR regime

BOUNCE signals (retest of support) are dangerous in bear markets because the bounce is often a sucker's rally.

**Rule:** In BEAR regime, require either:
- **Bounce Quality ≥ GOLD** (90+), OR
- **Distance to SMA200 < 2%** (bounce at major support), OR
- Skip BOUNCE entirely; take BREAKOUT only

### Example 2: Position Sizing Scaling

```python
def scale_position_size(regime, base_size_pct, quality_score):
    """
    Scale position size based on regime + signal quality.
    
    base_size_pct: intended allocation (e.g., 10%)
    quality_score: 0–100 from scanner
    
    Returns: adjusted allocation percentage
    """
    regime_multipliers = {
        'bull': 1.0,
        'mixed': 0.75,
        'bear': 0.5,
    }
    mult = regime_multipliers.get(regime, 0.75)

    # Quality claws back part of the regime haircut: a GOLD signal in a bear
    # regime gets up to half the cut restored. Expressed as a fraction of the
    # haircut, NOT as raw percentage points — otherwise the bonus is invisible.
    quality_frac = max(0.0, min(1.0, (quality_score - 60) / 40))   # 60→0, 100→1
    mult += (1.0 - mult) * 0.5 * quality_frac

    return min(base_size_pct * mult, base_size_pct)  # never exceed intended size
```

> **Why not just add the bonus to the percentage?** A common bug is
> `size = base * mult + bonus` where `bonus` is on the order of 0.04 while `base` is 10.
> The bonus is then ~0.4% of one position — arithmetically present, practically dead code —
> and in `bull` (mult 1.0) the trailing `min(..., base)` clips it away entirely. Scale the
> *multiplier*, not the output.

### Example 3: Stop Loss Distance

Wider stops in volatile markets, tighter in calm ones:

```python
def regime_adjusted_stop(regime, atr, base_mult=2.0):
    """
    Calculate stop-loss distance in ATR units, adjusted for regime.
    
    regime: 'bull' | 'bear' | 'mixed'
    atr: current ATR value
    base_mult: base multiplier (e.g., 2.0)
    
    Returns: stop_loss_distance
    """
    regime_mults = {'bull': 1.5, 'mixed': 2.0, 'bear': 2.5}
    mult = regime_mults.get(regime, 2.0)
    return atr * mult
```

---

## Regime Transitions & Cooldowns

**Problem:** Regime can flip mid-session on a single large move. This creates whipsaw.

**Solution:** Apply a **cooldown** after regime transitions. Don't immediately flip trading mode.

```python
def apply_regime_cooldown(prev_regime, curr_regime, days_since_transition=0, cooldown_days=3):
    """
    Suppress regime changes that flip back within N days.
    
    Returns: effective_regime (either prev or curr)
    """
    if prev_regime == curr_regime:
        return curr_regime
    
    # If we just flipped, stick with the new regime for at least cooldown_days
    if days_since_transition < cooldown_days:
        return prev_regime
    
    return curr_regime
```

---

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Using symbol-specific trend SMAs instead of index | Always use SPY/QQQ for regime; symbol SMAs are for trade confirmation |
| SMA periods too short (10, 20 instead of 50, 150, 200) | Short SMAs whipsaw. Use 50/150/200 for daily, 20/100 for intraday. |
| Switching regime on every bar (no stickiness) | Cache regime for entire session; update daily at market open. |
| Bear regime = never trade | Wrong. Bear regime = tighter filters, shorter holds, bigger stops. Still trade. |
| Not accounting for regime transition lag | SMA 200 is slow — expect 5–10 days lag after market inflection. |
| Applying same TP target across regimes | Bull TP 3.0×ATR, bear TP 1.5×ATR — big difference in expectancy. |
| No position-size scaling with regime | Same size in bull and bear → bear losses avalanche. Scale with regime. |

---

## Integration Examples

### Example: Regime-filtered scanner

```python
from quantkit.regime import detect_regime, suggest_params
from quantkit.indicators import calculate_all_indicators

# Load SPY daily
spy_df = pd.read_csv('spy_daily.csv')
spy_df.columns = spy_df.columns.str.lower()

# Detect regime
regime, metrics = detect_regime(spy_df)
params = suggest_params('swing', regime)          # 'swing' or 'daytrade' only
if not params:
    params = suggest_params('swing', 'mixed')     # explicit fallback — there is no implicit one

print(f"Regime: {regime}  (trend={metrics['trend_score']}, vol={metrics['vol_score']})")
print(f"Quality filter: {params['quality_filter']}")   # a STRING: 'STANDARD'|'HIGH'|'PREMIUM'

# In your signal loop — compare by tier rank, never with `<` on the string:
TIER_RANK = {'STANDARD': 0, 'HIGH': 1, 'PREMIUM': 2, 'GOLD': 3}
if TIER_RANK[signal_tier] < TIER_RANK[params['quality_filter']]:
    continue  # skip signals below the regime's quality floor
```

### Example: Regime-aware exits

```python
def exit_decision(position, current_regime, atr):
    """
    Determine exit action based on regime.

    position['pnl'] is SIGNED (positive = profit, negative = loss) — one key, not
    separate 'gain'/'loss' keys. Mixing the two is a classic source of exit bugs:
    a `position['loss'] > x` test on a winning trade silently reads as False forever.
    """
    pnl = position['pnl']

    if current_regime == 'bear':
        # Bear: bank profit early, cut losses fast.
        if pnl >= atr * 1.5:
            return 'EXIT_PARTIAL'
        if pnl <= -atr * 1.5:
            return 'EXIT_FULL'
        return 'HOLD'

    if current_regime == 'bull':
        # Bull: let winners run to 3.0× ATR, same stop distance on the downside.
        if pnl >= atr * 3.0:
            return 'EXIT_PARTIAL'
        if pnl <= -atr * 1.5:
            return 'EXIT_FULL'
        return 'HOLD'

    # Mixed: trail once the trade is working.
    if pnl >= atr:
        return 'TRAIL'
    if pnl <= -atr * 1.5:
        return 'EXIT_FULL'
    return 'HOLD'
```

> Exit *triggers* should be evaluated on the **closing** price, not an intraday tick —
> intraday triggering turns normal noise into stop-outs. See the `portfolio-exits` skill.

---

## Instructions

**Explain regime detection**: describe the SMA alignment rules, the role of price vs SMA200, and how to interpret each regime type.

**Add regime awareness**: ask what parameters change (TP, position size, stops, signal filters), then produce regime rules + parameter table.

**Debug a regime mismatch**: ask for the symptom (signals failing in bear, over-trading in mixed). Walk through SMA alignment → price position → parameter lookup.

**Integrate with quantkit**: show how to call `detect_regime()` and `suggest_params()` in the trading loop, then apply regime-scaled position sizing or stop distances.

**Tune cooldowns**: propose transition lag or flip-back threshold, explain the trade-off (responsiveness vs. stability), and recommend backtesting around major regime changes (Feb 2020, Mar 2022, Sep 2023).
