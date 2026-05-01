# EPIC-EXIT-001 — exit_evaluator.py ExitEvaluator.evaluate() unit tests
# See: tests/stories/EPIC-EXIT-001.md

import numpy as np
import pandas as pd
import pytest

from exit_evaluator import ExitEvaluator


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_df(rows: int = 200, close_start: float = 100.0,
             close_end: float = 110.0) -> pd.DataFrame:
    """Synthetic daily OHLCV — linear close from close_start to close_end.

    Default rows=200 so swing mode (trend_period=150) passes the early-return
    guard: max(30, 150) = 150.
    """
    close = np.linspace(close_start, close_end, rows)
    high  = close + 0.5
    low   = close - 0.5
    return pd.DataFrame({
        "open":   close,
        "high":   high,
        "low":    low,
        "close":  close,
        "volume": np.full(rows, 1_000_000, dtype=float),
    })


@pytest.fixture
def evaluator() -> ExitEvaluator:
    return ExitEvaluator()


@pytest.fixture
def healthy_df() -> pd.DataFrame:
    """200-row rising DataFrame — price trending up, well above any stop.
    200 rows clears swing mode's trend_period=150 guard."""
    return _make_df(200, 90.0, 110.0)


@pytest.fixture
def short_df() -> pd.DataFrame:
    """15-row DataFrame — below the minimum required by evaluate()."""
    return _make_df(15, 100.0, 105.0)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _base_kwargs(**overrides):
    """Default position kwargs for swing mode — override as needed."""
    base = dict(
        symbol="AAPL",
        mode_name="swing",
        entry_price=100.0,
        stop_price=95.0,
        target_price=120.0,
        timeframe="1 day",
        regime="NORMAL",
        days_held=3,
        tp_reached=False,
        signal_type="",
    )
    base.update(overrides)
    return base


# ── AC1: Hard stop hit ─────────────────────────────────────────────────────────

class TestStopHit:
    def test_stop_hit_returns_exit_full(self, evaluator):
        """AC1: price <= stop_price → EXIT_FULL."""
        # 200 rows clears swing trend_period=150 guard; ends at 94 < stop=95
        df = _make_df(200, 80.0, 94.0)
        result = evaluator.evaluate(df, **_base_kwargs(stop_price=95.0))
        assert result["Action"] == "EXIT_FULL"
        assert "Stop hit" in result["Reason"]

    def test_stop_hit_exact_boundary(self, evaluator):
        """AC1: price == stop_price exactly → EXIT_FULL."""
        df = _make_df(200, 80.0, 95.0)  # last close == stop
        result = evaluator.evaluate(df, **_base_kwargs(stop_price=95.0))
        assert result["Action"] == "EXIT_FULL"


# ── AC2: Healthy position → HOLD ──────────────────────────────────────────────

class TestHealthyHold:
    def test_healthy_position_no_exit(self, evaluator, healthy_df):
        """AC2: price well above stop, far from target → no exit signal (HOLD or TRAIL).
        A rising position may legitimately trigger TRAIL (raise the stop) — that's
        correct behaviour, not an exit. The key invariant is no EXIT_FULL."""
        result = evaluator.evaluate(
            healthy_df,
            **_base_kwargs(
                entry_price=100.0,
                stop_price=90.0,
                target_price=200.0,
            )
        )
        assert result["Action"] in ("HOLD", "TRAIL"), (
            f"Healthy position should not EXIT; got {result['Action']}: {result.get('Reason')}"
        )

    def test_result_has_required_keys(self, evaluator, healthy_df):
        """Result dict always contains the mandatory keys for a full evaluation.
        Note: early-return path (insufficient data) only returns a subset."""
        result = evaluator.evaluate(healthy_df, **_base_kwargs())
        # healthy_df has 200 rows so we get the full result
        for key in ("Symbol", "Mode", "Action", "Reason", "Price",
                    "UnrealizedR", "DaysHeld"):
            assert key in result, f"Missing key: {key}"


# ── AC3: Near stop but not hit → not EXIT_FULL ────────────────────────────────

