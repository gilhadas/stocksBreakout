"""
Zero-dependency memory tracing for the scan pipeline.

WHY THIS EXISTS
---------------
Four wide scans were OOM-killed in July 2026 (CLAUDE.md §15-§18) and every
"measured peak" recorded during them turned out to be just the cgroup cap: the
process expanded to fill whatever ceiling it was given, dying at ~1.37 GiB under
a 1400m limit and again at ~2.45 GiB under a 2500m one. Each diagnosis had to be
reconstructed afterwards from `dmesg` and the last line of a truncated log,
because nothing in the pipeline reported its own footprint while it ran.

This module makes the scan say where its memory goes *as it goes*. Design rules,
all of them learned from those incidents:

* **Off by default.** Enabled only by ``SB_MEM_TRACE=1``. Production cron lines
  are unchanged until someone opts in.
* **Stdlib only.** The container image is rebuilt from ``requirements.txt``; a
  debug tool that needs a new dependency is a tool you cannot use during an
  incident. No psutil.
* **Never raises into the caller.** A probe that fails degrades to a missing
  number. Instrumentation must not be able to kill a scan.
* **Reports the number the OOM killer actually reads.** §17 found anon-RSS and
  the cgroup charge diverge sharply (kills at 724-1009 MiB anon against a
  1024 MiB cap), because the cgroup also carries page cache and kernel memory.
  On Linux this reports both; RSS alone is why the earlier "peaks" misled.
* **tracemalloc is separately gated** (``SB_MEM_TRACE_ALLOC=1``). It roughly
  doubles the footprint of the thing it measures, so it must never ride along
  with a plain RSS trace.

Usage::

    import memory_trace as memt

    memt.mark('scan:start', symbols=len(watchlist))
    with memt.stage('detection'):
        ...
    memt.tick('symbols', i, total)      # cheap, rate-limited in-loop probe
    memt.report()                        # final summary table

Read the output with one question in mind: **does the curve plateau?** A stage
that keeps climbing with items consumed is a leak. A stage that rises once and
holds is a working set.
"""
from __future__ import annotations

import atexit
import csv
import gc
import logging
import os
import sys
import threading
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger('memtrace')

_MB = 1024.0 * 1024.0

# ─────────────────────────────────────────────────────────────────────────────
# Enablement
# ─────────────────────────────────────────────────────────────────────────────


def _truthy(name: str) -> bool:
    return os.getenv(name, '').strip().lower() in ('1', 'true', 'yes', 'on')


def enabled() -> bool:
    """True when SB_MEM_TRACE is set. Checked live so tests can toggle it."""
    return _truthy('SB_MEM_TRACE')


def alloc_enabled() -> bool:
    """True when SB_MEM_TRACE_ALLOC is set (tracemalloc; ~2x memory overhead)."""
    return _truthy('SB_MEM_TRACE_ALLOC')


# ─────────────────────────────────────────────────────────────────────────────
# RSS readers — resolved once, cheapest-first
# ─────────────────────────────────────────────────────────────────────────────


def _reader_proc() -> Optional[Callable[[], float]]:
    """Linux: /proc/self/statm field 2 is resident pages. One read, no fork."""
    if not os.path.exists('/proc/self/statm'):
        return None
    page = os.sysconf('SC_PAGE_SIZE')

    def read() -> float:
        with open('/proc/self/statm', 'rb') as fh:
            return int(fh.read().split()[1]) * page / _MB

    read()  # probe now so a broken reader is rejected here, not mid-scan
    return read


def _reader_mach() -> Optional[Callable[[], float]]:
    """macOS: task_info(MACH_TASK_BASIC_INFO). A syscall, no fork."""
    if sys.platform != 'darwin':
        return None
    import ctypes

    class _TimeValue(ctypes.Structure):
        _fields_ = [('seconds', ctypes.c_int32), ('microseconds', ctypes.c_int32)]

    class _BasicInfo(ctypes.Structure):
        _fields_ = [
            ('virtual_size', ctypes.c_uint64),
            ('resident_size', ctypes.c_uint64),
            ('resident_size_max', ctypes.c_uint64),
            ('user_time', _TimeValue),
            ('system_time', _TimeValue),
            ('policy', ctypes.c_int32),
            ('suspend_count', ctypes.c_int32),
        ]

    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    MACH_TASK_BASIC_INFO = 20
    n_words = ctypes.sizeof(_BasicInfo) // ctypes.sizeof(ctypes.c_uint32)
    task = libc.mach_task_self()

    def read() -> float:
        info = _BasicInfo()
        count = ctypes.c_uint32(n_words)
        rc = libc.task_info(task, MACH_TASK_BASIC_INFO,
                            ctypes.byref(info), ctypes.byref(count))
        if rc != 0:
            raise OSError(f'task_info returned {rc}')
        return info.resident_size / _MB

    value = read()
    # Sanity-gate the struct layout: a mis-decoded field yields absurd numbers,
    # and a silently wrong RSS is worse than no RSS at all.
    if not (0.5 < value < 1_000_000):
        raise ValueError(f'implausible RSS {value}')
    return read


