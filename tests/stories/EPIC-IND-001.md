# EPIC-IND-001 — indicators.py RSI + ATR + MACD Math Validation

```
Story ID:     EPIC-IND-001
Module:       indicators.py
Title:        Validate RSI Wilder EMA, ATR Wilder smoothing, and MACD math

AS A:         quant engineer
I WANT:       indicator functions to produce mathematically verifiable outputs
SO THAT:      signals are not built on silently wrong indicator values

GIVEN:        a synthetic 60-row OHLCV DataFrame with deterministic price series
WHEN:         compute_rsi(14), compute_atr(14), compute_macd(12,26,9) are called
THEN:         RSI values fall strictly within (0, 100) for mixed series
AND:          RSI = 100.0 for a monotonically rising series (all gains, no losses)
AND:          RSI = 0.0 for a monotonically falling series (all losses, no gains)
AND:          ATR row N equals Wilder smoothed value: ATR_n = (ATR_{n-1}*13 + TR_n) / 14
AND:          MACD line = EMA(close,12) - EMA(close,26) at every row
AND:          signal line = EMA(MACD,9) at every row

ACCEPTANCE CRITERIA:
  AC1: RSI strictly bounded — assert (rsi > 0).all() and (rsi < 100).all() on mixed series
  AC2: RSI = 100.0 on all-gains series (all prices strictly increasing by constant delta)
  AC3: RSI = 0.0 on all-losses series (all prices strictly decreasing by constant delta)
  AC4: ATR at index 28 matches manual Wilder formula to 6 decimal places
  AC5: MACD line == EMA12 - EMA26 at every non-NaN row (allclose tol=1e-6)
  AC6: Signal line == EMA9 of MACD at every non-NaN row (allclose tol=1e-6)
  AC7: No NaN after warmup period (row >= 33 for MACD, row >= 14 for RSI/ATR)

DEFINITION OF DONE:
  □ Test written and passing
  □ Edge cases covered (empty DataFrame, single-row, NaN-heavy series)
  □ No live IB/S3 dependency (fully mocked)
  □ Added to CI pipeline
  □ Product Owner signed off
```

## Background

RSI uses Wilder's EMA (alpha = 1/period), NOT a simple rolling mean. A bug was
previously fixed (commit documented in CLAUDE.md backtest section). This test
locks in the correct Wilder smoothing behavior permanently.

ATR formula: seed = simple mean of first N true ranges; subsequent:
  ATR_n = (ATR_{n-1} * (period-1) + TR_n) / period

MACD: compute_macd() is expected to return a 3-tuple (macd_line, signal_line, histogram).
Verify signature before writing the test skeleton.

## Sprint Assignment

**Sprint 1** — Guard the Money (foundation layer)

## Test File

`tests/test_indicators_math.py`
