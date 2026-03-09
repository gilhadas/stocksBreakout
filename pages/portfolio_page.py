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


def render_portfolio_page():
    # Three tabs: Auto Portfolio is first — it is ALWAYS the active tab on load
    tab_auto, tab_manual, tab_lists = st.tabs([
        "📊 Auto Portfolio (V9-C)", "📋 Manual Portfolio", "📂 Watch Lists"
    ])

    with tab_auto:
        _render_auto_portfolio()

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
        })

        df_pos = pd.DataFrame(rows)
        pnl_cols = [c for c in ['Change%', 'P&L $'] if c in df_pos.columns]
        styled = df_pos.style.applymap(_color_pnl, subset=pnl_cols)
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
                                          key="exit_price")

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
                                        value=float(edit_pos['stop']),
                                        min_value=0.01, key="edit_stop")
            new_target = st.number_input("New Target",
                                          value=float(edit_pos['target']),
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
                'Symbol': t['symbol'],
                'Entry': f"${t['entry_price']:.2f}",
                'Exit': f"${t['exit_price']:.2f}",
                'P&L': f"${t['pnl']:+,.0f}",
                'P&L%': f"{t['pnl_pct']:+.1f}%",
                'Hold': f"{t['hold_days']}d",
                'Quality': t['quality'],
                'Date': t['exit_date'],
            })
        st.dataframe(pd.DataFrame(hist_rows), use_container_width=True, hide_index=True)


def _render_auto_portfolio():
    """Auto Virtual Portfolio section — tracks all V9-C signals automatically."""
    import auto_portfolio as ap

    st.subheader("Auto Virtual Portfolio (V9-C Signals)")
    st.caption(
        "Automatically tracks every GOLD/PREMIUM signal with Minervini≥7. "
        "Position size is configurable below. Stops auto-close positions. "
        "No duplicates — each ticker enters once while open."
    )

    data    = ap.load()
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
            options=["10% ($10K)", "5% ($5K)"],
            key="ap_pos_pct",
            help="Percentage of $100K capital allocated per trade",
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
    col_scan, col_recalc, col_refresh, col_trail, col_missed, col_reset = st.columns(
        [1.2, 1.5, 1.3, 1.5, 1.4, 0.8]
    )

    with col_scan:
        if st.button("📥 Scan Signals", key="ap_scan",
                     help="Add new V9-C signals not yet in portfolio (from date if filter set)"):
            with st.spinner("Scanning signal files..."):
                result = ap.scan_and_add(min_date=scan_min_date, position_pct=pos_pct)
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
                result = ap.recalculate(position_pct=pos_pct, min_date=scan_min_date)
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
                result = ap.refresh_prices()
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
                result = ap.simulate_trailing_stops()
            data    = result['data']
            summary = ap.get_summary(data)
            if result['closed']:
                st.toast(f"Trailing stop triggered — closed: {', '.join(result['closed'])}")
            else:
                st.toast(f"No trailing stops hit ({result['checked']} positions checked).")
            st.rerun()

    with col_missed:
        if st.button("🔍 Find Missed Trades", key="ap_missed",
                     help="Re-scan all signal files for V9-C signals never taken (missed opportunities)"):
            with st.spinner("Scanning all signal files for missed trades..."):
                result = ap.rebuild_skipped_cash()
            data    = result['data']
            summary = ap.get_summary(data)
            st.toast(f"Found {result['found']} missed trade(s).")
            st.rerun()

    with col_reset:
        if st.button("🗑️ Reset", key="ap_reset",
                     help="Clear all auto-portfolio positions and history"):
            ap.reset()
            st.toast("Auto portfolio reset.")
            st.rerun()

    st.divider()

    # ── Open Positions ──
    positions = data.get('positions', [])
    if positions:
        st.markdown(f"**Open Positions ({len(positions)})**")
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
        })

        df_open = pd.DataFrame(rows)
        styled_open = df_open.style.applymap(
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
                min_value=0.01, key="ap_close_price"
            )
            est = round((exit_px - pos_data['entry_price']) * pos_data['shares'], 2)
            st.caption(f"Entry: ${pos_data['entry_price']:.2f} | Shares: {pos_data['shares']} | Est P&L: ${est:+,.0f}")
            if st.button("Close Position", key="ap_close_btn"):
                ap.close_position(sym_to_close, exit_px, reason='manual')
                st.toast(f"Closed {sym_to_close}: ${est:+,.0f}")
                st.rerun()
    else:
        st.info("No open positions. Click **Scan Signals** to add V9-C signals.")

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
        styled_hist = df_hist.style.applymap(
            _color_pnl, subset=[c for c in ['P&L $', 'P&L%'] if c in df_hist.columns]
        )
        st.dataframe(styled_hist, use_container_width=True, hide_index=True)

    # ── Missed Trades (skipped due to insufficient cash) ─────────────────────
    skipped = data.get('skipped_cash', [])
    if skipped:
        st.markdown(f"**Missed Trades — Insufficient Cash ({len(skipped)})**")
        st.caption(
            "Signals that were not taken because available cash was below the required position size. "
            "P&L shown is hypothetical (entry price on signal date → today's price)."
        )
        missed_rows = []
        total_hyp_pnl = 0.0
        for s in reversed(skipped):
            ep  = s.get('entry_price', 0)
            cur = s.get('current_price', ep)
            shares = s.get('shares', 0)
            hyp_pnl     = round((cur - ep) * shares, 0) if ep else 0
            hyp_pnl_pct = round((cur - ep) / ep * 100, 1) if ep else 0
            total_hyp_pnl += hyp_pnl
            dist_stop   = round((cur - s['stop'])   / ep * 100, 1) if ep else 0
            dist_target = round((s['target'] - cur) / ep * 100, 1) if ep else 0
            missed_rows.append({
                'Symbol':    s['symbol'],
                'Date':      s['date_added'],
                'Type':      s.get('mode', ''),
                'Quality':   s.get('quality', ''),
                'Minervini': s.get('minervini_score', ''),
                'Entry $':   f"${ep:.2f}",
                'Current $': f"${cur:.2f}",
                'Chg%':      hyp_pnl_pct,
                'Hyp. P&L':  hyp_pnl,
                'Stop $':    f"${s['stop']:.2f}",
                'To Stop%':  dist_stop,
                'Target $':  f"${s['target']:.2f}",
                'To Tgt%':   dist_target,
                'Cost $':    f"${s.get('cost', 0):,.0f}",
            })
        total_hyp_wins = sum(1 for s in skipped
                             if s.get('current_price', 0) > s.get('entry_price', 0))
        st.caption(
            f"Hypothetical total P&L if all had been taken: "
            f"**${total_hyp_pnl:+,.0f}** | "
            f"Would-be winners: {total_hyp_wins}/{len(skipped)}"
        )
        df_missed = pd.DataFrame(missed_rows)
        styled_missed = df_missed.style.applymap(
            _color_pnl,
            subset=[c for c in ['Chg%', 'Hyp. P&L'] if c in df_missed.columns]
        )
        st.dataframe(styled_missed, use_container_width=True, hide_index=True)


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
                    styled = styled.applymap(_color_quality_cell, subset=['quality'])

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
