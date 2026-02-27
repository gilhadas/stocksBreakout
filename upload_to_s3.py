"""
upload_to_s3.py — Sync scanner_output directories to S3.

Called by cron after each scan so Streamlit Cloud can read the results.

Usage:
    python upload_to_s3.py                          # upload default dirs
    python upload_to_s3.py --dirs scanner_output/signals scanner_output/portfolio
    python upload_to_s3.py --hours 2               # only files modified in last 2 h

Credentials (in priority order):
    1. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in environment
    2. Same keys in .env file at project root
    3. IAM role / ~/.aws/credentials (boto3 default chain)
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# ── Load .env ─────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
_env_file = PROJECT_ROOT / '.env'
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                if _line.startswith('export '):
                    _line = _line[7:]
                _key, _, _val = _line.partition('=')
                _val = _val.split('#')[0].strip().strip('"').strip("'")  # strip inline comments
                os.environ.setdefault(_key.strip(), _val)

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

# ── Config ────────────────────────────────────────────────────────────────────
S3_BUCKET = os.environ.get('S3_BUCKET', 'stocks-breakout-scanner-s3-bucket')

# Directories synced by default (relative to project root)
DEFAULT_DIRS = [
    'scanner_output/signals',
    'scanner_output/portfolio',
    'scanner_output/signal_reports',
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


# ── Upload logic ──────────────────────────────────────────────────────────────

def _s3_client():
    """Create boto3 S3 client — raises NoCredentialsError if no creds found."""
    return boto3.client('s3')


def upload_dir(s3, bucket: str, local_dir: str,
               since_seconds: float | None = None,
               since_epoch: float | None = None) -> int:
    """Upload all files in local_dir to S3, preserving relative path structure.

    Args:
        s3:             boto3 S3 client
        bucket:         S3 bucket name
        local_dir:      Directory relative to PROJECT_ROOT
        since_seconds:  Only upload files modified within this many seconds
        since_epoch:    Only upload files with mtime > this Unix timestamp.
                        Use in cron: START=$(date +%s) before scanner run.

    Returns:
        Number of files uploaded.
    """
    local_path = PROJECT_ROOT / local_dir
    if not local_path.exists():
        logger.debug(f"Skipping {local_dir} (directory not found)")
        return 0

    now = time.time()
    count = 0

    for f in sorted(local_path.rglob('*')):
        if not f.is_file():
            continue
        mtime = f.stat().st_mtime
        if since_seconds is not None and (now - mtime) > since_seconds:
            continue
        if since_epoch is not None and mtime < since_epoch:
            continue

        # S3 key = path relative to project root (forward slashes for Windows compat)
        rel = f.relative_to(PROJECT_ROOT)
        s3_key = str(rel).replace('\\', '/')

        try:
            s3.upload_file(str(f), bucket, s3_key)
            logger.info(f"  ✓ {s3_key}")
            count += 1
        except ClientError as e:
            logger.error(f"  ✗ {s3_key}: {e}")

    return count


def main():
    parser = argparse.ArgumentParser(
        description='Upload scanner_output to S3 for Streamlit Cloud access'
    )
    parser.add_argument(
        '--dirs', nargs='+', default=DEFAULT_DIRS, metavar='DIR',
        help=f'Directories to upload (relative to project root). '
             f'Default: {" ".join(DEFAULT_DIRS)}'
    )
    parser.add_argument(
        '--hours', type=float, default=None, metavar='N',
        help='Only upload files modified in the last N hours (default: all files)'
    )
    parser.add_argument(
        '--since-epoch', type=float, default=None, metavar='TIMESTAMP',
        help='Only upload files with mtime > TIMESTAMP (Unix epoch seconds). '
             'Use in cron: START=$(date +%%s) before scanner, pass as --since-epoch $START'
    )
    parser.add_argument(
        '--bucket', default=S3_BUCKET,
        help=f'S3 bucket name (default: {S3_BUCKET})'
    )
    args = parser.parse_args()

    since_seconds = args.hours * 3600 if args.hours else None
    since_epoch = args.since_epoch

    try:
        s3 = _s3_client()
    except NoCredentialsError:
        logger.error(
            "No AWS credentials found. Set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY "
            "in environment or .env file."
        )
        sys.exit(1)

    logger.info(f"Uploading to s3://{args.bucket} ...")
    if since_epoch:
        logger.info(f"  (only files with mtime > epoch {since_epoch:.0f} — current run only)")
    elif since_seconds:
        logger.info(f"  (only files modified in last {args.hours:.1f} h)")

    total = 0
    for d in args.dirs:
        n = upload_dir(s3, args.bucket, d, since_seconds, since_epoch)
        total += n
        if n:
            logger.info(f"  {d}: {n} file(s)")
        else:
            logger.info(f"  {d}: nothing new")

    logger.info(f"Done — {total} file(s) uploaded to s3://{args.bucket}")


if __name__ == '__main__':
    main()
