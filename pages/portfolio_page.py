"""
Portfolio Page — Streamlit UI for the core Portfolio module.
Buy/sell positions, update stop/target, view P&L and performance.
"""

import streamlit as st
import pandas as pd
from datetime import datetime

from utils import load_data, load_text, save_data, save_text, list_files


def _get_portfolio():
    """Get or create Portfolio instance (cached in session state)."""
    if 'portfolio_obj' not in st.session_state:
        from portfolio import Portfolio
        st.session_state['portfolio_obj'] = Portfolio()
        st.session_state['prices_fetched_session'] = False  # refresh once per session
    return st.session_state['portfolio_obj']


def _fetch_price_for_ticker(symbol: str):
    """Fetch current price via yfinance. Returns float or None."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol.replace(' ', '-'))
        data = ticker.history(period='1d')
        if data is not None and not data.empty:
            return float(data['Close'].iloc[-1])
    except Exception:
        pass
    return None


def _color_pnl(val):
    """Style helper: green for positive P&L, red for negative."""
    if pd.isna(val):
        return ''
    try:
        v = float(str(val).replace('$', '').replace('%', '').replace(',', '').replace('+', ''))
    except (ValueError, TypeError):
        return ''
    if v > 0:
        return 'color: #4caf50'
    elif v < 0:
        return 'color: #ef5350'
    return ''


# ── Earnings badge helpers ──────────────────────────────────────────────────
# Held positions are cross-referenced with:
#   1. input/reporting_watchlist.txt (curated earnings-this-week list)
#   2. yfinance ticker.calendar (actual scheduled earnings date)
# A position gets a warning badge if earnings are within 7 calendar days.

_EARNINGS_WARN_DAYS = 7


@st.cache_data(ttl=3600, show_spinner=False)
def _load_reporting_watchlist() -> set:
    """Return the set of symbols listed in input/reporting_watchlist.txt."""
    from pathlib import Path as _Path
    p = _Path('input/reporting_watchlist.txt')
    if not p.exists():
        return set()
    try:
        text = p.read_text(encoding='utf-8')
    except Exception:
        return set()
    out: set = set()
    for tok in text.replace('\n', ',').split(','):
        s = tok.strip().split(':')[-1].strip().upper()
        if s:
            out.add(s)
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_earnings_info(symbols: tuple) -> dict:
    """Fetch earnings calendar for *symbols*. Returns {SYM: {days_until, timing, date}}.

    Cached per (sorted) symbol-tuple for 30 min to avoid hammering yfinance on reruns.
    Symbols with no upcoming earnings (or fetch failures) are omitted from the result.
    """
    if not symbols:
        return {}
    from datetime import datetime as _dt
    import yfinance as _yf

    today = _dt.now().date()
    out: dict = {}
    for sym in symbols:
        try:
            cal = _yf.Ticker(sym).calendar
            if cal is None:
                continue
            raw = None
            if isinstance(cal, dict):
                ed = cal.get('Earnings Date')
                if ed:
                    raw = ed[0] if isinstance(ed, list) else ed
            elif hasattr(cal, 'iloc'):
                try:
                    raw = cal.iloc[0, 0]
                except Exception:
                    continue
            if raw is None:
                continue
            if isinstance(raw, str):
                raw = pd.Timestamp(raw)
            ed_date = raw.date() if callable(getattr(raw, 'date', None)) else raw.date
            days = (ed_date - today).days
            if days < 0 or days > _EARNINGS_WARN_DAYS:
                continue
            timing = ''
            if hasattr(raw, 'hour') and raw.hour > 0:
                timing = 'AMC' if raw.hour >= 12 else 'BMO'
            out[sym.upper()] = {
                'days_until': days,
                'timing': timing,
                'date': str(ed_date),
            }
        except Exception:
            continue
    return out


def _earnings_cell(symbol: str, earnings_info: dict, watchlist: set) -> str:
    """Compact earnings badge for a given position symbol."""
    sym = symbol.upper()
    info = earnings_info.get(sym)
    if info:
        days = info['days_until']
        timing = f" {info['timing']}" if info['timing'] else ''
        if days == 0:
            return f"⚠ today{timing}"
        if days == 1:
            return f"⚠ 1d{timing}"
        return f"⚠ {days}d{timing}"
    if sym in watchlist:
        return "📋 watch"
    return ''


def _render_earnings_banner(position_symbols: list) -> None:
    """Show a warning banner if any held position has earnings within 7 days."""
    if not position_symbols:
        return
    info = _fetch_earnings_info(tuple(sorted({s.upper() for s in position_symbols})))
    if not info:
        return
    alerts = []
    for sym, meta in sorted(info.items(), key=lambda kv: kv[1]['days_until']):
        days = meta['days_until']
        timing = f" {meta['timing']}" if meta['timing'] else ''
        when = 'today' if days == 0 else ('tomorrow' if days == 1 else f'in {days}d')
        alerts.append(f"**{sym}** {when}{timing}")
    st.warning(
        "⚠ Earnings within 7 days for held positions: "
        + ", ".join(alerts)
    )


def render_portfolio_page():
    tab_auto, tab_scalp, tab_manual, tab_lists = st.tabs([
        "📊 Auto Portfolio (V9-C)", "⚡ Scalping", "📋 Manual Portfolio", "📂 Watch Lists"
    ])

    with tab_auto:
        _render_auto_portfolio()

    with tab_scalp:
        _render_scalping_portfolio()

    with tab_manual:
        _render_manual_portfolio()

    with tab_lists:
        _render_watch_lists()


def _render_manual_portfolio():
    """Manual portfolio section (buy/sell, edit, performance)."""
    portfolio = _get_portfolio()

    # Auto-fetch prices once per session (on first load) if positions exist
    if not st.session_state.get('prices_fetched_session', False):
        if portfolio.positions_count > 0:
            with st.spinner("Fetching current prices..."):
                portfolio.update_prices()
        # Ensure today's snapshot exists (needed for tomorrow's daily P&L)
        portfolio.ensure_today_snapshot()
        st.session_state['prices_fetched_session'] = True

    # ── Refresh / Snapshot buttons ──
    col_refresh, col_snap, _ = st.columns([1, 1, 4])
    with col_refresh:
        if st.button("Refresh Prices"):
            with st.spinner("Fetching prices..."):
                portfolio.update_prices()
                st.session_state['prices_fetched_session'] = True
                st.toast("Prices updated")
                st.rerun()
    with col_snap:
        if st.button("Save Snapshot"):
            snap = portfolio.daily_snapshot()
            st.toast(f"Snapshot saved: ${snap['total_value']:,.0f}")

    # ── Summary metrics ──
    summary = portfolio.get_summary()

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    def _fmt_pnl(v):
        if v is None:
            return "N/A"
        return f"${v:+,.0f}" if v != 0 else "$0"

    m1.metric("Total Value", f"${summary['total_value']:,.0f}",
              delta=f"${summary['total_pnl']:+,.0f}" if summary['total_pnl'] != 0 else None)
    m2.metric("Cash", f"${summary['cash']:,.0f}")
    m3.metric("Market Value", f"${summary['market_value']:,.0f}")
    m4.metric("Daily P&L",  _fmt_pnl(summary['daily_pnl']),
              help="Change vs yesterday's snapshot. Requires daily snapshots.")
    m5.metric("WTD P&L",   _fmt_pnl(summary['wtd_pnl']),
              help="Change since Monday's snapshot.")
    m6.metric("YTD P&L",   _fmt_pnl(summary['ytd_pnl']),
              help="Change since first snapshot of the year.")

    st.divider()

    # ── Open Positions ──
    positions = portfolio.get_positions()
    if positions:
        st.subheader(f"Open Positions ({len(positions)})")

        _sym_list = [p['symbol'] for p in positions]
        _earn_info = _fetch_earnings_info(tuple(sorted({s.upper() for s in _sym_list})))
        _watchlist = _load_reporting_watchlist()
        _render_earnings_banner(_sym_list)

        rows = []
        today = datetime.now(tz=None).date()
        for p in positions:
            change_pct = ((p['current_price'] - p['entry_price']) / p['entry_price']) * 100
            entry_date = p.get('entry_date', '')
            hold_days = None
            if entry_date:
                try:
                    hold_days = (today - datetime.strptime(entry_date, '%Y-%m-%d').date()).days
                except ValueError:
                    pass
            rows.append({
                'Symbol': p['symbol'],
                'Date': entry_date,
                'Days': hold_days,
                'Mode': p.get('mode', ''),
                'Shares': p['shares'],
                'Entry $': f"${p['entry_price']:.2f}",
                'Current $': f"${p['current_price']:.2f}",
                'Change%': round(change_pct, 1),
                'P&L $': round(p.get('unrealized_pnl', 0), 0),
                'Quality': p['quality'],
                'Stop': f"${p['stop']:.2f}",
                'Target': f"${p['target']:.2f}",
                'Sector': p.get('sector', ''),
                'Earnings': _earnings_cell(p['symbol'], _earn_info, _watchlist),
            })

        # ── Total row ──
        total_market = sum(p['current_price'] * p['shares'] for p in positions)
        total_cost   = sum(p.get('cost_basis', p['entry_price'] * p['shares']) for p in positions)
        total_pnl    = sum(p.get('unrealized_pnl', 0) for p in positions)
        total_pnl_pct = ((total_market - total_cost) / total_cost * 100) if total_cost > 0 else 0
        rows.append({
            'Symbol': 'TOTAL',
            'Date': '',
            'Days': None,
            'Mode': '',
            'Shares': None,
            'Entry $': f"${total_cost:,.0f}",
            'Current $': f"${total_market:,.0f}",
            'Change%': round(total_pnl_pct, 1),
            'P&L $': round(total_pnl, 0),
            'Quality': '',
            'Stop': '',
            'Target': '',
            'Sector': '',
            'Earnings': '',
        })

        df_pos = pd.DataFrame(rows)
        pnl_cols = [c for c in ['Change%', 'P&L $'] if c in df_pos.columns]
        styled = df_pos.style.map(_color_pnl, subset=pnl_cols)
        st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        st.info("No open positions. Buy a position below.")

    st.divider()

    # ── Buy Position ──
    st.subheader("Buy Position")
    tab_manual, tab_scan = st.tabs(["Manual Entry", "From Scan Results"])

    with tab_manual:
        ticker_input = st.text_input("Ticker Symbol", key="manual_ticker",
                                      placeholder="e.g. AAPL")

        if st.button("Fetch Price", key="fetch_price_btn") and ticker_input:
            sym = ticker_input.strip().upper()
            with st.spinner(f"Fetching {sym}..."):
                price = _fetch_price_for_ticker(sym)
            if price:
                st.session_state['manual_price'] = price
                st.session_state['manual_ticker_resolved'] = sym
            else:
                st.error(f"Could not fetch price for {ticker_input}")

        if 'manual_price' in st.session_state and 'manual_ticker_resolved' in st.session_state:
            sym = st.session_state['manual_ticker_resolved']
            price = st.session_state['manual_price']
            st.success(f"{sym}: ${price:.2f}")

            col1, col2 = st.columns(2)
            with col1:
                mode = st.selectbox("Mode", ['swing', 'daytrade', 'longterm', 'scalping'],
                                     key="manual_mode")
                quality = st.selectbox("Quality", ['GOLD', 'PREMIUM', 'HIGH', 'STANDARD'],
                                        key="manual_quality")
            with col2:
                stop = st.number_input("Stop Loss", value=round(price * 0.95, 2),
                                        min_value=0.01, key="manual_stop")
                target = st.number_input("Target", value=round(price * 1.10, 2),
                                          min_value=0.01, key="manual_target")

            # Default shares = 10% of total portfolio value
            alloc_10pct = summary['total_value'] * 0.10
            auto_shares = max(1, int(alloc_10pct / price))
            shares = st.number_input("Shares", min_value=1, value=auto_shares,
                                      key="manual_shares")

            rr = round((target - price) / max(price - stop, 0.01), 2)
            cost = price * shares
            alloc_pct = (cost / summary['total_value'] * 100) if summary['total_value'] > 0 else 0
            st.caption(f"R:R = {rr:.1f} | Cost: ${cost:,.0f} ({alloc_pct:.0f}%) | Cash: ${summary['cash']:,.0f}")

            if st.button("Buy", type="primary", key="manual_buy_btn"):
                try:
                    portfolio.buy_at_market(sym, shares, stop, target, mode, quality)
                    st.toast(f"Bought {shares} shares of {sym}")
                    del st.session_state['manual_price']
                    del st.session_state['manual_ticker_resolved']
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

    with tab_scan:
        # Get scan results from session, or load latest signals if none
        scan_results = st.session_state.get('scan_results', None)

        if scan_results is None:
            # Try to load latest signals from CSV
            signals_dir = 'scanner_output/signals'
            scan_results = []

            csvs = list_files(signals_dir, 'signals_*.csv')
            # Search first 20 recent files for HIGH+ signals
            for csv_name in csvs[:20]:
                try:
                    scan_df = load_data(f"{signals_dir}/{csv_name}")
                    if scan_df is not None and not scan_df.empty and 'Quality' in scan_df.columns:
                        # Filter by quality (HIGH+ by default, like scan_page)
                        scan_df = scan_df[scan_df['Quality'].isin(['GOLD', 'PREMIUM', 'HIGH'])]

                        if not scan_df.empty:
                            scan_results = scan_df.to_dict('records')
                            st.session_state['scan_results'] = scan_results
                            break  # Found signals, stop searching
                except Exception:
                    continue  # Try next file

        if scan_results and len(scan_results) > 0:
            symbols = [r.get('Symbol', r.get('symbol', '')) for r in scan_results]
            existing = {p['symbol'] for p in positions}
            available = [s for s in symbols if s not in existing]

            if available:
                pick = st.selectbox("Signal", available, key="add_signal_pick")
                sig = next(r for r in scan_results
                           if r.get('Symbol', r.get('symbol', '')) == pick)

                sig_price = float(sig.get('Price', 0))
                alloc_10pct = summary['total_value'] * 0.10
                auto_shares = max(1, int(alloc_10pct / sig_price)) if sig_price > 0 else 1
                # Use dynamic key so each stock has its own input value
                shares_input = st.number_input("Shares", min_value=1,
                                                value=auto_shares,
                                                key=f"add_shares_{pick}")

                cost = sig_price * shares_input
                alloc_pct = (cost / summary['total_value'] * 100) if summary['total_value'] > 0 else 0
                st.caption(f"Quality: {sig.get('Quality', '?')} | "
                           f"Price: ${sig_price:.2f} | "
                           f"R:R: {sig.get('R:R', 0):.1f} | "
                           f"Cost: ${cost:,.0f} ({alloc_pct:.0f}%)")

                if st.button("Add to Portfolio", type="primary", key="scan_add_btn"):
                    try:
                        from sentiment import get_sector_for_ticker
                        sector = get_sector_for_ticker(pick)
                    except Exception:
                        sector = ''
                    try:
                        portfolio.add_position(sig, shares_input, sector=sector)
                        st.toast(f"Added {shares_input} shares of {pick}")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
            else:
                st.caption("All scan signals already in portfolio.")
        else:
            st.caption("No recent scan signals found. Run a scan to generate signals.")

    st.divider()

    # ── Close / Edit in two columns ──
    col_close, col_edit = st.columns(2)

    with col_close:
        st.subheader("Close Position")
        if positions:
            pos_symbols = [p['symbol'] for p in positions]
            close_sym = st.selectbox("Position", pos_symbols, key="close_pick")
            pos_data = next(p for p in positions if p['symbol'] == close_sym)

            exit_price = st.number_input("Exit Price",
                                          value=float(pos_data['current_price']),
                                          min_value=0.01,
                                          key=f"exit_price_{close_sym}")

            est_pnl = (exit_price - pos_data['entry_price']) * pos_data['shares']
            st.caption(f"Entry: ${pos_data['entry_price']:.2f} | "
                       f"Shares: {pos_data['shares']} | "
                       f"Est P&L: ${est_pnl:+,.0f}")

            if st.button("Close Position", type="secondary", key="close_btn"):
                trade = portfolio.close_position(close_sym, exit_price)
                st.toast(f"Closed {close_sym}: ${trade['pnl']:+,.0f}")
                st.rerun()
        else:
            st.caption("No open positions to close.")

    with col_edit:
        st.subheader("Edit Position")
        if positions:
            edit_sym = st.selectbox("Position to Edit",
                                     [p['symbol'] for p in positions], key="edit_pick")
            edit_pos = next(p for p in positions if p['symbol'] == edit_sym)

            new_stop = st.number_input("New Stop Loss",
                                        value=max(0.01, float(edit_pos['stop'])),
                                        min_value=0.01, key="edit_stop")
            new_target = st.number_input("New Target",
                                          value=max(0.01, float(edit_pos['target'])),
                                          min_value=0.01, key="edit_target")

            changes = []
            if abs(new_stop - edit_pos['stop']) > 0.001:
                changes.append(f"Stop: ${edit_pos['stop']:.2f} -> ${new_stop:.2f}")
            if abs(new_target - edit_pos['target']) > 0.001:
                changes.append(f"Target: ${edit_pos['target']:.2f} -> ${new_target:.2f}")

            if changes:
                st.caption(" | ".join(changes))

            if st.button("Update Position", key="edit_btn"):
                try:
                    if abs(new_stop - edit_pos['stop']) > 0.001:
                        portfolio.update_stop(edit_sym, new_stop)
                    if abs(new_target - edit_pos['target']) > 0.001:
                        portfolio.update_target(edit_sym, new_target)
                    st.toast(f"Updated {edit_sym}")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
        else:
            st.caption("No open positions to edit.")

    st.divider()

    # ── Performance ──
    perf = portfolio.get_performance()

    st.subheader("Performance")
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Sharpe Ratio", f"{perf['sharpe']:.2f}")
    p2.metric("Max Drawdown", f"-{perf['max_drawdown_pct']:.1f}%")
    p3.metric("Win Rate", f"{perf['win_rate']:.0f}%")
    p4.metric("Total Trades", perf['total_trades'])

    if perf['equity_curve']:
        eq_df = pd.DataFrame(perf['equity_curve'], columns=['Date', 'Value'])
        eq_df['Date'] = pd.to_datetime(eq_df['Date'])
        st.line_chart(eq_df.set_index('Date')['Value'])

    # ── Trade History ──
    trades = portfolio.get_trade_history()
    if trades:
        st.subheader("Trade History")
        hist_rows = []
        for t in reversed(trades):
            hist_rows.append({
                'Symbol': t.get('symbol', ''),
                'Entry': f"${t.get('entry_price', 0):.2f}",
                'Exit': f"${t.get('exit_price', 0):.2f}",
                'P&L': f"${t.get('pnl', 0):+,.0f}",
                'P&L%': f"{t.get('pnl_pct', 0):+.1f}%",
                'Hold': f"{t.get('hold_days', 0)}d",
                'Quality': t.get('quality', ''),
                'Date': t.get('exit_date', ''),
            })
        st.dataframe(pd.DataFrame(hist_rows), use_container_width=True, hide_index=True)


def _render_auto_portfolio():
    """Auto Virtual Portfolio section — tracks all V9-C signals automatically."""
    import auto_portfolio as ap

    # Decode user_id from JWT without importing api.auth (avoids heavy deps in Streamlit context)
    def _decode_sub(token: str):
        try:
            import base64, json as _json
            parts = token.split('.')
            if len(parts) != 3:
                return None
            pad = parts[1] + '=' * (-len(parts[1]) % 4)
            return _json.loads(base64.urlsafe_b64decode(pad)).get('sub')
        except Exception:
            return None

    _token = st.session_state.get('token', '')
    _user_id = _decode_sub(_token) if _token else None

    st.subheader("Auto Virtual Portfolio (V9-H Signals)")
    st.caption(
        "Automatically tracks GOLD/PREMIUM signals (breakouts require Minervini≥7; "
        "BOUNCE/CONTINUATION exempt). Position size is configurable below. "
        "Stops auto-close positions. No duplicates — each ticker enters once while open."
    )

    data = ap.load(user_id=_user_id)

    # Auto-refresh prices on page load (at most once per 5 minutes)
    _REFRESH_KEY = '_ap_last_refresh'
    import time as _time
    _now = _time.time()
    if data['positions'] and (_now - st.session_state.get(_REFRESH_KEY, 0)) > 300:
        try:
            result = ap.refresh_prices(user_id=_user_id)
            data = result['data']
            st.session_state[_REFRESH_KEY] = _now
        except Exception:
            pass  # fall through with stale prices

    summary = ap.get_summary(data)
    cash    = summary['cash']
    cap     = summary['capital']

    # ── Capital-depleted warning ──
    if cash < cap * 0.10 and summary['open_count'] > 0:
        st.warning(
            f"⚠️ Capital nearly fully deployed — "
            f"${cash:,.0f} remaining. New signals will be skipped until positions close."
        )

    # ── Summary metrics ──
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Capital",      f"${cap:,.0f}")
    m2.metric("Cash",         f"${cash:,.0f}",
              delta=f"{cash/cap*100:.0f}% free")
    m3.metric("Market Value", f"${summary['market_value']:,.0f}")
    m4.metric("Unrealized",   f"${summary['unrealized']:+,.0f}")
    m5.metric("Realized P&L", f"${summary['realized']:+,.0f}")
    m6.metric("Open / Closed",
              f"{summary['open_count']} / {summary['closed_count']}")

    # ── Action controls ──
    from datetime import date as _date

    # Row 1: position size + date filter (settings)
    cfg_col1, cfg_col2, cfg_col3 = st.columns([1, 1.5, 3])
    with cfg_col1:
        pos_pct_label = st.selectbox(
            "Position size",
            options=["10% ($1K)", "5% ($500)"],
            key="ap_pos_pct",
            help="Percentage of $10K capital allocated per trade",
        )
        pos_pct = 0.05 if pos_pct_label.startswith("5%") else 0.10

    with cfg_col2:
        use_filter = st.checkbox("Filter by date", value=False, key="ap_use_date_filter")
        scan_min_date = None
        if use_filter:
            scan_from = st.date_input(
                "From date",
                value=_date.today().replace(day=1),
                min_value=_date(2023, 1, 1),
                max_value=_date.today(),
                key="ap_scan_from",
            )
            scan_min_date = scan_from.strftime('%Y-%m-%d')

    # Row 2: action buttons
    col_scan, col_recalc, col_refresh, col_trail, col_missed, col_swap, col_reset = st.columns(
        [1.2, 1.5, 1.3, 1.5, 1.4, 1.2, 0.8]
    )

    with col_scan:
        if st.button("📥 Scan Signals", key="ap_scan",
                     help="Add new V9-C signals not yet in portfolio (from date if filter set)"):
            with st.spinner("Scanning signal files..."):
                result = ap.scan_and_add(min_date=scan_min_date, position_pct=pos_pct, user_id=_user_id)
            data    = result['data']
            summary = ap.get_summary(data)
            if result['added']:
                st.toast(f"Added: {', '.join(result['added_symbols'])}")
            else:
                st.toast("No new signals found.")
            msgs = []
            if result['skipped_dup']:
                msgs.append(f"{result['skipped_dup']} already open")
            if result['skipped_cash']:
                msgs.append(f"{result['skipped_cash']} skipped (no cash): {', '.join(result['skipped_cash_syms'])}")
            if msgs:
                st.info(" | ".join(msgs))
            st.rerun()

    with col_recalc:
        if st.button("♻️ Recalculate", key="ap_recalc",
                     help="Reset portfolio and rescan ALL signals with selected position size & date filter"):
            with st.spinner(f"Recalculating with {pos_pct_label}..."):
                result = ap.recalculate(position_pct=pos_pct, min_date=scan_min_date, user_id=_user_id)
            data    = result['data']
            summary = ap.get_summary(data)
            st.toast(
                f"Recalculated: {result['added']} positions added "
                f"({pos_pct_label}, "
                f"{result['files_scanned']} files scanned)"
            )
            if result['skipped_cash']:
                st.info(f"{result['skipped_cash']} signals skipped (no cash)")
            st.rerun()

    with col_refresh:
        if st.button("🔄 Refresh & Check Stops", key="ap_refresh",
                     help="Fetch current prices; auto-close if initial stop hit"):
            with st.spinner("Fetching prices..."):
                result = ap.refresh_prices(user_id=_user_id)
            data    = result['data']
            summary = ap.get_summary(data)
            if result['closed']:
                st.toast(f"Stop hit — closed: {', '.join(result['closed'])}")
            else:
                st.toast(f"Prices updated ({result['updated']} symbols). No stops hit.")
            st.rerun()

    with col_trail:
        if st.button("📈 Simulate Trailing Stops", key="ap_trail",
                     help=f"Walk every day since entry; close on trailing stop hit ({int(ap.TRAIL_PCT*100)}% from high)"):
            with st.spinner("Simulating trailing stops..."):
                result = ap.simulate_trailing_stops(user_id=_user_id)
            data    = result['data']
            summary = ap.get_summary(data)
            if result['closed']:
                st.toast(f"Trailing stop triggered — closed: {', '.join(result['closed'])}")
            else:
                st.toast(f"No trailing stops hit ({result['checked']} positions checked).")
            st.rerun()

    with col_missed:
        _missed_days = ap.MISSED_TRADE_MAX_AGE_DAYS
        if st.button("🔍 Find Missed Trades", key="ap_missed",
                     help=f"Re-scan the last {_missed_days} days of signal files for "
                          f"V9-C signals never taken (missed opportunities)"):
            with st.spinner(f"Scanning the last {_missed_days} days for missed trades..."):
                result = ap.rebuild_skipped_cash(user_id=_user_id)
            data    = result['data']
            summary = ap.get_summary(data)
            st.toast(f"Found {result['found']} missed trade(s).")
            st.rerun()

    with col_swap:
        if st.button("⚡ Swap Advisor", key="ap_swap",
                     help="Find skipped signals that could replace weaker open positions"):
            with st.spinner("Analysing swap opportunities..."):
                swaps = ap.suggest_swaps(notify=True, user_id=_user_id)
            st.session_state['ap_swap_results'] = swaps
            if swaps:
                st.toast(f"Found {len(swaps)} swap suggestion(s).")
            else:
                st.toast("No swap opportunities found (no weak positions or no fresh strong signals).")

    # ── Swap suggestions panel (persists across reruns via session_state) ──
    _swaps_saved = st.session_state.get('ap_swap_results')
    if _swaps_saved:
        with st.container(border=True):
            st.markdown(f"**⚡ {len(_swaps_saved)} swap suggestion(s)**")
            for _i, _sw in enumerate(_swaps_saved):
                _c1, _c2, _c3 = st.columns([3, 3, 1.2])
                _c1.markdown(
                    f"Close **{_sw['close_symbol']}** ({_sw['close_pnl_pct']:+.1f}%)"
                )
                _upside_pct = ''
                if _sw.get('open_entry') and _sw.get('open_target'):
                    _upside_pct = (
                        f"  ·  {((_sw['open_target'] - _sw['open_entry']) / _sw['open_entry'] * 100):.1f}% upside"
                    )
                _c2.markdown(
                    f"Open **{_sw['open_symbol']}** [{_sw.get('open_quality', '')}]  "
                    f"R:R {_sw.get('open_rr', 0):.2f}{_upside_pct}"
                )
                if _c3.button("Execute", key=f"ap_exec_swap_{_i}_{_sw['close_symbol']}_{_sw['open_symbol']}"):
                    _res = ap.execute_swap(_sw['close_symbol'], _sw['open_symbol'], user_id=_user_id)
                    if _res.get('ok'):
                        st.session_state['ap_swap_last_undo'] = True
                        st.session_state['ap_swap_results'] = [
                            s for s in _swaps_saved
                            if not (s['close_symbol'] == _sw['close_symbol']
                                    and s['open_symbol'] == _sw['open_symbol'])
                        ]
                        st.toast(
                            f"Closed {_sw['close_symbol']} @ ${_res['closed'].get('exit_price', 0):.2f} → "
                            f"Opened {_sw['open_symbol']} @ ${_res['opened'].get('entry_price', 0):.2f}"
                        )
                        st.rerun()
                    else:
                        st.error(f"Swap failed: {_res.get('reason')}")

            if st.session_state.get('ap_swap_last_undo'):
                if st.button("↩️ Undo last swap", key="ap_swap_undo"):
                    _u = ap.undo_last_swap(user_id=_user_id)
                    if _u.get('ok'):
                        st.session_state['ap_swap_last_undo'] = False
                        st.toast(
                            f"Restored {_u.get('restored_position')}, removed {_u.get('removed_position')}."
                        )
                        st.rerun()
                    else:
                        st.error(f"Undo failed: {_u.get('reason')}")

    with col_reset:
        if st.button("🗑️ Reset", key="ap_reset",
                     help="Clear all auto-portfolio positions and history"):
            st.session_state['ap_confirm_reset'] = True

    if st.session_state.get('ap_confirm_reset'):
        n_open = len(data.get('positions', []))
        st.warning(
            f"This will immediately clear all {n_open} open position(s), capital, "
            f"and history for this account's auto-portfolio. This cannot be undone. "
            f"Are you sure?"
        )
        c1, c2 = st.columns(2)
        if c1.button("Yes, reset it", key="ap_reset_yes", type="primary"):
            ap.reset(user_id=_user_id)
            st.session_state['ap_confirm_reset'] = False
            st.toast("Auto portfolio reset.")
            st.rerun()
        if c2.button("Cancel", key="ap_reset_cancel"):
            st.session_state['ap_confirm_reset'] = False
            st.rerun()

    st.divider()

    # ── Open Positions ──
    positions = data.get('positions', [])
    if positions:
        st.markdown(f"**Open Positions ({len(positions)})**")

        _sym_list = [p['symbol'] for p in positions]
        _earn_info = _fetch_earnings_info(tuple(sorted({s.upper() for s in _sym_list})))
        _watchlist = _load_reporting_watchlist()
        _render_earnings_banner(_sym_list)

        rows = []
        for p in positions:
            ep  = p['entry_price']
            cur = p.get('current_price', ep)
            chg = round((cur - ep) / ep * 100, 1) if ep else 0
            unr = round((cur - ep) * p['shares'], 0)
            dist_stop   = round((cur - p['stop'])   / ep * 100, 1) if ep else 0
            dist_target = round((p['target'] - cur) / ep * 100, 1) if ep else 0
            trail_stop = p.get('trail_stop')
            rows.append({
                'Symbol':       p['symbol'],
                'Added':        p['date_added'],
                'Type':         p['mode'],
                'Quality':      p['quality'],
                'Minervini':    p.get('minervini_score', ''),
                'Entry $':      f"${ep:.2f}",
                'Current $':    f"${cur:.2f}",
                'Chg%':         chg,
                'Unr. P&L':     unr,
                'Stop $':       f"${p['stop']:.2f}",
                'Trail Stop $': f"${trail_stop:.2f}" if trail_stop else '—',
                'To Stop%':     dist_stop,
                'Target $':     f"${p['target']:.2f}",
                'To Tgt%':      dist_target,
                'Shares':       p['shares'],
                'Cost $':       f"${p['cost']:,.0f}",
                'Earnings':     _earnings_cell(p['symbol'], _earn_info, _watchlist),
            })
        # ── TOTAL row ──
        total_cost   = sum(p['cost'] for p in positions)
        total_mktval = sum(p.get('current_price', p['entry_price']) * p['shares']
                          for p in positions)
        total_unr    = round(total_mktval - total_cost, 0)
        total_chg    = round((total_mktval - total_cost) / total_cost * 100, 1) if total_cost else 0
        rows.append({
            'Symbol':       'TOTAL',
            'Added':        '',
            'Type':         '',
            'Quality':      '',
            'Minervini':    '',
            'Entry $':      f"${total_cost:,.0f}",
            'Current $':    f"${total_mktval:,.0f}",
            'Chg%':         total_chg,
            'Unr. P&L':     total_unr,
            'Stop $':       '',
            'Trail Stop $': '',
            'To Stop%':     '',
            'Target $':     '',
            'To Tgt%':      '',
            'Shares':       '',
            'Cost $':       f"${total_cost:,.0f}",
            'Earnings':     '',
        })

        df_open = pd.DataFrame(rows)
        styled_open = df_open.style.map(
            _color_pnl, subset=[c for c in ['Chg%', 'Unr. P&L', 'To Stop%'] if c in df_open.columns]
        )
        st.dataframe(styled_open, use_container_width=True, hide_index=True)

        # Manual close for individual positions
        with st.expander("Close a Position Manually"):
            sym_to_close = st.selectbox(
                "Symbol", [p['symbol'] for p in positions], key="ap_close_sym"
            )
            pos_data = next(p for p in positions if p['symbol'] == sym_to_close)
            exit_px  = st.number_input(
                "Exit Price", value=float(pos_data.get('current_price', pos_data['entry_price'])),
                min_value=0.01, key=f"ap_close_price_{sym_to_close}"
            )
            est = round((exit_px - pos_data['entry_price']) * pos_data['shares'], 2)
            st.caption(f"Entry: ${pos_data['entry_price']:.2f} | Shares: {pos_data['shares']} | Est P&L: ${est:+,.0f}")
            if st.button("Close Position", key="ap_close_btn"):
                ap.close_position(sym_to_close, exit_px, reason='manual')
                st.toast(f"Closed {sym_to_close}: ${est:+,.0f}")
                st.rerun()
    else:
        st.info("No open positions. Click **Scan Signals** to add V9-C signals.")

    # ── IB Execution Controls ──
    with st.expander("IB Live Execution", expanded=False):
        try:
            from ib_executor import (
                IB_EXEC_CONFIG, is_kill_switch_active,
                activate_kill_switch, deactivate_kill_switch,
                get_daily_pnl, _load_exec_log,
            )

            ib_c1, ib_c2, ib_c3, ib_c4 = st.columns(4)
            ib_c1.metric("Enabled", "YES" if IB_EXEC_CONFIG['enabled'] else "NO")
            ib_c2.metric("Mode", "PAPER" if IB_EXEC_CONFIG['paper_mode'] else "LIVE")
            ib_c3.metric("Kill Switch", "ACTIVE" if is_kill_switch_active() else "Off")
            ib_c4.metric("Daily P&L", f"${get_daily_pnl():+,.0f}")

            ib_a1, ib_a2, ib_a3 = st.columns(3)
            with ib_a1:
                if st.button("Enable Execution", key="ib_enable"):
                    IB_EXEC_CONFIG['enabled'] = True
                    st.toast("IB execution enabled")
                    st.rerun()
            with ib_a2:
                if st.button("Disable Execution", key="ib_disable"):
                    IB_EXEC_CONFIG['enabled'] = False
                    st.toast("IB execution disabled")
                    st.rerun()
            with ib_a3:
                if is_kill_switch_active():
                    if st.button("Deactivate Kill Switch", key="ib_unkill", type="primary"):
                        deactivate_kill_switch()
                        st.toast("Kill switch deactivated")
                        st.rerun()
                else:
                    if st.button("KILL SWITCH", key="ib_kill", type="primary"):
                        activate_kill_switch("Streamlit UI")
                        st.toast("Kill switch ACTIVATED — all orders halted")
                        st.rerun()

            # Recent execution log
            log = _load_exec_log()
            if log:
                from datetime import date as _dt
                today_str = _dt.today().isoformat()
                recent = [e for e in log[-20:]]
                if recent:
                    st.caption(f"Recent orders ({len(log)} total)")
                    log_rows = []
                    for e in reversed(recent):
                        log_rows.append({
                            'Time': e.get('timestamp', '?')[:19],
                            'Mode': 'LIVE' if e.get('live') else 'PAPER',
                            'Action': e.get('action', '?'),
                            'Symbol': e.get('symbol', '?'),
                            'Shares': e.get('shares', 0),
                            'Price': f"${e.get('entry_price', 0):.2f}",
                            'Status': e.get('status', '?'),
                        })
                    st.dataframe(pd.DataFrame(log_rows), use_container_width=True, hide_index=True)
            else:
                st.caption("No execution history yet")

            st.caption(
                f"Max position: ${IB_EXEC_CONFIG['max_position_value']:,} | "
                f"Daily loss limit: ${IB_EXEC_CONFIG['max_daily_loss_usd']} | "
                f"Order type: {IB_EXEC_CONFIG['order_type']} | "
                f"Bracket: {IB_EXEC_CONFIG['use_bracket']}"
            )
        except Exception as e:
            st.warning(f"IB executor not available: {e}")

    # ── Portfolio Health & Diversity (advisory) ──
    if positions:
        with st.expander("Portfolio Health & Diversity", expanded=False):
            try:
                from position_health import evaluate_portfolio_health, is_etf

                health = evaluate_portfolio_health(data)

                # Cash status bar
                cash_pct = health['cash_pct']
                if health['cash_status'] == 'CRITICAL':
                    st.error(f"CRITICAL: Cash at {cash_pct:.1%} (${health['cash']:,.0f} of ${health['total_value']:,.0f})")
                elif health['cash_status'] == 'LOW':
                    st.warning(f"Low cash: {cash_pct:.1%} (${health['cash']:,.0f} of ${health['total_value']:,.0f})")
                else:
                    st.success(f"Cash OK: {cash_pct:.1%} (${health['cash']:,.0f})")

                # Diversity metrics row
                div = health['diversity']
                d1, d2, d3, d4 = st.columns(4)
                d1.metric("Sectors", f"{len(div['sector_counts'])}")
                d2.metric("Modes", f"{len(div['mode_counts'])}")
                d3.metric("ETF %", f"{div['etf_pct']:.0%}")
                safe_color = "normal" if div['safe_pct'] >= 0.65 else "off"
                d4.metric("Safe / Risky", f"{div['safe_pct']:.0%} / {div['risky_pct']:.0%}",
                          delta="balanced" if div['risky_pct'] <= 0.30 else "rebalance needed",
                          delta_color=safe_color)

                # Sector breakdown
                sc1, sc2 = st.columns(2)
                with sc1:
                    st.caption("Sector Breakdown")
                    for sector, count in sorted(div['sector_counts'].items(), key=lambda x: -x[1]):
                        bar = '█' * count
                        flag = ' ⚠' if count > 3 else ''
                        st.text(f"  {sector:15s} {bar} {count}{flag}")

                with sc2:
                    st.caption("Mode Breakdown")
                    for mode, count in sorted(div['mode_counts'].items(), key=lambda x: -x[1]):
                        bar = '█' * count
                        flag = ' ⚠' if count > 5 else ''
                        st.text(f"  {mode:15s} {bar} {count}{flag}")

                # Risk ranking table
                st.caption("Risk Ranking (riskiest first — trim candidates)")
                risk_rows = []
                for p in health['risk_ranking']:
                    rf = p['risk_factors']
                    risk_rows.append({
                        'Symbol':     p['symbol'],
                        'Risk':       p['risk_score'],
                        'P&L%':       rf['gain_pct'],
                        'Days':       rf['days_held'],
                        'ATR%':       rf.get('atr_pct', 0),
                        'Stale':      'Yes' if rf['stale'] else '',
                        'Sector':     p.get('sector', ''),
                        'Mode':       p.get('mode', ''),
                        'ETF':        'Yes' if is_etf(p['symbol']) else '',
                        'Value%':     rf['value_pct'],
                    })
                if risk_rows:
                    df_risk = pd.DataFrame(risk_rows)
                    st.dataframe(df_risk, use_container_width=True, hide_index=True)

                # Alerts
                if health['alerts']:
                    st.caption("Alerts & Suggestions")
                    for a in health['alerts']:
                        if a['severity'] == 'CRITICAL':
                            st.error(f"[{a['type']}] {a['message']}")
                        elif a['severity'] == 'WARNING':
                            st.warning(f"[{a['type']}] {a['message']}")
                        else:
                            st.info(f"[{a['type']}] {a['message']}")
            except Exception as _health_err:
                st.caption(f"Health check unavailable: {_health_err}")

    # ── Closed Positions ──
    closed = data.get('closed', [])
    if closed:
        st.markdown(f"**Closed Positions ({len(closed)})**")
        win_count = sum(1 for t in closed if t.get('pnl', 0) > 0)
        total_pnl = sum(t.get('pnl', 0) for t in closed)
        win_rate  = win_count / len(closed) * 100 if closed else 0
        st.caption(
            f"Realized P&L: **${total_pnl:+,.0f}** | "
            f"Win rate: **{win_rate:.0f}%** ({win_count}/{len(closed)})"
        )
        hist_rows = []
        for t in reversed(closed):
            reason = t.get('close_reason', '')
            if reason == 'trailing_stop' and t.get('trail_pct'):
                reason = f"trailing {int(t['trail_pct']*100)}%"
            hist_rows.append({
                'Symbol':    t['symbol'],
                'Added':     t['date_added'],
                'Closed':    t.get('date_closed', ''),
                'Type':      t.get('mode', ''),
                'Quality':   t.get('quality', ''),
                'Entry $':   f"${t['entry_price']:.2f}",
                'Stop $':    f"${t['stop']:.2f}" if t.get('stop') else '—',
                'High $':    f"${t['highest_close']:.2f}" if t.get('highest_close') else '—',
                'Exit $':    f"${t['exit_price']:.2f}",
                'P&L $':     round(t.get('pnl', 0), 0),
                'P&L%':      round(t.get('pnl_pct', 0), 1),
                'Reason':    reason,
                'Shares':    t.get('shares', ''),
            })
        df_hist = pd.DataFrame(hist_rows)
        styled_hist = df_hist.style.map(
            _color_pnl, subset=[c for c in ['P&L $', 'P&L%'] if c in df_hist.columns]
        )
        st.dataframe(styled_hist, use_container_width=True, hide_index=True)

    # ── Trading History (chronological activity log) ────────────────────────
    if positions or closed:
        with st.expander("Trading History", expanded=False):
            history = []
            for p in positions:
                history.append({
                    'Date':     p['date_added'],
                    'Action':   'BUY',
                    'Symbol':   p['symbol'],
                    'Mode':     p.get('mode', ''),
                    'Quality':  p.get('quality', ''),
                    'Price':    p['entry_price'],
                    'Shares':   p['shares'],
                    'Value':    round(p['entry_price'] * p['shares'], 2),
                    'P&L':     '',
                    'Reason':   '',
                })
            for t in closed:
                # Entry event
                history.append({
                    'Date':     t['date_added'],
                    'Action':   'BUY',
                    'Symbol':   t['symbol'],
                    'Mode':     t.get('mode', ''),
                    'Quality':  t.get('quality', ''),
                    'Price':    t['entry_price'],
                    'Shares':   t.get('shares', ''),
                    'Value':    round(t['entry_price'] * t.get('shares', 0), 2),
                    'P&L':     '',
                    'Reason':   '',
                })
                # Exit event
                reason = t.get('close_reason', '')
                if reason == 'trailing_stop' and t.get('trail_pct'):
                    reason = f"trailing {int(t['trail_pct']*100)}%"
                history.append({
                    'Date':     t.get('date_closed', ''),
                    'Action':   'SELL',
                    'Symbol':   t['symbol'],
                    'Mode':     t.get('mode', ''),
                    'Quality':  '',
                    'Price':    t.get('exit_price', 0),
                    'Shares':   t.get('shares', ''),
                    'Value':    round(t.get('exit_price', 0) * t.get('shares', 0), 2),
                    'P&L':     round(t.get('pnl', 0), 0),
                    'Reason':   reason,
                })
            # Sort by date descending, SELL before BUY on same date
            action_order = {'SELL': 0, 'BUY': 1}
            history.sort(key=lambda r: (r['Date'], action_order.get(r['Action'], 2)), reverse=True)

            df_history = pd.DataFrame(history)
            df_history['Price'] = df_history['Price'].apply(
                lambda x: f"${x:.2f}" if isinstance(x, (int, float)) and x else ''
            )
            df_history['Value'] = df_history['Value'].apply(
                lambda x: f"${x:,.0f}" if isinstance(x, (int, float)) and x else ''
            )
            styled_history = df_history.style.map(
                _color_pnl, subset=['P&L']
            ).map(
                lambda v: 'color: green' if v == 'BUY' else ('color: red' if v == 'SELL' else ''),
                subset=['Action']
            )
            st.dataframe(styled_history, use_container_width=True, hide_index=True)

    # ── Missed Trades (skipped — sorted by priority_score) ───────────────────
    skipped = data.get('skipped_cash', [])
    if skipped:
        skip_reason_counts = {}
        for s in skipped:
            r = s.get('skip_reason', 'unknown')
            skip_reason_counts[r] = skip_reason_counts.get(r, 0) + 1
        reason_str = ' | '.join(f"{v} {k}" for k, v in skip_reason_counts.items())
        st.markdown(f"**Missed Trades ({len(skipped)}) — sorted by predicted quality**")
        st.caption(
            f"Skipped signals: {reason_str}. "
            "Priority score = WinProb × quality × R:R × volume. "
            "Momentum% = actual return since signal date."
        )
        missed_rows = []
        total_hyp_pnl = 0.0
        # Already sorted by priority_score desc from auto_portfolio
        for s in skipped:
            ep  = s.get('entry_price', 0)
            cur = s.get('current_price', ep)
            shares = s.get('shares', 0)
            hyp_pnl     = round((cur - ep) * shares, 0) if ep else 0
            hyp_pnl_pct = round((cur - ep) / ep * 100, 1) if ep else 0
            total_hyp_pnl += hyp_pnl
            dist_target = round((s['target'] - cur) / ep * 100, 1) if ep else 0
            missed_rows.append({
                'Symbol':    s['symbol'],
                'Date':      s['date_added'][:10],
                'Reason':    s.get('skip_reason', ''),
                'Quality':   s.get('quality', ''),
                'Type':      s.get('type', s.get('mode', '')),
                'WinProb%':  s.get('win_prob', ''),
                'Grade':     s.get('win_grade', ''),
                'R:R':       s.get('rr', ''),
                'Vol×':      s.get('vol', ''),
                'Priority':  s.get('priority_score', ''),
                'Momentum%': hyp_pnl_pct,
                'Hyp. P&L':  hyp_pnl,
                'Entry $':   f"${ep:.2f}",
                'Current $': f"${cur:.2f}",
                'To Tgt%':   dist_target,
            })
        total_hyp_wins = sum(1 for s in skipped
                             if s.get('current_price', 0) > s.get('entry_price', 0))
        st.caption(
            f"Hypothetical total P&L if all had been taken: "
            f"**${total_hyp_pnl:+,.0f}** | "
            f"Would-be winners: {total_hyp_wins}/{len(skipped)}"
        )
        df_missed = pd.DataFrame(missed_rows)
        styled_missed = df_missed.style.map(
            _color_pnl,
            subset=[c for c in ['Momentum%', 'Hyp. P&L'] if c in df_missed.columns]
        )
        st.dataframe(styled_missed, use_container_width=True, hide_index=True)


# ── Scalping Portfolio tab ────────────────────────────────────────────────────

def _render_scalping_portfolio():
    """Scalping portfolio — auto-populated from feedback agent BREAKOUT/EXIT events."""
    import scalp_portfolio as sp

    st.subheader("⚡ Scalping Portfolio")
    st.caption(
        "Automatically trades on feedback agent BREAKOUT alerts. "
        "Positions are opened on BREAKOUT and closed on EXIT (TRAIL_STOP or FAILED). "
        "Fixed-cent stops: 2¢ fail / 3¢ trail. All positions should close by EOD."
    )

    data    = sp.load()
    summary = sp.get_summary(data)
    cash    = summary['cash']
    cap     = summary['capital']

    # ── Summary metrics ──
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Capital",      f"${cap:,.0f}")
    m2.metric("Cash",         f"${cash:,.0f}",
              delta=f"{cash/cap*100:.0f}% free" if cap else "")
    m3.metric("Market Value", f"${summary['market_value']:,.0f}")
    m4.metric("Unrealized",   f"${summary['unrealized']:+,.0f}")
    m5.metric("Realized P&L", f"${summary['realized']:+,.0f}")
    m6.metric("Open / Closed",
              f"{summary['open_count']} / {summary['closed_count']}")

    # ── Action buttons ──
    col_refresh, col_eod, col_reset = st.columns([1.5, 1.5, 1])

    with col_refresh:
        if st.button("🔄 Refresh Prices", key="sp_refresh"):
            with st.spinner("Fetching prices..."):
                result = sp.refresh_prices()
            data    = result['data']
            summary = sp.get_summary(data)
            st.toast(f"Prices updated ({result['updated']} symbols).")
            st.rerun()

    with col_eod:
        if st.button("🔔 Close All (EOD)", key="sp_eod",
                     help="Close all open scalping positions at current price"):
            with st.spinner("Closing all positions..."):
                result = sp.close_all_eod()
            data    = result['data']
            summary = sp.get_summary(data)
            if result['closed']:
                st.toast(f"EOD close: {', '.join(result['closed'])}")
            else:
                st.toast("No open positions to close.")
            st.rerun()

    with col_reset:
        if st.button("🗑️ Reset", key="sp_reset",
                     help="Clear all scalping portfolio positions and history"):
            sp.reset()
            st.toast("Scalping portfolio reset.")
            st.rerun()

    st.divider()

    # ── Open Positions ──
    positions = data.get('positions', [])
    if positions:
        st.markdown(f"**Open Positions ({len(positions)})**")

        _sym_list = [p['symbol'] for p in positions]
        _earn_info = _fetch_earnings_info(tuple(sorted({s.upper() for s in _sym_list})))
        _watchlist = _load_reporting_watchlist()
        _render_earnings_banner(_sym_list)

        rows = []
        for p in positions:
            ep  = p['entry_price']
            cur = p.get('current_price', ep)
            chg = round((cur - ep) / ep * 100, 2) if ep else 0
            unr = round((cur - ep) * p['shares'], 0)
            risk_cents = round((ep - p['stop']) * 100, 1)
            rows.append({
                'Symbol':     p['symbol'],
                'Time':       p['date_added'],
                'Quality':    p.get('quality', ''),
                'Entry $':    f"${ep:.2f}",
                'Current $':  f"${cur:.2f}",
                'Chg%':       chg,
                'Unr. P&L':   unr,
                'Stop $':     f"${p['stop']:.2f}",
                'Risk ¢':     risk_cents,
                'Target $':   f"${p['target']:.2f}",
                'Vol':        f"{p.get('vol_ratio', 0):.1f}x",
                'Shares':     p['shares'],
                'Cost $':     f"${p['cost']:,.0f}",
                'Earnings':   _earnings_cell(p['symbol'], _earn_info, _watchlist),
            })
        # TOTAL row
        total_cost = sum(p['cost'] for p in positions)
        total_mkt  = sum(p.get('current_price', p['entry_price']) * p['shares']
                         for p in positions)
        total_unr  = round(total_mkt - total_cost, 0)
        total_chg  = round((total_mkt - total_cost) / total_cost * 100, 2) if total_cost else 0
        rows.append({
            'Symbol':    'TOTAL',
            'Time':      '',
            'Quality':   '',
            'Entry $':   f"${total_cost:,.0f}",
            'Current $': f"${total_mkt:,.0f}",
            'Chg%':      total_chg,
            'Unr. P&L':  total_unr,
            'Stop $':    '',
            'Risk ¢':    '',
            'Target $':  '',
            'Vol':       '',
            'Shares':    '',
            'Cost $':    f"${total_cost:,.0f}",
            'Earnings':  '',
        })

        df_open = pd.DataFrame(rows)
        styled_open = df_open.style.map(
            _color_pnl, subset=[c for c in ['Chg%', 'Unr. P&L'] if c in df_open.columns]
        )
        st.dataframe(styled_open, use_container_width=True, hide_index=True)

        # Manual close
        with st.expander("Close a Position Manually"):
            sym_to_close = st.selectbox(
                "Symbol", [p['symbol'] for p in positions], key="sp_close_sym"
            )
            pos_data = next(p for p in positions if p['symbol'] == sym_to_close)
            exit_px = st.number_input(
                "Exit Price",
                value=float(pos_data.get('current_price', pos_data['entry_price'])),
                min_value=0.01, key=f"sp_close_price_{sym_to_close}"
            )
            est = round((exit_px - pos_data['entry_price']) * pos_data['shares'], 2)
            st.caption(
                f"Entry: ${pos_data['entry_price']:.2f} | "
                f"Shares: {pos_data['shares']} | Est P&L: ${est:+,.0f}"
            )
            if st.button("Close Position", key="sp_close_btn"):
                sp.close_position(sym_to_close, exit_px, reason='manual')
                st.toast(f"Closed {sym_to_close}: ${est:+,.0f}")
                st.rerun()
    else:
        st.info(
            "No open scalping positions. "
            "Positions are added automatically when the feedback agent fires a BREAKOUT alert."
        )

    # ── Closed Positions ──
    closed = data.get('closed', [])
    if closed:
        st.markdown(f"**Trade History ({len(closed)})**")
        win_count = sum(1 for t in closed if t.get('pnl', 0) > 0)
        total_pnl = sum(t.get('pnl', 0) for t in closed)
        win_rate  = win_count / len(closed) * 100 if closed else 0
        avg_win   = 0
        avg_loss  = 0
        winners = [t for t in closed if t.get('pnl', 0) > 0]
        losers  = [t for t in closed if t.get('pnl', 0) <= 0]
        if winners:
            avg_win = sum(t.get('pnl_pct', 0) for t in winners) / len(winners)
        if losers:
            avg_loss = sum(t.get('pnl_pct', 0) for t in losers) / len(losers)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total P&L", f"${total_pnl:+,.0f}")
        c2.metric("Win Rate",  f"{win_rate:.0f}%",
                  delta=f"{win_count}W / {len(closed)-win_count}L")
        c3.metric("Avg Win",   f"{avg_win:+.2f}%")
        c4.metric("Avg Loss",  f"{avg_loss:+.2f}%")
        c5.metric("Trades",    f"{len(closed)}")

        hist_rows = []
        for t in reversed(closed):
            hist_rows.append({
                'Symbol':  t['symbol'],
                'Opened':  t['date_added'],
                'Closed':  t.get('date_closed', ''),
                'Entry $': f"${t['entry_price']:.2f}",
                'Exit $':  f"${t['exit_price']:.2f}",
                'P&L $':   round(t.get('pnl', 0), 0),
                'P&L%':    round(t.get('pnl_pct', 0), 2),
                'Reason':  t.get('close_reason', ''),
                'Shares':  t.get('shares', ''),
                'Vol':     f"{t.get('vol_ratio', 0):.1f}x",
            })
        df_hist = pd.DataFrame(hist_rows)
        styled_hist = df_hist.style.map(
            _color_pnl, subset=[c for c in ['P&L $', 'P&L%'] if c in df_hist.columns]
        )
        st.dataframe(styled_hist, use_container_width=True, hide_index=True)


# ── Watch Lists tab ───────────────────────────────────────────────────────────

_LISTS_DIR = "scanner_output/lists"

# Files to display, in order: (filename, label, description)
_WATCHLIST_FILES = [
    ("premium_longterm.txt",       "Premium Long-term",  "PREMIUM/GOLD signals from last long-term (weekly) scan"),
    ("momentum_watch_daytrade.txt","Momentum Watch",     "PREMIUM/GOLD + HIGH-momentum + near-miss — daytrade Phase 2 & monitor input"),
    ("optimizer_watch.txt",        "Optimizer Watch",    "Manually curated watchlist for weight optimizer backtests"),
]

_POSITIONS_FILES = [
    ("positions_swing_mock.csv",   "Swing Positions",    "Auto-appended PREMIUM/GOLD swing signals (mock portfolio)"),
    ("positions_daytrade_mock.csv","Daytrade Positions", "Auto-appended PREMIUM/GOLD daytrade signals (mock portfolio)"),
]

_QUALITY_COLORS = {
    'GOLD':     '#f9a825',
    'PREMIUM':  '#1976d2',
    'HIGH':     '#f57c00',
    'STANDARD': '#757575',
    'REJECT':   '#c62828',
}


def _color_quality_cell(val: str) -> str:
    """Return CSS color for a quality cell value."""
    color = _QUALITY_COLORS.get(str(val).upper(), '')
    if color:
        return f'color: {color}; font-weight: bold'
    return ''


def _read_txt_symbols(path: str) -> tuple[list[str], bool]:
    """Read a symbol list — S3 on cloud, local filesystem otherwise.

    Returns:
        (symbols, found) — found=True means the file existed (even if empty)
    """
    text = load_text(path)
    if text is None:
        return [], False          # file not found
    if not text:
        return [], True           # file found but empty
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith('#')], True


def _read_positions_csv(path: str) -> pd.DataFrame | None:
    """Read a positions CSV — S3 on cloud, local filesystem otherwise.

    Returns:
        DataFrame with rows  — file found and has data
        Empty DataFrame      — file found but has no position rows yet
        None                 — file not found / read failed
    """
    df = load_data(path)
    return df  # preserve empty DataFrame vs None distinction


def _render_watch_lists():
    """Render the Watch Lists tab — shows contents of scanner_output/lists/ files."""
    st.subheader("scanner_output/lists/ — Live Working Files")
    st.caption(
        "These files are auto-generated by cron scans. "
        "They reset each trading day. Click **Refresh** to reload current contents."
    )

    if st.button("↻ Refresh", key="wl_refresh"):
        st.rerun()

    # ── Symbol watchlists (.txt) ──────────────────────────────────────────────
    st.markdown("### Symbol Watchlists")
    cols = st.columns(len(_WATCHLIST_FILES))
    for col, (fname, label, desc) in zip(cols, _WATCHLIST_FILES):
        path = f"{_LISTS_DIR}/{fname}"
        st.text(path)  # show the file path being read for transparency/debugging   

        symbols, found = _read_txt_symbols(path)
        col.metric(label=label, value=f"{len(symbols)} symbols" if found else "Not found")
        col.caption(desc)

    st.divider()

    for fname, label, desc in _WATCHLIST_FILES:
        path = f"{_LISTS_DIR}/{fname}"
        symbols, found = _read_txt_symbols(path)
        with st.expander(f"**{label}** — {fname}  ({len(symbols)} symbols)", expanded=False):
            st.caption(desc)
            if symbols:
                # Display in columns of ~10
                chunk = 10
                rows = [symbols[i:i+chunk] for i in range(0, len(symbols), chunk)]
                for row in rows:
                    st.write("  ".join(f"`{s}`" for s in row))
            elif found:
                st.info("File found but empty — will populate after next scan.")
            else:
                s3_key = f"s3://stocks-breakout-scanner-s3-bucket/{path}"
                st.warning(f"File not found. Expected S3 key: `{s3_key}`")

    # ── Positions CSVs ────────────────────────────────────────────────────────
    st.markdown("### Mock Positions (Auto-Portfolio)")
    for fname, label, desc in _POSITIONS_FILES:
        path = f"{_LISTS_DIR}/{fname}"
        df = _read_positions_csv(path)

        n_rows = len(df) if (df is not None and not df.empty) else 0
        with st.expander(
            f"**{label}** — {fname}  ({n_rows} positions)",
            expanded=True,
        ):
            st.caption(desc)
            if df is not None and not df.empty:
                # Quality summary metrics
                if 'quality' in df.columns:
                    qual_counts = df['quality'].value_counts()
                    m_cols = st.columns(min(len(qual_counts), 5))
                    for i, (q, cnt) in enumerate(qual_counts.items()):
                        m_cols[i].metric(label=q, value=cnt)

                # Style quality column
                styled = df.style
                if 'quality' in df.columns:
                    styled = styled.map(_color_quality_cell, subset=['quality'])

                # Color entry/stop/target columns for readability
                numeric_cols = [c for c in ['entry', 'stop', 'target'] if c in df.columns]
                if numeric_cols:
                    styled = styled.format(
                        {c: "{:.2f}" for c in numeric_cols},
                        na_rep="—"
                    )

                st.dataframe(styled, use_container_width=True, hide_index=True)

                # Action: clear file
                if st.button(f"Clear {label}", key=f"clear_{fname}", type="secondary"):
                    confirm_key = f"confirm_clear_{fname}"
                    st.session_state[confirm_key] = True

                if st.session_state.get(f"confirm_clear_{fname}"):
                    st.warning(f"This will erase all positions in `{path}`. Are you sure?")
                    c1, c2 = st.columns(2)
                    if c1.button("Yes, clear it", key=f"yes_clear_{fname}", type="primary"):
                        _POSITIONS_COLS = ['symbol', 'mode', 'entry', 'entry_date',
                                           'stop', 'target', 'timeframe', 'quality']
                        save_data(pd.DataFrame(columns=_POSITIONS_COLS), path)
                        st.session_state[f"confirm_clear_{fname}"] = False
                        st.toast(f"Cleared {fname}")
                        st.rerun()
                    if c2.button("Cancel", key=f"cancel_clear_{fname}"):
                        st.session_state[f"confirm_clear_{fname}"] = False
                        st.rerun()
            elif df is not None:
                # File was found on S3/local but has no position rows yet
                st.info(
                    "File found but no positions yet — will populate after the next "
                    "scan finds PREMIUM/GOLD signals and the cron uploads."
                )
            else:
                # File not found — show the exact S3 key that was tried
                s3_key = f"s3://stocks-breakout-scanner-s3-bucket/{path}"
                st.warning(
                    f"File not found.  \n"
                    f"Expected S3 key: `{s3_key}`  \n"
                    f"Make sure the cron has run at least once since the last deploy "
                    f"and that `scanner_output/lists` is in the `--dirs` upload argument."
                )
