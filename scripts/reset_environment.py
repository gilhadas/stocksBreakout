#!/usr/bin/env python3
"""
Environment Reset Script
------------------------
Steps:
  1. Migrate misplaced S3 objects (signals/, exits/, rejections/ at root)
     → scanner_output/signals/, scanner_output/exits/, scanner_output/rejections/
  2. Back up all S3 scanner_output/ objects → scanner_output_backup/
  3. Delete original scanner_output/ objects from S3
  4. Back up local scanner_output/ → scanner_output_backup/ (skip cache/)
  5. Wipe local scanner_output/ and recreate fresh empty tree

Usage:
  python scripts/reset_environment.py              # full run
  python scripts/reset_environment.py --dry-run    # preview only
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import boto3
from dotenv import load_dotenv

# ── Config ─────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUCKET       = "stocks-breakout-scanner-s3-bucket"
OUTPUT_DIR   = "scanner_output"
BACKUP_DIR   = "scanner_output_backup"

# Misplaced top-level prefixes to migrate first (bug in orchestrator.py — now fixed)
MISPLACED_PREFIXES = ["signals/", "exits/", "rejections/"]

# backtests/ lives at bucket ROOT — shared between all environments, never wiped.

# Fresh subdirs to recreate after wipe
FRESH_SUBDIRS = [
    "cache",
    "exits",
    "lists",
    "logs",
    "macd_rsi",
    "outcomes",
    "portfolio",
    "portfolio/snapshots",
    "rejections",
    "scan_decisions",
    "signal_reports",
    "signals",
    "state",
]

FRESH_PORTFOLIO_FILES = {
    "portfolio/portfolio.json":       {"positions": [], "capital": 100000, "closed": [], "skipped_cash": []},
    "portfolio/auto_portfolio.json":  {"positions": [], "capital": 100000, "closed": [], "skipped_cash": []},
    "portfolio/scalp_portfolio.json": {"positions": [], "capital": 100000, "closed": [], "skipped_cash": []},
}


# ── Helpers ─────────────────────────────────────────────────────────────────
def s3_client():
    load_dotenv(PROJECT_ROOT / ".env")
    return boto3.client(
        "s3",
        region_name=os.environ.get("AWS_DEFAULT_REGION", "eu-central-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )


def list_objects(s3, prefix):
    """Yield all object Keys under prefix."""
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield obj["Key"]


def s3_copy(s3, src_key, dst_key, dry_run):
    if dry_run:
        print(f"  [DRY] copy  s3://{BUCKET}/{src_key}  →  s3://{BUCKET}/{dst_key}")
        return
    s3.copy_object(
        Bucket=BUCKET,
        CopySource={"Bucket": BUCKET, "Key": src_key},
        Key=dst_key,
    )


def s3_delete_batch(s3, keys, dry_run):
    if not keys:
        return
    if dry_run:
        for k in keys:
            print(f"  [DRY] delete s3://{BUCKET}/{k}")
        return
    for i in range(0, len(keys), 1000):
        batch = [{"Key": k} for k in keys[i:i+1000]]
        s3.delete_objects(Bucket=BUCKET, Delete={"Objects": batch})


# ── Steps ────────────────────────────────────────────────────────────────────
def step1_migrate_misplaced(s3, dry_run):
    """Move signals/, exits/, rejections/ (top-level) → scanner_output/…
    Move scanner_output/backtests/ → backtests/ (shared root, permanent)."""
    print("\n── Step 1: Migrate misplaced S3 objects ──")
    total = 0

    # Fix orchestrator.py bug: signals/exits/rejections at wrong level
    for prefix in MISPLACED_PREFIXES:
        keys = list(list_objects(s3, prefix))
        if not keys:
            print(f"  {prefix}: nothing found, skipping")
            continue
        print(f"  {prefix}: {len(keys)} objects → scanner_output/{prefix}")
        to_delete = []
        for src_key in keys:
            dst_key = f"{OUTPUT_DIR}/{src_key}"
            s3_copy(s3, src_key, dst_key, dry_run)
            to_delete.append(src_key)
        s3_delete_batch(s3, to_delete, dry_run)
        total += len(keys)

    # Move backtests to bucket root so they're shared across environments
    backtest_src_prefix = f"{OUTPUT_DIR}/backtests/"
    backtest_keys = list(list_objects(s3, backtest_src_prefix))
    if backtest_keys:
        print(f"  scanner_output/backtests/: {len(backtest_keys)} objects → backtests/ (shared root)")
        to_delete = []
        for src_key in backtest_keys:
            rel = src_key[len(backtest_src_prefix):]
            dst_key = f"backtests/{rel}"
            s3_copy(s3, src_key, dst_key, dry_run)
            to_delete.append(src_key)
        s3_delete_batch(s3, to_delete, dry_run)
        total += len(backtest_keys)
    else:
        print(f"  scanner_output/backtests/: nothing found, skipping")

    print(f"  Migrated {total} objects total")


def step2_backup_s3(s3, dry_run):
    """Copy scanner_output/ → scanner_output_backup/ then delete originals."""
    print("\n── Step 2: S3 backup scanner_output/ → scanner_output_backup/ ──")
    keys = list(list_objects(s3, f"{OUTPUT_DIR}/"))
    if not keys:
        print("  No S3 objects under scanner_output/, skipping")
        return

    print(f"  Copying {len(keys)} objects…")
    for src_key in keys:
        rel     = src_key[len(f"{OUTPUT_DIR}/"):]
        dst_key = f"{BACKUP_DIR}/{rel}"
        s3_copy(s3, src_key, dst_key, dry_run)

    print(f"  Deleting {len(keys)} original objects…")
    s3_delete_batch(s3, keys, dry_run)
    print(f"  S3 backup complete: {len(keys)} objects → scanner_output_backup/")


def step3_backup_local(dry_run):
    """copytree scanner_output/ → scanner_output_backup/ (skip cache/)."""
    print("\n── Step 3: Local backup scanner_output/ → scanner_output_backup/ ──")
    src = PROJECT_ROOT / OUTPUT_DIR
    dst = PROJECT_ROOT / BACKUP_DIR

    if dst.exists():
        print(f"  ERROR: {BACKUP_DIR}/ already exists — aborting to avoid overwrite.")
        print(f"  Remove it manually if you want to re-run.")
        sys.exit(1)

    if not src.exists():
        print(f"  {OUTPUT_DIR}/ does not exist locally, skipping")
        return

    if dry_run:
        count = sum(1 for _ in src.rglob("*") if _.is_file() and "cache" not in _.parts)
        print(f"  [DRY] Would copy ~{count} files (excluding cache/) to {BACKUP_DIR}/")
        return

    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("cache"))
    count = sum(1 for _ in dst.rglob("*") if _.is_file())
    print(f"  Copied {count} files to {BACKUP_DIR}/")


def step4_reset_local(dry_run):
    """Wipe scanner_output/ and recreate fresh empty tree."""
    print("\n── Step 4: Reset local scanner_output/ ──")
    src = PROJECT_ROOT / OUTPUT_DIR

    if dry_run:
        print(f"  [DRY] Would delete {OUTPUT_DIR}/ and recreate empty tree")
        return

    if src.exists():
        shutil.rmtree(src)
        print(f"  Deleted {OUTPUT_DIR}/ (backtests/ are preserved at S3 root)")

    # Recreate subdirs
    for subdir in FRESH_SUBDIRS:
        (src / subdir).mkdir(parents=True, exist_ok=True)

    # Write fresh portfolio JSON files
    for rel_path, content in FRESH_PORTFOLIO_FILES.items():
        dest = src / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(content, indent=2))
        print(f"  Created {rel_path}")

    print(f"  Fresh {OUTPUT_DIR}/ tree ready ({len(FRESH_SUBDIRS)} subdirs)")


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Reset scanner_output environment")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without executing")
    args = parser.parse_args()

    if args.dry_run:
        print("═" * 60)
        print("DRY RUN — no changes will be made")
        print("═" * 60)

    s3 = s3_client()

    step1_migrate_misplaced(s3, args.dry_run)
    step2_backup_s3(s3, args.dry_run)
    step3_backup_local(args.dry_run)
    step4_reset_local(args.dry_run)

    print("\n" + "═" * 60)
    if args.dry_run:
        print("Dry run complete. Run without --dry-run to execute.")
    else:
        print("Environment reset complete.")
        print(f"  Backup:  ./{BACKUP_DIR}/  +  s3://{BUCKET}/{BACKUP_DIR}/")
        print(f"  Fresh:   ./{OUTPUT_DIR}/  +  s3://{BUCKET}/{OUTPUT_DIR}/  (empty)")
    print("═" * 60)


if __name__ == "__main__":
    main()
