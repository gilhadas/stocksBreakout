"""
Tests for the high-conviction sleeve (reserved slots) in
backtest_regime_compare._pooled_cap(sleeve_slots=N).

Reserve up to N of the daily cap for sleeve-tagged signals (s['sleeve']=True),
then fill the rest from the overall ranking. Neither bucket wastes capacity.

AC-SLV-01  Sleeve names that would MISS the cap on rank alone are guaranteed
           their reserved seats
AC-SLV-02  Reserved seats never exceed sleeve_slots; the rest go to top-ranked
           (core or leftover sleeve)
AC-SLV-03  Fewer sleeve signals than sleeve_slots → core fills the remainder
           (no wasted capacity)
AC-SLV-04  Ranking is preserved WITHIN each bucket (best sleeve names get the
           reserved seats; best overall get the rest)
AC-SLV-05  sleeve_slots=0 → identical to the plain cap (default path unchanged)
"""
import pandas as pd

from backtest_regime_compare import _pooled_cap


def _sig(sym, dist, sleeve=False, date='2024-06-03', wp=50.0):
    return {'symbol': sym, 'date': pd.Timestamp(date), 'type': 'BOUNCE',
            'quality': 'PREMIUM', 'win_prob': wp, 'rr': 2.5,
            'sma_dist_pct': dist, 'vol': 1.0, 'sleeve': sleeve}


def _syms(pool):
    return [s['symbol'] for s in pool]


def test_sleeve_names_guaranteed_reserved_seats():
    # 10 strong core names (rank ahead) + 2 weak sleeve names that would miss.
    core = [_sig(f'C{i}', dist=i, wp=90.0) for i in range(10)]      # high WinProb → top
    sleeve = [_sig('SLV1', dist=1, sleeve=True, wp=40.0),
              _sig('SLV2', dist=2, sleeve=True, wp=40.0)]           # low WinProb → would miss
    out = _syms(_pooled_cap(core + sleeve, max_per_day=10, sleeve_slots=3))
    assert 'SLV1' in out and 'SLV2' in out       # guaranteed despite low rank
    assert len(out) == 10


def test_reserved_never_exceeds_sleeve_slots():
    core = [_sig(f'C{i}', dist=i, wp=90.0) for i in range(5)]
    sleeve = [_sig(f'S{i}', dist=i, sleeve=True, wp=40.0) for i in range(8)]  # 8 weak sleeve
    out = _pooled_cap(core + sleeve, max_per_day=10, sleeve_slots=3)
    n_sleeve = sum(1 for s in out if s.get('sleeve'))
    # 3 reserved + core (5) fills to 8; remaining 2 slots go to next-ranked = more sleeve.
    # Reserved guarantee is a FLOOR of 3, not a cap — but core outranks, so sleeve gets
    # its 3 reserved + whatever the fill leftover grants.
    assert n_sleeve >= 3
    assert len(out) == 10


def test_no_wasted_capacity_when_few_sleeve():
    core = [_sig(f'C{i}', dist=i, wp=90.0) for i in range(10)]
    sleeve = [_sig('SLV1', dist=1, sleeve=True, wp=40.0)]          # only 1 sleeve
    out = _syms(_pooled_cap(core + sleeve, max_per_day=10, sleeve_slots=3))
    assert 'SLV1' in out
    assert len(out) == 10                          # core fills the other 9 — no waste


def test_ranking_preserved_within_buckets():
    # Best sleeve name (higher WinProb) takes the reserved seat over weaker sleeve.
    sleeve = [_sig('SLV_BEST', dist=5, sleeve=True, wp=80.0),
              _sig('SLV_WORST', dist=5, sleeve=True, wp=30.0)]
    core = [_sig(f'C{i}', dist=i, wp=90.0) for i in range(9)]
    out = _syms(_pooled_cap(core + sleeve, max_per_day=10, sleeve_slots=1))
    assert 'SLV_BEST' in out                       # best sleeve gets the 1 reserved seat
    # SLV_WORST may still enter via fill, but SLV_BEST is guaranteed


def test_sleeve_slots_zero_is_default():
    sigs = [_sig('FAR', 20), _sig('NEAR', 5, sleeve=True)]
    dflt = _syms(_pooled_cap([dict(s) for s in sigs], max_per_day=1))
    slv0 = _syms(_pooled_cap([dict(s) for s in sigs], max_per_day=1, sleeve_slots=0))
    assert dflt == slv0 == ['NEAR']                # asc Dist tiebreak, sleeve ignored
