#!/usr/bin/env python3
"""
automated_test_agent.py
───────────────────────
Daily trading day scheduler — runs for today's date (NY time).

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

# Compute today's date in NY time at startup — used in grep patterns and CronTrigger
_TODAY_ET = datetime.now(NY_TZ)
TODAY_YYYYMMDD = _TODAY_ET.strftime('%Y%m%d')   # e.g. "20260313"
TODAY_YYYY_MM_DD = _TODAY_ET.strftime('%Y-%m-%d')  # e.g. "2026-03-13"

# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULE
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# FLOW 1: Breakout Scanner (cron_agent)
# Same watchlists and times as production cron_jobs.txt
# ─────────────────────────────────────────────────────────────────────────────

CRON_SCHEDULE = [
    {
        'time': '08:00', 'id': 'cron_0800',
        'label': '[BREAKOUT] Premarket Gap Check #1',
        'command': 'python cron_agent.py --run-now premarket',
        'checks': [
            ('Gap alerts', 'tail -20 scanner_output/logs/cron_premarket.log'),
        ]
    },
    {
        'time': '08:45', 'id': 'cron_0845',
        'label': '[BREAKOUT] Premarket Gap Check #2',
        'command': 'python cron_agent.py --run-now premarket',
        'checks': [
            ('Gap alerts', 'tail -20 scanner_output/logs/cron_premarket.log'),
        ]
    },
    {
        'time': '09:31', 'id': 'cron_0931',
        'label': '[BREAKOUT] Opening Surge Check',
        'command': 'python cron_agent.py --run-now premarket',
        'checks': [
            ('Opening surge alerts', 'tail -20 scanner_output/logs/cron_premarket.log'),
        ]
    },
    {
        'time': '09:35', 'id': 'cron_0935',
        'label': '[BREAKOUT] Phase 1: Daytrade Scan',
        'command': 'python cron_agent.py --run-now daytrade',
        'checks': [
            ('New signal types', f'grep "CONTINUATION\\|SMA20_CROSS" scanner_output/signals/*{TODAY_YYYYMMDD}*.csv 2>/dev/null | head -10'),
            ('Scan summary', 'tail -20 scanner_output/logs/cron_daytrade*.log'),
        ]
    },
    {
        'time': '09:45', 'id': 'cron_0945',
        'label': '[BREAKOUT] Phase 1: Swing Scan',
        'command': 'python cron_agent.py --run-now swing',
        'checks': [
            ('New signal types', f'grep "CONTINUATION\\|SMA20_CROSS" scanner_output/signals/*{TODAY_YYYYMMDD}*.csv 2>/dev/null | head -10'),
            ('Scan summary', 'tail -20 scanner_output/logs/cron_swing*.log'),
        ]
    },
    {
        'time': '10:00', 'id': 'cron_1000',
        'label': '[BREAKOUT] Hourly Monitor 10:00',
        'command': 'python cron_agent.py --run-now momentum_watch && python cron_agent.py --run-now portfolio',
        'checks': [
            ('Watch monitor alerts', 'tail -10 scanner_output/logs/cron_watch_monitor.log'),
            ('Portfolio alerts', 'tail -10 scanner_output/logs/cron_monitor.log'),
            ('Alert count', f'grep "Sent" scanner_output/logs/cron_watch_monitor.log | grep "{TODAY_YYYY_MM_DD}" | wc -l'),
        ]
    },
    {
        'time': '11:00', 'id': 'cron_1100',
        'label': '[BREAKOUT] Hourly Monitor 11:00',
        'command': 'python cron_agent.py --run-now momentum_watch && python cron_agent.py --run-now portfolio',
        'checks': [
            ('Watch alerts', 'tail -10 scanner_output/logs/cron_watch_monitor.log'),
            ('Portfolio alerts', 'tail -10 scanner_output/logs/cron_monitor.log'),
            ('Missed movers', 'grep "MISSED MOVERS" scanner_output/logs/cron_*.log | tail -10'),
            ('Total signals so far', f'find scanner_output/signals -name "*{TODAY_YYYYMMDD}*.csv" -exec wc -l {{}} \\; | tail -5'),
        ]
    },
    {
        'time': '12:00', 'id': 'cron_1200',
        'label': '[BREAKOUT] Hourly Monitor 12:00',
        'command': 'python cron_agent.py --run-now momentum_watch && python cron_agent.py --run-now portfolio',
        'checks': [
            ('Watch alerts', 'tail -10 scanner_output/logs/cron_watch_monitor.log'),
            ('Portfolio alerts', 'tail -10 scanner_output/logs/cron_monitor.log'),
            ('Alert count today', f'grep "Sent" scanner_output/logs/cron_watch_monitor.log | grep "{TODAY_YYYY_MM_DD}" | wc -l'),
        ]
    },
    {
        'time': '13:00', 'id': 'cron_1300',
        'label': '[BREAKOUT] Hourly Monitor 13:00',
        'command': 'python cron_agent.py --run-now momentum_watch && python cron_agent.py --run-now portfolio',
        'checks': [
            ('Watch alerts', 'tail -10 scanner_output/logs/cron_watch_monitor.log'),
            ('Portfolio alerts', 'tail -10 scanner_output/logs/cron_monitor.log'),
            ('Alert count today', f'grep "Sent" scanner_output/logs/cron_watch_monitor.log | grep "{TODAY_YYYY_MM_DD}" | wc -l'),
        ]
    },
    {
        'time': '14:00', 'id': 'cron_1400',
        'label': '[BREAKOUT] Phase 2: Daytrade Re-evaluation (2 PM)',
        'command': 'python cron_agent.py --run-now daytrade && python cron_agent.py --run-now momentum_watch && python cron_agent.py --run-now portfolio',
        'checks': [
            ('New signals since 9:35', f'grep "CONTINUATION\\|SMA20_CROSS" scanner_output/signals/*{TODAY_YYYYMMDD}*.csv 2>/dev/null | tail -10'),
            ('Daytrade log', 'tail -15 scanner_output/logs/cron_daytrade*.log'),
            ('Watch alerts', 'tail -10 scanner_output/logs/cron_watch_monitor.log'),
            ('Portfolio alerts', 'tail -10 scanner_output/logs/cron_monitor.log'),
        ]
    },
    {
        'time': '15:00', 'id': 'cron_1500',
        'label': '[BREAKOUT] Hourly Monitor 15:00',
        'command': 'python cron_agent.py --run-now momentum_watch && python cron_agent.py --run-now portfolio',
        'checks': [
            ('Watch alerts', 'tail -10 scanner_output/logs/cron_watch_monitor.log'),
            ('Portfolio alerts', 'tail -10 scanner_output/logs/cron_monitor.log'),
            ('Alert count today', f'grep "Sent" scanner_output/logs/cron_watch_monitor.log | grep "{TODAY_YYYY_MM_DD}" | wc -l'),
        ]
    },
    {
        'time': '15:30', 'id': 'cron_1530',
        'label': '[BREAKOUT] Daytrade Exit Check (3:30 PM)',
        'command': 'python cron_agent.py --run-now daytrade && python cron_agent.py --run-now momentum_watch && python cron_agent.py --run-now portfolio',
        'checks': [
            ('Exit evaluation', 'tail -10 scanner_output/logs/cron_daytrade_exit*.log'),
            ('Positions closed', 'grep -i "closed\\|exit" scanner_output/logs/cron_daytrade*.log | tail -5'),
            ('Watch alerts', 'tail -10 scanner_output/logs/cron_watch_monitor.log'),
            ('Portfolio alerts', 'tail -10 scanner_output/logs/cron_monitor.log'),
        ]
    },
    {
        'time': '16:00', 'id': 'cron_1600',
        'label': '[BREAKOUT] Market Close: Final Monitoring',
        'command': 'python cron_agent.py --run-now momentum_watch && python cron_agent.py --run-now portfolio',
        'checks': [
            ('Watch monitor final', 'tail -10 scanner_output/logs/cron_watch_monitor.log'),
            ('Portfolio final', 'tail -10 scanner_output/logs/cron_monitor.log'),
        ]
    },
    {
        'time': '16:15', 'id': 'cron_1615',
        'label': '[BREAKOUT] End-of-Day Summary',
        'command': None,
        'checks': [
            ('Total CONTINUATION signals', f'grep -c "CONTINUATION" scanner_output/signals/*{TODAY_YYYYMMDD}*.csv 2>/dev/null || echo "0"'),
            ('Total SMA20_CROSS signals', f'grep -c "SMA20_CROSS" scanner_output/signals/*{TODAY_YYYYMMDD}*.csv 2>/dev/null || echo "0"'),
            ('Total watch alerts sent', f'grep "Sent" scanner_output/logs/cron_watch_monitor.log | grep "{TODAY_YYYY_MM_DD}" | wc -l'),
            ('Total portfolio alerts', f'grep "Sent" scanner_output/logs/cron_monitor.log | grep "{TODAY_YYYY_MM_DD}" | wc -l'),
            ('Missed movers final', 'grep "MISSED MOVERS" scanner_output/logs/cron_*.log | tail -10'),
        ]
    },
]

SCHEDULE = CRON_SCHEDULE

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
    stdout = ''
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
    logger.info(f"📅 SCHEDULING JOBS FOR {TODAY_YYYY_MM_DD} (NY time)")
    logger.info(f"{'='*70}\n")

    for job_config in CRON_SCHEDULE:
        job_time = job_config['time']
        hour, minute = map(int, job_time.split(':'))
        scheduler.add_job(
            execute_job,
            CronTrigger(hour=hour, minute=minute, day=_TODAY_ET.day,
                        month=_TODAY_ET.month, second=0, timezone=NY_TZ),
            args=[job_config, dry_run],
            id=job_config['id'],
            name=job_config['label'],
        )
        logger.info(f"  ⏰ {job_time} ET  →  {job_config['label']}")

    logger.info(f"\n{'-'*70}")
    logger.info(f"Total jobs scheduled: {len(SCHEDULE)}")
    logger.info(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description='Automated testing scheduler for today')
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
