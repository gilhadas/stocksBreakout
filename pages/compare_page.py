"""Book comparison — control vs auto-swap, side by side."""
import base64
import json as _json

import pandas as pd
import streamlit as st

import auto_portfolio as ap
import book_compare


def _decode_sub(token: str):
    """Read `sub` out of the JWT without importing api.auth (heavy deps)."""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        pad = parts[1] + '=' * (-len(parts[1]) % 4)
        return _json.loads(base64.urlsafe_b64decode(pad)).get('sub')
    except Exception:
        return None


def _fmt(v, spec='{:,.2f}', dash='—'):
    return dash if v is None else spec.format(v)


def render_compare_page():
    st.subheader("Book Comparison — does swapping pay?")
    st.caption(
        "Two books run off the SAME signal stream. Control only suggests swaps; "
        "Auto-swap executes them. Everything below is measured from the fork date — "
        "history before it is shared by construction."
    )

    user_id = _decode_sub(st.session_state.get('token', '') or '')

    with st.spinner("Loading both books…"):
        try:
            report = book_compare.compare_books(user_id=user_id)
        except Exception as exc:
            st.error(f"Could not build the comparison: {exc}")
            return

    fork = report.get('fork_date')
    if not fork:
        st.warning(
            "These books have not been forked yet, so there is nothing to compare. "
            "Run `python3 fork_books.py` **on the production box** to start the "
            "experiment (the local users.db is stale — see fork_books._users)."
        )

    books = report.get('books', {})
    if not books:
        st.info("No books found.")
        return

    # ── Headline metrics, one column per book ────────────────────────────────
    cols = st.columns(len(books))
    for col, (name, m) in zip(cols, books.items()):
        with col:
            st.markdown(f"**{m.get('label', name)}**")
            st.metric("Total value", f"${_fmt(m.get('total_value'))}")
            st.metric("Return since fork", f"{_fmt(m.get('return_pct'), '{:+.2f}')}%")
            st.metric("Sharpe", _fmt(m.get('sharpe'), '{:.2f}'))
            st.metric("Max drawdown", f"{_fmt(m.get('max_drawdown_pct'), '{:.2f}')}%")
            st.metric("Closed since fork",
                      f"{m.get('closed_since', 0)}"
                      + (f" · {_fmt(m.get('win_rate'), '{:.0f}')}% win"
                         if m.get('win_rate') is not None else ""))

    # Honest caveat, stated where the numbers are read rather than buried.
    thin = min((m.get('equity_points') or 0) for m in books.values())
    if thin < 20:
        st.warning(
            f"Only {thin} equity point(s) since the fork. Both books trade the same "
            "signals on the same days, so their curves are highly correlated and the "
            "difference is a small residual — Sharpe and return are **not** meaningful "
            "yet. Read the per-swap attribution below instead; it becomes readable "
            "far sooner.",
            icon="⚠️",
        )

    # ── Equity curves ────────────────────────────────────────────────────────
    frames = {}
    for name, m in books.items():
        curve = m.get('equity_curve') or []
        if curve:
            s = pd.DataFrame(curve)
            s['date'] = pd.to_datetime(s['date'])
            frames[m.get('label', name)] = s.set_index('date')['total_value']
    if frames:
        st.markdown("#### Equity since fork")
        st.line_chart(pd.DataFrame(frames))
    else:
        st.info(
            "No equity history recorded yet — points are written by "
            "`refresh_prices` (cron 10:00 and 15:45 ET), one per day per book."
        )

    # ── Per-swap attribution: the primary readout ────────────────────────────
    st.markdown("#### Per-swap attribution")
    st.caption(
        "For each executed swap: what the replacement did versus what the position "
        "it displaced would have done over the same window. This answers "
        "\"was **this** swap right?\" one decision at a time — usable in weeks, "
        "where the curves above need months."
    )

    any_swaps = False
    for name, att in (report.get('attribution') or {}).items():
        rows = att.get('swaps') or []
        if not rows:
            continue
        any_swaps = True
        a1, a2, a3 = st.columns(3)
        a1.metric("Swaps executed", att.get('n_total', 0))
        a2.metric("Hit rate",
                  f"{_fmt(att.get('hit_rate'), '{:.0f}')}%"
                  + (f" ({att['n_better']}/{att['n_scored']})"
                     if att.get('n_scored') else ""))
        a3.metric("Avg edge / swap", f"{_fmt(att.get('avg_edge_pct'), '{:+.2f}')}%")

        df = pd.DataFrame(rows)
        show = [c for c in ['ts', 'close_symbol', 'open_symbol', 'held_return_pct',
                            'swap_return_pct', 'edge_pct', 'verdict',
                            'score_improvement'] if c in df.columns]
        st.dataframe(
            df[show].rename(columns={
                'ts': 'When', 'close_symbol': 'Closed', 'open_symbol': 'Opened',
                'held_return_pct': 'If held %', 'swap_return_pct': 'Swapped into %',
                'edge_pct': 'Edge %', 'verdict': 'Verdict',
                'score_improvement': 'Score Δ',
            }),
            width='stretch', hide_index=True,
        )

    if not any_swaps:
        st.info(
            "No swaps have been executed yet. The auto-swap book only acts when "
            "`suggest_swaps` finds a candidate beating a held position by "
            f"{ap._SWAP_MIN_SCORE_DELTA:.0f}+ points, capped at "
            f"{ap.BOOKS.get('autoswap', {}).get('max_swaps_per_day', 0)}/day."
        )

    # ── Deltas ───────────────────────────────────────────────────────────────
    if report.get('vs_control'):
        st.markdown("#### Difference vs control")
        st.dataframe(pd.DataFrame(report['vs_control']).T, width='stretch')
        st.caption(
            "Positive = the auto-swap book did better. CLAUDE.md §11 measured auto "
            "swap-on-skip in backtest at −4.68 Sharpe on all.txt 2026 with 0 swaps "
            "fired in every spy_plus/plus.txt year — this table is the live check on "
            "that single sample."
        )
