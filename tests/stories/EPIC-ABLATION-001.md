# EPIC-ABLATION-001 — Pooled-Cap Regression Contract

**Created:** 2026-05-01
**Owner:** Quant Infrastructure
**Status:** OPEN
**Test file:** `tests/test_backtest_pooled_cap.py`
**Related file:** `backtest_regime_compare.py` → `_pooled_cap()`

---

## Context

`_pooled_cap(signals, max_per_day)` is the gating function introduced with the
pooled-cap feature (+86 pts 5yr compound vs per-file cap). It ranks signals
globally by `Quality → WinProb → R:R → Dist≤25% → Vol` and keeps at most
`max_per_day` signals per calendar date.

A regression in this function silently destroys the edge without surfacing an
error. These acceptance criteria are the minimum contract.

The ablation experiment this unlocks:

| Run | CLI Flags | What It Isolates |
|-----|-----------|-----------------|
| A — Baseline | *(default)* | pooled-cap=10, current champion |
| B — Cap only | `--pooled-cap 2` | Does tighter cap cut losers without cutting winners? |
| C — Filter only | `--selective --pooled-cap 10` | Does signal-type gating alone improve hold duration? |
| D — Both | `--selective --pooled-cap 2` | Compound effect |

---

## Acceptance Criteria

### AC1 — Hard cap per date
> `_pooled_cap(signals, max_per_day=2)` **never** returns more than 2 signals
> for any single calendar date, regardless of input size.

**Test shape:**
```python
# Arrange: 10 signals all dated "2024-01-15"
# Act: result = _pooled_cap(signals, max_per_day=2)
# Assert: len([s for s in result if s["date"] == "2024-01-15"]) <= 2
```

---

### AC2 — Quality ordering within a date
> Within any single date, **GOLD signals always rank before PREMIUM signals**,
> regardless of WinProb.

**Test shape:**
```python
# Arrange: 1 GOLD (wp=0.65) + 2 PREMIUM (wp=0.90, 0.80) on same date; cap=2
# Act: result = _pooled_cap(signals, max_per_day=2)
# Assert: result[0]["quality"] == "GOLD"
```

---

### AC3 — WinProb ordering within same quality tier
> Within the same quality tier on the same date, the signal with the **higher
> WinProb** is ranked first and survives under a tight cap.

**Test shape:**
```python
# Arrange: 3 PREMIUM signals (wp=0.55, 0.75, 0.65) on same date; cap=1
# Act: result = _pooled_cap(signals, max_per_day=1)
# Assert: result[0]["win_prob"] == pytest.approx(0.75)
```

---

### AC4 — Default max_per_day=10 is backward-compatible
> Calling `_pooled_cap(signals)` with no `max_per_day` argument produces
> identical output to `_pooled_cap(signals, max_per_day=10)`.

**Test shape:**
```python
# Arrange: 10 signals across 2 dates
# Assert: _pooled_cap(signals) == _pooled_cap(signals, max_per_day=10)
```

---

### AC5 — Edge cases: empty / single-signal / cap=0
> - Empty list → empty list, no exception
> - Single signal → always returned for any positive cap
> - `max_per_day=0` → empty list

---

## Definition of Done

- [ ] All 5 ACs passing in `tests/test_backtest_pooled_cap.py`
- [ ] Tests run in < 2 seconds (synthetic data only — no IB, no S3, no yfinance)
- [ ] `pytest tests/test_backtest_pooled_cap.py` added to CI gate
- [ ] CLAUDE.md Section 8 ablation table updated with actual Sharpe values after
      all four runs (A, B, C, D) complete
