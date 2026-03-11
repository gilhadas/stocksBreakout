#!/usr/bin/env python3
"""
automated_test_agent.py
───────────────────────
Tomorrow's testing scheduler (March 13, 2026).

Schedules all cron_agent commands at NY times, captures results,
sends email with grep output + Telegram notification.

Usage:
  python automated_test_agent.py              # Schedule and run
  python automated_test_agent.py --dry-run    # Preview schedule only
"""

import argparse
import asyncio
import logging
import subprocess
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from notifier import Notifier

# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo('America/New_York')
OUTPUT_DIR = Path('scanner_output')

# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULE
# ─────────────────────────────────────────────────────────────────────────────

SCHEDULE = [
    {
        'time': '08:00',
        'label': 'Premarket Gap Check #1',
        'command': 'python cron_agent.py --run-now premarket --notify',
        'checks': [
            ('Gap alerts', 'scanner_output/logs/cron_premarket.log'),
        ]
    },
    {
        'time': '08:45',
        'label': 'Premarket Gap Check #2',
        'command': 'python cron_agent.py --run-now premarket --notify',
        'checks': [
            ('Gap alerts', 'scanner_output/logs/cron_premarket.log'),
        ]
    },
    {
        'time': '09:31',
        'label': 'Opening Surge Check',
        'command': 'python cron_agent.py --run-now premarket --notify',
        'checks': [
            ('Opening surge alerts', 'scanner_output/logs/cron_premarket.log'),
        ]
    },
    {
        'time': '09:35',
        'label': 'Phase 1: Daytrade Scan',
        'command': 'python cron_agent.py --run-now daytrade --notify',
        'checks': [
            ('New signal types', 'grep "CONTINUATION\\|SMA20_CROSS" scanner_output/signals/*20260313*.csv 2>/dev/null | head -10'),
            ('Scan summary', 'tail -20 scanner_output/logs/cron_daytrade*.log'),
        ]
    },
    {
        'time': '09:45',
        'label': 'Phase 1: Swing Scan',
        'command': 'python cron_agent.py --run-now swing --notify',
        'checks': [
            ('New signal types', 'grep "CONTINUATION\\|SMA20_CROSS" scanner_output/signals/*20260313*.csv 2>/dev/null | head -10'),
            ('Scan summary', 'tail -20 scanner_output/logs/cron_swing*.log'),
        ]
    },
    {
        'time': '10:00',
        'label': 'Momentum Watch Monitor',
        'command': 'python cron_agent.py --run-now momentum_watch --notify',
        'checks': [
            ('Watch monitor alerts', 'tail -10 scanner_output/logs/cron_watch_monitor.log'),
            ('Alert count', 'grep "Sent" scanner_output/logs/cron_watch_monitor.log | grep "2026-03-13" | wc -l'),
        ]
    },
    {
        'time': '10:05',
        'label': 'Portfolio Monitor',
        'command': 'python cron_agent.py --run-now portfolio --notify',
        'checks': [
            ('Portfolio alerts', 'tail -10 scanner_output/logs/cron_monitor.log'),
            ('Alert count', 'grep "Sent" scanner_output/logs/cron_monitor.log | grep "2026-03-13" | wc -l'),
        ]
    },
    {
        'time': '11:00',
        'label': 'Mid-morning Summary',
        'command': None,  # No command, just grep check
        'checks': [
            ('Missed movers', 'grep "MISSED MOVERS" scanner_output/logs/cron_*.log | tail -15'),
            ('Total signals so far', 'find scanner_output/signals -name "*20260313*.csv" -exec wc -l {} \\; | tail -5'),
        ]
    },
]

# ─────────────────────────────────────────────────────────────────────────────

