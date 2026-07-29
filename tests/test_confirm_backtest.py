"""
Regression test for research/confirm_backtest.py's run() helper.

Context: run() referenced an undefined name `stamp` in its progress print
(stamp is a local variable inside the *separate* log_path() helper, never
passed to or recomputed in run()). This meant every call to run() raised
NameError immediately after printing "running ..." — i.e. confirm_backtest.py
had never completed a single invocation since its first commit (af29578),
including every --mult and --rank-scores usage. Found 2026-07-29 while running
the H6 gate for the first time. subprocess.run is mocked so this stays fast
and network-free; the point is only to catch the NameError class of bug.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research import confirm_backtest


def test_run_does_not_raise_on_undefined_stamp(tmp_path, monkeypatch):
    monkeypatch.setattr(confirm_backtest, 'OUTDIR', tmp_path)
    fake_result = MagicMock(returncode=0)
    with patch('subprocess.run', return_value=fake_result) as mock_run:
        log = confirm_backtest.run('2022,2024', 'input/optimizer_watch.txt',
                                    'candidate', rank_scores='scores.csv')
    assert mock_run.called
    assert log == tmp_path / 'candidate_live_rank_2022-2024.log'


def test_run_mult_variant_also_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(confirm_backtest, 'OUTDIR', tmp_path)
    fake_result = MagicMock(returncode=0)
    with patch('subprocess.run', return_value=fake_result):
        log = confirm_backtest.run('2022,2024', 'input/spx_plus.txt',
                                    'baseline', mult=2.5)
    assert log == tmp_path / 'baseline_live_mult2.5_2022-2024.log'
