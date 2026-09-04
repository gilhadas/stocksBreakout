#!/usr/bin/env python3
"""
fork_books.py — seed the live swap A/B by cloning each user's control book.

Why this exists
---------------
The experiment asks one question: *does swapping a weak holding for a
better-scoring skipped signal make money?* CLAUDE.md §11 measured auto
swap-on-skip in the backtest at −4.68 Sharpe on all.txt 2026 and 0 swaps fired
in every spy_plus/plus.txt year — one strongly negative sample and no data
anywhere else. This runs it for real.

For the answer to mean anything the two arms must start identical, so this
copies `auto_portfolio.json` → `auto_portfolio_autoswap.json` byte for byte and
stamps BOTH books with the same fork record. Everything before `fork.date` is
shared history and must be excluded from every comparison metric — see
`book_compare.compare_books()`, which does exactly that.

Safety
------
Refuses to overwrite an existing autoswap book without --force. A silent re-run
would reset the experiment to zero and destroy however many weeks of divergence
had accumulated, with no way to tell it had happened.

Usage
-----
    python3 fork_books.py --dry-run          # show what would happen
    python3 fork_books.py                    # fork every user
    python3 fork_books.py --user <uuid>      # fork one user
    python3 fork_books.py --force            # re-fork, discarding the variant
"""
import argparse
import sys

import auto_portfolio as ap
from auto_portfolio import BOOKS, DEFAULT_BOOK


VARIANT_BOOKS = [b for b in BOOKS if b != DEFAULT_BOOK]


def _users() -> list[tuple[str, str]]:
    """Return [(user_id, label)]. Falls back to the flat default book.

    ⚠ RUN THIS ON THE PRODUCTION BOX, NOT LOCALLY. `users.db` is per-box and is
    never synced to S3 (api/database.py), so the repo's local copy is a stale
    artifact of the retired Mac deployment — it still lists users deleted in
    production, whose books were archived on 2026-08-04 (CLAUDE.md §23.1).
    Forking from the stale list would resurrect a book for a user who no longer
    exists, in the live S3 bucket.
    """
    try:
        from api.database import get_db
        from api.models import User as _User
        db = next(get_db())
        return [(u.id, u.email) for u in db.query(_User).all()]
    except Exception as exc:
        print(f"  ! could not read users DB ({exc}) — falling back to default book")
        return [(None, 'default')]


def fork_user(user_id, label, *, force=False, dry_run=False) -> dict:
    """Clone one user's control book into every variant book.

    Returns {'forked': [book], 'skipped': [book], 'label': label}.
    """
    out = {'label': label, 'forked': [], 'skipped': []}
    control = ap.load(user_id=user_id, book=DEFAULT_BOOK)

    n_pos = len(control.get('positions', []))
    n_closed = len(control.get('closed', []))

    if not (n_pos or n_closed or control.get('processed_files')):
        # Not fatal — a genuinely new user starts empty and both books will fill
        # from the same scans. But it is also exactly what a STALE users.db entry
        # looks like (see _users), so say so rather than forking in silence.
        print(f"  ! {label}: control book is empty — new user, or a stale users.db "
              f"entry for a deleted user? (see fork_books._users)")

    for variant in VARIANT_BOOKS:
        existing = ap.load(user_id=user_id, book=variant)
        # A book that has never been written comes back as _empty(); treat a
        # book with any state at all as live and refuse to clobber it. Shared
        # with the automatic path (ap._load_for_write → ap.ensure_forked) so the
        # manual and automatic forks cannot disagree about what "already forked"
        # means — the §20 one-filter-not-two rule.
        is_live = ap._book_has_state(existing)
        if is_live and not force:
            print(f"  = {label} [{variant}]: already forked "
                  f"({len(existing.get('positions', []))} positions, "
                  f"fork={(existing.get('fork') or {}).get('date', '?')}) — skipped, "
                  f"pass --force to overwrite")
            out['skipped'].append(variant)
            continue

        # What the variant will actually contain depends on its seed. A book
        # seeded from control gets control's holdings; a `seed: None` book
        # (universe variants — it could never have bought control's names) starts
        # empty at INITIAL_CAPITAL. Reporting control's counts for both made the
        # 2026-09-04 trend fork print "WOULD fork (12 positions, 21 closed,
        # capital $99,111.00)" for a book that was about to be created empty —
        # a dry run that describes the wrong outcome is worse than no dry run.
        if ap.BOOKS[variant].get('seed'):
            shape = (f"{n_pos} positions, {n_closed} closed, "
                     f"capital ${control.get('capital', 0):,.2f}")
        else:
            shape = (f"empty book, capital ${ap.INITIAL_CAPITAL:,.2f} "
                     f"— seed=None, not a clone of control")

        if dry_run:
            print(f"  → {label} [{variant}]: WOULD fork ({shape})")
        else:
            # The clone itself lives in auto_portfolio.ensure_forked — the same
            # code the automatic first-write path runs — so an explicit fork and
            # an auto-fork produce byte-identical books. This script keeps the
            # --force / --dry-run / reporting shell around it.
            ap.ensure_forked(user_id=user_id, book=variant, force=True)
            print(f"  ✓ {label} [{variant}]: forked ({shape})")
        out['forked'].append(variant)

    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--user', help='fork only this user_id (default: all users)')
    p.add_argument('--force', action='store_true',
                   help='overwrite an existing variant book — DESTROYS the running experiment')
    p.add_argument('--dry-run', action='store_true', help='show what would happen, write nothing')
    args = p.parse_args(argv)

    if not VARIANT_BOOKS:
        print('No variant books configured in auto_portfolio.BOOKS — nothing to fork.')
        return 1

    targets = _users()
    if args.user:
        targets = [(uid, lbl) for uid, lbl in targets if uid == args.user]
        if not targets:
            print(f"user {args.user} not found")
            return 1

    mode = 'DRY RUN — ' if args.dry_run else ''
    print(f"{mode}Forking {DEFAULT_BOOK} → {', '.join(VARIANT_BOOKS)} "
          f"for {len(targets)} user(s)\n")

    if args.force and not args.dry_run:
        print("  !! --force: existing variant books will be DISCARDED, resetting\n"
              "     the experiment to zero. Ctrl-C now if that is not intended.\n")

    results = [fork_user(uid, lbl, force=args.force, dry_run=args.dry_run)
               for uid, lbl in targets]

    forked = sum(len(r['forked']) for r in results)
    skipped = sum(len(r['skipped']) for r in results)
    print(f"\n{mode}{forked} book(s) forked, {skipped} skipped.")
    if forked and not args.dry_run:
        print("Compare with:  python3 -c \"import book_compare, json; "
              "print(json.dumps(book_compare.compare_books(), indent=2, default=str))\"")
    return 0


if __name__ == '__main__':
    sys.exit(main())