def _reader_ps() -> Optional[Callable[[], float]]:
    """Last resort: fork `ps`. ~5 ms per call — fine for marks, not for ticks."""
    import subprocess

    pid = str(os.getpid())

    def read() -> float:
        out = subprocess.run(['ps', '-o', 'rss=', '-p', pid],
                             capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip()) / 1024.0

    read()
    return read


def _resolve_rss_reader() -> Callable[[], float]:
    for factory in (_reader_proc, _reader_mach, _reader_ps):
        try:
            reader = factory()
            if reader is not None:
                return reader
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.debug(f'memtrace: {factory.__name__} unavailable: {exc}')
    return lambda: float('nan')


_rss_reader: Optional[Callable[[], float]] = None


def rss_mb() -> float:
    """Current resident set size in MiB. NaN if no probe works on this box."""
    global _rss_reader
    if _rss_reader is None:
        _rss_reader = _resolve_rss_reader()
    try:
        return _rss_reader()
    except Exception:
        return float('nan')


# ─────────────────────────────────────────────────────────────────────────────
# cgroup — the number the OOM killer actually enforces
# ─────────────────────────────────────────────────────────────────────────────

_CGROUP_PATHS = (
    # (current, limit) — v2 first, then v1
    ('/sys/fs/cgroup/memory.current', '/sys/fs/cgroup/memory.max'),
    ('/sys/fs/cgroup/memory/memory.usage_in_bytes',
     '/sys/fs/cgroup/memory/memory.limit_in_bytes'),
)


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, 'rb') as fh:
            raw = fh.read().strip()
        if raw in (b'max', b''):
            return None
        return int(raw)
    except Exception:
        return None


def cgroup_mb() -> tuple[Optional[float], Optional[float]]:
    """(current, limit) charge in MiB, or (None, None) off Linux/cgroups.

    §17's lesson: this, not RSS, is what the container is killed on. A gap
    between the two is page cache the scan is holding open.
    """
    for cur_path, max_path in _CGROUP_PATHS:
        cur = _read_int(cur_path)
        if cur is None:
            continue
        limit = _read_int(max_path)
        # v1 reports a sentinel ~2^63 when unlimited
        if limit is not None and limit > (1 << 62):
            limit = None
        return cur / _MB, (limit / _MB if limit is not None else None)
    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Samples
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Sample:
    label: str
    elapsed: float
    rss: float
    cgroup: Optional[float]
    cgroup_limit: Optional[float]
    delta: float           # since previous mark
    total_delta: float     # since first mark
    extra: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        bits = [f'[MEM] {self.label:<34}',
                f'rss={self.rss:8.1f}MB',
                f'Δ={self.delta:+8.1f}',
                f'Σ={self.total_delta:+8.1f}',
                f't={self.elapsed:6.1f}s']
        if self.cgroup is not None:
            cap = f'/{self.cgroup_limit:.0f}' if self.cgroup_limit else ''
            bits.append(f'cg={self.cgroup:.1f}{cap}MB')
        for key, value in self.extra.items():
            bits.append(f'{key}={value}')
        return ' '.join(bits)


_lock = threading.Lock()
_samples: list[Sample] = []
_t0: Optional[float] = None
_rss0: Optional[float] = None
_last_rss: Optional[float] = None
_peak: float = 0.0
_tick_state: dict[str, float] = {}
_csv_writer = None
_csv_fh = None


def _emit(sample: Sample) -> None:
    logger.info(sample.line())
    if _csv_writer is not None:
        try:
            _csv_writer.writerow([f'{sample.elapsed:.3f}', sample.label,
                                  f'{sample.rss:.2f}',
                                  '' if sample.cgroup is None else f'{sample.cgroup:.2f}',
                                  f'{sample.delta:.2f}', f'{sample.total_delta:.2f}'])
            _csv_fh.flush()
        except Exception:
            pass


