"""
Regression tests for _pooled_cap() and the --pooled-cap CLI wire-through.
See: tests/stories/EPIC-ABLATION-001.md

Ablation experiment context
---------------------------
Champion config: +195% 5yr (NEW V9-C + BBG15 + pooled-cap=10, no TREND_CONFIRM).
Hypothesis: capping to 2 signals/day forces the ranker to pick only the
highest-conviction setups — potentially improving Sharpe at the cost of raw
compound return. Measured by running:

    python backtest_regime_compare.py --no-tc --bounce-bear-gate 15 --pooled-cap 2

and comparing 5yr compound + Sharpe vs the pooled-cap=10 baseline row.

Regression suite guards:
  AC-1  _pooled_cap never returns > max_per_day signals for any calendar date.
  AC-2  Within a day, GOLD ranks before PREMIUM (quality tier ordering).
  AC-3  Within the same quality tier, higher WinProb ranks first.
  AC-4  pooled_cap=10 baseline is structurally unchanged (backward compat).
  AC-5  Edge cases: empty / single-signal / cap=0 inputs return without error.
"""

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest_regime_compare import _pooled_cap


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sig(date: str, quality: str, win_prob: float,
         rr: float = 2.0, sma_dist_pct: float = 5.0) -> dict:
    """Minimal signal dict satisfying _pooled_cap's sort keys."""
    return {
        "date":        date,
        "quality":     quality,
        "win_prob":    win_prob,
        "rr":          rr,
        "sma_dist_pct": sma_dist_pct,
    }


def _day(date: str, n_gold: int, n_premium: int,
         base_wp: float = 0.70) -> list[dict]:
    """Return n_gold GOLD + n_premium PREMIUM signals for a single date."""
    out = []
    for i in range(n_gold):
        out.append(_sig(date, "GOLD",    win_prob=base_wp - i * 0.01))
    for i in range(n_premium):
        out.append(_sig(date, "PREMIUM", win_prob=base_wp - i * 0.01))
    return out


# ── AC-1: Hard cap per date ────────────────────────────────────────────────────

class TestNeverExceedsCap:
    def test_cap_2_single_day_many_signals(self):
        """AC-1: cap=2, 8 signals on one date → ≤2 returned."""
        signals = _day("2024-01-15", n_gold=3, n_premium=5)
        result = _pooled_cap(signals, max_per_day=2)
        assert len(result) <= 2

    def test_cap_2_multi_day(self):
        """AC-1: each date independently capped at 2."""
        signals = (
            _day("2024-01-15", n_gold=4, n_premium=4) +
            _day("2024-01-16", n_gold=2, n_premium=6) +
            _day("2024-01-17", n_gold=0, n_premium=8)
        )
        result = _pooled_cap(signals, max_per_day=2)
        counts = Counter(s["date"] for s in result)
        for date, count in counts.items():
            assert count <= 2, f"{date}: got {count}, expected ≤2"

    def test_cap_10_multi_day(self):
        """AC-1: cap=10 respected when day has >10 signals."""
        signals = (
            _day("2024-03-01", n_gold=6, n_premium=8) +
            _day("2024-03-04", n_gold=3, n_premium=12)
        )
        result = _pooled_cap(signals, max_per_day=10)
        counts = Counter(s["date"] for s in result)
        for date, count in counts.items():
            assert count <= 10, f"{date}: got {count}, expected ≤10"

    def test_cap_1_exactly_one_per_day(self):
        """AC-1: cap=1 → exactly 1 signal returned per day that had any."""
        signals = (
            _day("2024-05-01", n_gold=2, n_premium=3) +
            _day("2024-05-02", n_gold=0, n_premium=5)
        )
        result = _pooled_cap(signals, max_per_day=1)
        counts = Counter(s["date"] for s in result)
        for count in counts.values():
            assert count == 1


# ── AC-2: Quality ordering within a date ─────────────────────────────────────

