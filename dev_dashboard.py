"""
dev_dashboard.py — run the Streamlit dashboard locally against a SANDBOX copy
of the books, with S3 writes disabled and auth pre-satisfied.

    streamlit run dev_dashboard.py

Why this exists
---------------
`streamlit run app.py` on this Mac is NOT a safe rehearsal. `utils._is_cloud()`
returns True here — AWS keys are present in both the environment and
`.streamlit/secrets.toml` — so the dashboard reads *and writes* the live
production books in S3. Several buttons on the portfolio page are writes:
Scan & Add, Recalculate, Reset, Simulate Trailing Stops, Rebuild Missed, Execute
Swap, Undo Swap.

That was merely risky before the book A/B. It is worse now: "Scan & Add" with the
book selector on **Auto-swap** runs the auto-swap stage, which really closes and
opens positions and really sends Telegram. There is no dry-run flag on that path.

So this runner:
  1. copies the current books into `scanner_output/_devsandbox/` (a read of S3,
     never a write),
  2. forces `_is_cloud()` False and repoints the project root at the sandbox, so
     every subsequent read and write is local-only,
  3. mints a JWT locally so you land straight on the app instead of hunting for
     a password or bouncing through Google OAuth,
  4. then runs `app.py` unchanged.

Nothing here touches production. Delete `scanner_output/_devsandbox/` whenever
you want a fresh copy — it is rebuilt on every launch by default.

Env knobs (all optional):
    SB_DEV_USER=<user_id|email>   which user to impersonate (default: first)
    SB_DEV_KEEP=1                 on a fresh `streamlit run`, reuse whatever
                                   sandbox is already on disk instead of
                                   re-pulling from S3. Bootstrapping itself only
                                   ever runs once per running server regardless
                                   — this only matters across process restarts.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
SANDBOX = REPO / 'scanner_output' / '_devsandbox'


def _users() -> list[tuple[str, str]]:
    try:
        from api.database import get_db
        from api.models import User
        db = next(get_db())
        return [(u.id, u.email) for u in db.query(User).all()]
    except Exception as exc:                                # pragma: no cover
        print(f"[dev] could not read users.db: {exc}")
        return []


def _pick_user(users):
    want = os.environ.get('SB_DEV_USER', '').strip()
    if want:
        for uid, email in users:
            if want in (uid, email):
                return uid, email
        raise SystemExit(f"[dev] SB_DEV_USER={want!r} not found in "
                         f"{[e for _, e in users]}")
    return users[0] if users else (None, 'anonymous')


def _seed_sandbox_via_subprocess(user_id: str | None) -> bool:
    """Run `_seed_sandbox` in a fresh, non-Streamlit process.

    S3 reads that work fine from a plain script raise
    `AioSession.__init__() got an unexpected keyword argument 'bucket'` when
    called from INSIDE Streamlit's script-runner thread — a threading/event-loop
    interaction with aiobotocore, the same bug class CLAUDE.md §19 documents
    (the `skip_instance_cache=True` workaround in `utils._s3_fs()` apparently
    doesn't survive Streamlit's thread model). Confirmed: `python3 -c
    "from utils import load_json; load_json(...)"` reads the real book fine;
    the identical call made from a running `streamlit run` process fails every
    time. Shelling out to a genuinely separate interpreter sidesteps it —
    that subprocess never touches Streamlit's threading at all.

    Returns True on success. On failure the sandbox stays whatever it already
    was (possibly empty on a first run) rather than silently claiming success.
    """
    import subprocess
    result = subprocess.run(
        [sys.executable, str(REPO / 'dev_dashboard.py'),
         '--seed-subprocess', user_id or '__NONE__'],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
    )
    for line in result.stdout.splitlines():
        if line.startswith('[dev]'):
            print(line)
    if result.returncode != 0:
        print(f"[dev] seed subprocess failed (exit {result.returncode}): "
             f"{result.stderr.strip()[-800:]}")
        return False
    return True


def _seed_sandbox(user_id: str | None) -> None:
    """Copy the live books into the sandbox. Reads S3, never writes it."""
    import auto_portfolio as ap
    from utils import load_json

    if SANDBOX.exists() and os.environ.get('SB_DEV_KEEP'):
        print(f"[dev] reusing existing sandbox at {SANDBOX}")
        return
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)

    copied = 0
    for uid in {user_id, None}:
        for book in ap.BOOKS:
            rel = ap._portfolio_path_for(uid, book)
            data = load_json(rel)          # S3 read (or local fallback)
            if data is None:
                continue
            dst = SANDBOX / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            import json
            dst.write_text(json.dumps(data, indent=2, default=str))
            copied += 1
            print(f"[dev] seeded {rel} "
                  f"({len(data.get('positions', []))} positions, "
                  f"{len(data.get('skipped_cash', []))} skipped)")

    # Signal files are read-only inputs; symlink so scans see real data without
    # copying hundreds of CSVs.
    for name in ('signals', 'logs'):
        src, dst = REPO / 'scanner_output' / name, SANDBOX / 'scanner_output' / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists() and not dst.exists():
            dst.symlink_to(src)

    if not copied:
        print("[dev] no books found to seed — the sandbox will start empty")

    # NB: the variant books are NOT forked here. This function runs with S3 live
    # (that is the whole point — it is the read), and ap.ensure_forked() writes
    # through ap._save, which would land in the production bucket. Forking is
    # done by _fork_sandbox_books() once the caller has cut S3 off.


def _fork_sandbox_books(user_id: str | None) -> None:
    """Fork each variant book off control, inside the sandbox.

    ⚠ Only safe to call AFTER utils._is_cloud has been forced False and the
    project root repointed at SANDBOX — ensure_forked writes through ap._save,
    which goes to the production S3 bucket otherwise.

    Why it is needed at all: _seed_sandbox only copies books that already exist
    upstream, and the autoswap book does not exist until someone forks it. Without
    this the sandbox held control alone, the dashboard rendered Auto-swap as a
    fresh $100k/0-position book, and clicking Scan Signals wrote real positions
    into it — starting the A/B from mismatched state with no error anywhere
    (observed 2026-08-11: control 14 positions / 62 files vs autoswap 2 / 2).
    Forking here makes a local rehearsal the same shape as the deployed system.
    """
    import auto_portfolio as ap
    from utils import _is_cloud
    if _is_cloud():                                          # pragma: no cover
        print("[dev] REFUSING to fork — S3 is still live; this would write "
              "to the production bucket")
        return
    for uid in {user_id, None}:
        for book in ap.BOOKS:
            if book == ap.DEFAULT_BOOK:
                continue
            before = ap.load(uid, book)
            if ap._book_has_state(before):
                continue
            forked = ap.ensure_forked(uid, book)
            print(f"[dev] forked {book} for {(uid or 'default')[:8]} "
                  f"({len(forked.get('positions', []))} positions)")


def _bootstrap():
    """Seed the sandbox and cut every write path to shared state.

    MUST run exactly once per server process, not once per script execution.
    Streamlit reruns dev_dashboard.py top-to-bottom on every interaction — every
    button click, every tab switch, even the portfolio page's own 5-minute
    auto-refresh timer — so a plain module-level call here re-seeds on EVERY
    rerun: `shutil.rmtree(SANDBOX)` deletes the tree a page might be reading
    from that same instant (a page landing mid-wipe sees "file not found" and
    renders an empty book), and any in-session edits — an executed swap, an
    added position — get silently erased on the very next click.

    `st.cache_resource` is Streamlit's own idiom for "run once for the life of
    this server process, shared across every rerun and every session" — exactly
    the seed-once semantics wanted here, with no hand-rolled state tracking.
    """
    import streamlit as st

    @st.cache_resource
    def _once():
        users = _users()
        user_id, email = _pick_user(users)

        # Seed BEFORE cutting S3 off, since seeding is what reads it. Via a
        # subprocess — see _seed_sandbox_via_subprocess for why in-process
        # fails here. Fall back to the direct call so a working environment
        # (e.g. once the underlying aiobotocore/Streamlit incompatibility is
        # fixed) doesn't pay subprocess overhead for no reason forever, and so
        # a subprocess-launch failure (not the S3 bug — e.g. the interpreter
        # itself not being spawnable) still gets a real attempt.
        if not _seed_sandbox_via_subprocess(user_id):
            print("[dev] subprocess seed failed — retrying in-process "
                 "(may hit the S3-in-Streamlit-thread bug and come up empty)")
            _seed_sandbox(user_id)

        # Cut every write path to shared state. Order matters: patch before the
        # first load()/save(), i.e. before app.py imports the pages.
        import utils
        utils._is_cloud = lambda: False
        utils._PROJECT_ROOT = str(SANDBOX)
        utils.PROJECT_ROOT = SANDBOX

        import auto_portfolio as ap
        ap._ENTRY_CACHE_PATH = str(SANDBOX / 'scanner_output' / 'portfolio' /
                                   'entry_price_cache.json')

        # Now that S3 is off and the root points at SANDBOX, it is safe to write.
        _fork_sandbox_books(user_id)

        token = ''
        try:
            from trading_api_kit.auth import create_user_token
            if user_id:
                token = create_user_token(user_id, email)
        except Exception as exc:
            print(f"[dev] could not mint a token ({exc}) — "
                  f"the page will load as an anonymous user")
        return user_id, email, token

    return _once()


def main() -> None:
    sys.path.insert(0, str(REPO))

    user_id, email, token = _bootstrap()

    # Session-state assignment stays outside the cached function: cache_resource
    # is process-wide, but session_state is per-browser-session, and a second
    # tab/session opened against the same server still needs its own login.
    import streamlit as st
    if not st.session_state.get('authenticated'):
        st.session_state.token = token
        st.session_state.authenticated = True

    st.sidebar.warning(
        f"🧪 DEV SANDBOX — {email}\n\n"
        f"Books are local copies under `_devsandbox/`. S3 is disabled, so every "
        f"write stays here. Safe to click anything, including Scan & Add on the "
        f"Auto-swap book.",
        icon="🧪",
    )

    # 4. Run the real app unchanged.
    import runpy
    runpy.run_path(str(REPO / 'app.py'), run_name='__main__')


if __name__ == '__main__':
    # `--seed-subprocess <user_id|__NONE__>` is the escape hatch spawned by
    # _seed_sandbox_via_subprocess: run the S3 read in a plain interpreter,
    # outside Streamlit's script-runner thread, then exit. Never reaches
    # main()/app.py — this process's only job is to write the sandbox files.
    if len(sys.argv) >= 3 and sys.argv[1] == '--seed-subprocess':
        _uid = sys.argv[2]
        _seed_sandbox(None if _uid == '__NONE__' else _uid)
    else:
        main()
