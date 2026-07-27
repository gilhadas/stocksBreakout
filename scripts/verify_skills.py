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

    # Everything else the skill ships as runnable code. Extended 2026-07-27 —
    # the original three (ATR/RSI/ADX) were the only *functions*; the rest are bare
    # snippets, which were never machine-checked and are exactly where VWAP drifted.
    def snippet(needle: str, want: str, canonical, tol: float = 1e-9):
        ns = run_fence(fence_with('technical-indicators', needle),
                       {'pd': pd, 'np': np, 'df': df.copy(),
                        'calculate_atr': qk.calculate_atr, 'calculate_rsi': qk.calculate_rsi,
                        'close': df['close'], 'rsi': qk.calculate_rsi(df), 'lookback': 20})
        series_match(ns[want], canonical, f'{want} snippet', tol)

    snippet('vol_ratio', 'vol_ratio', qk.calculate_volume_ratio(df))
    snippet('roc =', 'roc', qk.calculate_roc(df))

    # BB returns (upper, lower, width, avg_width, is_consolidating) — NOT
    # (upper, middle, lower). Mis-unpacking this produces a fake 283-point diff.
    bb_u, bb_l, bb_w, _, bb_c = qk.calculate_bollinger_bands(df)
    for want, canon in (('upper', bb_u), ('lower', bb_l), ('width', bb_w)):
        snippet('upper = sma + 2 * std', want, canon)

    m_line, m_sig, m_hist = qk.calculate_macd(df)
    for want, canon in (('macd', m_line), ('signal', m_sig), ('hist', m_hist)):
        snippet('ema_fast', want, canon)

    stoch = qk.calculate_stochastic_rsi(df)
    snippet('stoch_k', 'stoch_k', stoch[0] if isinstance(stoch, tuple) else stoch)

    aroon = qk.calculate_aroon(df)          # DataFrame, lowercase columns
    for want in ('aroon_up', 'aroon_down', 'aroon_osc'):
        snippet('aroon_up', want, aroon[want])

    # VWAP: the skill must document the session reset, not a bare continuous cumsum.
    # A DatetimeIndex always has .date, so the reset degenerates to typical price on
    # daily bars — dormant live (calculate_all_indicators NaNs daily vwap) but a trap
    # for anyone reusing the function elsewhere.
    vwap_blk = fence_with('technical-indicators', 'typical_price')
    check('groupby' in vwap_blk and 'hasattr' in vwap_blk,
          'VWAP snippet documents the per-session reset, not a bare cumsum')
    daily_vwap = qk.calculate_vwap(df, '1d').dropna()
    tp = ((df['high'] + df['low'] + df['close']) / 3).loc[daily_vwap.index]
    collapses = bool(np.allclose(daily_vwap, tp))
    # Look for the warning near the VWAP section rather than keyword-matching the
    # whole file: the claim is "the skill tells you daily VWAP is just typical price".
    vwap_section = (SKILLS / 'technical-indicators' / 'SKILL.md').read_text()
    vwap_section = vwap_section.split('#### VWAP')[1].split('####')[0].lower()
    # Normalise: prose wraps mid-phrase and blockquote lines carry a leading '>',
    # so a raw substring search misses "typical\n> price".
    vwap_section = re.sub(r'\s*>?\s+', ' ', vwap_section)
    warned = 'typical price' in vwap_section and 'daily' in vwap_section
    check(collapses == warned,
          'skill warns iff daily VWAP collapses to typical price',
          f'collapses={collapses}, skill warns={warned}')

    check(qk.calculate_trend_line(df, 'EMA', 21).equals(
              df['close'].ewm(span=21, adjust=False).mean()),
          'EMA idiom matches calculate_trend_line (span, adjust=False)')
    check(qk.calculate_trend_line(df, 'SMA', 50).equals(df['close'].rolling(50).mean()),
          'SMA idiom matches calculate_trend_line')

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


