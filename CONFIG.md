# stocksBreakout Scanner — Configuration Reference

**File:** `config.py`  
**Last Updated:** 2026-05-26  
**Scope:** All tunable parameters for breakout detection, position sizing, risk management, regime detection, and data sources.

---

## 1. Portfolio & Risk Management

### `PORTFOLIO`
```python
PORTFOLIO = {
    'max_single_position_pct': 10,      # Max capital allocated to one position (%)
    'max_concurrent_positions': 20,     # Max open positions at any time
    'max_sector_pct': 25,               # Max total capital in one sector (%)
    'use_trend_filter': True,           # Require SMA150 > SMA200 before trading
    'max_buy_per_day': 20,              # Max new entries per calendar day
}
```
- **max_single_position_pct**: Prevents concentration risk. 10% = on $100k account, max $10k per trade.
- **max_concurrent_positions**: Portfolio ceiling. 20 = never hold >20 open positions.
- **max_sector_pct**: Sector hedge. 25% = can't have >25% in tech/healthcare/etc.
- **use_trend_filter**: Macro filter. When FALSE, trades in bear markets; when TRUE, waits for SMA150>SMA200.
- **max_buy_per_day**: Rate limit. 20 = admit max 20 new signals per day (via pooled-cap ranking).

---

## 2. Signal Quality & Scoring

### `SCORING_WEIGHTS`
Weighted multi-component system (0–100 scale). Each component contributes points toward final quality tier.

```python
SCORING_WEIGHTS = {
    'dist_confirm': 24,              # Distance breakout (% above support/resistance)
    'at_key_support': 24,            # Price near tested support level
    'candle_ok': 19,                 # Candle structure (body size, shadows)
    'near_52w_high': 17,             # Proximity to 52-week high
    'vol_confirm': 16,               # Volume expansion on breakout
    'rs_ok': 16,                     # Relative strength (outperforms SPY/sector)
    'atr_trail': 14,                 # Stop position (closer = higher confidence)
    'bb_squeeze': 12,                # Bollinger Band width contraction
    'pattern': 12,                   # Chart pattern detected (flag, triangle, etc.)
    # ... 7 more components
}
```
**Total = ~200 points max.** Score is normalized to 0–100 range. Components are **disabled**=0 score contribution if condition fails.

### `SCORE_THRESHOLDS`
Quality tiers based on final score:

```python
SCORE_THRESHOLDS = {
    'GOLD': 90,       # Top 10% signals (highest conviction)
    'PREMIUM': 75,    # Strong signals (above 75)
    'HIGH': 65,       # Decent signals (65–74)
    'STANDARD': 50,   # Tradeable baseline (50–64)
}
```
**Rule of thumb:** GOLD WR ~82%, PREMIUM ~73%, HIGH ~69%, STANDARD ~58%.

### `QUALITY_SIZING`
Position size multiplier per quality tier:

```python
QUALITY_SIZING = {
    'GOLD': 1.20,         # 120% of base position (20% bonus for conviction)
    'PREMIUM': 1.00,      # 100% of base position
    'HIGH': 0.80,         # 80% of base position (smaller due to lower confidence)
    'STANDARD': 0.50,     # 50% of base position (small, experimental)
}
```
Base position = `capital / max_concurrent_positions`. Multipliers adjust for signal quality.

### `AROON_N` & `AROON_CONFIRM_THRESHOLD`
Aroon Oscillator confirmation:

```python
AROON_N = 25                    # Lookback bars (standard 25)
AROON_CONFIRM_THRESHOLD = 50    # Oscillator > 50 = uptrend confirmed
```
Aroon = (bars since 25-bar high − bars since 25-bar low) / 25. Ranges [−1, +1]. Threshold 50 means >50% of last 25 bars are driving uptrend.

---

## 3. Exit Logic & Position Management

### `ATR_TRAIL_MULT` & `ATR_TRAIL_FLOOR_BARS`
Trailing stop configuration:

```python
ATR_TRAIL_MULT = 2.0              # Trail at ATR×2.0 (tightest tested sweep winner: +234% 5yr)
ATR_TRAIL_FLOOR_BARS = 14         # Wilder's ATR(14) needs 14 bars minimum
```
- **ATR_TRAIL_MULT=2.0**: Tight trail. Exits on 2× ATR below entry-day high. Tighter than ATR×3.0 (saves losers faster).
- **ATR_TRAIL_FLOOR_BARS=14**: Don't use trail until bar 14 (when ATR is meaningful).

