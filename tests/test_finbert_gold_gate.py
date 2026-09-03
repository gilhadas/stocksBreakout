#!/usr/bin/env python3
"""FinBERT sentiment must not mint GOLD for detector types with no native
structural GOLD gate.

WHY THIS EXISTS
---------------
_apply_finbert_promotion (extracted from run_scan_mode's inline FinBERT
promotion block) upgraded PREMIUM->GOLD on sentiment score alone, regardless
of signal Type. But detect_bounce()/detect_continuation()/detect_sma20_cross()
never natively assign Quality='GOLD' — the ONLY path a BOUNCE/CONTINUATION/
SMA20_CROSS signal could ever reach GOLD was this promotion. Meanwhile
downstream code treats GOLD as "passed hard structural gates":
  - breakout_scanner.py's regime-restricted watchlist: BOUNCE only admitted
    in CHOPPY/RED_MARKET if Quality == 'GOLD'
  - the BOUNCE notification gate: "GOLD only — PREMIUM bounces have negative
    expected value"
  - auto_portfolio.py priority ranking and quality_risk_penalty (0.0 for GOLD)
So a bullish headline alone could silently earn a BOUNCE the same trust as a
signal that passed R:R/trend/volume/52w-high/sector gates. Only BREAKOUT
('' or 'Momentum' Type, from scanner.py's detect()) and TREND_CONFIRM define
a real structural GOLD gate — only those may be promoted into it now.

Run:
    python -m pytest tests/test_finbert_gold_gate.py -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from breakout_scanner import _apply_finbert_promotion


def _sig(quality, sig_type, score=0.95, net=0.9, headlines=5, label='bullish'):
    return {
        'Symbol': 'TEST', 'Quality': quality, 'Type': sig_type,
        'FinBERT': label, 'FinBERT_Score': score, 'FinBERT_Net': net,
        'FinBERT_Total': headlines,
    }


class TestNativeGoldTypesStillPromote:
    """BREAKOUT and TREND_CONFIRM keep working exactly as before — this fix
    must not regress the types that DO have a structural GOLD gate."""

    def test_breakout_blank_type_promotes_to_gold(self):
        sig = _sig('PREMIUM', '')
        _apply_finbert_promotion([sig])
        assert sig['Quality'] == 'GOLD'
        assert sig['FinBERT_Promoted'] == 'PREMIUM→GOLD'

    def test_momentum_type_promotes_to_gold(self):
        sig = _sig('PREMIUM', 'Momentum')
        _apply_finbert_promotion([sig])
        assert sig['Quality'] == 'GOLD'

    def test_trend_confirm_type_promotes_to_gold(self):
        sig = _sig('PREMIUM', 'TREND_CONFIRM')
        _apply_finbert_promotion([sig])
        assert sig['Quality'] == 'GOLD'


class TestNoNativeGoldGateStaysCapped:
    """The actual fix: types with no structural GOLD tier must never be
    promoted past PREMIUM, no matter how strong the sentiment."""

    @pytest.mark.parametrize('sig_type', [
        'BOUNCE', 'CONTINUATION', 'SMA20_CROSS', 'PULLBACK', 'SLOW_GRIND',
    ])
    def test_type_capped_at_premium_despite_strong_sentiment(self, sig_type):
        sig = _sig('PREMIUM', sig_type, score=0.99, net=0.99, headlines=10)
        _apply_finbert_promotion([sig])
        assert sig['Quality'] == 'PREMIUM', (
            f"{sig_type} has no native GOLD gate — FinBERT must not promote it there")
        assert 'FinBERT_Promoted' not in sig, (
            'no promotion should be stamped when the promotion was refused')

    def test_bounce_high_to_premium_promotion_is_unaffected(self):
        """The fix only touches PREMIUM->GOLD. HIGH->PREMIUM for a
        no-native-GOLD type must still work — that tier IS reachable
        natively by these detectors, sentiment is just accelerating it."""
        sig = _sig('HIGH', 'BOUNCE', score=0.95, net=0.9, headlines=5)
        _apply_finbert_promotion([sig])
        assert sig['Quality'] == 'PREMIUM'
        assert sig['FinBERT_Promoted'] == 'HIGH→PREMIUM'


class TestConfigDriven:
    """The allowed-types set is read from config, not hardcoded twice."""

    def test_premium_to_gold_types_configured_in_config_py(self):
        from config import FINBERT_PROMOTION
        assert 'premium_to_gold_types' in FINBERT_PROMOTION
        types = FINBERT_PROMOTION['premium_to_gold_types']
        assert '' in types and 'Momentum' in types and 'TREND_CONFIRM' in types
        assert 'BOUNCE' not in types

    def test_missing_config_key_falls_back_to_the_safe_default(self, monkeypatch):
        """If premium_to_gold_types is ever removed from config.py, the
        fallback default in breakout_scanner.py must still exclude BOUNCE —
        never silently reopen the gate by defaulting to 'allow everything'."""
        import config
        stripped = dict(config.FINBERT_PROMOTION)
        stripped.pop('premium_to_gold_types', None)
        monkeypatch.setattr(config, 'FINBERT_PROMOTION', stripped)

        bounce_sig = _sig('PREMIUM', 'BOUNCE')
        breakout_sig = _sig('PREMIUM', '')
        _apply_finbert_promotion([bounce_sig, breakout_sig])

        assert bounce_sig['Quality'] == 'PREMIUM'
        assert breakout_sig['Quality'] == 'GOLD'


class TestUnaffectedBehaviorPreserved:
    """Sanity checks that the extraction didn't change unrelated behavior."""

    def test_bearish_sentiment_never_promotes(self):
        sig = _sig('PREMIUM', '', label='bearish', score=0.99, net=-0.9)
        _apply_finbert_promotion([sig])
        assert sig['Quality'] == 'PREMIUM'

    def test_below_score_threshold_does_not_promote(self):
        sig = _sig('PREMIUM', '', score=0.5, net=0.9, headlines=5)
        _apply_finbert_promotion([sig])
        assert sig['Quality'] == 'PREMIUM'

    def test_disabled_config_promotes_nothing(self, monkeypatch):
        import config
        monkeypatch.setattr(config, 'FINBERT_PROMOTION', {'enabled': False})
        sig = _sig('PREMIUM', '')
        _apply_finbert_promotion([sig])
        assert sig['Quality'] == 'PREMIUM'

    def test_gold_already_stays_gold(self):
        sig = _sig('GOLD', 'BOUNCE')
        _apply_finbert_promotion([sig])
        assert sig['Quality'] == 'GOLD'
