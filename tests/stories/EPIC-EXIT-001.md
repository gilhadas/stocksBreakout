# EPIC-EXIT-001 — exit_evaluator.py: ExitEvaluator.evaluate() exit signal logic
# Sprint: 1 (Guard Money) | Tier: 1 (unit, no IB)

## Why this story matters
exit_evaluator.py is the last line of defence before a losing trade destroys capital.
Commit b824013 fixed a regression where NEAR STOP was emitting SELL SIGNAL; that bug
must never return. The evaluator's priority hierarchy (stop hit → trend broken → trail
→ HOLD) must be deterministic and verifiable without a live broker.

## Module
`exit_evaluator.py` — `ExitEvaluator.evaluate()`

## Acceptance Criteria

| # | Given | When | Then |
|---|-------|------|------|
| AC1 | price <= stop_price | evaluate() called | Action == 'EXIT_FULL', Reason contains 'Stop hit' |
| AC2 | price well above stop, far from target | evaluate() called | Action == 'HOLD' |
| AC3 | price within 1×ATR of stop but above it | evaluate() called | Action != 'EXIT_FULL' (not a stop hit yet) |
| AC4 | price >= target_price, unrealized_r >= 1.0 | evaluate() called | tp_reached=True in result, Action may be TRAIL |
| AC5 | signal_type='BOUNCE', price >= trend_line | evaluate() called | Action may be TRAIL (recovery), NOT EXIT_FULL for trend-broken |
| AC6 | regime='CHOPPY', price stuck mid-range, unrealized_r < 0.5 | evaluate() called | Action == 'EXIT_FULL', Reason contains 'Choppy' |
| AC7 | mode='daytrade', days_held > MAX_HOLD_BARS, trend intact | evaluate() called | Action == 'PROMOTE_MODE', NewMode == 'swing' |
| AC8 | df has < 30 rows | evaluate() called | Action == 'HOLD', Reason contains 'Insufficient data' |

## Definition of Validated
All 8 ACs pass as Tier 1 pytest tests with no IB connection required.
No real market data fetched — synthetic OHLCV DataFrames only.

## Test file
`tests/test_exit_evaluator.py`

## Notes
- `ExitEvaluator` constructor takes no arguments
- `evaluate()` calls `calculate_all_indicators()` internally — DataFrame must be long
  enough (≥30 rows, ideally ≥50) with open/high/low/close/volume columns
- Return dict keys: Symbol, Mode, Action, Reason, Price, Stop, Target, VolRatio,
  UnrealizedR, DaysHeld, TPReached
- `MODES` in config.py must have the `mode_name` key — use 'swing' for generic tests
- b824013 regression guard: NEAR STOP (price close to stop but not hit) → HOLD or TRAIL,
  never EXIT_FULL
