"""
cron_agent.py
─────────────────────────────────────────────────────────────────────────────
Unified scheduler and executor for all breakout scanner cron jobs.

Manages:
  - Long-term position trading (weekly scans)
  - Swing trading (daily scans)
  - Day trading (intraday scans)
  - Pre-market monitoring
  - Momentum-watch monitoring (every 15 min)
  - Portfolio monitoring (every 15 min)
  - Signal validation & learning
  - Maintenance tasks

Features:
  - Parse cron_jobs.txt to extract all job definitions
  - Run jobs manually by time or mode
  - Daemon mode to run on schedule
  - Logging to scanner_output/logs/cron_agent.log
  - Healthchecks.io integration
  - Dry-run mode to preview execution

Usage:
  python cron_agent.py --help                    # Show all commands
  python cron_agent.py --run-now longterm        # Run a specific job now
  python cron_agent.py --run-all                 # Run all jobs for today
  python cron_agent.py --daemon                  # Run as daemon (respects cron schedule)
  python cron_agent.py --daemon --sim-time "09:35"  # Simulate market time
  python cron_agent.py --list-jobs               # Show all parsed jobs
"""

import argparse
import json
import logging
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()  # Load .env before any AWS calls

_NY_TZ = ZoneInfo('America/New_York')
_PROJECT_ROOT = Path(__file__).parent
_LOGS_DIR = _PROJECT_ROOT / 'scanner_output' / 'logs'
_LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(_LOGS_DIR / 'cron_agent.log'),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data Structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CronJob:
    """Represents a single cron job."""
    name: str                  # Descriptive name
    minute: List[int]          # List of valid minutes, or [-1] for wildcard (e.g., [0, 15, 30, 45] for */15)
    hour: List[int]            # List of valid hours, or [-1] for wildcard
    day: int                   # 1-31 or -1 for wildcard
    month: int                 # 1-12 or -1 for wildcard
    weekday: List[int]         # 0-6 (0=Sunday) or [-1] for wildcard
    command: str               # Full shell command
    hc_uuid: Optional[str]     # Healthchecks.io UUID if present
    category: str              # Category: longterm, swing, daytrade, premarket, monitor, validate, maint

    def should_run(self, dt: datetime) -> bool:
        """Check if job should run at given datetime."""
        if self.minute != [-1] and dt.minute not in self.minute:
            return False
        if self.hour != [-1] and dt.hour not in self.hour:
            return False
        if self.day != -1 and dt.day != self.day:
            return False
        if self.month != -1 and dt.month != self.month:
            return False
        if self.weekday != [-1] and dt.weekday() not in self.weekday:
            # Convert Python weekday (0=Mon) to cron (0=Sun)
            cron_weekday = (dt.weekday() + 1) % 7
            if cron_weekday not in self.weekday:
                return False
        return True

    def __repr__(self) -> str:
        wd_str = ','.join(map(str, self.weekday)) if self.weekday != [-1] else '*'
        min_str = f"{self.minute:02d}" if self.minute >= 0 else '*'
        hour_str = f"{self.hour:02d}" if self.hour >= 0 else '*'
        day_str = f"{self.day:02d}" if self.day >= 0 else '*'
        month_str = f"{self.month:02d}" if self.month >= 0 else '*'
        return (
            f"{self.name:30} | "
            f"{min_str}:{hour_str} {day_str} {month_str} {wd_str:5} | "
            f"{self.command[:50]}..."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_cron_time(minute: str, hour: str, day: str, month: str, weekday: str) -> Tuple[List[int], List[int], int, int, List[int]]:
    """
    Parse cron time fields. Returns (minutes_list, hours_list, day, month, weekdays_list).

    Supports:
      - Exact values: 30, 9
      - Wildcards: *
      - Lists: 0,1,2,3,4 or 1-5
      - Ranges: 10-15
      - Step values: */15, 10-20/2
    """
    def parse_field(field: str, min_val: int, max_val: int) -> List[int]:
        if field == '*':
            return [-1]  # Wildcard marker
        if '/' in field:
            # */15, 0-14/2, etc.
            parts = field.split('/')
            step = int(parts[1])
            if parts[0] == '*':
                return list(range(min_val, max_val + 1, step))
            else:
                # Range with step
                range_parts = parts[0].split('-')
                start = int(range_parts[0])
                end = int(range_parts[1]) if len(range_parts) > 1 else max_val
                return list(range(start, end + 1, step))
        if '-' in field and ',' not in field:
            # Range like 10-15
            parts = field.split('-')
            return list(range(int(parts[0]), int(parts[1]) + 1))
        if ',' in field:
            # List like 1,2,3
            return [int(x) for x in field.split(',')]
        return [int(field)]

    m = parse_field(minute, 0, 59)
    h = parse_field(hour, 0, 23)
    d_list = parse_field(day, 1, 31)
    mo_list = parse_field(month, 1, 12)
    wd = parse_field(weekday, 0, 6)

    # For day and month, collapse to single value (they're rarely stepped)
    d = d_list[0] if len(d_list) == 1 else -1
    mo = mo_list[0] if len(mo_list) == 1 else -1

    return m, h, d, mo, wd


def parse_cron_jobs(cron_file: Path) -> List[CronJob]:
    """Parse cron_jobs.txt and extract all job definitions."""
    jobs: List[CronJob] = []

    with open(cron_file) as f:
        all_lines = f.readlines()

    category = 'unknown'
    for line_idx, raw_line in enumerate(all_lines):
        line = raw_line.strip()

        # Detect category from section headers (look at comment lines)
        if line.startswith('#') and '============' in line:
            # Header line found, check nearby lines for category
            for nearby_idx in range(max(0, line_idx - 1), min(len(all_lines), line_idx + 2)):
                nearby_line = all_lines[nearby_idx].upper()
                if 'LONG-TERM' in nearby_line:
                    category = 'longterm'
                    break
                elif 'SWING TRADING' in nearby_line:
                    category = 'swing'
                    break
                elif 'DAY TRADING' in nearby_line or 'DAYTRADE' in nearby_line:
                    category = 'daytrade'
                    break
                elif 'PRE-MARKET' in nearby_line or 'PREMARKET' in nearby_line:
                    category = 'premarket'
                    break
                elif 'MOMENTUM-WATCH' in nearby_line:
                    category = 'momentum_watch'
                    break
                elif 'PORTFOLIO' in nearby_line:
                    category = 'portfolio'
                    break
                elif 'VALIDATION' in nearby_line or 'VALIDATE' in nearby_line:
                    category = 'validate'
                    break
                elif 'MAINTENANCE' in nearby_line:
                    category = 'maint'
                    break
            continue

        # Skip empty lines and comments
        if not line or line.startswith('#'):
            continue

        # Skip environment lines (SHELL=, PATH=, etc.) but not cron lines
        if '=' in line and 'TZ=' not in line and not re.match(r'(\d+|@|\*/)', line):
            continue

        # Extract: minute hour day month weekday TZ=... cd ... && command ...
        match = re.match(
            r'(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+TZ=\S+\s+(.+)',
            line
        )
        if not match:
            continue

        minute_str, hour_str, day_str, month_str, weekday_str, rest = match.groups()

        try:
            minute, hour, day, month, weekdays = parse_cron_time(
                minute_str, hour_str, day_str, month_str, weekday_str
            )
        except Exception as e:
            logger.warning(f"Failed to parse cron time {minute_str} {hour_str} {day_str} {month_str} {weekday_str}: {e}")
            continue

        # Extract healthchecks UUID if present
        hc_uuid = None
        hc_match = re.search(r'HC_BASE/HC_UUID_(\w+)', rest)
        if hc_match:
            hc_uuid = hc_match.group(1)

        # Extract command: look for the first Python command
        cmd_match = re.search(r'\$PYTHON_BIN\s+(\S+(?:\s+[\S"]+)*)', rest)
        if cmd_match:
            command = cmd_match.group(0).replace('$PYTHON_BIN', 'python3')
        else:
            # Fallback: capture everything after 'cd $PROJECT_ROOT &&'
            if '&&' in rest:
                command = rest.split('&&', 1)[1].strip()
            else:
                command = rest

        # Generate name from command and time
        mode_match = re.search(r'--mode\s+(\w+)', command)
        mode = mode_match.group(1) if mode_match else ''

        # Format time for display (handle lists and wildcards)
        if minute == [-1]:
            min_str = "*"
        elif len(minute) == 1:
            min_str = f"{minute[0]:02d}"
        else:
            min_str = f"*/{minute[1]-minute[0]}" if len(minute) > 1 and all(minute[i+1]-minute[i] == minute[1]-minute[0] for i in range(len(minute)-1)) else "*"

        if hour == [-1]:
            hour_str = "*"
        elif len(hour) == 1:
            hour_str = f"{hour[0]:02d}"
        else:
            hour_str = f"{hour[0]}-{hour[-1]}" if hour[-1] - hour[0] + 1 == len(hour) else "*"

        name = f"{category}_{hour_str}:{min_str}_{mode}" if mode else f"{category}_{hour_str}:{min_str}"

        job = CronJob(
            name=name,
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            weekday=weekdays,
            command=command,
            hc_uuid=hc_uuid,
            category=category,
        )
        jobs.append(job)

    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# Execution
# ─────────────────────────────────────────────────────────────────────────────

def run_job(job: CronJob, dry_run: bool = False) -> bool:
    """
    Execute a single cron job.

    Returns True if successful, False otherwise.
    """
    logger.info(f"▶ Running: {job.name}")

    if dry_run:
        logger.info(f"  [DRY-RUN] Would execute: {job.command}")
        return True

    # Wide-universe scans (ALL.txt) need more time (1332 symbols)
    job_timeout = 1800 if 'ALL.txt' in job.command else 900

    try:
        result = subprocess.run(
            job.command,
            shell=True,
            cwd=_PROJECT_ROOT,
            timeout=job_timeout,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            logger.info(f"✓ Success: {job.name}")
            _ping_healthcheck(job.hc_uuid, status='success')
            return True
        else:
            logger.error(f"✗ Failed: {job.name} (exit code {result.returncode})")
            logger.error(f"  stderr: {result.stderr[:200]}")
            _ping_healthcheck(job.hc_uuid, status='fail')
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"✗ Timeout: {job.name} ({job_timeout}s)")
        _ping_healthcheck(job.hc_uuid, status='fail')
        return False
    except Exception as e:
        logger.error(f"✗ Error: {job.name} — {e}")
        _ping_healthcheck(job.hc_uuid, status='fail')
        return False


def _ping_healthcheck(hc_uuid: Optional[str], status: str = 'success') -> None:
    """Ping healthchecks.io if UUID is configured."""
    if not hc_uuid:
        return

    url_map = {
        'success': f"https://hc-ping.com/{hc_uuid}",
        'fail': f"https://hc-ping.com/{hc_uuid}/fail",
        'start': f"https://hc-ping.com/{hc_uuid}/start",
    }

    url = url_map.get(status)
    if not url:
        return

    try:
        result = subprocess.run(
            ['curl', '-fsS', '-m', '10', '--retry', '3', url],
            capture_output=True,
            timeout=15,
        )
        if result.returncode == 0:
            logger.debug(f"  Healthcheck pinged: {hc_uuid}")
    except Exception as e:
        logger.debug(f"  Healthcheck ping failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Daemon & Scheduling
# ─────────────────────────────────────────────────────────────────────────────

def run_daemon(jobs: List[CronJob], sim_time: Optional[str] = None, dry_run: bool = False):
    """
    Run as daemon, continuously checking for scheduled jobs.

    Args:
      jobs: All parsed cron jobs
      sim_time: Optional "HH:MM" to simulate market time (for testing)
      dry_run: Only log, don't execute
    """
    logger.info("=" * 80)
    logger.info(f"Cron Agent Daemon Started — {len(jobs)} jobs loaded")
    if sim_time:
        logger.info(f"⏰ Simulating time: {sim_time}")
    if dry_run:
        logger.info("🔒 DRY-RUN mode: no commands will execute")
    logger.info("=" * 80)

    last_run: Dict[str, datetime] = {}

    while True:
        if sim_time:
            # Parse simulated time (HH:MM)
            h, m = map(int, sim_time.split(':'))
            now = datetime.now(_NY_TZ).replace(hour=h, minute=m, second=0, microsecond=0)
        else:
            now = datetime.now(_NY_TZ)

        # Find jobs that should run now
        for job in jobs:
            if job.should_run(now):
                job_key = (job.name, now.strftime('%Y-%m-%d %H:%M'))
                if job_key not in last_run:
                    last_run[job_key] = now
                    run_job(job, dry_run=dry_run)
                    # Prevent running same job twice in same minute
                    time.sleep(2)

        # Sleep for 30 seconds before next check
        if not sim_time:
            time.sleep(30)
        else:
            # In sim mode, advance 1 minute
            break


def run_jobs_for_category(jobs: List[CronJob], category: str, dry_run: bool = False) -> int:
    """
    Run all jobs in a specific category now.

    Returns count of jobs run.
    """
    matching = [j for j in jobs if j.category == category]
    if not matching:
        logger.warning(f"No jobs found in category '{category}'")
        return 0

    logger.info(f"Running {len(matching)} job(s) in category '{category}'")
    count = 0
    for job in matching:
        if run_job(job, dry_run=dry_run):
            count += 1

    logger.info(f"Completed {count}/{len(matching)} jobs")
    return count


def run_jobs_for_time(jobs: List[CronJob], hour: int, minute: int, dry_run: bool = False) -> int:
    """
    Run all jobs scheduled for specific time now.
    Intelligently batches simultaneous breakout_scanner jobs by mode using job_launcher.py.

    Returns count of jobs run.
    """
    now = datetime.now(_NY_TZ).replace(hour=hour, minute=minute)
    matching = [j for j in jobs if j.should_run(now)]

    if not matching:
        logger.warning(f"No jobs scheduled for {hour:02d}:{minute:02d}")
        return 0

    logger.info(f"Running {len(matching)} job(s) scheduled for {hour:02d}:{minute:02d}")

    # Separate breakout_scanner jobs by mode (for batching) from other jobs
    scanner_jobs_by_mode = {}
    other_jobs = []

    for job in matching:
        if 'breakout_scanner.py' in job.command:
            mode_match = re.search(r'--mode\s+(\w+)', job.command)
            mode = mode_match.group(1) if mode_match else 'unknown'
            if mode not in scanner_jobs_by_mode:
                scanner_jobs_by_mode[mode] = job
            other_jobs.append(job)
        else:
            other_jobs.append(job)

    # If multiple scanner modes at same time, use job_launcher for batching
    count = 0
    if len(scanner_jobs_by_mode) > 1:
        logger.info(f"📦 Detected {len(scanner_jobs_by_mode)} simultaneous breakout scanner modes")
        logger.info(f"   Using job_launcher.py for batch data loading optimization")

        modes = list(scanner_jobs_by_mode.keys())
        watchlist_match = re.search(r'breakout_scanner\.py\s+(\S+)', matching[0].command)
        watchlist = watchlist_match.group(1) if watchlist_match else 'input/1_26_Setups.txt'

        cmd = f"python3 job_launcher.py --time {hour:02d}:{minute:02d} --modes {','.join(modes)} --file {watchlist}"
        if '--notify' in matching[0].command:
            cmd += " --notify"
        if '--cron' in matching[0].command:
            cmd += " --cron"

        logger.info(f"  Command: {cmd}")

        if dry_run:
            logger.info(f"  [DRY-RUN] Would execute: {cmd}")
            count = len(scanner_jobs_by_mode)
        else:
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=_PROJECT_ROOT,
                    timeout=1800,  # 30 min timeout for batch
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    logger.info(f"✓ Batch job launcher completed successfully")
                    count = len(scanner_jobs_by_mode)
                else:
                    logger.error(f"✗ Batch job launcher failed (exit code {result.returncode})")
                    logger.error(f"  stderr: {result.stderr[:500]}")
            except subprocess.TimeoutExpired:
                logger.error(f"✗ Batch job launcher timed out (1800s)")
            except Exception as e:
                logger.error(f"✗ Batch job launcher error: {e}")

    else:
        # Single mode or no scanner jobs, run normally
        for job in matching:
            if run_job(job, dry_run=dry_run):
                count += 1

    logger.info(f"Completed {count}/{len(matching)} jobs")
    return count


def list_jobs(jobs: List[CronJob], category: Optional[str] = None):
    """Print all jobs in human-readable format."""
    if category:
        jobs = [j for j in jobs if j.category == category]

    print(f"\n{'Job Name':<40} | {'Time':<12} | {'Weekday':<15} | {'Category':<15}")
    print("-" * 90)

    def sort_key(job):
        # For sorting: prioritize TIME (h, m) first, then category
        # This shows daily execution flow chronologically
        h = job.hour[0] if job.hour != [-1] else 99
        m = job.minute[0] if job.minute != [-1] else 99
        return (h, m, job.category)

    for job in sorted(jobs, key=sort_key):
        wd_str = ','.join(map(str, job.weekday)) if job.weekday != [-1] else 'All'

        # Format time display
        if job.hour == [-1]:
            hour_str = "--"
        else:
            hour_str = f"{job.hour[0]:02d}" if len(job.hour) == 1 else f"{job.hour[0]}-{job.hour[-1]}"

        if job.minute == [-1]:
            min_str = "--"
        elif len(job.minute) == 1:
            min_str = f"{job.minute[0]:02d}"
        elif len(job.minute) <= 4:
            min_str = f"*/{job.minute[1]-job.minute[0]}"
        else:
            min_str = "*"

        time_str = f"{hour_str}:{min_str} ET"
        print(f"{job.name:<40} | {time_str:<12} | {wd_str:<15} | {job.category:<15}")

    print()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Unified cron job scheduler and executor for breakout scanner'
    )
    parser.add_argument(
        '--list-jobs',
        action='store_true',
        help='List all parsed jobs and exit'
    )
    parser.add_argument(
        '--category',
        choices=['longterm', 'swing', 'daytrade', 'premarket', 'momentum_watch', 'portfolio', 'validate', 'maint'],
        help='Filter jobs by category'
    )
    parser.add_argument(
        '--run-now',
        metavar='CATEGORY',
        help='Run all jobs in category NOW (bypasses schedule)'
    )
    parser.add_argument(
        '--run-time',
        metavar='HH:MM',
        help='Run jobs scheduled for specific time (e.g., 09:35)'
    )
    parser.add_argument(
        '--run-all',
        action='store_true',
        help='Run all jobs that should run today (checks schedule)'
    )
    parser.add_argument(
        '--daemon',
        action='store_true',
        help='Run as daemon (continuous monitoring)'
    )
    parser.add_argument(
        '--sim-time',
        metavar='HH:MM',
        help='Simulate market time (for testing, use with --daemon)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview jobs without executing'
    )

    args = parser.parse_args()

    # Load all jobs
    cron_file = _PROJECT_ROOT / 'cron_jobs.txt'
    if not cron_file.exists():
        logger.error(f"cron_jobs.txt not found at {cron_file}")
        sys.exit(1)

    jobs = parse_cron_jobs(cron_file)
    logger.info(f"Loaded {len(jobs)} cron jobs from {cron_file}")

    # Route to appropriate action
    if args.list_jobs:
        list_jobs(jobs, category=args.category)
        return

    if args.run_now:
        run_jobs_for_category(jobs, args.run_now, dry_run=args.dry_run)
        return

    if args.run_time:
        try:
            h, m = map(int, args.run_time.split(':'))
            run_jobs_for_time(jobs, h, m, dry_run=args.dry_run)
        except ValueError:
            logger.error(f"Invalid time format: {args.run_time}. Use HH:MM")
            sys.exit(1)
        return

    if args.daemon:
        run_daemon(jobs, sim_time=args.sim_time, dry_run=args.dry_run)
        return

    if args.run_all:
        now = datetime.now(_NY_TZ)
        matching = [j for j in jobs if j.should_run(now)]
        if not matching:
            logger.info("No jobs scheduled for right now")
            return
        logger.info(f"Running {len(matching)} job(s) scheduled for now")
        for job in matching:
            run_job(job, dry_run=args.dry_run)
        return

    # Default: show help
    parser.print_help()


if __name__ == '__main__':
    main()
