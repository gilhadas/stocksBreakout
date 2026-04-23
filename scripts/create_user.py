#!/usr/bin/env python3
"""
Admin CLI: create a user in users.db.

Usage:
    venv/bin/python scripts/create_user.py --email user@example.com --name "Alice" --password "s3cret"
    venv/bin/python scripts/create_user.py --email user@example.com   # Google-only (no password)

Registration is invite-only — this is the only way to add users.
"""
import argparse
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Make project root importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

from api.database import create_tables, SessionLocal
from api.models import User
from api.auth import hash_password


def main():
    parser = argparse.ArgumentParser(description="Create a user account")
    parser.add_argument('--email', required=True, help="User email address")
    parser.add_argument('--name', default='', help="Display name")
    parser.add_argument('--password', default='', help="Password (omit for Google-only accounts)")
    args = parser.parse_args()

    create_tables()
    db = SessionLocal()

    try:
        existing = db.query(User).filter(User.email == args.email.lower()).first()
        if existing:
            print(f"Error: {args.email} is already registered (id={existing.id})")
            sys.exit(1)

        user = User(
            id=str(uuid.uuid4()),
            email=args.email.lower(),
            password_hash=hash_password(args.password) if args.password else None,
            name=args.name or args.email.split('@')[0],
            created_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.commit()

        print(f"Created user:")
        print(f"  id:    {user.id}")
        print(f"  email: {user.email}")
        print(f"  name:  {user.name}")
        print(f"  auth:  {'password' if args.password else 'Google OAuth only'}")
    finally:
        db.close()


if __name__ == '__main__':
    main()