### `ATR_SIZING`
Position sizing based on volatility (ATR):

```python
ATR_SIZING = {
    'base_atr': 2.0,                # Base ATR multiplier for stop-loss distance
    'high_volatility_mult': 0.8,    # Reduce position size in high-volatility regimes
    'low_volatility_mult': 1.2,     # Increase position size in calm markets
}
```
Converts ATR into dollars: `stop_distance = ATR × ATR_SIZING['base_atr']`. Scaled by regime volatility.

### `MAX_HOLD_BARS`
Position hold time limits by signal type:

```python
MAX_HOLD_BARS = {
    'BOUNCE': 45,           # BOUNCE signals held max 45 trading days (~2 months)
    'BREAKOUT': 60,         # BREAKOUT signals held max 60 trading days (~3 months)
    'SMA20_CROSS': 30,      # SMA20_CROSS held max 30 days
}
```
**Key finding:** System edge concentrates in >15-day holds (67–78% WR). Signals exiting before day 15 show ~10% WR (not worth trading).

### `CASH_MANAGEMENT`
Reserve capital settings:

```python
CASH_MANAGEMENT = {
    'min_cash_pct': 5,              # Always keep 5% cash buffer
    'scale_out_levels': [0.5, 0.75],  # Close 50% at 0.5× target, 25% at 0.75× target
}
```

---

## 4. Risk & Probability Scoring

### `RR_GRADE_CONFIG` & `RR_GRADE_SCORES`
Risk-Reward grading:

```python
RR_GRADE_CONFIG = {
    'low': (0, 1.0),           # R:R 0–1.0 = "D" grade (avoid)
    'medium': (1.0, 1.5),      # R:R 1.0–1.5 = "C" grade (ok)
    'good': (1.5, 2.0),        # R:R 1.5–2.0 = "B" grade (good)
    'excellent': (2.0, 999),   # R:R >2.0 = "A" grade (best)
}
RR_GRADE_SCORES = {'A': 0.53, 'B': 1.0, 'C': 0.57, 'D': 0.0}
```
- **A (R:R >2.0)**: Expected value +53 bps per trade. Preferred.
- **B (1.5–2.0)**: Expected value +100 bps. Solid.
- **C (1.0–1.5)**: Expected value +57 bps. Acceptable.
- **D (<1.0)**: Skip entirely (expected value negative).

### `WIN_PROBABILITY`
WR-based multipliers (prediction of trade outcome):

```python
WIN_PROBABILITY = {
    'gold_high_rr':     (0.70, 1.15),     # GOLD + R:R A = 70% expected WR, 1.15× size mult
    'premium_high_rr':  (0.65, 1.08),
    'gold_medium_rr':   (0.60, 1.00),
    'premium_medium_rr': (0.55, 0.95),
}
```
Tuple = (expected_win_rate, position_size_multiplier). Used to dynamically size positions pre-trade.

---

## 5. Regime Detection & Gating

### `REGIME_CONFIG`
Market regime parameters with adaptive scaling:

```python
REGIME_CONFIG = {
    'BULL': {
        'tp_mult': 1.0,              # TP at 1× measured move (most aggressive)
        'stop_mult': 1.0,            # Stop at 1× ATR (tightest)
        'position_mult': 1.15,       # 15% larger positions
        'max_entries': 20,           # Accept up to 20 new entries per day
    },
    'BEARISH': {
        'tp_mult': 0.5,              # TP at 0.5× measured move (conservative)
        'stop_mult': 1.5,            # Stop at 1.5× ATR (wider, protect against noise)
        'position_mult': 0.50,       # 50% smaller positions
        'max_entries': 0,            # No new entries in pure bearish
    },
    # ... EXPANSION, NORMAL, CHOPPY, RED_MARKET
}
```
**Regime Rules:**
- BULL (SMA50>SMA150>SMA200, price above all): Aggressive. Full size, tight stops.
- BEARISH (price below all SMAs): Avoid. No new entries, 50% sizing on existing.
- RED_MARKET (SPY below SMA200): Mixed. Block BOUNCE only if SPY ≥15 consecutive days below SMA200 (BBG15 gate).
- CHOPPY (BB width >20%, ADX <25): Whipsaw risk. Wider stops, smaller size.

