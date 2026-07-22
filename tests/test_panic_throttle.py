"""
Tests for the --panic-throttle ablation (CLAUDE.md §12 Task 4):
backtest_regime_compare._compute_panic_days() + simulate(panic_throttle=True).

AC-PT-01  Bear + high-vol days are flagged; calm bull days are not
AC-PT-02  simulate(panic_throttle=True) halves entry qty on a panic day
AC-PT-03  panic_throttle=False leaves sizing untouched (default path identity)
"""
import numpy as np
import pandas as pd

from backtest_regime_compare import _compute_panic_days, simulate


def _bars(closes, start='2020-01-02'):
    idx = pd.bdate_range(start=start, periods=len(closes))
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        'open': closes, 'high': closes * 1.005, 'low': closes * 0.995,
        'close': closes, 'volume': np.full(len(closes), 1e6),
    }, index=idx)


def _panic_spy(n_calm=250, n_crash=60, seed=11):
    """Long calm uptrend, then a violent decline below SMA200."""
    rng = np.random.default_rng(seed)
    calm  = 100 * np.cumprod(1 + rng.normal(0.0005, 0.004, n_calm))
    crash = calm[-1] * np.cumprod(1 + rng.normal(-0.01, 0.03, n_crash))
    return _bars(np.concatenate([calm, crash]))


def test_panic_days_flagged_in_bear_high_vol_only():
    spy = _panic_spy()
    panic = _compute_panic_days(spy)
    assert panic, "violent decline below SMA200 must flag panic days"
    calm_dates = set(pd.Timestamp(d).normalize() for d in spy.index[:250])
    assert not (panic & calm_dates), "calm uptrend days must not be panic"
    assert all(pd.Timestamp(d).normalize() in
               {pd.Timestamp(x).normalize() for x in spy.index[250:]}
               for d in panic)


def test_throttle_halves_qty_on_panic_day():
    spy = _panic_spy()
    panic = _compute_panic_days(spy)
    entry_day = sorted(panic)[5]
    # A flat, liquid stock so sizing is deterministic
    stock = _bars(np.full(len(spy), 50.0))
    hist = {'SPY': spy, 'TST': stock}
    sig = [{'date': entry_day, 'symbol': 'TST', 'action': 'BUY', 'price': 50.0,
            'entry_price': 50.0, 'stop_loss': 45.0, 'take_profit': 60.0,
            'quality': 'PREMIUM', 'mode': 'swing', 'type': 'BOUNCE',
            'regime': 'RED_MARKET', 'minervini_score': 0, 'is_momentum': False,
            'is_vcp': False, 'checks': {}, 'bear_macro': False,
            'rr': 2.0, 'win_prob': 50.0, 'sma_dist_pct': 5.0}]
    start = str(spy.index[0].date())
    end   = str(spy.index[-1].date())
    end_prices = {'TST': 50.0}

    base = simulate(sig, start, end, end_prices, hist, 100_000,
                    tp_as_trail=True, panic_throttle=False)
    thr  = simulate(sig, start, end, end_prices, hist, 100_000,
                    tp_as_trail=True, panic_throttle=True)
    q_base = base['trades'][0]['qty'] if base['trades'] else None
    q_thr  = thr['trades'][0]['qty'] if thr['trades'] else None
    assert q_base and q_thr, "both runs must enter the position"
    assert q_thr == q_base // 2 or abs(q_thr - q_base / 2) <= 1, \
        f"panic day entry must be half-sized (base={q_base}, throttled={q_thr})"


def test_default_path_identity():
    spy = _panic_spy()
    stock = _bars(np.full(len(spy), 50.0))
    hist = {'SPY': spy, 'TST': stock}
    entry_day = spy.index[100]                  # calm period
    sig = [{'date': entry_day, 'symbol': 'TST', 'action': 'BUY', 'price': 50.0,
            'entry_price': 50.0, 'stop_loss': 45.0, 'take_profit': 60.0,
            'quality': 'PREMIUM', 'mode': 'swing', 'type': 'BOUNCE',
            'regime': 'NORMAL', 'minervini_score': 0, 'is_momentum': False,
            'is_vcp': False, 'checks': {}, 'bear_macro': False,
            'rr': 2.0, 'win_prob': 50.0, 'sma_dist_pct': 5.0}]
    start, end = str(spy.index[0].date()), str(spy.index[-1].date())
    a = simulate(sig, start, end, {'TST': 50.0}, hist, 100_000,
                 tp_as_trail=True, panic_throttle=False)
    b = simulate(sig, start, end, {'TST': 50.0}, hist, 100_000,
                 tp_as_trail=True, panic_throttle=True)   # calm day → no effect
    assert a['trades'][0]['qty'] == b['trades'][0]['qty']
