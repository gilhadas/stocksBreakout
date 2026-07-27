#!/usr/bin/env python3
"""
Verify the global Claude Code skills against this repo's canonical code.

The skills in ~/.claude/skills/ are prose+code documents that live OUTSIDE this
repo — no tests, no review, no git history.  Three of six were found to contain
formula or API errors on 2026-07-26.  This script is the standing guard: it
extracts the code out of the skill markdown, executes it against real data, and
diffs the result against quantkit / auto_portfolio.

Run:
    ./venv/bin/python scripts/verify_skills.py
    ./venv/bin/python scripts/verify_skills.py --symbol MSFT --start 2020-01-01

Exit code 0 = every checkable claim matches.  Non-zero = a skill has drifted.

WHAT THIS CAN AND CANNOT CHECK
------------------------------
CHECKABLE (and checked below):
  * Formula parity — skill code vs quantkit, executed on identical bars.
  * API contracts  — every function name, signature and return key a skill
                     claims quantkit exposes, verified by introspection.

NOT CHECKABLE (deliberately):
  * Generic teaching content.  These skills are written to work in *any* Python
    project, so several sections describe a reasonable generic approach that is
    intentionally NOT this repo's implementation (e.g. market-regime's SMA-order
    model).  Those are labelled as such in the skill text.  A mismatch there is
    by design; a mismatch in anything below is a bug.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
SKILLS = Path.home() / '.claude' / 'skills'
sys.path.insert(0, str(REPO))

FAILURES: list[str] = []
CHECKS = 0


# ── reporting ─────────────────────────────────────────────────────────────────

def check(ok: bool, label: str, detail: str = '') -> bool:
    global CHECKS
    CHECKS += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ''))
    if not ok:
        FAILURES.append(f"{label}: {detail}")
    return ok


# ── skill-markdown extraction ─────────────────────────────────────────────────

def fences(skill: str) -> list[str]:
    """Every ```python block in a skill, with blockquote '> ' prefixes stripped."""
    path = SKILLS / skill / 'SKILL.md'
    if not path.exists():
        return []
    out = []
    for blk in re.findall(r'```python\n(.*?)```', path.read_text(), re.S):
        if blk.lstrip().startswith('>'):
            blk = '\n'.join(l[2:] if l.startswith('> ') else l.lstrip('>')
                            for l in blk.splitlines())
        out.append(blk)
    return out


def fence_with(skill: str, needle: str) -> str:
    """The first python fence in `skill` containing `needle`. Raises if absent."""
    for blk in fences(skill):
        if needle in blk:
            return blk
    raise LookupError(f"no python fence in '{skill}' contains {needle!r}")


def run_fence(code: str, env: dict) -> dict:
    """exec a fence in a fresh namespace seeded with `env`; return the namespace."""
    ns = dict(env)
    exec(compile(ast.parse(code), '<skill>', 'exec'), ns)
    return ns


def series_match(a: pd.Series, b: pd.Series, label: str, tol: float = 1e-9) -> bool:
    a, b = pd.Series(a).astype(float), pd.Series(b).astype(float)
    both = a.notna() & b.notna()
    if both.sum() == 0:
        return check(False, label, 'no overlapping non-NaN values')
    diff = (a[both] - b[both]).abs()
    ok = bool(np.allclose(a[both], b[both], rtol=0, atol=tol))
    return check(ok, label, f"n={both.sum()} max|Δ|={diff.max():.10f}")


# ── data ──────────────────────────────────────────────────────────────────────

def load_bars(symbol: str, start: str, end: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=False)
    if df is None or df.empty:
        sys.exit(f"ERROR: no bars for {symbol} {start}..{end}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.columns = df.columns.str.lower()
    return df


# ── 1. formula parity ─────────────────────────────────────────────────────────

def verify_indicator_formulas(df: pd.DataFrame) -> None:
    print("\n[1] technical-indicators — formula parity vs quantkit.indicators")
    from quantkit import indicators as qk

    # ATR — Wilder smoothing (ewm com=period-1), NOT a simple rolling mean
    ns = run_fence(fence_with('technical-indicators', 'def calculate_atr'), {'pd': pd, 'np': np})
    series_match(ns['calculate_atr'](df), qk.calculate_atr(df), 'ATR(14)')

    # RSI — Wilder smoothing (ewm alpha=1/period)
    ns = run_fence(fence_with('technical-indicators', 'def calculate_rsi'), {'pd': pd, 'np': np})
    series_match(ns['calculate_rsi'](df), qk.calculate_rsi(df), 'RSI(14)')

    # ADX — the 2026-07-26 fix: Wilder's directional-exclusivity rule.
    # The bare snippet needs `df` and a `calculate_atr` in scope.
    ns = run_fence(
        fence_with('technical-indicators', 'plus_dm'),
        {'pd': pd, 'np': np, 'df': df, 'calculate_atr': qk.calculate_atr},
    )
    series_match(ns['adx'], qk.calculate_adx(df), 'ADX(14)')

    # Regression guard: prove the OLD buggy form would have failed this check,
    # so a PASS above is meaningful rather than vacuous.
    plus_dm = df['high'].diff().clip(lower=0)
    minus_dm = (-df['low'].diff()).clip(lower=0)
    atr = qk.calculate_atr(df)
    p_di = 100 * plus_dm.rolling(14).mean() / atr.replace(0, 1e-10)
    m_di = 100 * minus_dm.rolling(14).mean() / atr.replace(0, 1e-10)
    dx = 100 * (p_di - m_di).abs() / (p_di + m_di).replace(0, 1e-10)
    buggy = dx.rolling(14).mean()
    canon = qk.calculate_adx(df)
    both = buggy.notna() & canon.notna()
    gate_disagree = ((buggy[both] > 25) != (canon[both] > 25)).mean() * 100
    check((buggy[both] - canon[both]).abs().max() > 1.0,
          'ADX .clip(lower=0) form is genuinely different (guard is not vacuous)',
          f"max|Δ|={(buggy[both] - canon[both]).abs().max():.2f}pts, "
          f"ADX>25 gate disagrees on {gate_disagree:.1f}% of bars")


def verify_trailing_stop(df: pd.DataFrame) -> None:
    print("\n[2] portfolio-exits — ATR trail vs auto_portfolio._raise_atr_trail")
    from auto_portfolio import _raise_atr_trail
    from config import ATR_TRAIL_MULT, ATR_TRAIL_FLOOR_BARS
    from quantkit import indicators as qk

    blk = fence_with('portfolio-exits', 'trail_candidate')

    # Structural: the three properties the champion exit depends on (CLAUDE.md §12
    # Task 1 / commit dc3e252). Low-based triggering gave 2022 -24.8% vs -10.8%.
    # Scope these to the TRAIL assignment only — `entry_price + atr * tp_mult`
    # elsewhere in the same fence is the take-profit target, which is correctly
    # entry-anchored. Asserting on the whole fence conflates the two.
    trail_line = next(l for l in blk.splitlines() if 'trail_candidate =' in l)
    ratchet_line = next(l for l in blk.splitlines() if 'new_stop =' in l)

    check('current_price - atr' in ' '.join(trail_line.split()),
          'trail SUBTRACTS the ATR band from the close', trail_line.strip())
    check('max(' in ratchet_line and 'stop_price' in ratchet_line,
          'trail RATCHETS via max() — never loosens', ratchet_line.strip())
    check('entry_price' not in trail_line,
          'trail is NOT anchored to entry_price (would never move)', trail_line.strip())

    # Numeric: replay the live helper over real bars, and drive the SKILL'S OWN two
    # lines of arithmetic in lockstep.
    #
    # Executing the skill's source (rather than re-deriving the formula here) is what
    # makes this test non-circular: a re-derivation validates the author's description
    # of live behaviour, not the document, and would pass even if the skill were
    # reverted to `entry_price + atr * mult`. Verified by mutation test.
    trail_expr = trail_line.split('=', 1)[1].strip()
    ratchet_expr = ratchet_line.split('=', 1)[1].strip()

    hist = df.rename(columns=str.title).tail(120)          # live uses Title-case OHLC
    start_stop = float(hist['Close'].iloc[0]) * 0.90
    pos = {'stop': start_stop}
    skill_stop = start_stop
    live_stops, skill_stops = [], []

    for i in range(ATR_TRAIL_FLOOR_BARS + 1, len(hist)):
        window = hist.iloc[:i]
        _raise_atr_trail(pos, window)
        live_stops.append(pos['stop'])

        w = window.tail(ATR_TRAIL_FLOOR_BARS + 1)
        tr = pd.concat([w['High'] - w['Low'],
                        (w['High'] - w['Close'].shift(1)).abs(),
                        (w['Low'] - w['Close'].shift(1)).abs()], axis=1).max(axis=1)
        scope = {
            'current_price': float(window['Close'].iloc[-1]),
            'entry_price':   float(hist['Close'].iloc[0]),
            'atr':           float(tr.mean()),
            'cfg':           {'trail_mult': ATR_TRAIL_MULT},
            'stop_price':    skill_stop,
            'max':           max,
        }
        scope['trail_candidate'] = eval(trail_expr, {'__builtins__': {}}, scope)
        skill_stop = round(float(eval(ratchet_expr, {'__builtins__': {}}, scope)), 4)
        skill_stops.append(skill_stop)

    series_match(pd.Series(live_stops), pd.Series(skill_stops),
                 f"skill's own trail arithmetic vs live, {len(live_stops)} bars", tol=1e-4)
    check(all(b >= a for a, b in zip(live_stops, live_stops[1:])),
          'trail is monotonically non-decreasing on real data')


# ── 2. API contracts ──────────────────────────────────────────────────────────

def verify_api_contracts() -> None:
    print("\n[3] API contracts — every quantkit claim made by a skill")
    import inspect

    from quantkit import fib, regime
    from quantkit.sentiment import finbert, finnhub

    # --- fibonacci-bounce ----------------------------------------------------
    check(inspect.signature(fib.detect_swing).parameters['window'].default == 120,
          'fib.detect_swing window default is 120 (skill says 120)')
    swing_keys = {'swing_high', 'swing_high_date', 'swing_low', 'swing_low_date', 'range'}
    doc = (SKILLS / 'fibonacci-bounce' / 'SKILL.md').read_text()
    check(all(k in doc for k in swing_keys) and 'swing_high_idx' not in doc.split('Real return')[1][:900],
          'fib skill documents the real detect_swing keys, not *_idx')

    # --- market-regime -------------------------------------------------------
    p = regime.suggest_params('swing', 'bull')
    check(set(p) == {'vol_thresh', 'atr_mult', 'sl_mult', 'tp_mult', 'min_rr',
                     'minervini_min', 'quality_filter', 'description'},
          'regime.suggest_params key set unchanged', str(sorted(p)))
    check(isinstance(p['quality_filter'], str),
          "quality_filter is a STRING (skill warns against `<` comparison)")
    check(regime.suggest_params('longterm', 'bull') == {},
          "'longterm' mode returns {} (skill documents no implicit fallback)")

    # --- sentiment-analysis --------------------------------------------------
    check(not hasattr(finnhub, 'get_buzz_ratio') and hasattr(finnhub, 'get_buzz'),
          'finnhub exposes get_buzz, NOT get_buzz_ratio')
    check('api_key' not in inspect.signature(finnhub.get_buzz).parameters,
          'finnhub.get_buzz takes no api_key parameter')
    check(set(finnhub.FinnhubBuzz.__annotations__) ==
          {'articles_last_week', 'weekly_average', 'buzz_ratio'},
          'FinnhubBuzz keys unchanged — still no sentiment field')
    check(finbert._LABEL_MAP == {'positive': 'bullish', 'negative': 'bearish',
                                 'neutral': 'neutral'},
          "finbert remaps labels to bullish/bearish (skill uses 'bullish')")
    check(set(finbert.SentimentResult.__annotations__) ==
          {'label', 'score', 'net_score', 'breakdown', 'headlines', 'top_headline', 'emoji'},
          'SentimentResult keys unchanged (no avg_score/recent_headlines)')
    sig = inspect.signature(finbert.get_ticker_sentiment).parameters
    check('days_back' not in sig and 'max_age_hours' in sig,
          'get_ticker_sentiment uses max_age_hours, not days_back')
    check(list(inspect.signature(finbert.batch_sentiment).parameters)[0] == 'symbols',
          'batch_sentiment batches SYMBOLS, not headlines')

    # --- portfolio-exits -----------------------------------------------------
    # Added 2026-07-27 after a /portfolio-exits run found a whole class of drift the
    # original import-resolution check could not see: the names imported fine, but the
    # kwargs and config keys the skill used against them were fabricated.
    from quantkit.portfolio import ExitEvaluator, DEFAULT_EXIT_CONFIG
    ev_params = set(inspect.signature(ExitEvaluator.evaluate).parameters)
    check('regime' in ev_params and 'current_regime' not in ev_params,
          "ExitEvaluator.evaluate takes regime=, not current_regime=")
    check({'tp_reached', 'signal_type'} <= ev_params,
          'evaluate exposes tp_reached / signal_type (skill documents both)')
    check(set(DEFAULT_EXIT_CONFIG) == {'trend_type', 'trend_period', 'atr_mult', 'sl_mult',
                                       'tp_mult', 'min_rr', 'trail_activation',
                                       'partial_exit_r', 'partial_exit_pct'},
          'DEFAULT_EXIT_CONFIG key set unchanged', str(sorted(DEFAULT_EXIT_CONFIG)))

    # Any cfg['key'] the skill uses against the REAL config must exist. Bracket access
    # on a missing key is a KeyError; the library itself uses .get() with defaults.
    pe = (SKILLS / 'portfolio-exits' / 'SKILL.md').read_text()
    real_arm = pe.split('### Example 1')[1] if '### Example 1' in pe else ''
    fabricated = {k for k in re.findall(r"cfg\['(\w+)'\]", real_arm)
                  if k not in DEFAULT_EXIT_CONFIG}
    check(not fabricated,
          'no fabricated cfg[key] in the real-quantkit example',
          ', '.join(sorted(fabricated)) if fabricated else 'all keys real')

    # Kwarg names used in every documented evaluate(...) call must be accepted.
    bad_kwargs = set()
    for call in re.findall(r'ev\.evaluate\((.*?)\n\s*\)', pe, re.S):
        bad_kwargs |= {k for k in re.findall(r'^\s*(\w+)=', call, re.M) if k not in ev_params}
    check(not bad_kwargs, 'every documented evaluate() kwarg is accepted',
          ', '.join(sorted(bad_kwargs)) if bad_kwargs else 'all valid')

    # --- every `from quantkit... import X` in any skill actually resolves -----
    import importlib
    bad = []
    for sk in sorted(p.name for p in SKILLS.iterdir() if (p / 'SKILL.md').exists()):
        text = (SKILLS / sk / 'SKILL.md').read_text()
        # Handle BOTH single-line imports and parenthesised multi-line ones.
        # A naive `import ([^\n#]+)` captures a bare '(' and then reports the
        # literal name '(' as missing — a false positive, not a broken skill.
        for mod, names in re.findall(
                r'from (quantkit[\w.]*) import (\([^)]*\)|[^\n#]+)', text):
            names = re.sub(r'^\s*>\s?', '', names.strip('() \n'), flags=re.M)
            try:
                m = importlib.import_module(mod)
            except ImportError:
                bad.append(f"{sk}: module {mod}")
                continue
            for n in [x.strip() for x in names.split(',') if x.strip()]:
                if not hasattr(m, n):
                    bad.append(f"{sk}: {mod}.{n}")
    check(not bad, 'every `from quantkit… import …` across all skills resolves',
          '; '.join(bad) if bad else 'all resolve')


# ── 3. static hygiene ─────────────────────────────────────────────────────────

def verify_static() -> None:
    print("\n[4] static — every python block parses with no undefined names")
    KNOWN = set(dir(__builtins__)) | {
        'pd', 'np', 'df', 'datetime', 'timedelta', 'yf', 'argrelextrema', 'calculate_rsi',
        'analyze_text', 'get_ticker_sentiment', 'get_buzz', 'batch_buzz', 'batch_sentiment',
        'detect_regime', 'suggest_params', 'signal_quality', 'signal_tier', 'condition_met',
        'MIN_BARS', 'WINDOW', 'target', 'Optional', 'Dict', 'cfg', 'atr', 'current_price'}
    problems = []
    for sk in sorted(p.name for p in SKILLS.iterdir() if (p / 'SKILL.md').exists()):
        for i, blk in enumerate(fences(sk)):
            try:
                tree = ast.parse(blk)
            except SyntaxError as e:
                problems.append(f"{sk} block{i}: SyntaxError {e.msg}")
                continue
            for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
                bound = {a.arg for a in fn.args.args} | KNOWN
                for n in ast.walk(fn):
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                        bound.add(n.id)
                    elif isinstance(n, ast.ExceptHandler) and n.name:
                        bound.add(n.name)
                    elif isinstance(n, (ast.Import, ast.ImportFrom)):
                        bound |= {(a.asname or a.name).split('.')[0] for a in n.names}
                    elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                        bound.add(n.name)
                    elif isinstance(n, ast.comprehension):
                        bound |= {x.id for x in ast.walk(n.target) if isinstance(x, ast.Name)}
                for n in ast.walk(fn):
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in bound:
                        problems.append(f"{sk} block{i} {fn.name}(): undefined {n.id!r}")
    check(not problems, 'all python fences clean', '; '.join(problems[:5]) or 'no issues')


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='AAPL')
    ap.add_argument('--start', default='2025-01-01')
    ap.add_argument('--end', default=None, help='default: today')
    args = ap.parse_args()

    end = args.end or pd.Timestamp.today().strftime('%Y-%m-%d')
    if not SKILLS.exists():
        sys.exit(f"ERROR: no skills directory at {SKILLS}")

    print(f"Skills : {SKILLS}")
    print(f"Data   : {args.symbol} {args.start} → {end}")
    df = load_bars(args.symbol, args.start, end)
    print(f"Bars   : {len(df)}")

    verify_indicator_formulas(df)
    verify_trailing_stop(df)
    verify_api_contracts()
    verify_static()

    print(f"\n{'=' * 68}")
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} of {CHECKS} checks:")
        for f in FAILURES:
            print(f"  • {f}")
        return 1
    print(f"OK — all {CHECKS} checks pass. Skills match repo canonical code.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
