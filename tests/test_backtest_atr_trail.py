"""
Regression tests for the always-on ATR trail branch in
backtest_regime_compare.simulate() (champion exit, restored 2026-07-02).

The original implementation was lost in commit 240f96c (the commit message
claims the branch was added, but the committed file never contained it).
These tests pin the recovered semantics so they can't silently vanish again:

AC-BTTRAIL-01  CLOSE-based trigger: an intraday low below the trail does NOT
               exit while the close holds above it (whipsaw immunity — the
               core difference from the post-TP lo-based trail)
AC-BTTRAIL-02  a close below the ratcheted trail exits at the trail level
               with reason TrailStop
AC-BTTRAIL-03  fixed stop is the floor: immediate breakdown exits at the
               original stop with reason StopLoss
AC-BTTRAIL-04  atr_trail_always takes priority over tp_as_trail=True
"""
import numpy as np
import pandas as pd
import pytest

from backtest_regime_compare import simulate


def _bars(closes, spread=1.0, start='2024-01-02'):
    """Business-day OHLC bars with |high-low| = 2*spread around the close."""
    idx = pd.bdate_range(start=start, periods=len(closes))
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        'open':  closes,
        'high':  closes + spread,
        'low':   closes - spread,
        'close': closes,
        'volume': np.full(len(closes), 1_000_000.0),
    }, index=idx)


def _run(sym_closes, entry_idx, stop, sym_lows=None, **sim_kw):
    """Run simulate() on one synthetic symbol; entry at bar entry_idx."""
    df = _bars(sym_closes)
    if sym_lows is not None:
        for i, lo in sym_lows.items():
            df.iloc[i, df.columns.get_loc('low')] = lo
    spy = _bars(np.full(len(sym_closes), 500.0))
    historical = {'XYZ': df, 'SPY': spy}
    entry_date = df.index[entry_idx].strftime('%Y-%m-%d')
    signals = [{
        'date': entry_date, 'symbol': 'XYZ',
        'price': float(df['close'].iloc[entry_idx]), 'stop_loss': stop,
        'take_profit': 999.0,   # unreachable — trail must do the exiting
        'quality': 'PREMIUM', 'regime': 'NORMAL', 'type': 'BOUNCE',
    }]
    end_prices = {'XYZ': float(df['close'].iloc[-1])}
    start, end = entry_date, df.index[-1].strftime('%Y-%m-%d')
    return simulate(signals, start, end, end_prices, historical,
                    capital=100_000, label='test', **sim_kw)


class TestAlwaysOnTrail:
    def test_intraday_dip_below_trail_does_not_exit(self):
        # 20 warmup bars @100, entry, ramp to 130, then a bar whose LOW crashes
        # to 100 (far below any trail) but whose CLOSE holds at 129, then flat.
        closes = [100.0] * 20 + [100, 105, 110, 115, 120, 125, 130, 129, 129, 129]
        rpt = _run(closes, entry_idx=20, stop=95.0,
                   sym_lows={27: 100.0},   # the 129-close bar dips to 100 intraday
                   tp_as_trail=False, atr_trail_always=True)
        trades = rpt['trades']
        assert len(trades) == 1
        # survived the dip → still open at sim end (or exited later, but NOT
        # on the dip bar)
        dip_date = _bars(closes).index[27].strftime('%Y-%m-%d')
        assert trades[0]['exit_date'] != dip_date
        assert trades[0]['reason'] == 'SimEnd'

    def test_close_below_trail_exits_at_trail_level(self):
        # ramp to 130 then close crashes to 100 → close-cross exits at the
        # ratcheted trail (well above 100), reason TrailStop
        closes = [100.0] * 20 + [100, 105, 110, 115, 120, 125, 130, 100, 100, 100]
        rpt = _run(closes, entry_idx=20, stop=95.0,
                   tp_as_trail=False, atr_trail_always=True)
        t = rpt['trades'][0]
        assert t['reason'] == 'TrailStop'
        # trail had ratcheted from the 130 peak: exit far above the crash close
        # and above entry (profit locked)
        assert t['exit'] > 115.0
        assert t['pnl'] > 0

    def test_fixed_stop_is_floor_on_immediate_breakdown(self):
        # crashes the day after entry. Bars have TR=2 → ATR=2 → day-1 trail
        # arms at 100 − 2×2 = 96. A fixed stop ABOVE that (98) must act as
        # the floor: exit at 98, reason StopLoss (not at the lower trail).
        closes = [100.0] * 20 + [100, 80, 80, 80]
        rpt = _run(closes, entry_idx=20, stop=98.0,
                   tp_as_trail=False, atr_trail_always=True)
        t = rpt['trades'][0]
        assert t['reason'] == 'StopLoss'
        assert t['exit'] == pytest.approx(98.0, abs=0.01)

    def test_day1_trail_beats_lower_fixed_stop(self):
        # same crash, but fixed stop (95) BELOW the day-1 trail (96): the
        # armed trail exits first, at 96, reason TrailStop — "trail activates
        # from entry bar 1" (mirrors live refresh the same evening)
        closes = [100.0] * 20 + [100, 80, 80, 80]
        rpt = _run(closes, entry_idx=20, stop=95.0,
                   tp_as_trail=False, atr_trail_always=True)
        t = rpt['trades'][0]
        assert t['reason'] == 'TrailStop'
        assert t['exit'] == pytest.approx(96.0, abs=0.05)

    def test_priority_over_tp_as_trail(self):
        # tp_as_trail=True AND atr_trail_always=True → always-on branch wins:
        # results identical to the tp_as_trail=False run
        closes = [100.0] * 20 + [100, 105, 110, 115, 120, 125, 130, 100, 100, 100]
        r1 = _run(closes, entry_idx=20, stop=95.0,
                  tp_as_trail=False, atr_trail_always=True)
        r2 = _run(closes, entry_idx=20, stop=95.0,
                  tp_as_trail=True, atr_trail_always=True)
        assert r1['trades'][0]['exit'] == r2['trades'][0]['exit']
        assert r1['trades'][0]['reason'] == r2['trades'][0]['reason']