def run_command(cmd: str) -> tuple[int, str, str]:
    """Execute shell command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=600  # 10 min timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, '', 'Command timed out after 10 minutes'
    except Exception as e:
        return -1, '', str(e)


def collect_results(checks: list) -> str:
    """Run grep/check commands and collect results."""
    output = []
    for label, cmd in checks:
        output.append(f"\n🔍 {label}:")
        output.append('─' * 60)
        rc, stdout, stderr = run_command(cmd)
        if rc == 0 and stdout.strip():
            output.append(stdout.strip())
        elif stderr.strip():
            output.append(f"Error: {stderr.strip()}")
        else:
            output.append("(no results)")
    return '\n'.join(output)


def execute_job(job_config: dict, dry_run: bool = False):
    """Execute a scheduled job: run command, collect results, send notifications."""
    now_et = datetime.now(NY_TZ)
    label = job_config['label']
    cmd = job_config['command']
    checks = job_config['checks']

    logger.info(f"{'='*70}")
    logger.info(f"🚀 {label} [{now_et.strftime('%H:%M ET')}]")
    logger.info(f"{'='*70}")

    if dry_run:
        logger.info("[DRY-RUN] Would execute:")
        if cmd:
            logger.info(f"  $ {cmd}")
        logger.info("\nWould run checks:")
        for check_label, _ in checks:
            logger.info(f"  - {check_label}")
        return

    # ─ Run main command ─
    if cmd:
        logger.info(f"Running: {cmd}")
        rc, stdout, stderr = run_command(cmd)
        if rc != 0:
            logger.warning(f"⚠️  Command exited with code {rc}")
            if stderr:
                logger.warning(f"Error: {stderr[:200]}")

    # ─ Collect grep results ─
    logger.info("Collecting results...")
    results = collect_results(checks)

    # ─ Send email ─
    notifier = Notifier()
    subject = f"🧪 Test Agent: {label}  [{now_et.strftime('%H:%M ET')}]"
    body = f"""
Test Agent Report
─────────────────────────────────────────

Task: {label}
Time: {now_et.strftime('%Y-%m-%d %H:%M:%S ET')}

RESULTS:
{results}

─────────────────────────────────────────
Check your dashboard or logs for full details.
"""

    if notifier.email_enabled:
        notifier.send_email(subject, body)
        logger.info(f"✉️  Email sent: {subject}")
    else:
        logger.warning("⚠️  Email disabled in config.py")

    # ─ Send Telegram notification (brief) ─
    if notifier.telegram_enabled:
        signal_count = stdout.count('\n') if stdout else 0
        telegram_msg = (
            f"✅ *{label}* completed at {now_et.strftime('%H:%M ET')}\n\n"
            f"📧 Check email for detailed results.\n\n"
            f"Signals found: {signal_count if signal_count > 0 else 'See email'}"
        )
        notifier.send_telegram(telegram_msg)
        logger.info(f"📱 Telegram sent")
    else:
        logger.warning("⚠️  Telegram disabled in config.py")

    logger.info("")


def schedule_jobs(scheduler: BackgroundScheduler, dry_run: bool = False):
    """Add all jobs to scheduler."""
    logger.info(f"\n{'='*70}")
    logger.info(f"📅 SCHEDULING JOBS FOR TOMORROW (March 13, 2026 ET)")
    logger.info(f"{'='*70}\n")

    for job_config in SCHEDULE:
        job_time = job_config['time']
        label = job_config['label']
        hour, minute = map(int, job_time.split(':'))

        scheduler.add_job(
            execute_job,
            CronTrigger(hour=hour, minute=minute, day=13, month=3, second=0, timezone=NY_TZ),
            args=[job_config, dry_run],
            id=f"job_{job_time.replace(':', '')}",
            name=label,
        )
        logger.info(f"  ⏰ {job_time} ET  →  {label}")

    logger.info(f"\n{'-'*70}")
    logger.info(f"Total jobs scheduled: {len(SCHEDULE)}")
    logger.info(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description='Automated testing scheduler for tomorrow')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview schedule without executing')
    args = parser.parse_args()

    # ─ Create scheduler ─
    scheduler = BackgroundScheduler(timezone=NY_TZ)

    # ─ Schedule jobs ─
    schedule_jobs(scheduler, dry_run=args.dry_run)

    if args.dry_run:
        logger.info("✅ Dry-run mode: jobs were previewed but NOT scheduled.")
        return

    # ─ Start scheduler ─
    scheduler.start()
    logger.info("🎯 Scheduler started. Press Ctrl+C to stop.\n")

    try:
        # Keep scheduler running
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n⏹️  Scheduler stopped by user")
        scheduler.shutdown()


if __name__ == '__main__':
    main()