def verify_composite_scores(df: pd.DataFrame) -> None:
    """
    The parts of technical-indicators that are TABLES, not runnable code.

    Roughly a third of that skill is prose: composite-score weight tables, the 8
    Minervini conditions, the Volume Profile description. None of it was machine
    checked — it was verified by reading, and reading is not proving. These are the
    claims a reader would copy into real scoring code, so they are worth pinning.
    """
    import inspect
    from quantkit import indicators as qk

    text = (SKILLS / 'technical-indicators' / 'SKILL.md').read_text()

    def table_weights(heading: str) -> dict[str, int]:
        """Parse '| RSI | 30 | ... |' rows out of a markdown section."""
        section = text.split(heading)[1].split('####')[0]
        out = {}
        for row in re.findall(r'^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|', section, re.M):
            out[row[0].strip()] = int(row[1])
        return out

    # ── Momentum Score ───────────────────────────────────────────────────────
    # quantkit's own docstring is machine-readable: "Components: RSI (0-30), ..."
    doc = inspect.getdoc(qk.calculate_momentum_score) or ''
    # The docstring's opening line also says "score (0-100)" — that is the total,
    # not a component. Match only the "Components: ..." line.
    comp_line = next((l for l in doc.splitlines() if l.strip().startswith('Components:')), '')
    canon = {k: int(v) for k, v in re.findall(r'(\w+)\s*\(0-(\d+)\)', comp_line)}
    skill_w = table_weights('#### Momentum Score')
    check(canon == skill_w,
          'Momentum Score component weights match quantkit',
          f'quantkit={canon} skill={skill_w}')
    check(sum(canon.values()) == 100, 'Momentum Score weights sum to 100', str(sum(canon.values())))

    # ── Breakout Conviction ──────────────────────────────────────────────────
    doc = inspect.getdoc(qk.calculate_breakout_conviction) or ''
    comp_line = next((l for l in doc.splitlines() if l.strip().startswith('Components:')), '')
    canon_c = {k.strip(): int(v) for k, v in re.findall(r'([\w ]+?)\s*\(0-(\d+)\)', comp_line)}
    skill_c = table_weights('#### Breakout Conviction Score')
    check(sum(canon_c.values()) == 100 == sum(skill_c.values()),
          'Breakout Conviction weights sum to 100 in both',
          f'quantkit={canon_c} skill={skill_c}')

    # Behavioural: the tier thresholds the skill documents must actually fire.
    # Synthetic bar — flat history, then one bar with a known gap / close position.
    n = 40
    base = pd.DataFrame({
        'open': 100.0, 'high': 100.5, 'low': 99.5, 'close': 100.0, 'volume': 1_000_000.0,
    }, index=pd.date_range('2025-01-01', periods=n, freq='B'))
    spike = base.copy()
    spike.iloc[-1, spike.columns.get_loc('open')] = 102.0      # +2.0% gap  -> 20
    spike.iloc[-1, spike.columns.get_loc('low')] = 102.0
    spike.iloc[-1, spike.columns.get_loc('high')] = 110.0
    spike.iloc[-1, spike.columns.get_loc('close')] = 110.0     # close at high -> 30
    # NOTE 10M, not 2M: calculate_volume_ratio divides by rolling(20).mean(), which
    # INCLUDES the spike bar itself. A "2x" bar therefore scores 2M/1.05M = 1.905 and
    # lands in the >=1.5 tier, not >=2.0. Easy to get wrong when hand-checking.
    spike.iloc[-1, spike.columns.get_loc('volume')] = 10_000_000.0
    conviction = float(qk.calculate_breakout_conviction(spike).iloc[-1])
    # close 30 + vol 30 + gap 20 + green streak (1 green bar) 7 = 87
    check(abs(conviction - 87.0) < 1e-6,
          'Conviction tiers fire as documented (close30+vol30+gap20+green7)',
          f'got {conviction}')

    # ── Minervini: 8 conditions, in the documented order ─────────────────────
    cond, score = qk.calculate_minervini_template(df)
    check(len(cond) == 8, 'Minervini returns exactly 8 conditions', str(len(cond)))
    check(0 <= score <= 8, 'Minervini score is 0–8', str(score))
    # Split on a horizontal rule at line start, NOT bare '---': the markdown table's
    # own separator row (|---|---|) contains '---' and truncates the section to nothing.
    mini_section = re.split(r'\n---\n', text.split('Minervini Stage 2 Template')[1])[0]
    for tag, must in (
        ('c1', ['sma150', 'sma200']), ('c2', ['sma150', 'sma200']),
        ('c3', ['sma200', 'slope']), ('c4', ['sma50']),
        ('c5', ['sma50']), ('c6', ['25%', '52-week low']),
        ('c7', ['25%', '52-week high']), ('c8', ['252']),
    ):
        row = next((l for l in mini_section.splitlines()
                    if re.match(rf'^\|\s*{tag.upper()}\s*\|', l.strip(), re.I)), '')
        low = row.lower()
        check(bool(row) and all(m.lower() in low for m in must),
              f'Minervini {tag.upper()} documented as in quantkit',
              row.strip()[:80] or 'ROW MISSING')

    # ── TradingView parity cheatsheet ────────────────────────────────────────
    # The ✅ marks were pure assertion until 2026-07-27. Two were wrong. These pin
    # the **measured*_ relationship so the table cannot drift back to claiming parity.
    tv_section = text.split('TradingView Parity Cheatsheet')[1]

    def tv_row(name: str) -> str:
        """
        The cheatsheet TABLE ROW for one indicator.

        Must target the row, not the whole section: the explanatory prose *below* the
        table also mentions ddof=0 and ❌, so a section-wide substring search passes
        even after the table itself is reverted to claiming parity. (Caught by the
        mutation test — the first version of this check was vacuous.)
        """
        for line in tv_section.splitlines():
            if re.match(rf'^\|\s*{re.escape(name)}\s*\|', line.strip()):
                return line
        return ''

    # BB: pandas .std() is ddof=1 (sample); TradingView ta.stdev is ddof=0 (population).
    sma20 = df['close'].rolling(20).mean()
    qk_upper = qk.calculate_bollinger_bands(df)[0]
    tv_upper = sma20 + 2 * df['close'].rolling(20).std(ddof=0)
    band_gap = (qk_upper - tv_upper).abs().dropna()
    check(band_gap.max() > 0.01,
          'BB really does differ from TradingView (ddof=1 vs 0)',
          f'max ${band_gap.max():.4f}, ratio sqrt(20/19)={np.sqrt(20/19):.6f}')
    bb_row = tv_row('BB')
    check('ddof=0' in bb_row and '✅' not in bb_row,
          'cheatsheet BB row requires ddof=0 and does NOT claim parity',
          bb_row.strip()[:90] or 'BB ROW MISSING')

    # ...but the squeeze verdict must be unaffected, because is_consolidating is a
    # ratio to its own mean and the constant factor cancels. If this ever fails, the
    # scanner's consolidation step HAS changed and the skill's reassurance is stale.
    _, _, qk_w, _, qk_cons = qk.calculate_bollinger_bands(df)
    tv_w = (tv_upper - (sma20 - 2 * df['close'].rolling(20).std(ddof=0))) / sma20 * 100
    tv_cons = tv_w < tv_w.rolling(20).mean() * 0.6
    both = qk_cons.notna() & tv_cons.notna()
    check(int((qk_cons[both] != tv_cons[both]).sum()) == 0,
          'BB squeeze verdict is ddof-invariant (the 2.6% factor cancels)',
          f'{int((qk_cons[both] != tv_cons[both]).sum())} of {int(both.sum())} bars differ')

    # ADX: quantkit smooths DI and ADX with an SMA; Wilder/TradingView use RMA.
    adx_qk = qk.calculate_adx(df)
    pdm = df['high'].diff(); mdm = -df['low'].diff()
    pdm = pdm.where((pdm > mdm) & (pdm > 0), 0)
    mdm = mdm.where((mdm > pdm) & (mdm > 0), 0)
    atr14 = qk.calculate_atr(df)
    pdi = 100 * pdm.ewm(alpha=1/14, adjust=False).mean() / atr14.replace(0, 1e-10)
    mdi = 100 * mdm.ewm(alpha=1/14, adjust=False).mean() / atr14.replace(0, 1e-10)
    dx_w = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, 1e-10)
    adx_wilder = dx_w.ewm(alpha=1/14, adjust=False).mean()
    ok = adx_qk.notna() & adx_wilder.notna()
    gap = (adx_qk[ok] - adx_wilder[ok]).abs().mean()
    check(gap > 1.0,
          'ADX genuinely diverges from Wilder/TradingView (skill must not claim parity)',
          f'mean|Δ| {gap:.1f} pts; >25 gate fires on '
          f'{(adx_qk[ok] > 25).mean()*100:.0f}% vs {(adx_wilder[ok] > 25).mean()*100:.0f}% of bars')
    adx_row = tv_row('ADX')
    check('❌' in adx_row and '✅' not in adx_row,
          'cheatsheet ADX row marks it NOT TradingView-equivalent',
          adx_row.strip()[:90] or 'ADX ROW MISSING')

    # And the rows that ARE exact must keep saying so — a guard that only fires on
    # bad news would let a correct claim be quietly deleted.
    for name in ('RSI', 'ATR', 'EMA', 'SMA', 'MACD'):
        row = tv_row(name)
        check('✅' in row, f'cheatsheet still claims exact parity for {name}',
              row.strip()[:70] or f'{name} ROW MISSING')

    # ── Volume Profile ───────────────────────────────────────────────────────
    vp = qk.compute_volume_profile(df)
    for key in ('vpoc', 'value_area_high', 'value_area_low',
                'high_volume_nodes', 'low_volume_nodes'):
        check(key in vp, f'volume profile returns {key}')
    src = inspect.getsource(qk.compute_volume_profile)
    check('0.70' in src or '0.7' in src, 'Value Area really is 70% of volume (skill says 70%)')
    check('1.5 * median_vol' in src, 'HVN threshold really is 1.5x median (skill says >1.5x)')
    check('0.5 * median_vol' in src, 'LVN threshold really is 0.5x median (skill says <0.5x)')


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
                    elif isinstance(n, ast.Lambda):
                        bound |= {a.arg for a in n.args.args}
                    elif isinstance(n, ast.withitem) and n.optional_vars is not None:
                        bound |= {x.id for x in ast.walk(n.optional_vars)
                                  if isinstance(x, ast.Name)}
                for n in ast.walk(fn):
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in bound:
                        problems.append(f"{sk} block{i} {fn.name}(): undefined {n.id!r}")
    check(not problems, 'all python fences clean', '; '.join(problems[:5]) or 'no issues')


