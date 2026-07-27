"""
Guard against cron_jobs.txt ↔ docker/crontab drift.

WHY THIS EXISTS
---------------
`cron_jobs.txt` (retired Mac schedule) and `docker/crontab` (what actually runs on
the EC2 box) are two hand-maintained copies of the same schedule. Nothing bound them
together, and the drift has now bitten three times:

  1. 2026-03-19 `75a638f` migrated cron_jobs.txt exits to `--exit-from-portfolio`;
     docker/crontab kept `--exit-file input/positions_*_mock.csv`. The July cutover
     silently reverted the fix in production → daily EXIT_FULL notifications on
     long-dead positions (CLAUDE.md §13).
  2. Same commit, the monitor path — `--monitor input/positions_*_mock.csv` vs
     `--monitor-portfolio --monitor-auto-portfolio`. Fixing only the exit path would
     have left 15-minute alerts firing on the same corpses.
  3. 2026-07-27: `refresh_prices()` was chained onto cron_jobs.txt's exit jobs but
     never present in docker/crontab. Since refresh_prices is the ONLY path that
     raises the ATR trail and auto-closes on a stop breach, production had 25 open
     positions with `current_price == entry_price`, zero closes ever, and three
     positions 27–30% below their stops.

§13's own stated lesson was that the absence of such a check is the root cause.
These tests encode the invariants that actually matter, rather than demanding
line-by-line equality — the two files legitimately differ (paths, TZ prefixes,
per-day log filenames, healthcheck pings, retired daytrade jobs).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKER_CRONTAB = ROOT / 'docker' / 'crontab'
MAC_CRONTAB = ROOT / 'cron_jobs.txt'


def _active_lines(path: Path) -> list[str]:
    """Non-comment, non-blank lines."""
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    return [l.strip() for l in path.read_text().splitlines()
            if l.strip() and not l.strip().startswith('#')]


def _docker() -> str:
    return '\n'.join(_active_lines(DOCKER_CRONTAB))


# ── The three capabilities that have actually drifted ────────────────────────

def test_refresh_prices_is_wired_in_production():
    """
    refresh_prices is the ONLY code path that raises the ATR×2.0 trail and closes
    a position when its stop is breached. If it is not in docker/crontab, the live
    system holds every position forever regardless of stops — the champion's single
    largest edge (+97 pts compound / +0.45 Sharpe) simply does not run.
    """
    assert 'refresh_prices' in _docker(), (
        "docker/crontab has no refresh_prices call — trails will never ratchet and "
        "stopped-out positions will never close. See CLAUDE.md §13."
    )


def test_refresh_prices_covers_all_users_not_just_default():
    """
    `refresh_prices()` with no user_id only touches scanner_output/portfolio/
    auto_portfolio.json. Real books live under per-user UUID subdirectories, so the
    bare call silently no-ops on every actual portfolio.
    """
    for line in _active_lines(DOCKER_CRONTAB):
        if 'refresh_prices' not in line:
            continue
        assert 'refresh_prices_all_users' in line, (
            f"cron calls bare refresh_prices() — only refreshes the default book, "
            f"not per-user portfolios:\n  {line}"
        )


def test_refresh_runs_in_both_close_basis_windows():
    """
    _close_basis_history() is designed around TWO runs (§12 Task 1, dc3e252):
    a pre-15:30 run that decides on the last COMPLETED daily bar (overnight-gap
    catch-up), and a >=15:30 run where the near-close price proxies today's close.
    One run alone loses half the design.
    """
    hours = set()
    for line in _active_lines(DOCKER_CRONTAB):
        if 'refresh_prices' not in line:
            continue
        m = re.match(r'^\S+\s+(\d{1,2})\s', line)
        if m:
            hours.add(int(m.group(1)))

    early = {h for h in hours if h < 15}
    late = {h for h in hours if h >= 15}
    assert early, f"no pre-15:30 refresh run (close-basis catch-up); hours seen: {sorted(hours)}"
    assert late, f"no >=15:30 refresh run (near-close proxy); hours seen: {sorted(hours)}"


def test_exits_read_the_portfolio_not_the_mock_csvs():
    """Regression for drift instance #1 (CLAUDE.md §13)."""
    for line in _active_lines(DOCKER_CRONTAB):
        if '--exit-file' in line and 'mock' in line:
            pytest.fail(f"exit job reads an append-only mock CSV, which has no "
                        f"remover and re-fires forever:\n  {line}")


def test_monitor_reads_the_portfolio_not_the_mock_csvs():
    """Regression for drift instance #2 (CLAUDE.md §13)."""
    for line in _active_lines(DOCKER_CRONTAB):
        if '--monitor ' in line and 'mock' in line:
            pytest.fail(f"monitor job reads an append-only mock CSV:\n  {line}")


# ── Generic drift detector ───────────────────────────────────────────────────

# Flags that change WHAT a job does (not where it logs or how it pings).
# If one appears in the Mac schedule but nowhere in Docker's, that is drift.
SEMANTIC_FLAGS = [
    '--exit-from-portfolio',
    '--monitor-portfolio',
    '--monitor-auto-portfolio',
    'refresh_prices',
]


@pytest.mark.parametrize('flag', SEMANTIC_FLAGS)
def test_semantic_flag_present_in_both_schedules(flag):
    """
    Every behaviour-defining flag used by the Mac schedule must also appear in the
    production one. Catches the whole drift class, not just today's instance.
    """
    if not MAC_CRONTAB.exists():
        pytest.skip('cron_jobs.txt not present')
    mac = '\n'.join(_active_lines(MAC_CRONTAB))
    if flag not in mac:
        pytest.skip(f'{flag} not used by cron_jobs.txt')
    assert flag in _docker(), (
        f"'{flag}' is used in cron_jobs.txt but missing from docker/crontab — "
        f"production runs docker/crontab, so this capability is dark in prod."
    )


def test_crontab_lines_have_valid_schedule_fields():
    """A malformed line is silently ignored by supercronic — cheap sanity check."""
    for line in _active_lines(DOCKER_CRONTAB):
        fields = line.split(maxsplit=5)
        assert len(fields) >= 6, f"too few cron fields:\n  {line}"
        minute, hour, dom, month, dow = fields[:5]
        for name, val, lo, hi in (('minute', minute, 0, 59), ('hour', hour, 0, 23)):
            for part in re.split(r'[,/]', val.split('-')[0]):
                if part in ('*',):
                    continue
                assert part.isdigit() and lo <= int(part) <= hi, \
                    f"bad {name} field {val!r} in:\n  {line}"
