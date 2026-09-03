#!/usr/bin/env python3
"""save_json's local write must be atomic: a reader never sees a truncated
or partially-written file, even if the process is killed mid-write.

WHY THIS EXISTS
---------------
The prior implementation was `open(abs_path, 'w')` followed by `f.write(content)`.
`open(..., 'w')` truncates the file to zero bytes BEFORE a single byte of the
new content is written. A process killed between the truncate and the write
completing (OOM kill, container restart — this box has taken 13+ documented
OOM kills, CLAUDE.md §17-§21) left a book at 0 bytes or a half-written JSON
object — every open position gone, with nothing left running to raise an
error about it.

The fix writes the new content to a sibling temp file first, then
os.replace()s it into place. os.replace is atomic on POSIX — the target
inode is swapped in one step, so a reader (or a process killed at any point)
always sees either the complete OLD file or the complete NEW one, never a
partial one.

Run:
    python -m pytest tests/test_atomic_json_write.py -v
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import utils


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, '_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setattr(utils, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(utils, '_is_cloud', lambda: False)
    return tmp_path


def test_normal_save_round_trips(sandbox):
    utils.save_json({'a': 1, 'positions': ['AAPL']}, 'book/data.json')
    assert utils.load_json('book/data.json') == {'a': 1, 'positions': ['AAPL']}


def test_write_goes_through_a_sibling_temp_file_then_replace(sandbox):
    """Structural guard on the mechanism itself, not just its outcome — a
    future edit that reintroduces a direct truncating write must fail here
    even if it happens to survive the crash-simulation test below."""
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((src, dst))
        assert src != dst, 'must rename a TEMP file into place, not "replace" the target with itself'
        assert Path(src).parent == Path(dst).parent, (
            'temp file must live next to the target so the replace stays on one filesystem')
        assert Path(src).name != Path(dst).name
        return real_replace(src, dst)

    monkeypatch_target = utils.os
    monkeypatch_target.replace = spy_replace
    try:
        utils.save_json({'a': 1}, 'book/data.json')
    finally:
        monkeypatch_target.replace = real_replace

    assert len(calls) == 1, 'save_json must swap the file into place with exactly one os.replace()'


def test_crash_before_replace_leaves_old_file_completely_intact(sandbox, monkeypatch):
    """The actual failure mode this exists to prevent: a kill between
    'new content written to the temp file' and 'renamed into place' must
    never corrupt what a reader sees."""
    path = 'book/data.json'
    utils.save_json({'version': 'old', 'positions': ['AAPL', 'MSFT']}, path)
    original_bytes = (sandbox / path).read_bytes()
    assert len(original_bytes) > 0

    monkeypatch.setattr(os, 'replace', lambda *a, **k: (_ for _ in ()).throw(
        OSError('simulated OOM kill mid-write')))

    with pytest.raises(OSError):
        utils.save_json({'version': 'new', 'positions': []}, path)

    # The old, complete file must still be there — not truncated, not gone,
    # not a mix of old and new content.
    assert (sandbox / path).read_bytes() == original_bytes
    assert utils.load_json(path) == {'version': 'old', 'positions': ['AAPL', 'MSFT']}


def test_no_leftover_temp_file_after_a_failed_write(sandbox, monkeypatch):
    utils.save_json({'v': 1}, 'book/data.json')
    monkeypatch.setattr(os, 'replace', lambda *a, **k: (_ for _ in ()).throw(OSError('boom')))

    with pytest.raises(OSError):
        utils.save_json({'v': 2}, 'book/data.json')

    leftovers = list((sandbox / 'book').glob('.tmp-*'))
    assert leftovers == [], f'temp file(s) left behind after a failed write: {leftovers}'


def test_crash_during_the_write_itself_leaves_old_file_intact(sandbox, monkeypatch):
    """A kill can also land while bytes are still being flushed to the temp
    file, before os.replace is ever reached."""
    path = 'book/data.json'
    utils.save_json({'version': 'old'}, path)
    original_bytes = (sandbox / path).read_bytes()

    monkeypatch.setattr(os, 'fsync', lambda *a, **k: (_ for _ in ()).throw(
        OSError('simulated crash during write')))

    with pytest.raises(OSError):
        utils.save_json({'version': 'new', 'positions': list(range(1000))}, path)

    assert (sandbox / path).read_bytes() == original_bytes
    leftovers = list((sandbox / 'book').glob('.tmp-*'))
    assert leftovers == [], f'temp file(s) left behind after a failed write: {leftovers}'