# ── main ──────────────────────────────────────────────────────────────────────

VENDORED = ('technical-indicators', 'chart-patterns', 'portfolio-exits',
            'market-regime', 'fibonacci-bounce', 'sentiment-analysis')


def verify_vendoring() -> None:
    """
    The vendored skills must be the SAME BYTES as the repo copy, not a duplicate.

    Two hand-maintained copies of one document with nothing binding them is the
    cron_jobs.txt <-> docker/crontab arrangement that caused three production
    incidents. Symlinking makes drift impossible; this check makes sure nobody
    quietly replaces a symlink with a copy and reintroduces the failure mode.
    """
    print("\n[0] vendoring — ~/.claude/skills is linked to the repo, not copied")
    repo_skills = REPO / 'skills'
    if not repo_skills.exists():
        check(False, 'repo skills/ directory exists', str(repo_skills))
        return

    for name in VENDORED:
        live = SKILLS / name
        vendored = repo_skills / name / 'SKILL.md'
        if not live.exists():
            check(False, f'{name}: present in ~/.claude/skills', 'MISSING — broken symlink?')
            continue
        if not vendored.exists():
            check(False, f'{name}: vendored into repo skills/', 'MISSING')
            continue

        # A symlink is the intended arrangement; identical content is acceptable
        # (e.g. a fresh clone before linking). Divergent content never is.
        linked = live.is_symlink() and live.resolve() == (repo_skills / name).resolve()
        same = (live / 'SKILL.md').read_text() == vendored.read_text()
        check(linked or same, f'{name}: live copy tracks the repo',
              'symlinked' if linked else ('identical content (not linked)' if same
                                          else 'DIVERGED — live and repo differ'))
        if same and not linked:
            print(f"       note: {name} is a COPY, not a symlink — it will drift. "
                  f"See skills/README.md.")


def guarded(fn, label: str) -> None:
    """
    Run a check group, converting an exception into a FAIL rather than a crash.

    A missing snippet makes fence_with() raise LookupError — and that is precisely
    the "someone gutted a section" case the guard exists to catch. Crashing there
    reports nothing at all; a FAIL names the section.
    """
    try:
        fn()
    except Exception as exc:                                  # noqa: BLE001
        check(False, f'{label} (raised {type(exc).__name__})', str(exc)[:160])


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

    guarded(verify_vendoring, 'vendoring')
    guarded(lambda: verify_indicator_formulas(df), 'technical-indicators formula parity')
    guarded(lambda: verify_composite_scores(df), 'technical-indicators composite/prose claims')
    guarded(lambda: verify_trailing_stop(df), 'portfolio-exits trail parity')
    guarded(verify_api_contracts, 'API contracts')
    guarded(verify_static, 'static hygiene')

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
