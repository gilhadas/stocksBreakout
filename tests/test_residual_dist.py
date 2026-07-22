"""
Tests for the --residual-dist ablation (CLAUDE.md §12 Task 3):
backtest_regime_compare._stamp_residual_momentum() + _pooled_cap(residual_dist=True).

AC-RD-01  A pure-beta stock (returns = β × SPY) gets resid_mom ≈ 0
AC-RD-02  An idiosyncratic mover (flat SPY) keeps its own return as resid_mom
AC-RD-03  residual_dist=True flips a tie: idiosyncratic winner ranks above the
          pure-beta name that raw-extension ranking would not distinguish
AC-RD-04  Insufficient history → resid_mom stamped 0 (neutral), no crash
AC-RD-05  Default path (residual_dist=False) is unchanged: sma_dist_pct
          ascending tiebreak still decides
"""
import numpy as np
import pandas as pd

from backtest_regime_compare import _stamp_residual_momentum, _pooled_cap


def _df_from_rets(rets, start_px=100.0, start='2024-01-02'):
    rets = np.asarray(rets, dtype=float)
    closes = start_px * np.cumprod(1 + rets)
    idx = pd.bdate_range(start=start, periods=len(closes))
    return pd.DataFrame({
        'open': closes, 'high': closes * 1.01, 'low': closes * 0.99,
        'close': closes, 'volume': np.full(len(closes), 1e6),
    }, index=idx)


def _sig(sym, date, **kw):
    base = {'symbol': sym, 'date': pd.Timestamp(date), 'type': 'BOUNCE',
            'quality': 'PREMIUM', 'win_prob': 50.0, 'rr': 2.5}
    base.update(kw)
    return base


def _mkt(n=120, seed=7):
    rng = np.random.default_rng(seed)
    return rng.normal(0.001, 0.01, n)


def test_pure_beta_stock_resid_near_zero():
    spy_r = _mkt()
    hist = {'SPY': _df_from_rets(spy_r), 'BETA2': _df_from_rets(2.0 * spy_r)}
    d = hist['SPY'].index[-1]
    sigs = [_sig('BETA2', d)]
    _stamp_residual_momentum(sigs, hist)
    assert abs(sigs[0]['resid_mom']) < 1.5   # ≈0 vs raw 15d return of ~2× SPY


def test_idiosyncratic_mover_keeps_return():
    n = 120
    spy_r = np.zeros(n)                       # flat market
    own_r = np.full(n, 0.005)                 # steady +0.5%/day idiosyncratic
    hist = {'SPY': _df_from_rets(spy_r), 'IDIO': _df_from_rets(own_r)}
    d = hist['SPY'].index[-1]
    sigs = [_sig('IDIO', d)]
    _stamp_residual_momentum(sigs, hist)
    # 15d own return ≈ (1.005^15 − 1) ≈ 7.8%; beta≈0 vs flat SPY
    assert sigs[0]['resid_mom'] > 5.0


def test_residual_tiebreak_flips_ranking():
    spy_r = np.concatenate([_mkt(105), np.full(15, 0.01)])   # SPY rebounding
    hist = {
        'SPY':   _df_from_rets(spy_r),
        'BETA2': _df_from_rets(2.0 * spy_r),                 # pure beta ×2
        'IDIO':  _df_from_rets(np.concatenate([_mkt(105, seed=9), np.full(15, 0.008)])),
    }
    d = hist['SPY'].index[-1]
    sigs = [_sig('BETA2', d, sma_dist_pct=10.0), _sig('IDIO', d, sma_dist_pct=10.0)]
    _stamp_residual_momentum(sigs, hist)
    assert sigs[1]['resid_mom'] > sigs[0]['resid_mom']       # idio > pure beta
    pooled = _pooled_cap(sigs, max_per_day=1, residual_dist=True)
    assert pooled[0]['symbol'] == 'IDIO'


def test_insufficient_history_neutral():
    hist = {'SPY': _df_from_rets(_mkt(30)), 'NEWIPO': _df_from_rets(_mkt(30, seed=3))}
    d = hist['SPY'].index[-1]
    sigs = [_sig('NEWIPO', d)]
    _stamp_residual_momentum(sigs, hist)
    assert sigs[0]['resid_mom'] == 0.0


def test_default_path_unchanged():
    d = pd.Timestamp('2024-06-03')
    sigs = [_sig('FAR', d, sma_dist_pct=20.0), _sig('NEAR', d, sma_dist_pct=5.0)]
    pooled = _pooled_cap(sigs, max_per_day=1)                # residual_dist off
    assert pooled[0]['symbol'] == 'NEAR'                     # asc: closer wins
