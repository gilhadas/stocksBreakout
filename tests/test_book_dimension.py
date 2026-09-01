"""
A book variant must be a filename, and the default must not move.

WHY THIS EXISTS
---------------
Portfolios are FILES keyed by user_id in the path — the users DB holds identity
only, with no portfolio table (CLAUDE.md §23.1). Adding a second book variant for
the live control-vs-autoswap A/B therefore means a second filename in the same
per-user directory, resolved by `_portfolio_path_for`.

That function is the single seam the whole feature rests on, which makes it the
single point where the feature can silently destroy the existing deployment. The
control book MUST keep resolving to the exact path it resolved to before books
existed: two live users' books, plus every backup, cron job and S3 key, are
addressed by that literal name. A change there is not a regression that shows up
in a test run — it is a production book that stops being found.

The unknown-name test matters for the same reason. Silently defaulting on a typo
would create a third book that accumulates real trades nothing else ever reads.
"""
from __future__ import annotations

import pytest

import auto_portfolio as ap


# ── The backward-compatibility guard ─────────────────────────────────────────

def test_default_book_resolves_to_the_pre_book_path():
    """The control book's path is byte-identical to the pre-A/B behaviour."""
    assert ap._portfolio_path_for() == 'scanner_output/portfolio/auto_portfolio.json'
    assert ap._portfolio_path_for('U1') == 'scanner_output/portfolio/U1/auto_portfolio.json'


def test_omitting_book_is_the_same_as_asking_for_control():
    assert ap._portfolio_path_for('U1') == ap._portfolio_path_for('U1', 'control')
    assert ap._portfolio_path_for(None) == ap._portfolio_path_for(None, 'control')


def test_control_suffix_is_empty():
    """If this ever becomes non-empty, every existing book is orphaned."""
    assert ap.BOOKS[ap.DEFAULT_BOOK]['suffix'] == ''
    assert ap.DEFAULT_BOOK == 'control'


# ── The variant ──────────────────────────────────────────────────────────────

def test_variant_book_is_a_distinct_file_in_the_same_directory():
    ctrl = ap._portfolio_path_for('U1', 'control')
    var = ap._portfolio_path_for('U1', 'autoswap')
    assert ctrl != var
    assert var == 'scanner_output/portfolio/U1/auto_portfolio_autoswap.json'
    assert ctrl.rsplit('/', 1)[0] == var.rsplit('/', 1)[0]


def test_variant_works_without_a_user_id_too():
    """Background agents call load()/save() with no user_id."""
    assert ap._portfolio_path_for(None, 'autoswap') == \
        'scanner_output/portfolio/auto_portfolio_autoswap.json'


def test_every_configured_book_maps_to_a_unique_path():
    paths = {b: ap._portfolio_path_for('U1', b) for b in ap.BOOKS}
    assert len(set(paths.values())) == len(paths), f"two books share a file: {paths}"


def test_all_book_paths_end_in_json():
    for b in ap.BOOKS:
        assert ap._portfolio_path_for('U1', b).endswith('.json')


# ── Validation ───────────────────────────────────────────────────────────────

def test_unknown_book_raises_instead_of_creating_a_third_book():
    with pytest.raises(ValueError) as exc:
        ap._portfolio_path_for('U1', 'autoswapp')
    assert 'autoswapp' in str(exc.value)


def test_unknown_book_raises_on_load_and_save():
    with pytest.raises(ValueError):
        ap.load(user_id='U1', book='nope')
    with pytest.raises(ValueError):
        ap._save({}, user_id='U1', book='nope')


def test_book_cfg_exposes_the_policy_fields_the_scan_stage_reads():
    for name, cfg in ap.BOOKS.items():
        assert set(cfg) >= {'suffix', 'label', 'auto_swap', 'max_swaps_per_day'}, name
        assert isinstance(cfg['auto_swap'], bool)
        assert isinstance(cfg['max_swaps_per_day'], int)


def test_control_never_auto_swaps_and_autoswap_does():
    """The registry IS the experiment's treatment assignment."""
    assert ap.BOOKS['control']['auto_swap'] is False
    assert ap.BOOKS['control']['max_swaps_per_day'] == 0
    assert ap.BOOKS['autoswap']['auto_swap'] is True
    assert ap.BOOKS['autoswap']['max_swaps_per_day'] > 0


# ── S3 mirroring ─────────────────────────────────────────────────────────────

def test_variant_path_mirrors_to_a_distinct_s3_key():
    """utils derives the S3 key mechanically from the local path, so a new
    filename needs no extra wiring — but it must not collide with control."""
    from utils import _to_s3_key
    ctrl = _to_s3_key(ap._portfolio_path_for('U1', 'control'))
    var = _to_s3_key(ap._portfolio_path_for('U1', 'autoswap'))
    assert ctrl != var
    assert var.endswith('scanner_output/portfolio/U1/auto_portfolio_autoswap.json')
