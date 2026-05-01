# EPIC-PAT-001 — pattern_recognition.get_pattern_score() 7-Tuple Contract

```
Story ID:     EPIC-PAT-001
Module:       pattern_recognition.py
Title:        Validate get_pattern_score() always returns a 7-tuple with typed values

AS A:         signal scoring engineer
I WANT:       get_pattern_score() to enforce a stable 7-element contract on every call
SO THAT:      positional unpacking never silently assigns wrong values to wrong variables

GIVEN:        a variety of OHLCV DataFrames (empty, 1-row, 50-row synthetic)
WHEN:         get_pattern_score(df) is called
THEN:         the return value is a tuple of exactly length 7
AND:          position[0] is bool  — has_bullish
AND:          position[1] is bool  — has_bearish
AND:          position[2] is float — best_target (>= 0.0)
AND:          position[3] is list[str] — pattern_names (may be empty list, never None)
AND:          position[4] is bool  — vol_confirmed
AND:          position[5] is float or None — vcp_quality
AND:          position[6] is dict or None  — vcp_data
AND:          no exception is raised for any input shape

ACCEPTANCE CRITERIA:
  AC1: len(get_pattern_score(df)) == 7 for all inputs
  AC2: type(result[0]) is bool
  AC3: type(result[1]) is bool
  AC4: isinstance(result[2], float) and result[2] >= 0
  AC5: isinstance(result[3], list) and all(isinstance(x, str) for x in result[3])
  AC6: type(result[4]) is bool
  AC7: result[5] is None or isinstance(result[5], float)
  AC8: result[6] is None or isinstance(result[6], dict)
  AC9: No exception raised for empty DataFrame, 1-row DataFrame, NaN-filled DataFrame,
       or DataFrames of length 2, 5, 14, 26, 49

DEFINITION OF DONE:
  □ Test written and passing
  □ Edge cases: empty DataFrame, 1-row, NaN-filled, various row counts (2–49)
  □ Bullish engulfing forced in 50-row fixture → AC verifies detection
  □ No live IB/S3 dependency (fully mocked)
  □ Added to CI pipeline
  □ Product Owner signed off
```

## Background

`get_pattern_score()` returns a 7-tuple. Any caller unpacking positionally (not by name)
will silently receive wrong values if the return order ever changes or if an extra
value is added. This contract test locks in:
  (has_bullish, has_bearish, best_target, pattern_names, vol_confirmed, vcp_quality, vcp_data)

V12 system: 28 patterns total — 16 chart patterns + 11 candle patterns + VCP.
All 28 patterns should fire on appropriate synthetic fixtures.

## Sprint Assignment

**Sprint 2** — Protect the Signal (pattern engine)

## Test File

`tests/test_pattern_recognition_contract.py`
