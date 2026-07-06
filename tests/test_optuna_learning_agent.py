# Optuna-in-the-loop learning agent — pure-logic tests (no Optuna run).
# Covers the held-out improvement gate, weight-recommendation translation, and
# the ±3 over-cap flag that mirrors scanner._load_score_adjustments().

import json

import optuna_learning_agent as la


def _report(opt_val=1.50, base_val=1.20, weights=None):
    weights = weights or {'vol_confirm': 18, 'rs_ok': 16}
    return {
        'weights': weights,
        'per_fold': {
            '1': {'role': 'optimize', 'sharpe': 1.4},
            '4': {'role': 'validate', 'sharpe': opt_val},
        },
        'baseline_per_fold': {
            '1': {'role': 'optimize', 'sharpe': 1.1},
            '4': {'role': 'validate', 'sharpe': base_val},
        },
    }


def test_held_out_lift_picks_validate_fold():
    opt_s, base_s, lift = la.held_out_lift(_report(1.50, 1.20))
    assert opt_s == 1.50 and base_s == 1.20 and lift == 0.30


def test_held_out_lift_missing_baseline_returns_none_lift():
    r = _report()
    r['baseline_per_fold'] = {}
    _, base_s, lift = la.held_out_lift(r)
    assert base_s is None and lift is None


def test_gate_passes_only_above_threshold():
    assert la.gate_passes(0.30, 0.10) is True
    assert la.gate_passes(0.10, 0.10) is True       # boundary inclusive
    assert la.gate_passes(0.05, 0.10) is False
    assert la.gate_passes(None, 0.10) is False


def test_build_recommendations_only_changed_known_features():
    current = {'vol_confirm': 15, 'rs_ok': 16, 'unused': 5}
    report = _report(weights={'vol_confirm': 18, 'rs_ok': 16, 'ghost': 9})
    recs, over_cap = la.build_recommendations(report, current, 'now')
    feats = {r['feature'] for r in recs}
    assert feats == {'vol_confirm'}                 # rs_ok unchanged, ghost not in current
    rec = recs[0]
    assert rec['current_weight'] == 15 and rec['recommended_weight'] == 18


def test_over_cap_flags_moves_beyond_three():
    current = {'a': 10, 'b': 10}
    report = _report(weights={'a': 14, 'b': 12})    # a:+4 (>3), b:+2 (<=3)
    recs, over_cap = la.build_recommendations(report, current, 'now')
    assert {r['feature'] for r in recs} == {'a', 'b'}
    assert [f for f, _, _ in over_cap] == ['a']


def test_merge_adjustments_overwrites_recs_keeps_other_keys():
    existing = {'weight_recommendations': [{'feature': 'old', 'recommended_weight': 1}],
                'unrelated': True}
    recs = [{'feature': 'vol_confirm', 'current_weight': 15, 'recommended_weight': 18}]
    out = la.merge_adjustments(existing, recs, {'lift': 0.3})
    assert out['weight_recommendations'] == recs
    assert out['unrelated'] is True
    assert out['source'] == 'optuna_learning_agent' and out['meta']['lift'] == 0.3
    json.dumps(out)                                  # must stay serializable
