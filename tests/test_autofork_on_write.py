"""
A variant book must never be written to before it has been forked off control.

WHY THIS EXISTS
---------------
`ap.load()` returns `_empty()` — a plausible fresh $100k book — when the file does
not exist. For the control book that is correct: a new user starts empty. For a
VARIANT book it is a silent trap, because the A/B only means anything if both arms
start identical and diverge solely through swap policy.

Observed 2026-08-11, before this guard existed: the autoswap book had never been
forked, so selecting it in the dashboard rendered a convincing empty portfolio and
clicking Scan Signals wrote 2 fresh positions into it from $100k — while its
control held 14 positions and 62 processed files. Nothing errored. The comparison
was measuring starting state, not the treatment.

THE DANGEROUS DIRECTION IS THE OTHER ONE
----------------------------------------
`ensure_forked` firing a SECOND time, after the books have diverged, would clone
control over the top of the variant and destroy however many weeks of experiment
had accumulated — and it would look like a normal run. A re-forked book is by
construction indistinguishable from one that never diverged. That is what
`test_never_reforks_*` exists for, and why `_book_has_state` is deliberately broad.

ACs
---
1. A write to an unforked variant forks it from control first.
2. Forking is once-only: never re-fires after divergence, in either direction.
3. The control book is never a fork target.
4. Reads (`load`) never fork — a page render must not create a book in S3.
5. `reset()` empties a book without un-forking it (else the next write re-clones).
6. Both books end up stamped with the same fork date.
7. Every `_save`-reaching function routes through `_load_for_write` (structural).
"""
from __future__ import annotations

import inspect
import re

import pytest

