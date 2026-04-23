#!/usr/bin/env python3
"""
One-time migration: copy existing portfolio files to the default user's directory.

Run ONCE before (or immediately after) the first server restart with the new code.
The original files are kept — background agents still use them.

Usage:
    venv/bin/python scripts/migrate_portfolio_to_user.py
"""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

USER_ID = os.getenv('DEFAULT_USER_ID', '')
if not USER_ID:
    print("Error: DEFAULT_USER_ID not set in .env")
    sys.exit(1)

PORTFOLIO = Path(__file__).resolve().parent.parent / 'scanner_output' / 'portfolio'

FILES_TO_MIGRATE = [
    'auto_portfolio.json',
    'scalp_portfolio.json',
    'portfolio.json',
]

user_dir = PORTFOLIO / USER_ID
user_dir.mkdir(parents=True, exist_ok=True)
print(f"Migrating to: {user_dir}")

for fname in FILES_TO_MIGRATE:
    src = PORTFOLIO / fname
    dst = user_dir / fname
    if not src.exists():
        print(f"  skip {fname} (not found)")
        continue
    if dst.exists():
        print(f"  skip {fname} (already migrated)")
        continue
    shutil.copy2(src, dst)
    print(f"  copied {fname}")

snapshots_src = PORTFOLIO / 'snapshots'
if snapshots_src.exists():
    snapshots_dst = user_dir / 'snapshots'
    if not snapshots_dst.exists():
        shutil.copytree(snapshots_src, snapshots_dst)
        print(f"  copied snapshots/")

# S3 migration (only if cloud storage is configured)
try:
    import boto3, toml
    secrets_path = Path(__file__).resolve().parent.parent / '.streamlit' / 'secrets.toml'
    secrets = toml.loads(secrets_path.read_text())
    s3 = boto3.client(
        's3',
        aws_access_key_id     = secrets.get('AWS_ACCESS_KEY_ID') or secrets.get('key'),
        aws_secret_access_key = secrets.get('AWS_SECRET_ACCESS_KEY') or secrets.get('secret'),
        region_name           = secrets.get('AWS_DEFAULT_REGION') or secrets.get('region', 'eu-central-1'),
    )
    BUCKET = 'stocks-breakout-scanner-s3-bucket'
    for fname in FILES_TO_MIGRATE:
        src_key = f'scanner_output/portfolio/{fname}'
        dst_key = f'scanner_output/portfolio/{USER_ID}/{fname}'
        try:
            s3.copy_object(
                Bucket=BUCKET,
                CopySource={'Bucket': BUCKET, 'Key': src_key},
                Key=dst_key,
            )
            print(f"  S3: copied {fname}")
        except Exception as e:
            print(f"  S3: skip {fname} ({e})")
except Exception as e:
    print(f"  S3 migration skipped: {e}")

print("\nDone. Original files kept — background agents still read from them.")
print("Restart the API server: kill $(lsof -ti:8000)")
