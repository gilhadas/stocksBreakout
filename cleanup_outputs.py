"""
cleanup_outputs.py
------------------
Prune stale files from scanner_output/ to keep disk usage manageable.

Retention policy
----------------
logs/*.log             7 days  (rotate at 5 MB — truncate, keep last 500 lines)
scanner_YYYYMMDD.log   7 days
scan_decisions/*.csv   30 days (skip header-only files ≤ 200 B immediately)
scan_decisions_state_*.json  7 days
scan_feedback_*.csv    90 days
rejections/*.csv       14 days
exits/*.csv            30 days
signals/*.csv          30 days
outcomes/*.csv         60 days
signal_reports/*.csv   30 days
macd_rsi/*.csv         14 days
.monitor_alerts_*.txt  14 days
.watch_monitor_*.json  14 days
/tmp/feedback_test.log —      always delete (test artefact)

Usage
-----
  python cleanup_outputs.py             # real run
  python cleanup_outputs.py --dry-run   # preview only
  python cleanup_outputs.py --verbose   # show every file kept/deleted
"""

import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config import OUTPUT_DIR

_NY_TZ = ZoneInfo('America/New_York')
_NOW   = datetime.now(_NY_TZ)

# ── tunables ──────────────────────────────────────────────────────────────────
LOG_ROTATE_BYTES  = 5 * 1024 * 1024   # 5 MB — rotate (truncate) live log files
LOG_ROTATE_LINES  = 500               # lines to keep after rotation
HEADER_ONLY_BYTES = 200               # scan_decisions files this small are empty

RETENTION = {
    # (glob_pattern_relative_to_output_dir, days)
    'logs/*.log':                               7,   # covers all logs including scanner_YYYYMMDD.log
    'logs/cron_agent.log.*':                    14,  # TimedRotatingFileHandler backups (cron_agent.log.YYYYMMDD)
    'scan_decisions/scan_decisions_*.csv':      30,
    'scan_decisions/scan_decisions_state_*.json': 7,
    'scan_decisions/scan_feedback_*.csv':       90,
    'rejections/*.csv':                         14,
    'exits/*.csv':                              30,
    'signals/*.csv':                            30,
    'outcomes/*.csv':                           60,
    'signal_reports/*.csv':                     30,
    'macd_rsi/*.csv':                           14,
    'state/.monitor_alerts_*.txt':              14,
    'state/.watch_monitor_*.json':              14,
}

# Files that are always deleted regardless of age
ALWAYS_DELETE = [
    Path('/tmp/feedback_test.log'),
]


# ── helpers ───────────────────────────────────────────────────────────────────
def _age_days(path: Path) -> float:
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=_NY_TZ)
    return (_NOW - mtime).total_seconds() / 86400


def _rotate_log(path: Path, dry_run: bool, verbose: bool) -> bool:
    """Truncate a large log file, keeping the last LOG_ROTATE_LINES lines."""
    if path.stat().st_size < LOG_ROTATE_BYTES:
        return False
    size_mb = path.stat().st_size / 1_048_576
    lines = path.read_text(errors='replace').splitlines()
    kept   = lines[-LOG_ROTATE_LINES:]
    print(f"  ROTATE  {path.relative_to(Path(OUTPUT_DIR).parent)}  "
          f"({size_mb:.1f} MB → keep last {LOG_ROTATE_LINES} lines)")
    if not dry_run:
        path.write_text('\n'.join(kept) + '\n')
    return True


def _should_delete(path: Path, max_days: int) -> tuple[bool, str]:
    """Return (delete, reason)."""
    # Header-only scan_decisions files are worthless regardless of age
    if 'scan_decisions_' in path.name and path.suffix == '.csv':
        if path.stat().st_size <= HEADER_ONLY_BYTES:
            return True, f'header-only ({path.stat().st_size}B)'
    age = _age_days(path)
    if age > max_days:
        return True, f'{age:.0f}d old (limit {max_days}d)'
    return False, ''


# ── main ──────────────────────────────────────────────────────────────────────
def run(dry_run: bool = False, verbose: bool = False) -> None:
    base = Path(OUTPUT_DIR)
    deleted = 0
    rotated = 0
    kept    = 0

    # 1. Always-delete list
    for path in ALWAYS_DELETE:
        if path.exists():
            print(f"  DELETE  {path}  (test artefact)")
            if not dry_run:
                path.unlink()
            deleted += 1

    # 2. Retention-based cleanup + log rotation
    for pattern, max_days in RETENTION.items():
        for path in sorted(base.glob(pattern)):
            if not path.is_file():
                continue

            # Rotate large log files before checking age
            if path.suffix == '.log':
                if _rotate_log(path, dry_run, verbose):
                    rotated += 1
                    continue          # don't also age-delete a just-rotated log

            do_del, reason = _should_delete(path, max_days)
            if do_del:
                print(f"  DELETE  {path.relative_to(base.parent)}  ({reason})")
                if not dry_run:
                    path.unlink()
                deleted += 1
            elif verbose:
                age = _age_days(path)
                print(f"  keep    {path.relative_to(base.parent)}  ({age:.0f}d)")
                kept += 1

    # 3. Summary
    mode = '[DRY RUN] ' if dry_run else ''
    print(f"\n{mode}Cleanup done: {deleted} deleted, {rotated} rotated"
          + (f", {kept} kept" if verbose else ""))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Clean up stale scanner output files')
    parser.add_argument('--dry-run',  action='store_true', help='Preview without deleting')
    parser.add_argument('--verbose',  action='store_true', help='Show kept files too')
    args = parser.parse_args()

    print(f"cleanup_outputs.py  {'(DRY RUN)' if args.dry_run else ''}  {_NOW:%Y-%m-%d %H:%M %Z}")
    run(dry_run=args.dry_run, verbose=args.verbose)
