"""
Tests for the --live-tiebreak A/B (CLAUDE.md §12): backtest_regime_compare._pooled_cap
reproduces auto_portfolio.py's live Dist tiebreak when live_tiebreak=True.

The live semantic and the validated backtest semantic are OPPOSITE:
  live      — Dist DESCENDING, clip at 25 (parabolic ties at top), then Vol desc
  backtest  — Dist ASCENDING, >25 pushed to a back bucket, no Vol key

AC-LT-01  With a binding cap, live_tiebreak admits the MOST-extended names while
          the default admits the CLOSEST-to-trend — disjoint sets
AC-LT-02  A parabolic (>25%) name is admitted first under live_tiebreak (clip→tie
          at top) but LAST under the default (back-bucket)
AC-LT-03  Vol breaks ties under live_tiebreak when Dist is equal (higher Vol first)
AC-LT-04  Default path (live_tiebreak=False) is unchanged
"""
import pandas as pd

from backtest_regime_compare import _pooled_cap


def _sig(sym, dist, date='2024-06-03', vol=1.0, **kw):
    base = {'symbol': sym, 'date': pd.Timestamp(date), 'type': 'BOUNCE',
            'quality': 'PREMIUM', 'win_prob': 50.0, 'rr': 2.5,
            'sma_dist_pct': dist, 'vol': vol}
    base.update(kw)
    return base


def _syms(pool):
    return [s['symbol'] for s in pool]


def test_live_vs_default_disjoint_with_binding_cap():
    sigs = [_sig('CLOSE', 5), _sig('MID', 15), _sig('EXT', 24), _sig('PARA', 40)]
    live = _syms(_pooled_cap([dict(s) for s in sigs], max_per_day=2, live_tiebreak=True))
    dflt = _syms(_pooled_cap([dict(s) for s in sigs], max_per_day=2))
    assert live == ['PARA', 'EXT']     # most extended first (clip → PARA ties top)
    assert dflt == ['CLOSE', 'MID']    # closest to trend first
    assert set(live).isdisjoint(dflt)


def test_parabolic_first_live_last_default():
    sigs = [_sig('CLEAN', 10), _sig('PARA', 40)]
    live = _syms(_pooled_cap([dict(s) for s in sigs], max_per_day=2, live_tiebreak=True))
    dflt = _syms(_pooled_cap([dict(s) for s in sigs], max_per_day=2))
    assert live[0] == 'PARA'           # clipped to 25, ties at top, wins
    assert dflt[0] == 'CLEAN' and dflt[-1] == 'PARA'   # back-bucketed


def test_vol_breaks_dist_ties_under_live():
    # Both clip to 25 (Dist 30 and 26 → both 25), so Vol decides under live
    sigs = [_sig('LOWVOL', 30, vol=1.2), _sig('HIVOL', 26, vol=3.0)]
    live = _syms(_pooled_cap([dict(s) for s in sigs], max_per_day=1, live_tiebreak=True))
    assert live == ['HIVOL']


def test_default_path_unchanged():
    sigs = [_sig('FAR', 20), _sig('NEAR', 5)]
    dflt = _syms(_pooled_cap([dict(s) for s in sigs], max_per_day=1))
    assert dflt == ['NEAR']            # ascending: closest wins
