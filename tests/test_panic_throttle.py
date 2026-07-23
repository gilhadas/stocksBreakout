"""
Tests for the --panic-throttle ablation (CLAUDE.md §12 Task 4):
backtest_regime_compare._compute_panic_days() + simulate(panic_throttle=True).

AC-PT-01  Bear + high-vol days are flagged; calm bull days are not
AC-PT-02  simulate(panic_throttle=True) halves entry qty on a panic day
AC-PT-03  panic_throttle=False leaves sizing untouched (default path identity)

Tests for the --panic-throttle-bear-only variant (§12 Task 4b):
simulate(panic_throttle=True, panic_bear_only=True) — restricts panic days to
a SUSTAINED bear (SPY >= 15 consecutive days below its own SMA200), mirroring
BOUNCE_BEAR_GATE's validated distinction.

AC-PT-04  A brief dip (<15 consecutive days below SMA200, high vol) is
          flagged panic by the base lever but NOT throttled under bear-only
AC-PT-05  A sustained dip (>=15 consecutive days below SMA200) is throttled
          under both the base lever and bear-only
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


def _brief_dip_spy(n_calm=260, n_dip=6, n_recover=30):
    """Long calm uptrend, a brief (<15-trading-day) sharp decline below
    SMA200, then a fast recovery. Deterministic (no RNG) — models the
    April-2025 tariff dip (9-14 consecutive days) that panic_bear_only is
    designed to exclude while still being flagged panic by the unconditioned
    base lever. Parameters tuned (max consecutive days below SMA200 while
    panic-flagged = 4, well clear of the 15-day threshold) rather than
    derived analytically — SMA200 lag makes the exact figure hard to predict
    by hand."""
    calm = 100 * np.cumprod(np.full(n_calm, 1.0004))
    dip_rets = np.tile([-0.04, 0.012], n_dip // 2)[:n_dip]
    dip = calm[-1] * np.cumprod(1 + dip_rets)
    recover = dip[-1] * np.cumprod(1 + np.full(n_recover, 0.05))
    return _bars(np.concatenate([calm, dip, recover]))


def _consec_below_sma200(spy_df):
    """Same computation as simulate()'s spy_consec_below precompute — used
    here to pick test days by property instead of a magic index."""
    sma200 = spy_df['close'].rolling(200).mean()
    out, count = {}, 0
    for dt, cl, sm in zip(spy_df.index, spy_df['close'], sma200):
        count = count + 1 if (not pd.isna(sm) and cl < sm) else 0
        out[pd.Timestamp(dt).normalize()] = count
    return out


def _sig(entry_day, regime='RED_MARKET'):
    return [{'date': entry_day, 'symbol': 'TST', 'action': 'BUY', 'price': 50.0,
             'entry_price': 50.0, 'stop_loss': 45.0, 'take_profit': 60.0,
             'quality': 'PREMIUM', 'mode': 'swing', 'type': 'BOUNCE',
             'regime': regime, 'minervini_score': 0, 'is_momentum': False,
             'is_vcp': False, 'checks': {}, 'bear_macro': False,
             'rr': 2.0, 'win_prob': 50.0, 'sma_dist_pct': 5.0}]


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


def test_panic_bear_only_excludes_brief_dip():
    spy = _brief_dip_spy()
    panic = _compute_panic_days(spy)
    assert panic, "the brief sharp decline must still flag as panic (bear+high-vol)"

    consec = _consec_below_sma200(spy)
    assert all(consec.get(d, 0) < 15 for d in panic), \
        "fixture must be a brief dip (<15 consecutive days) for this test to be meaningful"

    entry_day = sorted(panic)[0]
    stock = _bars(np.full(len(spy), 50.0))
    hist = {'SPY': spy, 'TST': stock}
    sig = _sig(entry_day)
    start, end = str(spy.index[0].date()), str(spy.index[-1].date())
    end_prices = {'TST': 50.0}

    unthrottled = simulate(sig, start, end, end_prices, hist, 100_000,
                           tp_as_trail=True, panic_throttle=False)
    base        = simulate(sig, start, end, end_prices, hist, 100_000,
                           tp_as_trail=True, panic_throttle=True, panic_bear_only=False)
    bear_only   = simulate(sig, start, end, end_prices, hist, 100_000,
                           tp_as_trail=True, panic_throttle=True, panic_bear_only=True)
    q_full   = unthrottled['trades'][0]['qty'] if unthrottled['trades'] else None
    q_base   = base['trades'][0]['qty'] if base['trades'] else None
    q_bear   = bear_only['trades'][0]['qty'] if bear_only['trades'] else None
    assert q_full and q_base and q_bear, "all three runs must enter the position"

    assert q_base == q_full // 2 or abs(q_base - q_full / 2) <= 1, \
        f"base panic_throttle must still halve size on the brief dip (full={q_full}, base={q_base})"
    assert q_bear == q_full, \
        "brief dip must NOT be throttled under bear-only — sizing should match the " \
        f"unthrottled run (full={q_full}, bear_only={q_bear})"


def test_panic_bear_only_keeps_sustained_dip():
    spy = _panic_spy()  # 60-day violent decline — deep into it, consec exceeds 15
    panic = _compute_panic_days(spy)
    consec = _consec_below_sma200(spy)
    sustained_panic_days = sorted(d for d in panic if consec.get(d, 0) >= 15)
    assert sustained_panic_days, \
        "fixture must reach >=15 consecutive days below SMA200 for this test to be meaningful"
    entry_day = sustained_panic_days[0]

    stock = _bars(np.full(len(spy), 50.0))
    hist = {'SPY': spy, 'TST': stock}
    sig = _sig(entry_day)
    start, end = str(spy.index[0].date()), str(spy.index[-1].date())
    end_prices = {'TST': 50.0}

    base      = simulate(sig, start, end, end_prices, hist, 100_000,
                         tp_as_trail=True, panic_throttle=True, panic_bear_only=False)
    bear_only = simulate(sig, start, end, end_prices, hist, 100_000,
                         tp_as_trail=True, panic_throttle=True, panic_bear_only=True)
    q_base = base['trades'][0]['qty'] if base['trades'] else None
    q_bear = bear_only['trades'][0]['qty'] if bear_only['trades'] else None
    assert q_base and q_bear, "both runs must enter the position"
    assert q_bear == q_base, \
        f"sustained dip must be throttled identically under both (base={q_base}, bear_only={q_bear})"