### `BOUNCE_BEAR_GATE`
Sustained bear market filter:

```python
BOUNCE_BEAR_GATE = 15    # Days SPY must be below SMA200 before blocking BOUNCE+RED_MARKET
```
- **BBG15 = ON**: Skip BOUNCE entries only when SPY has been <SMA200 for ≥15 consecutive trading days (true sustained bear). Improves 2022 bear-year returns by +5.7%.
- **BBG15 = OFF**: Take BOUNCE entries anytime SPY is below SMA200 (false positives on brief dips). Hurts performance −9.3 pts.

### `TREND_CONFIRM`
Multi-gate signal confirmation (checks if prior bars align with current signal):

```python
TREND_CONFIRM = {
    'enabled': False,                    # DISABLED by default (Path B destroys edge)
    'enabled_paths': ['A'],              # Path A active but minimal impact (+2.7 pts)
    'path_a_gates': 7,                   # All 7 gates must pass in single bar
    'path_b_consecutive_bars': 4,        # 4 of last 5 bars must score ≥6
    'gate_requirements': {
        'sma_200': True,                 # Price above SMA200
        'sma_50_above_200': True,        # SMA50 > SMA200
        # ... 5 more gates
    }
}
```
**Warning:** Path B fires 3.4× more signals in choppy/trending markets (600–900 extra/year). Destroys edge (−24 pts). Path A is kept for textbook high-conviction setups.

---

## 6. Pattern Detection & Signal Types

### `VCP_CONFIG`
Volatility Contraction Pattern (Minervini) configuration:

```python
VCP_CONFIG = {
    'min_contractions': 2,               # Need 2+ pullback cycles
    'vol_dry_threshold': 0.70,           # Final vol < 70% of first contraction vol
    'contraction_strictness': 0.90,      # Each pullback <90% of prior (tightening)
    'quality_score_weight': {
        'contractions': 0.25,            # Number of contractions (max 0.25 pts)
        'decay_ratio': 0.25,             # Pullback size decay (0.25 pts)
        'vol_dry_up': 0.20,              # Volume drying up (0.20 pts)
        'tight_range': 0.15,             # Range tightness (0.15 pts)
        'proximity': 0.15,               # Near pivot/52-week high (0.15 pts)
    }
}
```
VCP quality score 0–1. >0.60 = tradeable; >0.80 = high conviction.

---

## 7. Data & Connectivity

### `MIN_DOLLAR_VOLUME`
Minimum daily dollar volume filter:

```python
MIN_DOLLAR_VOLUME = 5_000_000    # Only trade stocks with >$5M daily volume
```
Prevents illiquid microcaps where slippage / execution risk is high.

### `MAX_CONCURRENT_REQUESTS`
Rate limiting for IB data fetches:

```python
MAX_CONCURRENT_REQUESTS = 5      # Max 5 concurrent data requests to IB
```
IB throttles at ~10 req/sec. Setting to 5 keeps overhead low while scanning 50–300 symbols.

### `SCAN_DELAY`
Delay between symbol fetches:

```python
SCAN_DELAY = 0.03                # 30ms delay between symbols
```
Prevents API hammering during multi-symbol scans.

### `DATA_DURATION`
Historical data windows by timeframe:

```python
DATA_DURATION = {
    'daily': '2 y',              # 2 years of daily data for pattern detection
    'intraday': '10 d',          # 10 days of intraday data for momentum
}
```

### `IB_PAPER_PORT`, `IB_LIVE_PORT`, `IB_HOST`, `IB_CLIENT_ID`
Interactive Brokers connection settings:

```python
IB_PAPER_PORT = 7497             # Paper trading port
IB_LIVE_PORT = 7496              # Live trading port
IB_HOST = '127.0.0.1'            # Localhost (run TWS locally)
IB_CLIENT_ID = 1                 # Unique client ID (prevents conflicts)
```
Ensure TWS/Gateway is running on the correct port before scanner launch.

### `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
Alpaca broker credentials (loaded from `.env`):

```python
ALPACA_API_KEY = os.environ.get('ALPACA_API_KEY', '')
ALPACA_SECRET_KEY = os.environ.get('ALPACA_SECRET_KEY', '')
```
Used for live trading execution (if using Alpaca instead of IB).

---

