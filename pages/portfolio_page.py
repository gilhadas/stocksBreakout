"""
Portfolio Page — Streamlit UI for the core Portfolio module.
Buy/sell positions, update stop/target, view P&L and performance.
"""

import streamlit as st
import pandas as pd
from datetime import datetime

from utils import load_data, list_files, PROJECT_ROOT


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
            signals_dir = str(PROJECT_ROOT / 'scanner_output' / 'signals')
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
