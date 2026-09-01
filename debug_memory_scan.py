#!/usr/bin/env python3
"""
Local reproduction harness for the wide-scan OOM (CLAUDE.md §15-§18).

WHY THIS EXISTS
---------------
Four wide scans were OOM-killed in July 2026 and every diagnosis had to be made
post-mortem, on the box, from `dmesg` and a truncated log — a slow loop that
produced two wrong root causes (§15 "the cap is too tight", §17 "the box is too
small") before the real one (§18: `scan_and_add` was replaying the whole S3
archive). Each wrong turn cost a deploy and a day.

This runs the same shapes **locally, offline, in seconds**, so a memory
hypothesis can be tested before it becomes a resize.

SAFETY
------
The replay mode is sandboxed and must stay that way:

* ``utils._is_cloud`` is forced False, so nothing reads or writes S3. This is
  not optional — the first draft of this harness wrote a probe portfolio into
  the *production* bucket, because ``save_json`` mirrors to S3 whenever AWS
  creds are present, and then read it back on the next arm and silently
  invalidated the comparison.
* The probe book lives in a temp directory outside the project, so
  ``scanner_output/portfolio/`` is untouched.
* Every yfinance call is stubbed. The arm measures memory, not the network.

MODES
-----
``replay``   The §18 shape. Synthesises an archive of N signal CSVs and runs
             ``scan_and_add`` over it twice — once with the age bound that
             ``bc06c62`` added, once without — **each in its own process**, so
             one arm's unreleased allocator arenas cannot inflate the other's
             baseline. If the bound works, arm A plateaus and arm B climbs with
             files consumed.

``scan``     The real detection pipeline on a subsampled watchlist, traced end
             to end. Needs network. Use ``--symbols`` to keep it short.

``finbert``  Isolates model residency, the §16 measurement: import, first
             inference, then a batch 4x larger. If the last two are equal the
             cost is residency, and batching differently saves nothing.

USAGE
-----
    python debug_memory_scan.py replay --files 400
    python debug_memory_scan.py replay --files 858 --rows 25   # the prod shape
    python debug_memory_scan.py scan --symbols 40
    python debug_memory_scan.py finbert

READING THE OUTPUT
------------------
One question: **does the curve plateau?** RSS that keeps tracking items consumed
is a leak. RSS that rises once and holds is a working set. §18's lesson is that
peak == cap on every observation means unbounded growth, and no amount of extra
RAM fixes it — the process just takes longer to die.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Tracing must be on before the pipeline modules read the env var.
os.environ.setdefault('SB_MEM_TRACE', '1')

# --real-archive needs AWS credentials for utils._is_cloud() to return True.
# In the container those arrive as real env vars (compose `env_file: .env`), but
# a local run gets them only from .env — and nothing on this import path loads it
# (auto_portfolio and utils do not import config, which is where load_dotenv()
# normally happens). Without this the arm silently lists ZERO files and reports
# "avoided 0 file loads", which reads like a benign result rather than a broken
# measurement. Loaded here, before any module reads the credentials.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Python 3.14 compatibility: create the event loop before anything imports
# ib_insync, or eventkit raises at import time. Same guard as breakout_scanner.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import memory_trace as memt  # noqa: E402

logger = logging.getLogger('debug_memory_scan')

RESULT_PREFIX = '@@ARM@@ '

# A synthetic universe. Bigger than the live watchlist's overlap on any one day,
# so the (symbol|date)-keyed caches grow the way they do against a real archive.
_SYMBOLS = [f'SY{n:04d}' for n in range(400)]


# ─────────────────────────────────────────────────────────────────────────────
# Sandbox
# ─────────────────────────────────────────────────────────────────────────────


def _sandbox(tmpdir: str, allow_s3_reads: bool = False) -> None:
    """Cut every path out of this process that reaches shared state.

    Order matters: patch before the first load()/save() call, not after.

    ``allow_s3_reads`` keeps `_is_cloud()` True so `list_files`/`load_data` hit
    the real bucket (the point of the ``--real-archive`` arm), while replacing
    `save_json` with a local-only writer. Reads stay read-only; writes can never
    reach S3 either way.
    """
    import auto_portfolio as ap
    import utils

    if allow_s3_reads:
        _real_save_json = utils.save_json

        def local_only_save_json(data, local_path, s3_path=None):
            import json as _json
            os.makedirs(os.path.dirname(local_path) or '.', exist_ok=True)
            with open(local_path, 'w') as fh:
                fh.write(_json.dumps(data, indent=2, default=str))

        utils.save_json = local_only_save_json
    else:
        utils._is_cloud = lambda: False      # no S3 read, no S3 write

    # Mirror the real signature, including `book`. Two reasons this matters:
    #   1. Every book variant must land on its OWN file inside the sandbox, or a
    #      sandboxed run of the autoswap book silently reads and writes the
    #      control book's probe file and the measurement is meaningless.
    #   2. This is a permanent module-level reassignment, not a monkeypatch — it
    #      is never restored. Under pytest that leaks into every later test in
    #      the session, so a lambda pinned to yesterday's parameter list turns
    #      unrelated tests into TypeErrors. (It did exactly that.)
    def _sandboxed_path(user_id=None, book=ap.DEFAULT_BOOK):
        suffix = ap.BOOKS[book or ap.DEFAULT_BOOK]['suffix']
        return os.path.join(tmpdir, f'auto_portfolio{suffix}.json')

    ap._portfolio_path_for = _sandboxed_path
    ap._ENTRY_CACHE_PATH = os.path.join(tmpdir, 'entry_price_cache.json')
    ap._ENTRY_PRICE_CACHE.clear()
    ap._SPLIT_CACHE.clear()
    ap._CURRENT_PRICE_CACHE.clear()


def _synth_frame(n_rows: int, day_offset: int):
    """One day's signal CSV, shaped like the live writer's output."""
    import pandas as pd

    rows = []
    for i in range(n_rows):
        sym = _SYMBOLS[(i * 7 + day_offset * 13) % len(_SYMBOLS)]
        price = 50.0 + ((i * 7 + day_offset) % 200)
        rows.append({
            'Symbol': sym,
            'Quality': 'PREMIUM',
            'Type': 'BOUNCE',
            'Price': round(price, 2),
            'Stop': round(price * 0.97, 2),
            'Target': round(price * 1.12, 2),
            'R:R': 2.4,
            'WinProb': 62.0,
            'Dist': 12.5,
            'Vol': 1.8,
            'MinerviniScore': 7,
            'Mode': 'swing',
            'Score': 74.0,
            # Real signal CSVs carry ~40 columns of diagnostics; approximate the
            # per-row weight so the DataFrame cost is not understated.
            'Notes': 'x' * 200,
        })
    return pd.DataFrame(rows)


def _install_archive(n_files: int, n_rows: int) -> list[str]:
    """Stub list_files/load_data with an archive spanning n_files trading days.

    Frames are generated on demand rather than pre-built, so the harness's own
    footprint cannot masquerade as the pipeline's.
    """
    from datetime import datetime, timedelta

    import auto_portfolio as ap
    import utils

    today = datetime.now(ap._NY_TZ).date()
    names = [
        f"signals_swing_"
        f"{(today - timedelta(days=n_files - 1 - i)).strftime('%Y%m%d')}_1033{i % 60:02d}.csv"
        for i in range(n_files)
    ]
    loaded: list[str] = []

    utils.list_files = lambda *a, **k: list(names)

    def fake_load_data(path, *_a, **_k):
        loaded.append(path.rsplit('/', 1)[-1])
        return _synth_frame(n_rows, len(loaded))

    utils.load_data = fake_load_data
    return loaded


def _install_real_archive(n_files: int) -> list[str]:
    """Read the OLDEST n_files of the real S3 archive — read-only.

    The synthetic arm stubs `load_data`, which removes exactly the two layers
    most likely to hold memory: s3fs (which buffers whole objects and keeps a
    dircache) and pandas' real CSV parsing of ~40-column files. This arm keeps
    both. Oldest-first because that is what a far-behind book actually replays.
    """
    import auto_portfolio as ap
    import utils

    real_list, real_load = utils.list_files, utils.load_data
    names = sorted(real_list(ap._SIGNALS_DIR, 'signals_*.csv'))[:n_files]
    loaded: list[str] = []

    utils.list_files = lambda *a, **k: list(names)

    def counting_load(path, *a, **k):
        loaded.append(path.rsplit('/', 1)[-1])
        return real_load(path, *a, **k)

    utils.load_data = counting_load
    return loaded


def _stub_network() -> None:
    """Neutralise yfinance so the curve measures memory, not latency.

    Returns a plausible price rather than failing: the admission path must run
    to completion, or the accumulators under test (the entry/split caches,
    skipped_cash) never get exercised.
    """
    import auto_portfolio as ap

    def fake_fetch(symbol, date_added, csv_price):
        # Populate the real cache dicts — they are part of what is measured.
        ap._ENTRY_PRICE_CACHE[f'{symbol}|{date_added}'] = float(csv_price)
        ap._CURRENT_PRICE_CACHE[symbol] = (float(csv_price) * 1.02, 1e12)
        return float(csv_price), float(csv_price) * 1.02

    def fake_split(symbol, since_date):
        ap._SPLIT_CACHE[f'{symbol}|{since_date}'] = 1.0
        return 1.0

    ap._fetch_entry_and_current = fake_fetch
    ap._detect_split_factor = fake_split
    ap._save_entry_price_cache = lambda *a, **k: None
    ap._compute_atr_pct = lambda symbol: 3.0
    ap._queue_ib_order = lambda pos: None

    # The admission path also does a yfinance *sector* lookup per candidate
    # (auto_portfolio.py:530, via the portfolio-balance check). Unstubbed, that
    # is one network round trip per synthetic ticker and the run never finishes.
    import sentiment
    sentiment.get_sector_for_ticker = lambda ticker: 'Technology'


# ─────────────────────────────────────────────────────────────────────────────
# One arm — always runs in its own process (see cmd_replay)
# ─────────────────────────────────────────────────────────────────────────────


def cmd_arm(args) -> int:
    import auto_portfolio as ap

    with tempfile.TemporaryDirectory(prefix='memtrace-arm-') as tmpdir:
        _sandbox(tmpdir, allow_s3_reads=args.real_archive)
        loaded = (_install_real_archive(args.files) if args.real_archive
                  else _install_archive(args.files, args.rows))
        _stub_network()
        # A window wider than the archive == the pre-bc06c62 behaviour.
        ap.SIGNAL_MAX_AGE_DAYS = args.files * 10 if args.age < 0 else args.age

        memt.reset()
        if memt.alloc_start():
            # First diff only seeds the baseline snapshot; without this the
            # post-run call has nothing to compare against and prints nothing.
            memt.alloc_diff('arm:baseline')
        rss_before = memt.rss_mb()
        result = ap.scan_and_add(user_id='probe', notify=False)
        rss_after = memt.rss_mb()
        # Attribute the growth to source lines (stderr; parent shows it with -v).
        memt.alloc_diff(f'arm:{args.name}', top=15)

        curve = []
        for s in memt.samples():
            if s.label.startswith('signal_files['):
                idx = int(s.label.split('[')[1].rstrip(']'))
                curve.append([idx, round(s.rss, 1),
                              s.extra.get('entry_cache', 0)])

        book = ap.load(user_id='probe')
        payload = {
            'name': args.name,
            'age_days': ap.SIGNAL_MAX_AGE_DAYS,
            'rss_before': round(rss_before, 1),
            'rss_after': round(rss_after, 1),
            'growth': round(rss_after - rss_before, 1),
            'files_loaded': len(loaded),
            'added': result.get('added', 0),
            'entry_cache': len(ap._ENTRY_PRICE_CACHE),
            'split_cache': len(ap._SPLIT_CACHE),
            'skipped_cash': len(book.get('skipped_cash', [])),
            'curve': curve,
        }
        print(RESULT_PREFIX + json.dumps(payload))
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# replay
# ─────────────────────────────────────────────────────────────────────────────


def _spawn_arm(name: str, age: int, files: int, rows: int, verbose: bool,
               real_archive: bool = False) -> dict:
    cmd = [sys.executable, os.path.abspath(__file__), '_arm',
           '--name', name, '--age', str(age),
           '--files', str(files), '--rows', str(rows)]
    if real_archive:
        cmd.append('--real-archive')
    logger.info(f'  ↳ {name}')
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          env={**os.environ, 'SB_MEM_TRACE': '1'})
    if verbose:
        sys.stderr.write(proc.stderr)
    for line in proc.stdout.splitlines():
        if line.startswith(RESULT_PREFIX):
            return json.loads(line[len(RESULT_PREFIX):])
    sys.stderr.write(proc.stdout[-4000:])
    sys.stderr.write(proc.stderr[-4000:])
    raise RuntimeError(f'arm {name!r} produced no result (exit {proc.returncode})')


def _plateau_verdict(curve: list) -> str:
    """Compare the per-item growth *rate* over the first half against the second.

    Ratio alone is not enough, and getting this wrong is exactly the §15/§17
    error in miniature: the first real-archive run added +575MB then +232MB, and
    a naive "second < half of first" test called that a plateau. It is not — a
    process still paying 3MB per file has not stopped growing. So require the
    later rate to have genuinely collapsed *and* the remaining growth to be
    small in absolute terms.
    """
    if len(curve) < 4:
        return 'too few samples'
    mid = len(curve) // 2
    first_items = max(1, curve[mid][0] - curve[0][0])
    second_items = max(1, curve[-1][0] - curve[mid][0])
    first = curve[mid][1] - curve[0][1]
    second = curve[-1][1] - curve[mid][1]
    rate1, rate2 = first / first_items, second / second_items

    shape = (f'1st half {first:+.1f}MB ({rate1:+.2f}MB/item), '
             f'2nd half {second:+.1f}MB ({rate2:+.2f}MB/item)')
    if second > 10.0 and rate2 > 0.15 * max(rate1, 0.01):
        return f'CLIMBING — {shape} → still paying per item, this is a leak'
    return f'plateau — {shape} → growth front-loaded, this is a working set'


def cmd_replay(args) -> int:
    source = ('the REAL S3 archive (read-only)' if args.real_archive
              else f'a synthetic {args.files}-file x {args.rows}-row archive')
    logger.info(f'Replaying {source} through scan_and_add, one process per arm.')

    unbounded = _spawn_arm('B — no age bound (pre-bc06c62)', -1,
                           args.files, args.rows, args.verbose, args.real_archive)
    bounded = _spawn_arm(f'A — age bound {args.age}d (shipped)', args.age,
                         args.files, args.rows, args.verbose, args.real_archive)

    logger.info('')
    logger.info('=' * 78)
    logger.info(' RESULT')
    logger.info('=' * 78)
    for arm in (bounded, unbounded):
        logger.info(f"  {arm['name']}")
        logger.info(f"      files loaded {arm['files_loaded']:>5}   "
                    f"ΔRSS {arm['growth']:+8.1f}MB   "
                    f"({arm['rss_before']:.0f} → {arm['rss_after']:.0f}MB)")
        logger.info(f"      entry_cache {arm['entry_cache']:>6}   "
                    f"split_cache {arm['split_cache']:>6}   "
                    f"added {arm['added']}")
        if arm['curve']:
            logger.info(f"      curve: {_plateau_verdict(arm['curve'])}")

    logger.info('')
    logger.info(f"  The age bound avoided "
                f"{unbounded['files_loaded'] - bounded['files_loaded']} file loads "
                f"and {unbounded['growth'] - bounded['growth']:+.1f}MB of growth.")

    # A null measurement must fail loudly, not read as a benign result.
    # The unbounded arm exists precisely to load everything, so zero files there
    # is definitionally broken — yet it prints as "avoided 0 file loads and
    # -2.6MB", which looks like an answer. Every realistic cause is an
    # environment fault, not a finding: no AWS credentials (_is_cloud() False,
    # so --real-archive silently falls back to an empty local dir), an empty
    # archive, or a bad prefix. §19 records the sibling trap of an A/B that was
    # invalid but looked fine; this is the guard that class of bug needs.
    if unbounded['files_loaded'] == 0:
        logger.error('')
        logger.error('  ✗ INVALID MEASUREMENT — the unbounded arm loaded 0 files.')
        logger.error('    Nothing was measured; the numbers above are import overhead.')
        if args.real_archive:
            logger.error('    --real-archive needs AWS credentials. Check that .env is')
            logger.error('    present and readable, and that utils._is_cloud() is True.')
        else:
            logger.error('    The synthetic archive failed to build — check --files/--rows.')
        logger.error('=' * 78)
        return 1

    if args.csv and unbounded['curve']:
        import csv as _csv
        with open(args.csv, 'w', newline='') as fh:
            w = _csv.writer(fh)
            w.writerow(['arm', 'files_loaded', 'rss_mb', 'entry_cache'])
            for arm in (bounded, unbounded):
                for row in arm['curve']:
                    w.writerow([arm['name'], *row])
        logger.info(f'  curve → {args.csv}')
    logger.info('=' * 78)
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# scan
# ─────────────────────────────────────────────────────────────────────────────


def cmd_scan(args) -> int:
    """Trace the real detection pipeline on a subsampled watchlist (needs network)."""
    from config import MODES
    from orchestrator import ScannerOrchestrator
    from utils import get_watchlist_from_file

    # run_scan_mode resolves this from args/MODES; scan_watchlist itself has no
    # default and will TypeError on None.
    timeframe = args.timeframe or MODES[args.mode]['default_timeframe']
    watchlist = get_watchlist_from_file(args.watchlist)
    if not watchlist:
        logger.error(f'No symbols in {args.watchlist}')
        return 1
    if args.symbols:
        watchlist = watchlist[:args.symbols]
    logger.info(f'Scanning {len(watchlist)} symbols from {args.watchlist} '
                f'(mode={args.mode}, tf={timeframe})')

    memt.alloc_start()
    memt.start_sampler(csv_path=args.csv, interval=args.interval)

    async def _go():
        orch = ScannerOrchestrator(None, yf_fallback=True)
        memt.census('before-detection')
        with memt.stage('detection'):
            results = await orch.scan_watchlist(
                watchlist=watchlist, mode=args.mode,
                timeframe=timeframe, detect_bounces=True,
            )
        memt.census('after-detection')
        memt.alloc_diff('after-detection')
        logger.info(f'{len(results)} signal(s)')

    try:
        asyncio.run(_go())
    finally:
        memt.stop_sampler()
        memt.report()
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# finbert
# ─────────────────────────────────────────────────────────────────────────────


def cmd_finbert(args) -> int:
    """Re-measure §16: is the FinBERT cost residency or inference volume?"""
    memt.mark('finbert:baseline')
    try:
        # analyze_text() scores a supplied list — no network, unlike the
        # project-level batch_sentiment() which fetches headlines first.
        from quantkit.sentiment.finbert import analyze_text
    except ImportError as exc:
        logger.error(f'FinBERT unavailable ({exc}); pip install transformers torch')
        return 1
    memt.mark('finbert:imported')

    headlines = ['Board approves a $2bn buyback',
                 'Guidance cut for the coming quarter',
                 'Analyst upgrades on strong demand',
                 'Shares slip on soft outlook'] * 4

    analyze_text(headlines[:4])
    memt.mark('finbert:first_inference (model resident)')
    analyze_text(headlines)
    memt.mark(f'finbert:batch_x{len(headlines) // 4}')

    logger.info('Equal last two marks ⇒ cost is residency; batching differently '
                'saves nothing (§16).')
    memt.report()
    return 0


# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--csv', help='write the RSS timeline / curve here')
    parser.add_argument('--interval', type=float, default=2.0,
                        help='background sampler interval, seconds (default 2)')
    parser.add_argument('--alloc', action='store_true',
                        help='enable tracemalloc site attribution (~2x memory)')
    parser.add_argument('--verbose', action='store_true',
                        help='show each arm subprocess stderr')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_replay = sub.add_parser('replay', help='§18 archive-replay A/B (offline)')
    p_replay.add_argument('--files', type=int, default=400,
                          help='archive size in signal CSVs (prod hit 858)')
    p_replay.add_argument('--rows', type=int, default=20,
                          help='signal rows per file')
    p_replay.add_argument('--age', type=int, default=None,
                          help='age bound to test (default: the shipped constant)')
    p_replay.add_argument('--real-archive', action='store_true',
                          help='read the real S3 signal archive (read-only) '
                               'instead of synthesising one — keeps s3fs and '
                               'real CSV parsing in the measurement')
    p_replay.set_defaults(func=cmd_replay)

    p_arm = sub.add_parser('_arm', help=argparse.SUPPRESS)
    p_arm.add_argument('--name', default='arm')
    p_arm.add_argument('--age', type=int, required=True,
                       help='-1 disables the bound')
    p_arm.add_argument('--files', type=int, required=True)
    p_arm.add_argument('--rows', type=int, required=True)
    p_arm.add_argument('--real-archive', action='store_true')
    p_arm.set_defaults(func=cmd_arm)

    p_scan = sub.add_parser('scan', help='real detection pipeline (needs network)')
    p_scan.add_argument('--watchlist', default='input/all.txt')
    p_scan.add_argument('--symbols', type=int, default=50,
                        help='subsample to N symbols (0 = all)')
    p_scan.add_argument('--mode', default='swing')
    p_scan.add_argument('--timeframe', default=None,
                        help="default: the mode's default_timeframe")
    p_scan.set_defaults(func=cmd_scan)

    p_fb = sub.add_parser('finbert', help='isolate FinBERT model residency')
    p_fb.set_defaults(func=cmd_finbert)

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
    for noisy in ('yfinance', 'peewee', 'urllib3', 'botocore', 'boto3',
                  's3transfer', 'streamlit', 'auto_portfolio', 'position_health'):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # The arm subprocess must keep stdout clean for its JSON result line, so its
    # trace goes to stderr instead (surfaced by the parent's --verbose).
    if args.cmd == '_arm':
        mt = logging.getLogger('memtrace')
        mt.handlers = [logging.StreamHandler(sys.stderr)]
        mt.propagate = False
        mt.setLevel(logging.INFO)

    if args.alloc:
        os.environ['SB_MEM_TRACE_ALLOC'] = '1'
    if args.cmd == 'replay' and args.age is None:
        import auto_portfolio as ap
        args.age = ap.SIGNAL_MAX_AGE_DAYS

    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