import auto_portfolio as ap
import utils


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, '_is_cloud', lambda: False)
    monkeypatch.setattr(utils, '_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setattr(utils, 'PROJECT_ROOT', tmp_path)
    return tmp_path


def _seed_control(user_id='U1', *, n_pos=2):
    d = ap._empty()
    d['capital'] = 91_234.56
    d['positions'] = [
        {'symbol': f'S{i}', 'entry_price': 10.0 + i, 'stop': 9.0, 'target': 15.0,
         'shares': 10, 'cost': 100.0 + i, 'date_added': '2026-08-01',
         'quality': 'GOLD', 'mode': 'swing'}
        for i in range(n_pos)
    ]
    d['closed'] = [{'symbol': 'OLD', 'pnl': 42.0, 'date_added': '2026-07-01',
                    'date_closed': '2026-07-20'}]
    d['processed_files'] = ['signals_swing_20260801_090000.csv']
    ap._save(d, user_id=user_id, book='control')
    return d


# ── AC1: a write forks first ─────────────────────────────────────────────────

def test_write_to_unforked_variant_inherits_control(sandbox):
    """The reported bug: the variant must not start from $100k/0 positions."""
    _seed_control()
    assert ap.load('U1', 'autoswap')['positions'] == [], 'precondition: not forked'

    data = ap._load_for_write('U1', 'autoswap')

    assert [p['symbol'] for p in data['positions']] == ['S0', 'S1']
    assert data['capital'] == pytest.approx(91_234.56)
    assert data['processed_files'] == ['signals_swing_20260801_090000.csv']
    assert data['capital'] != ap.INITIAL_CAPITAL, \
        'a fresh $100k book is exactly the bug this guards'


def test_close_position_on_unforked_variant_acts_on_inherited_positions(sandbox):
    """A real mutating entry point, not just the seam helper."""
    _seed_control()
    out = ap.close_position('S0', 12.0, reason='manual', user_id='U1', book='autoswap')

    assert out.get('ok') is not False, out
    var = ap.load('U1', 'autoswap')
    assert [p['symbol'] for p in var['positions']] == ['S1']
    # control is untouched — the fork copied, it did not move
    assert [p['symbol'] for p in ap.load('U1', 'control')['positions']] == ['S0', 'S1']


def test_add_position_direct_forks_without_deadlocking(sandbox):
    """Regression: forking must happen OUTSIDE the book's fcntl lock.

    add_position_direct deliberately bypasses _save so that load/dedup/save
    happen in ONE lock acquisition. Forking from inside that lock calls _save,
    which grabs the same .lock file on a second descriptor — and fcntl.flock does
    not recurse, so the process blocks against itself forever. Caught only
    because the suite went from 22s to a hang; a deadlock has no traceback, so it
    needs an explicit timeout to fail rather than hang the runner.
    """
    import threading
    _seed_control()
    done, err = threading.Event(), []

    def _go():
        try:
            ap.add_position_direct(symbol='NEW', entry_price=10.0, stop=9.0,
                                   target=12.0, user_id='U1', book='autoswap')
        except Exception as exc:                            # pragma: no cover
            err.append(exc)
        finally:
            done.set()

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    assert done.wait(timeout=20), \
        'add_position_direct deadlocked — ensure_forked ran inside the file lock'
    assert not err, err

    var = ap.load('U1', 'autoswap')
    assert 'NEW' in [p['symbol'] for p in var['positions']]
    assert {'S0', 'S1'} <= {p['symbol'] for p in var['positions']}, \
        'the fork did not happen — the variant did not inherit control'


# ── AC2: once-only. The catastrophic direction. ──────────────────────────────

def test_never_reforks_after_the_variant_diverges(sandbox):
    """The single most important assertion in this file.

    A second fork would silently overwrite weeks of divergence with a copy of
    control, and the result would be indistinguishable from a book that never
    diverged.
    """
    _seed_control()
    ap.ensure_forked('U1', 'autoswap')

    # Variant diverges: loses a position. Control moves on independently.
    var = ap.load('U1', 'autoswap')
    var['positions'] = [p for p in var['positions'] if p['symbol'] != 'S0']
    var['closed'].append({'symbol': 'S0', 'pnl': -5.0, 'date_closed': '2026-08-05'})
    ap._save(var, user_id='U1', book='autoswap')

    ctl = ap.load('U1', 'control')
    ctl['positions'].append({'symbol': 'NEWCTL', 'entry_price': 5.0, 'stop': 4.0,
                             'target': 8.0, 'shares': 1, 'cost': 5.0,
                             'date_added': '2026-08-06', 'quality': 'GOLD'})
    ap._save(ctl, user_id='U1', book='control')

    again = ap._load_for_write('U1', 'autoswap')

    assert [p['symbol'] for p in again['positions']] == ['S1'], \
        'divergence was overwritten — the experiment would be silently destroyed'
    assert 'NEWCTL' not in [p['symbol'] for p in again['positions']], \
        "control's later position leaked into the variant"
    assert len(again['closed']) == 2


def test_never_reforks_a_variant_holding_only_a_fork_stamp(sandbox):
    """A book emptied down to its stamp still counts as used.

    This is the `reset()` case: no positions, no closed, no files — only the
    stamp separates it from a never-forked book.
    """
    _seed_control()
    ap.ensure_forked('U1', 'autoswap')
    ap.reset(user_id='U1', book='autoswap')

    after = ap._load_for_write('U1', 'autoswap')
    assert after['positions'] == [], 'reset was undone by a re-fork'


@pytest.mark.parametrize('field,value', [
    ('positions', [{'symbol': 'X', 'entry_price': 1.0, 'stop': 0.5, 'shares': 1,
                    'cost': 1.0, 'date_added': '2026-08-02'}]),
    ('closed', [{'symbol': 'Y', 'pnl': 1.0, 'date_closed': '2026-08-02'}]),
    ('processed_files', ['signals_swing_20260802_090000.csv']),
    ('fork', {'date': '2026-08-02', 'book': 'autoswap'}),
])
def test_any_trace_of_use_blocks_a_refork(sandbox, field, value):
    """`_book_has_state` is deliberately broad — each field alone must block."""
    _seed_control()
    var = ap._empty()
    var[field] = value
    ap._save(var, user_id='U1', book='autoswap')

    ap.ensure_forked('U1', 'autoswap')

    after = ap.load('U1', 'autoswap')
    assert after.get(field) == value, f'{field} alone failed to block a re-fork'
    assert not after['positions'] or after['positions'] == value, \
        "control's positions were cloned over a book already in use"


# ── AC3/AC4: control is never a target; reads never fork ─────────────────────

def test_control_is_never_a_fork_target(sandbox):
    _seed_control()
    before = ap.load('U1', 'control')
    ap.ensure_forked('U1', 'control')
    after = ap.load('U1', 'control')

    assert after['positions'] == before['positions']
    assert after.get('fork') is None, \
        'ensure_forked stamped control without any variant being created'


def test_control_is_never_a_fork_target_even_when_empty(sandbox):
    """The case that actually exercises the suffix guard.

    With a populated control, `_book_has_state` short-circuits first and hides a
    missing control check. An EMPTY control has no state, so only the
    `name == DEFAULT_BOOK` guard stands between it and being cloned onto itself
    and stamped — which would make every fresh user's control book claim to be a
    forked variant, and give book_compare a since-date that means nothing.
    """
    import os
    path = utils._to_local_abs(ap._portfolio_path_for('U3', 'control'))

    ap.ensure_forked('U3', 'control')

    assert not os.path.exists(path), \
        'ensure_forked created a control book for a user who has none'
    assert not ap.is_forked(ap.load('U3', 'control'))


def test_plain_load_never_creates_a_book(sandbox):
    """A page render or API GET must not write to S3."""
    _seed_control()
    path = utils._to_local_abs(ap._portfolio_path_for('U1', 'autoswap'))

    data = ap.load('U1', 'autoswap')

    assert data['positions'] == []
    import os
    assert not os.path.exists(path), 'load() created the variant book file'


def test_suggest_swaps_is_read_only(sandbox):
    """suggest_swaps runs on the advisory path and must not fork."""
    _seed_control()
    import os
    path = utils._to_local_abs(ap._portfolio_path_for('U1', 'autoswap'))
    ap.suggest_swaps(user_id='U1', book='autoswap', notify=False)
    assert not os.path.exists(path)


# ── AC5/AC6: reset semantics and stamping ────────────────────────────────────

def test_reset_keeps_the_fork_stamp(sandbox):
    """Without this, reset on a variant means 'restore control's positions'."""
    _seed_control()
    ap.ensure_forked('U1', 'autoswap')
    stamp = ap.load('U1', 'autoswap')['fork']

    out = ap.reset(user_id='U1', book='autoswap')

    assert out['positions'] == []
    assert out.get('fork') == stamp, 'fork stamp dropped — next write would re-clone'


def test_recalculate_does_not_resurrect_control(sandbox, monkeypatch):
    """reset+rescan on a variant must not come back holding control's book."""
    _seed_control()
    ap.ensure_forked('U1', 'autoswap')
    monkeypatch.setattr(ap, 'scan_and_add',
                        lambda **kw: {'added': 0, 'positions': []})

    ap.recalculate(user_id='U1', book='autoswap')

    after = ap.load('U1', 'autoswap')
    assert [p['symbol'] for p in after['positions']] == [], \
        "control's positions were restored into the variant by recalculate"


def test_both_books_share_one_fork_date(sandbox):
    _seed_control()
    ap.ensure_forked('U1', 'autoswap')

    ctl = ap.load('U1', 'control')
    var = ap.load('U1', 'autoswap')
    assert ctl['fork']['date'] == var['fork']['date']
    assert ctl['fork']['book'] == 'control' and var['fork']['book'] == 'autoswap'
    assert ap.is_forked(ctl) and ap.is_forked(var)


def test_empty_control_forks_to_an_empty_stamped_variant(sandbox):
    """A genuinely new user: both arms start blank and fill from the same scans."""
    var = ap.ensure_forked('U2', 'autoswap')

    assert var['positions'] == []
    assert var['capital'] == ap.INITIAL_CAPITAL
    assert ap.is_forked(var), 'book_compare needs a since-date on both sides'
    assert ap.is_forked(ap.load('U2', 'control'))


def test_fork_does_not_carry_swap_state(sandbox):
    """A cloned advice stamp would make the variant's first scan think it acted."""
    ctl = _seed_control()
    ctl['last_swap'] = {'close_symbol': 'A', 'open_symbol': 'B'}
    ctl['swap_advice'] = {'date': '2026-08-01', 'sends': 2, 'executed': 3}
    ap._save(ctl, user_id='U1', book='control')

    var = ap.ensure_forked('U1', 'autoswap')

    assert 'last_swap' not in var
    assert var['swap_advice'] == {}


# ── AC7: structural — no mutating function may bypass the seam ───────────────

# Functions that legitimately call plain load() while writing, with the reason.
_ALLOWED_PLAIN_LOAD = {
    'reset':          'intends an empty book; re-forking would restore control',
    'ensure_forked':  'is the fork itself — must read raw state',
    '_load_for_write': 'is the seam',
}


def test_every_saving_function_routes_through_the_seam():
    """A new mutating function that forgets the seam is exactly how this bug
    returns. Structural rather than behavioural, in the style of
    tests/test_crontab_parity.py's SEMANTIC_FLAGS check.
    """
    src = inspect.getsource(ap)
    offenders = []
    for name, fn in vars(ap).items():
        if not inspect.isfunction(fn) or fn.__module__ != ap.__name__:
            continue
        if name in _ALLOWED_PLAIN_LOAD:
            continue
        try:
            body = inspect.getsource(fn)
        except OSError:                                     # pragma: no cover
            continue
        writes = re.search(r'\b_save\(', body) or re.search(r'\bsave_json\(', body)
        if not writes:
            continue
        # It writes, so it must fork first — either via the seam, or by calling
        # ensure_forked() explicitly. add_position_direct does the latter because
        # it holds the book's fcntl lock across its load/dedup/save and
        # ensure_forked's own _save would deadlock against that same lock file.
        if re.search(r'\b(_load_for_write|ensure_forked)\(', body):
            continue
        if re.search(r'\bload\(\s*(user_id|data)', body):
            offenders.append(name)

    assert not offenders, (
        f"these functions write a book but read it with plain load(): {offenders}. "
        f"Use _load_for_write() or add an explicit exemption with a reason."
    )
    assert '_load_for_write' in src


def test_seam_is_actually_used_somewhere():
    """Guards the test above from passing vacuously if the seam is renamed."""
    src = inspect.getsource(ap)
    assert src.count('_load_for_write(') >= 8, \
        'the seam is barely used — did the call sites get reverted?'
