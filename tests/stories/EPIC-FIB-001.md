# EPIC-FIB-001 — fib_retracement.py: score_bounce math + scoring gates
# Sprint: 2 (Signal) | Tier: 1 (unit, no network)

## Why this story matters
The Fibonacci bounce scanner is used as a secondary signal source alongside the main
breakout scanner. score_bounce() is the core formula — its math (fib_levels, nearest
fib, scoring gates) must be deterministic and verifiable on synthetic data with no
network calls.

## Module
`fib_retracement.py`

## Acceptance Criteria

| # | Given | When | Then |
|---|-------|------|------|
| AC1 | swing_high=110, swing_low=100 | fib_levels(swing) called | 61.8% level ≈ 103.82, 50% level = 105.0, 38.2% level ≈ 106.18 |
| AC2 | price at exact 61.8% fib level | nearest_fib_to_price() called | returns (0.618, ..., dist_pct ≈ 0.0) |
| AC3 | price at 61.8% fib, Stage 2 trend, RSI=42, vol expansion | score_bounce() | score >= 60 (classic level 30 + stage2 15 + rsi 15 = 60 minimum) |
| AC4 | price at 23.6% fib (non-classic level) | score_bounce() | score += 0 for fib proximity (non-classic) |
| AC5 | SMA50 within 1.5% of nearest fib | score_bounce() | sma_confluence set to 'SMA50', score includes +25 |
| AC6 | price at 50% fib level | score_bounce() | golden pocket bonus applied (+5), nearest_fib == '50%' |
| AC7 | df has fewer than 30 rows | detect_swing() called | returns None, no crash |
| AC8 | swing_high == swing_low (degenerate case) | detect_swing() called | returns None |

## Definition of Validated
All 8 ACs pass as Tier 1 pytest tests with synthetic DataFrames — no yfinance calls.

## Test file
`tests/test_fib_retracement.py`

## Notes
- `detect_swing(df, window=120)` → dict or None
- `fib_levels(swing)` → {ratio: price}
- `nearest_fib_to_price(levels, price)` → (ratio, level_price, dist_pct)
- `score_bounce(df, swing)` → dict with keys: score, nearest_fib, sma_confluence,
  stage2, rsi_reset, vol_expansion, dist_to_fib_pct, retraced_pct, ...
- score_bounce() calls yfinance internally via _rsi_wilder (pure pandas) — safe to call
  if df is pre-loaded (no network needed when df is passed in)
- Classic levels: 0.382, 0.5, 0.618 — only these trigger +30 proximity bonus
- SMA confluence requires at_classic AND sma within 1.5% of fib_price to score +25