def mark(label: str, **extra: Any) -> Optional[Sample]:
    """Record a labelled memory sample. No-op (and returns None) when disabled."""
    if not enabled():
        return None
    global _t0, _rss0, _last_rss, _peak
    try:
        now = time.time()
        rss = rss_mb()
        cur, limit = cgroup_mb()
        with _lock:
            if _t0 is None:
                _t0, _rss0, _last_rss = now, rss, rss
            sample = Sample(
                label=label,
                elapsed=now - _t0,
                rss=rss,
                cgroup=cur,
                cgroup_limit=limit,
                delta=rss - (_last_rss if _last_rss is not None else rss),
                total_delta=rss - (_rss0 if _rss0 is not None else rss),
                extra=extra,
            )
            _last_rss = rss
            _peak = max(_peak, rss)
            _samples.append(sample)
        _emit(sample)
        return sample
    except Exception as exc:
        logger.debug(f'memtrace.mark({label}) failed: {exc}')
        return None


def tick(stream: str, index: int, total: Optional[int] = None,
         every: int = 25, **extra: Any) -> None:
    """In-loop probe, rate-limited to one sample per `every` calls.

    This is the probe that answers the plateau question — a stage whose RSS
    tracks `index` is consuming something it never releases.
    """
    if not enabled():
        return
    try:
        with _lock:
            seen = _tick_state.get(stream, 0) + 1
            _tick_state[stream] = seen
        if seen % max(1, every) and index != total:
            return
        label = f'{stream}[{index}/{total}]' if total else f'{stream}[{index}]'
        mark(label, **extra)
    except Exception:
        pass


@contextmanager
def stage(label: str, **extra: Any):
    """Bracket a pipeline stage: marks entry and exit, reports the stage's own Δ."""
    if not enabled():
        yield
        return
    before = mark(f'{label} ⟶ enter', **extra)
    try:
        yield
    finally:
        after = mark(f'{label} ⟵ exit')
        if before is not None and after is not None:
            logger.info(f'[MEM] {label:<34} STAGE Δ={after.rss - before.rss:+8.1f}MB '
                        f'in {after.elapsed - before.elapsed:.1f}s')


# ─────────────────────────────────────────────────────────────────────────────
# Heap census — *what* is accumulating, not just how much
# ─────────────────────────────────────────────────────────────────────────────

_last_census: Optional[Counter] = None


def census(label: str, top: int = 12, deep: bool = False) -> dict[str, int]:
    """Count live objects by type and diff against the previous census.

    RSS tells you memory grew; this tells you which type grew with it. Also
    totals live pandas objects, since DataFrames are the pipeline's bulk
    allocation (`deep=True` includes object-dtype string payloads and is slow).
    """
    if not enabled():
        return {}
    global _last_census
    try:
        gc.collect()
        counts: Counter = Counter()
        frame_bytes = 0
        frames = 0
        for obj in gc.get_objects():
            try:
                name = type(obj).__name__
            except Exception:
                continue
            counts[name] += 1
            if name in ('DataFrame', 'Series'):
                frames += 1
                try:
                    usage = obj.memory_usage(deep=deep)
                    frame_bytes += int(usage.sum() if hasattr(usage, 'sum') else usage)
                except Exception:
                    pass

        logger.info(f'[MEM] census {label}: {sum(counts.values()):,} objects, '
                    f'{frames:,} pandas objects holding {frame_bytes / _MB:.1f}MB '
                    f'(deep={deep})')

        if _last_census is not None:
            grown = [(name, counts[name] - _last_census.get(name, 0))
                     for name in counts]
            grown.sort(key=lambda kv: kv[1], reverse=True)
            for name, delta in grown[:top]:
                if delta > 0:
                    logger.info(f'[MEM]   +{delta:>9,} {name}  '
                                f'(now {counts[name]:,})')
        _last_census = counts
        return dict(counts)
    except Exception as exc:
        logger.debug(f'memtrace.census({label}) failed: {exc}')
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# tracemalloc — allocation sites, separately gated
# ─────────────────────────────────────────────────────────────────────────────

_tm_last = None


def alloc_start() -> bool:
    """Begin allocation tracing. Only acts when SB_MEM_TRACE_ALLOC=1."""
    if not (enabled() and alloc_enabled()):
        return False
    import tracemalloc
    if not tracemalloc.is_tracing():
        tracemalloc.start(int(os.getenv('SB_MEM_TRACE_FRAMES', '3')))
        logger.info('[MEM] tracemalloc started (this itself costs memory)')
    return True


