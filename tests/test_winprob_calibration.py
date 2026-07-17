"""
Tests for the empirical WinProb calibration (calibrate_winprob.py fit logic
+ scanner lookup/stamping wiring).

AC-WPCAL-01  fit() shrinks small buckets toward global WR, publishes n>=min_n only
AC-WPCAL-02  normalize_type maps empty/NaN signal_type to BREAKOUT
AC-WPCAL-03  _calibrated_winprob returns (prob, grade) for known bucket, None otherwise
AC-WPCAL-04  detector disabled/missing-file → None (heuristic fallback preserved)
"""
import json

import pandas as pd
import pytest


def _trades(rows):
    return pd.DataFrame(rows, columns=['type_key', 'quality', 'win', 'pnl_pct'])


class TestFit:
    def test_shrinkage_and_min_n(self):
        from calibrate_winprob import fit, SHRINK_K
        rows = (
            [('BOUNCE', 'PREMIUM', True, 5.0)] * 60 + [('BOUNCE', 'PREMIUM', False, -3.0)] * 40
            + [('SMA20_CROSS', 'PREMIUM', True, 2.0)] * 3  # below min_n → dropped
        )
        model = fit(_trades(rows), min_n=20)
        assert 'BOUNCE|PREMIUM' in model['buckets']
        assert 'SMA20_CROSS|PREMIUM' not in model['buckets']
        b = model['buckets']['BOUNCE|PREMIUM']
        assert b['n'] == 100 and b['raw_wr'] == 0.60
        # shrunk toward global WR (63/103 ≈ 0.6117): (60 + 10*0.6117) / 110
        global_wr = 63 / 103
        expected = (60 + SHRINK_K * global_wr) / (100 + SHRINK_K)
        assert abs(b['win_prob'] - expected) < 1e-3

    def test_normalize_type(self):
        from calibrate_winprob import normalize_type
        assert normalize_type('') == 'BREAKOUT'
        assert normalize_type(None) == 'BREAKOUT'
        assert normalize_type(float('nan')) == 'BREAKOUT'
        assert normalize_type('BOUNCE') == 'BOUNCE'
        assert normalize_type('Momentum') == 'Momentum'


class TestScannerLookup:
    @pytest.fixture
    def detector_with_table(self, tmp_path, monkeypatch):
        cal = {'buckets': {
            'BOUNCE|PREMIUM': {'win_prob': 0.66, 'raw_wr': 0.67, 'n': 100},
            'BREAKOUT|GOLD': {'win_prob': 0.45, 'raw_wr': 0.44, 'n': 50},
        }}
        path = tmp_path / 'winprob_calibration.json'
        path.write_text(json.dumps(cal))
        import config
        monkeypatch.setitem(config.WINPROB_CALIBRATION, 'enabled', True)
        monkeypatch.setitem(config.WINPROB_CALIBRATION, 'path', str(path))
        from scanner import BreakoutDetector
        return BreakoutDetector()

    def test_known_bucket_returns_prob_and_grade(self, detector_with_table):
        prob, grade = detector_with_table._calibrated_winprob('BOUNCE', 'PREMIUM')
        assert prob == 0.66
        assert grade == 'HIGH'   # >= WIN_PROBABILITY high_threshold (0.65)
        prob, grade = detector_with_table._calibrated_winprob('BREAKOUT', 'GOLD')
        assert prob == 0.45
        assert grade == 'LOW'    # < low_threshold (0.50)

    def test_unknown_bucket_returns_none(self, detector_with_table):
        assert detector_with_table._calibrated_winprob('SMA20_CROSS', 'HIGH') is None

    def test_disabled_returns_none(self, monkeypatch):
        import config
        monkeypatch.setitem(config.WINPROB_CALIBRATION, 'enabled', False)
        from scanner import BreakoutDetector
        det = BreakoutDetector()
        assert det.winprob_calibration is None
        assert det._calibrated_winprob('BOUNCE', 'PREMIUM') is None

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setitem(config.WINPROB_CALIBRATION, 'enabled', True)
        monkeypatch.setitem(config.WINPROB_CALIBRATION, 'path', str(tmp_path / 'nope.json'))
        from scanner import BreakoutDetector
        det = BreakoutDetector()
        assert det.winprob_calibration is None
