# EPIC-SCAN-001 — scanner.py / config.py threshold boundaries + volume gate
# See: tests/stories/EPIC-SCAN-001.md
#
# NOTE: Quality classification lives in config.SCORE_THRESHOLDS and is applied
# inside BreakoutDetector._calculate_signal_score(). The threshold values are
# tested directly here via config. The volume gate and per-method unit tests
# require extracting _classify_quality() and _check_volume_expansion() as
# standalone methods — marked as xfail until that refactor is done (SCAN-PREREQ-1).

import numpy as np
import pandas as pd
import pytest

from config import SCORE_THRESHOLDS


# ── SCORE_THRESHOLDS contract ─────────────────────────────────────────────────

class TestScoreThresholds:
    """AC1-AC6: Validate SCORE_THRESHOLDS values and ordering from config.py."""

    def test_gold_threshold_is_99(self):
        """AC1: GOLD threshold == 99."""
        assert SCORE_THRESHOLDS['GOLD'] == 99

    def test_premium_threshold_is_69(self):
        """AC2/AC3: PREMIUM lower bound == 69."""
        assert SCORE_THRESHOLDS['PREMIUM'] == 69

    def test_high_threshold_is_65(self):
        """AC4/AC5: HIGH lower bound == 65."""
        assert SCORE_THRESHOLDS['HIGH'] == 65

    def test_standard_threshold_is_50(self):
        """AC6: STANDARD lower bound == 50."""
        assert SCORE_THRESHOLDS['STANDARD'] == 50

    def test_threshold_strict_ordering(self):
        """GOLD > PREMIUM > HIGH > STANDARD — strict, no equals."""
        g = SCORE_THRESHOLDS['GOLD']
        p = SCORE_THRESHOLDS['PREMIUM']
        h = SCORE_THRESHOLDS['HIGH']
        s = SCORE_THRESHOLDS['STANDARD']
        assert g > p > h > s, (
            f"Threshold ordering violated: GOLD={g}, PREMIUM={p}, HIGH={h}, STANDARD={s}"
        )

    def test_all_required_keys_present(self):
        """All four quality tier keys must be present."""
        for key in ('GOLD', 'PREMIUM', 'HIGH', 'STANDARD'):
            assert key in SCORE_THRESHOLDS, f"Missing key: {key}"

    def test_all_thresholds_are_numeric(self):
        """All threshold values must be int or float."""
        for key, val in SCORE_THRESHOLDS.items():
            assert isinstance(val, (int, float)), (
                f"SCORE_THRESHOLDS[{key!r}] is {type(val)}, expected numeric"
            )

    @pytest.mark.parametrize("score, expected_quality", [
        (100, 'GOLD'),
        (99,  'GOLD'),
        (98,  'PREMIUM'),
        (69,  'PREMIUM'),
        (68,  'HIGH'),
        (65,  'HIGH'),
        (64,  'STANDARD'),
        (50,  'STANDARD'),
        (49,  'REJECT'),
    ])
    def test_score_maps_to_quality(self, score, expected_quality):
        """
        AC1-AC6: Simulate the quality classification logic from
        BreakoutDetector._calculate_signal_score() using SCORE_THRESHOLDS.
        This tests the threshold data, not the scanner method directly.
        """
        th = SCORE_THRESHOLDS
        if score >= th.get('GOLD', 99):
            quality = 'GOLD'
        elif score >= th.get('PREMIUM', 69):
            quality = 'PREMIUM'
        elif score >= th.get('HIGH', 65):
            quality = 'HIGH'
        elif score >= th.get('STANDARD', 50):
            quality = 'STANDARD'
        else:
            quality = 'REJECT'

        assert quality == expected_quality, (
            f"score={score}: expected {expected_quality!r}, got {quality!r}"
        )


# ── Volume gate tests ─────────────────────────────────────────────────────────

def _make_volume_fixture(ratio: float, n_bars: int = 25) -> pd.DataFrame:
    """Build a DataFrame where the last bar's volume = ratio × mean(vol[-21:-1])."""
    close = np.linspace(100, 115, n_bars)
    vol = np.full(n_bars, 1_000_000.0)
    avg_vol_20 = vol[:-1][-20:].mean()
    vol[-1] = avg_vol_20 * ratio
    return pd.DataFrame({
        "open":   close - 0.5,
        "high":   close + 1.0,
        "low":    close - 1.0,
        "close":  close,
        "volume": vol,
    })


def _volume_ratio(df: pd.DataFrame, bar_index: int = -1, window: int = 20) -> float:
    """Compute last bar's volume as a ratio of the prior N-bar average."""
    vols = df["volume"].values
    prior = vols[:bar_index][-window:]
    avg = float(np.mean(prior)) if len(prior) > 0 else 1.0
    return float(vols[bar_index]) / avg if avg > 0 else 0.0


class TestVolumeGate:
    """
    AC7-AC8: Volume expansion gate requires >150% of 20-bar MA.
    Tests use the helper _volume_ratio() since _check_volume_expansion()
    is not yet an extractable method on BreakoutDetector (SCAN-PREREQ-1).
    """

    def test_volume_ratio_149pct_below_threshold(self):
        """AC7: 149% volume ratio is below the 150% gate."""
        df = _make_volume_fixture(1.49)
        ratio = _volume_ratio(df)
        assert ratio < 1.50, f"Expected ratio < 1.50, got {ratio:.4f}"

    def test_volume_ratio_151pct_above_threshold(self):
        """AC8: 151% volume ratio is above the 150% gate."""
        df = _make_volume_fixture(1.51)
        ratio = _volume_ratio(df)
        assert ratio > 1.50, f"Expected ratio > 1.50, got {ratio:.4f}"

    def test_volume_ratio_150pct_boundary(self):
        """Boundary at exactly 150% — documents behavior (strictly > or >=)."""
        df = _make_volume_fixture(1.50)
        ratio = _volume_ratio(df)
        assert abs(ratio - 1.50) < 1e-6, f"Fixture construction error: ratio={ratio}"

    def test_zero_volume_ratio_is_zero(self):
        """Zero volume should produce ratio=0, not crash."""
        df = _make_volume_fixture(0.0)
        ratio = _volume_ratio(df)
        assert ratio == 0.0

    @pytest.mark.xfail(
        reason="SCAN-PREREQ-1: _check_volume_expansion() must be extracted as a "
               "standalone BreakoutDetector method before this test can be written. "
               "Currently the volume gate logic is inlined in detect().",
        strict=False,
    )
    def test_scanner_volume_expansion_method_exists():
        """Placeholder — fails until _check_volume_expansion() is extracted."""
        from scanner import BreakoutDetector
        detector = BreakoutDetector()
        assert hasattr(detector, "_check_volume_expansion"), (
            "BreakoutDetector._check_volume_expansion() not found. "
            "Refactor needed before EPIC-SCAN-001 can be fully implemented."
        )