def alloc_diff(label: str, top: int = 12) -> None:
    """Top allocation sites since the previous call. Attributes growth to code."""
    if not (enabled() and alloc_enabled()):
        return
    global _tm_last
    try:
        import tracemalloc
        if not tracemalloc.is_tracing():
            return
        snap = tracemalloc.take_snapshot().filter_traces((
            tracemalloc.Filter(False, tracemalloc.__file__),
            tracemalloc.Filter(False, __file__),
        ))
        if _tm_last is not None:
            stats = snap.compare_to(_tm_last, 'lineno')
            logger.info(f'[MEM] alloc diff @ {label} (top {top} by growth):')
            for stat in stats[:top]:
                if stat.size_diff <= 0:
                    continue
                frame = stat.traceback[0]
                path = frame.filename.replace(os.getcwd() + '/', '')
                logger.info(f'[MEM]   {stat.size_diff / _MB:+8.2f}MB  '
                            f'{stat.count_diff:+8,} blocks  {path}:{frame.lineno}')
        _tm_last = snap
    except Exception as exc:
        logger.debug(f'memtrace.alloc_diff({label}) failed: {exc}')


# ─────────────────────────────────────────────────────────────────────────────
# Background sampler — catches growth between marks
# ─────────────────────────────────────────────────────────────────────────────

_sampler: Optional[threading.Thread] = None
_sampler_stop = threading.Event()


def start_sampler(csv_path: Optional[str] = None, interval: float = 2.0) -> None:
    """Sample RSS on a daemon thread so unmarked stretches still get a curve."""
    if not enabled():
        return
    global _sampler, _csv_writer, _csv_fh
    if _sampler is not None:
        return
    path = csv_path or os.getenv('SB_MEM_TRACE_CSV')
    if path:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            _csv_fh = open(path, 'w', newline='')
            _csv_writer = csv.writer(_csv_fh)
            _csv_writer.writerow(['elapsed_s', 'label', 'rss_mb', 'cgroup_mb',
                                  'delta_mb', 'total_delta_mb'])
            logger.info(f'[MEM] timeline → {path}')
        except Exception as exc:
            logger.debug(f'memtrace: CSV open failed: {exc}')

    mark('sampler:start')

    def _loop() -> None:
        n = 0
        while not _sampler_stop.wait(interval):
            n += 1
            mark(f'·sample {n}')

    _sampler_stop.clear()
    _sampler = threading.Thread(target=_loop, name='memtrace', daemon=True)
    _sampler.start()


def stop_sampler() -> None:
    global _sampler, _csv_writer, _csv_fh
    _sampler_stop.set()
    if _sampler is not None:
        _sampler.join(timeout=5)
        _sampler = None
    if _csv_fh is not None:
        try:
            _csv_fh.close()
        except Exception:
            pass
        _csv_fh = _csv_writer = None


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────


def report(top: int = 15) -> None:
    """Final summary: the biggest per-mark jumps, peak, and the plateau verdict."""
    if not enabled() or not _samples:
        return
    try:
        named = [s for s in _samples if not s.label.startswith('·')]
        first, last = _samples[0], _samples[-1]
        logger.info('[MEM] ' + '=' * 72)
        logger.info(f'[MEM] SUMMARY  start={first.rss:.1f}MB  end={last.rss:.1f}MB  '
                    f'peak={_peak:.1f}MB  net={last.rss - first.rss:+.1f}MB  '
                    f'over {last.elapsed:.1f}s in {len(_samples)} samples')
        cur, limit = cgroup_mb()
        if limit:
            logger.info(f'[MEM] cgroup {cur:.1f}MB of {limit:.0f}MB cap '
                        f'({cur / limit:.1%}) — peak==cap means unbounded growth, '
                        f'not under-provisioning (§18)')
        biggest = sorted(named, key=lambda s: s.delta, reverse=True)[:top]
        logger.info(f'[MEM] largest jumps (top {top}):')
        for sample in biggest:
            if sample.delta <= 0:
                continue
            logger.info(f'[MEM]   {sample.delta:+8.1f}MB  {sample.label}')
        logger.info('[MEM] ' + '=' * 72)
    except Exception as exc:
        logger.debug(f'memtrace.report failed: {exc}')


def reset() -> None:
    """Clear accumulated state (tests, and back-to-back harness runs)."""
    global _t0, _rss0, _last_rss, _peak, _last_census, _tm_last
    with _lock:
        _samples.clear()
        _tick_state.clear()
        _t0 = _rss0 = _last_rss = None
        _peak = 0.0
    _last_census = None
    _tm_last = None


def samples() -> list[Sample]:
    """Copy of the recorded samples (harness/report consumers)."""
    with _lock:
        return list(_samples)


atexit.register(stop_sampler)