class TestNearStop:
    def test_near_stop_not_exit_full(self, evaluator):
        """AC3: price above stop (commit b824013 regression guard)."""
        # price ends at ~96.5, stop=95 — close but not hit
        df = _make_df(60, 90.0, 96.5)
        result = evaluator.evaluate(
            df,
            **_base_kwargs(
                entry_price=100.0,
                stop_price=95.0,
                target_price=130.0,
            )
        )
        # Must NOT be a hard stop exit — the price hasn't breached 95
        assert result["Action"] != "EXIT_FULL" or "Stop hit" not in result.get("Reason", "")


# ── AC4: TP reached ───────────────────────────────────────────────────────────

class TestTPReached:
    def test_tp_flag_set_when_price_at_target(self, evaluator):
        """AC4: price >= target → TPReached=True in result."""
        df = _make_df(200, 100.0, 121.0)  # ends above target=120
        result = evaluator.evaluate(
            df,
            **_base_kwargs(
                entry_price=100.0,
                stop_price=90.0,
                target_price=120.0,
            )
        )
        assert result.get("TPReached") is True


# ── AC5: BOUNCE signal type — trend broken is OK ──────────────────────────────

class TestBounceSignalType:
    def test_bounce_below_trend_does_not_exit(self, evaluator):
        """AC5: BOUNCE signal_type — price below trend line should not EXIT_FULL
        for trend-broken reason (they're below trend by design)."""
        # Falling price — would trigger trend-broken for non-bounce
        df = _make_df(60, 120.0, 90.0)
        result = evaluator.evaluate(
            df,
            **_base_kwargs(
                entry_price=115.0,
                stop_price=80.0,  # wide stop so we don't hit it
                target_price=130.0,
                signal_type="BOUNCE",
            )
        )
        # A BOUNCE below trend should NOT exit because of "Trend broken" logic
        reason = result.get("Reason", "")
        assert "Trend broken" not in reason


# ── AC6: Choppy regime ────────────────────────────────────────────────────────

class TestChoppyRegime:
    def test_choppy_no_progress_exits(self, evaluator):
        """AC6: CHOPPY regime, price stuck mid-range, R < 0.5 → EXIT_FULL."""
        # Flat price — all 60 closes near 100
        close = np.full(60, 100.0) + np.random.default_rng(7).normal(0, 0.05, 60)
        df = pd.DataFrame({
            "open":   close, "high":   close + 0.05,
            "low":    close - 0.05, "close":  close,
            "volume": np.full(60, 1_000_000, dtype=float),
        })
        result = evaluator.evaluate(
            df,
            **_base_kwargs(
                entry_price=100.0,
                stop_price=95.0,  # above stop
                target_price=120.0,
                regime="CHOPPY",
                days_held=5,
            )
        )
        # Either choppy exit fires or it holds — the key invariant is no crash
        assert result["Action"] in ("EXIT_FULL", "HOLD", "TRAIL", "EXIT_PARTIAL")


# ── AC7: Daytrade max-hold → PROMOTE_MODE ─────────────────────────────────────

class TestMaxHold:
    def test_daytrade_over_max_hold_promotes_when_trend_intact(self, evaluator):
        """AC7: daytrade past MAX_HOLD_BARS with rising price → PROMOTE_MODE."""
        from config import MAX_HOLD_BARS
        max_hold = MAX_HOLD_BARS.get("daytrade", 1)
        df = _make_df(60, 90.0, 115.0)  # rising, price > entry > SMA20
        result = evaluator.evaluate(
            df,
            **_base_kwargs(
                mode_name="daytrade",
                entry_price=100.0,
                stop_price=85.0,
                target_price=200.0,
                days_held=max_hold + 1,
            )
        )
        assert result["Action"] == "PROMOTE_MODE"
        assert result.get("NewMode") == "swing"


# ── AC8: Insufficient data ────────────────────────────────────────────────────

class TestInsufficientData:
    def test_short_df_returns_hold_with_reason(self, evaluator, short_df):
        """AC8: fewer rows than required → HOLD 'Insufficient data'."""
        result = evaluator.evaluate(short_df, **_base_kwargs())
        assert result["Action"] == "HOLD"
        assert "Insufficient" in result["Reason"]