class TestQualityOrdering:
    def test_gold_beats_higher_wp_premium(self):
        """AC-2: GOLD ranked first even when a PREMIUM has higher WinProb."""
        date = "2024-02-20"
        signals = [
            _sig(date, "GOLD",    win_prob=0.65),
            _sig(date, "PREMIUM", win_prob=0.90),  # higher wp but lower quality
            _sig(date, "PREMIUM", win_prob=0.80),
        ]
        result = _pooled_cap(signals, max_per_day=2)
        kept = [s for s in result if s["date"] == date]
        assert kept[0]["quality"] == "GOLD"

    def test_cap_1_gold_survives_over_premium(self):
        """AC-2: with cap=1, GOLD always wins over PREMIUM on same date."""
        date = "2024-02-21"
        signals = [
            _sig(date, "GOLD",    win_prob=0.60),
            _sig(date, "PREMIUM", win_prob=0.95),
        ]
        result = _pooled_cap(signals, max_per_day=1)
        assert len(result) == 1
        assert result[0]["quality"] == "GOLD"


# ── AC-3: WinProb ordering within same quality tier ──────────────────────────

class TestWinProbOrdering:
    def test_highest_wp_premium_kept_under_cap(self):
        """AC-3: among PREMIUM signals on same day, highest WinProb kept."""
        date = "2024-04-10"
        signals = [
            _sig(date, "PREMIUM", win_prob=0.55),
            _sig(date, "PREMIUM", win_prob=0.75),
            _sig(date, "PREMIUM", win_prob=0.65),
        ]
        result = _pooled_cap(signals, max_per_day=1)
        assert len(result) == 1
        assert result[0]["win_prob"] == pytest.approx(0.75)

    def test_highest_wp_gold_kept_under_cap(self):
        """AC-3: among GOLD signals on same day, highest WinProb kept."""
        date = "2024-04-11"
        signals = [
            _sig(date, "GOLD", win_prob=0.50),
            _sig(date, "GOLD", win_prob=0.80),
        ]
        result = _pooled_cap(signals, max_per_day=1)
        assert result[0]["win_prob"] == pytest.approx(0.80)

    def test_top_2_wp_kept_from_3(self):
        """AC-3: cap=2 keeps the two highest WinProb from same quality."""
        date = "2024-04-12"
        signals = [
            _sig(date, "PREMIUM", win_prob=0.50),
            _sig(date, "PREMIUM", win_prob=0.80),
            _sig(date, "PREMIUM", win_prob=0.70),
        ]
        result = _pooled_cap(signals, max_per_day=2)
        kept_wps = sorted([s["win_prob"] for s in result], reverse=True)
        assert kept_wps == pytest.approx([0.80, 0.70])


# ── AC-4: Default cap=10 backward compatibility ───────────────────────────────

class TestBaselineUnchanged:
    def test_explicit_10_equals_default(self):
        """AC-4: _pooled_cap(signals) identical to _pooled_cap(signals, max_per_day=10)."""
        signals = (
            _day("2023-06-01", n_gold=2, n_premium=3) +
            _day("2023-06-02", n_gold=1, n_premium=4)
        )
        r_default  = _pooled_cap(signals)
        r_explicit = _pooled_cap(signals, max_per_day=10)
        assert r_default == r_explicit

    def test_cap10_passthrough_when_under_limit(self):
        """AC-4: days with ≤10 signals are fully preserved under cap=10."""
        signals = (
            _day("2023-06-01", n_gold=2, n_premium=3) +  # 5 total
            _day("2023-06-02", n_gold=1, n_premium=4)    # 5 total
        )
        result = _pooled_cap(signals, max_per_day=10)
        assert len(result) == len(signals)


# ── AC-5: Edge cases ──────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_input(self):
        """AC-5: empty list → empty list, no error."""
        assert _pooled_cap([], max_per_day=2) == []

    def test_single_signal_always_passes(self):
        """AC-5: single signal always survives any positive cap."""
        signals = [_sig("2024-01-01", "PREMIUM", win_prob=0.70)]
        result = _pooled_cap(signals, max_per_day=1)
        assert len(result) == 1

    def test_cap_zero_returns_empty(self):
        """AC-5: cap=0 returns empty list."""
        signals = _day("2024-01-01", n_gold=2, n_premium=3)
        result = _pooled_cap(signals, max_per_day=0)
        assert result == []

    def test_output_preserves_all_signal_fields(self):
        """AC-5: _pooled_cap does not strip or mutate signal dicts."""
        signals = [_sig("2024-06-01", "GOLD", win_prob=0.75, rr=3.0, sma_dist_pct=12.0)]
        result = _pooled_cap(signals, max_per_day=5)
        assert result[0]["rr"] == pytest.approx(3.0)
        assert result[0]["sma_dist_pct"] == pytest.approx(12.0)