## 8. Sentiment & External Data

### `SENTIMENT`
FinBERT sentiment analysis configuration:

```python
SENTIMENT = {
    'model_name': 'ProsusAI/finbert',           # Pre-trained FinBERT model
    'threshold_bullish': 0.60,                  # Score >0.60 = bullish
    'threshold_bearish': 0.40,                  # Score <0.40 = bearish
    'premarket_required': True,                 # Run at 4 AM premarket scan
}
```
FinBERT outputs: positive, negative, neutral (each 0–1, sum=1). Custom threshold gates quality promotion.

### `FINBERT_PROMOTION`
Quality boost from bullish sentiment:

```python
FINBERT_PROMOTION = {
    'enable': True,
    'boost_threshold': 0.70,                    # Sentiment score >0.70 = boost quality
    'boost_grades': {'HIGH': 'PREMIUM', 'PREMIUM': 'GOLD'},  # Upgrade tier
}
```
Example: HIGH-quality signal with 75% bullish sentiment → promoted to PREMIUM quality (1.08× larger position).

---

## 9. Notifications

### `NOTIFICATIONS`
Alert delivery channels:

```python
NOTIFICATIONS = {
    'email': True,                              # Send email alerts
    'telegram': True,                           # Telegram group messages
    'discord': True,                            # Discord webhook
    'mac_native': True,                         # Mac notification center
    'expo_push': True,                          # Expo mobile push notifications
}
```
All channels can be toggled independently. Configure API keys in `.env`.

---

## 10. Key Parameter Interactions

| Scenario | Parameter | Impact |
|----------|-----------|--------|
| **Bear Market** | BOUNCE_BEAR_GATE=15 | +5.7% return in 2022 vs OFF |
| **Tight Stops** | ATR_TRAIL_MULT=2.0 | +234% 5yr (vs +137% with post-TP trail) |
| **High Conviction** | TREND_CONFIRM=['A'] | +2.7 pts (negligible but harmless) |
| **Signal Overload** | MAX_HOLD_BARS > 60 | −7% return per +10 days (hold longer = worse) |
| **Position Sizing** | QUALITY_SIZING['GOLD']=1.20 | GOLD trades 20% larger, 82% WR vs 73% avg |
| **Regime Filter** | use_trend_filter=True | Avoids bear trades; loses some bull trades |
| **Sentiment Boost** | FINBERT_PROMOTION=True | +1.7% edge on thematic signals, no impact on general |

---

## 11. How to Tune Parameters

### Safe Tweaks (Low Risk)
- **max_single_position_pct**: Adjust 5–15% based on account size and risk tolerance.
- **QUALITY_SIZING**: Tweak ±0.05 per tier to adjust conviction weighting.
- **ATR_TRAIL_MULT**: Test 1.5–3.0 (1.5 tightest, 3.0 loosest). Current champion: 2.0.

### Moderate Risk
- **SCORE_THRESHOLDS**: Adjust ±5 points per tier (test one year backtest for validation).
- **BOUNCE_BEAR_GATE**: Try 10, 15, 20 (15 is validated best; others untested).
- **MAX_HOLD_BARS**: Shorten to <15d only if you believe momentum flips faster.

### High Risk (Requires Full Backtest)
- **REGIME_CONFIG**: Changes compound across all regimes; requires `backtest_regime_compare.py --full-compare`.
- **TREND_CONFIRM**: Currently disabled; enabling Path B destroys −24 pts.
- **SCORING_WEIGHTS**: Rebalance weights with `weight_optimizer.py` (Optuna walk-forward).

---

## 12. Validation Workflow

After changing any parameter:

```bash
# 1. Backtest on optimizer_watch.txt (50 symbols, 5 years, ~2 min)
python backtest_regime_compare.py --no-tc --bounce-bear-gate 15 \
  --watchlist input/optimizer_watch.txt

# 2. Compare 5-year Sharpe vs baseline (+0.88 target)
# 3. If Sharpe improves by ≥+0.05, test on full universe
python backtest_regime_compare.py --no-tc --bounce-bear-gate 15 \
  --watchlist input/screener.txt --skip-old --trades-log

# 4. Run unit tests to ensure no regressions
pytest tests/ -v
```

---

**Document Version:** 2.0  
**Last Sync with Code:** 2026-05-26  
**Maintainer:** stocksBreakout Development
