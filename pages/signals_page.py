"""
Signal Performance Page — track how past signals performed.
Filters: date range, trade type, quality tier → Performance by Scan summary table.
Drill down into individual signals per scan.
"""

import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_ROOT = Path(__file__).parent.parent
SIGNALS_DIR = PROJECT_ROOT / 'scanner_output' / 'signals'
REPORTS_DIR = PROJECT_ROOT / 'scanner_output' / 'signal_reports'

QUALITY_FILTERS = {
    'Gold Only': ['GOLD'],
    'Premium and Above': ['GOLD', 'PREMIUM'],
    'High and Above': ['GOLD', 'PREMIUM', 'HIGH'],
    'Standard and Above': ['GOLD', 'PREMIUM', 'HIGH', 'STANDARD'],
}
TRADE_TYPES = ['All', 'swing', 'daytrade', 'longterm']


def _color_gain(val):
    """Style helper: green for positive, red for negative."""
    if pd.isna(val):
        return ''
    try:
        v = float(val)
    except (ValueError, TypeError):
        return ''
    if v > 0:
        return 'color: #4caf50'
    elif v < 0:
        return 'color: #ef5350'
    return 'color: #888'


def _get_available_dates():
    """Scan signal files and return sorted list of unique dates."""
    dates = set()
    for f in SIGNALS_DIR.glob('signals_*.csv'):
        parts = f.stem.split('_')
        for p in parts:
            if len(p) == 8 and p.isdigit():
                try:
                    dates.add(datetime.strptime(p, '%Y%m%d').date())
                except ValueError:
                    pass
    return sorted(dates, reverse=True)


def _find_signal_files(selected_date, trade_type):
    """Find signal CSV files matching the selected date and trade type."""
    date_str = selected_date.strftime('%Y%m%d')
    if trade_type == 'All':
        pattern = f'signals_*_{date_str}_*.csv'
    else:
        pattern = f'signals_{trade_type}_{date_str}_*.csv'
    return sorted(SIGNALS_DIR.glob(pattern), reverse=True)


def _find_signal_files_range(start_date, end_date, trade_type):
    """Find signal CSV files matching a date range and trade type."""
    all_files = []
    for f in SIGNALS_DIR.glob('signals_*.csv'):
        parts = f.stem.split('_')
        # Extract date from filename
        file_date = None
        for p in parts:
            if len(p) == 8 and p.isdigit():
                try:
                    file_date = datetime.strptime(p, '%Y%m%d').date()
                except ValueError:
                    pass
        if file_date is None:
            continue
        if file_date < start_date or file_date > end_date:
            continue
        # Filter by trade type
        if trade_type != 'All':
            mode = parts[1] if len(parts) >= 2 else ''
            if mode != trade_type:
                continue
        all_files.append(f)
    return sorted(all_files, reverse=True)


def _fetch_prices_at_date(symbols, target_date):
    """Fetch closing prices for symbols at a specific date using yfinance."""
    if not symbols:
        return {}
    try:
        import yfinance as yf
        # Fetch a few days around target to handle weekends/holidays
        start = target_date - timedelta(days=5)
        end = target_date + timedelta(days=1)
        yf_syms = [s.replace(' ', '-') for s in symbols]
        data = yf.download(yf_syms, start=start.strftime('%Y-%m-%d'),
                           end=end.strftime('%Y-%m-%d'), progress=False, auto_adjust=True)
        if data is None or data.empty:
            return {}
        close_df = data['Close']
        # If single ticker, close_df may be a Series
        if isinstance(close_df, pd.Series):
            close_df = close_df.to_frame(columns=[yf_syms[0]])
        prices = {}
        for sym, yf_sym in zip(symbols, yf_syms):
            try:
                col = close_df[yf_sym].dropna()
                if len(col) > 0:
                    # Get the last available price on or before target_date
                    valid = col[col.index.date <= target_date]
                    if len(valid) > 0:
                        prices[sym] = round(float(valid.iloc[-1]), 2)
            except (KeyError, IndexError, TypeError):
                pass
        return prices
    except Exception:
        return {}


def _compute_realized_gain(df):
    """Recompute Gain% using Target/Stop price when hit, else Current.

    Priority: HitTarget → use Target price, else HitStop → use Stop price.
    Updates df in-place and returns it.
    """
    if 'Price' not in df.columns or 'Gain%' not in df.columns:
        return df
    for idx, row in df.iterrows():
        entry = row.get('Price')
        if pd.isna(entry) or entry == 0:
            continue
        entry = float(entry)
        hit_target = row.get('HitTarget', False)
        hit_stop = row.get('HitStop', False)
        if hit_target and pd.notna(row.get('Target')) and float(row['Target']) > 0:
            realized = float(row['Target'])
            df.at[idx, 'Gain%'] = round(((realized - entry) / entry) * 100, 2)
        elif hit_stop and pd.notna(row.get('Stop')) and float(row['Stop']) > 0:
            realized = float(row['Stop'])
            df.at[idx, 'Gain%'] = round(((realized - entry) / entry) * 100, 2)
    return df


def _get_report_path(signal_file):
    """Get the corresponding report path for a signal file."""
    return REPORTS_DIR / signal_file.name.replace('signals_', 'report_')


def _build_quality_filtered_summary(report_path, signal_file, allowed_tiers):
    """Load a report CSV, filter by quality tiers, compute summary stats."""
    try:
        df = pd.read_csv(report_path)
    except Exception:
        return None

    if df.empty or 'Quality' not in df.columns:
        return None

    # Filter by quality
    filtered = df[df['Quality'].isin(allowed_tiers)].copy()
    filtered = _compute_realized_gain(filtered)
    valid = filtered.dropna(subset=['Gain%']) if 'Gain%' in filtered.columns else filtered

    n = len(valid)

    # Parse mode and time from filename
    parts = signal_file.stem.split('_')  # signals_swing_20260217_093039
    mode = parts[1] if len(parts) >= 2 else ''
    date_str = ''
    time_str = ''
    for p in parts:
        if len(p) == 8 and p.isdigit():
            date_str = p
        elif len(p) == 6 and p.isdigit() and date_str:
            time_str = p

    date_display = ''
    time_display = ''
    if date_str:
        try:
            date_display = datetime.strptime(date_str, '%Y%m%d').strftime('%Y-%m-%d')
        except ValueError:
            pass
    if time_str:
        time_display = f"{time_str[:2]}:{time_str[2:4]}"

    if n == 0:
        return {
            'Date': date_display, 'Time': time_display, 'Mode': mode.title(),
            'Signals': 0, 'Avg Gain': 0, 'Total Gain': 0, 'Win Rate': 0,
            'Winners': 0, 'Losers': 0, 'Best': 0, 'Worst': 0,
            'Hit Target': 0, 'Hit Stop': 0, '_file': signal_file.name,
        }

    winners = int((valid['Gain%'] > 0).sum())
    losers = int((valid['Gain%'] < 0).sum())

    return {
        'Date': date_display,
        'Time': time_display,
        'Mode': mode.title(),
        'Signals': n,
        'Avg Gain': round(valid['Gain%'].mean(), 2),
        'Total Gain': round(valid['Gain%'].sum(), 2),
        'Win Rate': round((winners / n) * 100, 1) if n > 0 else 0,
        'Winners': winners,
        'Losers': losers,
        'Best': round(valid['Gain%'].max(), 2),
        'Worst': round(valid['Gain%'].min(), 2),
        'Hit Target': int(valid['HitTarget'].sum()) if 'HitTarget' in valid.columns else 0,
        'Hit Stop': int(valid['HitStop'].sum()) if 'HitStop' in valid.columns else 0,
        '_file': signal_file.name,
    }


def render_signals_page():
    st.subheader("Signal Performance Tracker")

    # ── Filter Row ──
    col_date, col_type, col_quality, col_refresh = st.columns([2, 2, 2, 2])

    available_dates = _get_available_dates()
    if not available_dates:
        st.info("No signal files found in `scanner_output/signals/`.")
        return

    with col_date:
        date_range = st.date_input(
            "Signal Date Range",
            value=(available_dates[0], available_dates[0]),
            min_value=available_dates[-1],
            max_value=available_dates[0],
            key="sig_date_range",
        )
        # Handle single date or range tuple
        if isinstance(date_range, (list, tuple)):
            if len(date_range) == 2:
                start_date, end_date = date_range
            else:
                start_date = end_date = date_range[0]
        else:
            start_date = end_date = date_range

    with col_type:
        trade_type = st.selectbox("Trade Type", TRADE_TYPES, key="sig_trade_type")

    with col_quality:
        quality_filter = st.selectbox(
            "Quality Filter", list(QUALITY_FILTERS.keys()),
            index=2, key="sig_quality_filter",
        )

    allowed_tiers = QUALITY_FILTERS[quality_filter]

    # Find matching signal files for date range + type
    if start_date == end_date:
        matching_files = _find_signal_files(start_date, trade_type)
    else:
        matching_files = _find_signal_files_range(start_date, end_date, trade_type)

    # Check which have reports and which don't
    files_without_reports = [f for f in matching_files if not _get_report_path(f).exists()]

    with col_refresh:
        st.markdown("<br>", unsafe_allow_html=True)
        refresh_label = "Refresh Reports"
        if files_without_reports:
            refresh_label = f"Refresh ({len(files_without_reports)} new)"
        refresh_clicked = st.button(refresh_label, type="primary",
                                    use_container_width=True,
                                    disabled=len(matching_files) == 0)

    if not matching_files:
        date_label = str(start_date) if start_date == end_date else f"{start_date} to {end_date}"
        st.warning(f"No signals found for **{date_label}** / **{trade_type}**.")
        # Show available dates hint
        all_files = sorted(SIGNALS_DIR.glob('signals_*.csv'), reverse=True)
        if all_files:
            hint_dates = set()
            for f in all_files[:30]:
                parts = f.stem.split('_')
                for p in parts:
                    if len(p) == 8 and p.isdigit():
                        try:
                            hint_dates.add(datetime.strptime(p, '%Y%m%d').strftime('%Y-%m-%d'))
                        except ValueError:
                            pass
            if hint_dates:
                st.caption(f"Available dates: {', '.join(sorted(hint_dates, reverse=True)[:10])}")
        return

    # ── Refresh: process files without reports ──
    if refresh_clicked and files_without_reports:
        from signal_tracker import track_signal_file
        from yfinance_adapter import YFinanceAdapter
        yf = YFinanceAdapter()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        progress = st.progress(0, text="Processing signal files...")
        for i, sig_file in enumerate(files_without_reports):
            progress.progress((i + 1) / len(files_without_reports),
                              text=f"Processing {sig_file.name}...")
            df = track_signal_file(sig_file, yf)
            if not df.empty:
                report_path = _get_report_path(sig_file)
                df.to_csv(report_path, index=False)
        progress.empty()
        st.toast(f"Processed {len(files_without_reports)} signal files")
        st.rerun()

    # ── Build Performance by Scan table ──
    summary_rows = []
    files_with_reports = []
    for sig_file in matching_files:
        report_path = _get_report_path(sig_file)
        if report_path.exists():
            row = _build_quality_filtered_summary(report_path, sig_file, allowed_tiers)
            if row:
                summary_rows.append(row)
                files_with_reports.append(sig_file)

    if not summary_rows:
        if files_without_reports:
            st.info(f"Found {len(matching_files)} signal file(s) but no reports yet. "
                    "Click **Refresh Reports** to fetch current prices and generate reports.")
        else:
            st.info("No signals match the selected quality filter.")
        return

    summary_df = pd.DataFrame(summary_rows)

    # ── Overview Metrics (aggregated across all matching scans) ──
    total_signals = summary_df['Signals'].sum()
    total_winners = summary_df['Winners'].sum()
    total_losers = summary_df['Losers'].sum()
    overall_wr = (total_winners / total_signals * 100) if total_signals > 0 else 0
    # Weighted average gain (by number of signals per scan)
    if total_signals > 0:
        overall_avg = (summary_df['Avg Gain'] * summary_df['Signals']).sum() / total_signals
    else:
        overall_avg = 0
    total_hit_target = summary_df['Hit Target'].sum()
    total_hit_stop = summary_df['Hit Stop'].sum()

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Total Signals", f"{int(total_signals):,}")
    m2.metric("Avg Gain", f"{overall_avg:+.2f}%")
    m3.metric("Win Rate", f"{overall_wr:.0f}%")
    m4.metric("Winners", f"{int(total_winners):,}")
    m5.metric("Hit Target", f"{int(total_hit_target):,}")
    m6.metric("Hit Stop", f"{int(total_hit_stop):,}")

    st.divider()

    # ── Performance by Scan Table (clickable rows) ──
    st.subheader("Performance by Scan")
    st.caption("Click a row to see individual trades")

    display_cols = ['Date', 'Time', 'Mode', 'Signals', 'Avg Gain', 'Total Gain',
                    'Win Rate', 'Winners', 'Losers', 'Best', 'Worst',
                    'Hit Target', 'Hit Stop']
    display_cols = [c for c in display_cols if c in summary_df.columns]

    # ── Build TOTAL row for Performance by Scan ──
    scan_total = {}
    for c in display_cols:
        scan_total[c] = None
    scan_total['Date'] = 'TOTAL'
    scan_total['Time'] = ''
    scan_total['Mode'] = ''
    scan_total['Signals'] = int(summary_df['Signals'].sum())
    if 'Avg Gain' in summary_df.columns:
        # Weighted average gain
        total_sigs = summary_df['Signals'].sum()
        if total_sigs > 0:
            scan_total['Avg Gain'] = round(
                (summary_df['Avg Gain'] * summary_df['Signals']).sum() / total_sigs, 2)
        else:
            scan_total['Avg Gain'] = 0
    if 'Total Gain' in summary_df.columns:
        scan_total['Total Gain'] = round(summary_df['Total Gain'].sum(), 2)
    if 'Win Rate' in summary_df.columns:
        scan_total['Win Rate'] = round(summary_df['Win Rate'].mean(), 1)
    if 'Winners' in summary_df.columns:
        scan_total['Winners'] = int(summary_df['Winners'].sum())
    if 'Losers' in summary_df.columns:
        scan_total['Losers'] = int(summary_df['Losers'].sum())
    if 'Best' in summary_df.columns:
        scan_total['Best'] = round(summary_df['Best'].max(), 2)
    if 'Worst' in summary_df.columns:
        scan_total['Worst'] = round(summary_df['Worst'].min(), 2)
    if 'Hit Target' in summary_df.columns:
        scan_total['Hit Target'] = int(summary_df['Hit Target'].sum())
    if 'Hit Stop' in summary_df.columns:
        scan_total['Hit Stop'] = int(summary_df['Hit Stop'].sum())
    scan_total['_file'] = ''

    scan_total_df = pd.DataFrame([scan_total])
    scan_display_df = pd.concat([summary_df[display_cols], scan_total_df[display_cols]], ignore_index=True)

    gain_style_cols = [c for c in ['Avg Gain', 'Total Gain', 'Best', 'Worst']
                       if c in display_cols]

    def _color_scan_row(row):
        """Bold styling for TOTAL row in scan table."""
        if row.get('Date') == 'TOTAL':
            return ['background-color: #1a1a2e; color: #ffffff; font-weight: bold'] * len(row)
        return [''] * len(row)

    styled = (
        scan_display_df
        .style
        .applymap(_color_gain, subset=gain_style_cols)
        .apply(_color_scan_row, axis=1)
    )

    event = st.dataframe(
        styled,
        use_container_width=True,
        hide_index=True,
        height=min(400, 40 + len(scan_display_df) * 35),
        on_select="rerun",
        selection_mode="single-row",
        key="perf_scan_table",
    )

    # ── Determine selected scan row ──
    selected_rows = event.selection.rows if event and event.selection else []
    selected_scan_idx = selected_rows[0] if selected_rows else None

    # Ignore click on the TOTAL row
    if selected_scan_idx is not None and selected_scan_idx >= len(summary_df):
        selected_scan_idx = None

    if selected_scan_idx is None:
        st.info("Select a scan row above to view individual trade details.")
        return

    st.divider()

    # ── Signal Details (drill-down for the selected row) ──
    sel_row = summary_df.iloc[selected_scan_idx]
    scan_label = f"{sel_row['Mode']} | {sel_row['Date']} {sel_row['Time']} | {int(sel_row['Signals'])} signals"
    st.subheader(f"Trade Details — {scan_label}")

    selected_file_name = sel_row['_file']
    report_path = REPORTS_DIR / selected_file_name.replace('signals_', 'report_')

    try:
        detail_df = pd.read_csv(report_path)
    except Exception:
        st.error(f"Could not load report: {report_path.name}")
        return

    if detail_df.empty:
        st.warning("Empty report file.")
        return

    # Apply quality filter and recompute gain from stop/target
    if 'Quality' in detail_df.columns:
        detail_df = detail_df[detail_df['Quality'].isin(allowed_tiers)].copy()

    if detail_df.empty:
        st.info("No signals match the selected quality filter in this scan.")
        return

    detail_df = _compute_realized_gain(detail_df)

    # Determine signal date for Price@Date min constraint
    signal_date_str = sel_row.get('Date', '')
    try:
        signal_date = datetime.strptime(signal_date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        signal_date = available_dates[-1] if available_dates else None

    # Outcome filter + Gain filter + Price@Date picker
    filter_col, gain_filter_col, price_date_col = st.columns([3, 1, 1])
    with filter_col:
        outcome_filter = st.radio("Outcome", ["All", "Winners", "Losers", "Hit Target", "Hit Stop"],
                                  horizontal=True, key="sig_outcome")
    with gain_filter_col:
        min_abs_gain = st.number_input(
            "Min |Gain%|",
            min_value=0.0, max_value=100.0, value=0.0, step=0.5,
            help="Filter out rows where abs(Gain%) < this value",
            key="sig_min_abs_gain",
        )
    with price_date_col:
        price_at_date = st.date_input(
            "Price @ Date",
            value=None,
            min_value=signal_date,
            max_value=datetime.now().date(),
            key="sig_price_at_date",
        )

    filtered = detail_df.copy()
    if outcome_filter == 'Winners' and 'Gain%' in filtered.columns:
        filtered = filtered[filtered['Gain%'] > 0]
    elif outcome_filter == 'Losers' and 'Gain%' in filtered.columns:
        filtered = filtered[filtered['Gain%'] < 0]
    elif outcome_filter == 'Hit Target' and 'HitTarget' in filtered.columns:
        mask = filtered['HitTarget'] == True
        if 'HitStop' in filtered.columns:
            mask = mask & (filtered['HitStop'] != True)
        filtered = filtered[mask]
    elif outcome_filter == 'Hit Stop' and 'HitStop' in filtered.columns:
        mask = filtered['HitStop'] == True
        if 'HitTarget' in filtered.columns:
            mask = mask & (filtered['HitTarget'] != True)
        filtered = filtered[mask]

    # Apply abs gain filter
    if min_abs_gain > 0 and 'Gain%' in filtered.columns:
        filtered = filtered[filtered['Gain%'].abs() >= min_abs_gain]

    if filtered.empty:
        st.info("No signals match the selected filters.")
        return

    # Sort by gain
    if 'Gain%' in filtered.columns:
        filtered = filtered.sort_values('Gain%', ascending=False)

    # Fetch Price@Date if selected
    if price_at_date is not None:
        symbols = filtered['Symbol'].tolist()
        cache_key = f"price_at_{price_at_date}_{','.join(sorted(symbols))}"
        if cache_key not in st.session_state:
            with st.spinner(f"Fetching prices for {price_at_date}..."):
                st.session_state[cache_key] = _fetch_prices_at_date(symbols, price_at_date)
        prices_at = st.session_state[cache_key]
        col_label = f"Price@{price_at_date.strftime('%m/%d')}"
        filtered[col_label] = filtered['Symbol'].map(prices_at)
        # Calculate gain from entry to that date
        gain_label = f"Gain@{price_at_date.strftime('%m/%d')}%"
        if 'Price' in filtered.columns:
            filtered[gain_label] = filtered.apply(
                lambda r: round(((r[col_label] - r['Price']) / r['Price']) * 100, 2)
                if pd.notna(r.get(col_label)) and pd.notna(r.get('Price')) and r['Price'] > 0
                else None, axis=1)
    else:
        col_label = None
        gain_label = None

    # Signal table with full row coloring (green = winner, red = loser)
    detail_cols = ['Symbol', 'Quality', 'Price', 'Current', 'Gain%',
                   'Stop', 'Target', 'R:R', 'HitTarget', 'HitStop',
                   'Patterns', 'Sector', 'DaysSince']
    # Insert Price@Date columns after Current
    if col_label and col_label in filtered.columns:
        cur_idx = detail_cols.index('Current') + 1 if 'Current' in detail_cols else 4
        detail_cols.insert(cur_idx, col_label)
        if gain_label and gain_label in filtered.columns:
            detail_cols.insert(cur_idx + 1, gain_label)
    detail_cols = [c for c in detail_cols if c in filtered.columns]

    # ── Build TOTAL row ──
    total_row = {}
    for c in detail_cols:
        total_row[c] = None
    total_row['Symbol'] = 'TOTAL'
    if 'Gain%' in filtered.columns:
        total_row['Gain%'] = round(filtered['Gain%'].dropna().sum(), 2)
    if 'HitTarget' in filtered.columns:
        total_row['HitTarget'] = int(filtered['HitTarget'].sum())
    if 'HitStop' in filtered.columns:
        total_row['HitStop'] = int(filtered['HitStop'].sum())
    if col_label and col_label in detail_cols:
        total_row[col_label] = None
    if gain_label and gain_label in detail_cols:
        valid_gains = filtered[gain_label].dropna()
        total_row[gain_label] = round(valid_gains.sum(), 2) if len(valid_gains) > 0 else None

    total_df = pd.DataFrame([total_row])[detail_cols]

    def _color_trade_row(row):
        """Color entire row: green for winners, red for losers."""
        gain = row.get('Gain%', 0) if pd.notna(row.get('Gain%', 0)) else 0
        if gain > 0:
            return ['background-color: #0d2618; color: #4caf50'] * len(row)
        elif gain < 0:
            return ['background-color: #2a1111; color: #ef5350'] * len(row)
        return [''] * len(row)

    def _color_total_row(row):
        return ['background-color: #1a1a2e; color: #ffffff; font-weight: bold'] * len(row)

    # Build format dict
    fmt = {
        'Gain%': '{:+.2f}%',
        'Price': '${:.2f}',
        'Current': '${:.2f}',
        'Stop': '${:.2f}',
        'Target': '${:.2f}',
        'R:R': '{:.1f}',
    }
    if col_label:
        fmt[col_label] = '${:.2f}'
    if gain_label:
        fmt[gain_label] = '{:+.2f}%'
    fmt_subset = [c for c in fmt if c in detail_cols]

    styled_detail = (
        filtered[detail_cols]
        .style
        .apply(_color_trade_row, axis=1)
        .format(fmt, na_rep='N/A', subset=fmt_subset)
    )
    detail_height = min(600, 38 + len(filtered) * 35)
    st.dataframe(styled_detail, use_container_width=True, hide_index=True, height=detail_height)

    # TOTAL row displayed separately so sorting doesn't move it
    styled_total = (
        total_df
        .style
        .apply(_color_total_row, axis=1)
        .format(fmt, na_rep='', subset=fmt_subset)
    )
    st.dataframe(styled_total, use_container_width=True, hide_index=True, height=38 + 35)

    # Quick stats for this scan
    if 'Gain%' in filtered.columns and len(filtered) > 0:
        n_win = int((filtered['Gain%'] > 0).sum())
        n_lose = int((filtered['Gain%'] < 0).sum())
        avg_g = filtered['Gain%'].mean()
        best_sym = filtered.iloc[0]['Symbol'] if len(filtered) > 0 else ''
        best_g = filtered['Gain%'].max()
        worst_sym = filtered.iloc[-1]['Symbol'] if len(filtered) > 0 else ''
        worst_g = filtered['Gain%'].min()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Winners / Losers", f"{n_win} / {n_lose}")
        c2.metric("Avg Gain", f"{avg_g:+.2f}%")
        c3.metric("Best", f"{best_sym} {best_g:+.2f}%")
        c4.metric("Worst", f"{worst_sym} {worst_g:+.2f}%")

    # Per-quality tier breakdown
    if 'Quality' in filtered.columns and 'Gain%' in filtered.columns and len(filtered) > 1:
        st.subheader("Performance by Quality Tier")
        q_group = filtered.groupby('Quality').agg(
            Signals=('Gain%', 'count'),
            AvgGain=('Gain%', 'mean'),
            MedianGain=('Gain%', 'median'),
            WinRate=('Gain%', lambda x: (x > 0).mean() * 100),
            Best=('Gain%', 'max'),
            Worst=('Gain%', 'min'),
        ).round(2)
        tier_order = ['GOLD', 'PREMIUM', 'HIGH', 'STANDARD']
        q_group = q_group.reindex([t for t in tier_order if t in q_group.index])
        st.dataframe(q_group, use_container_width=True)

    # Chart buttons
    if len(filtered) > 0:
        st.markdown("---")
        n_buttons = min(6, len(filtered))
        chart_cols = st.columns(n_buttons)
        for i, (_, row) in enumerate(filtered.head(n_buttons).iterrows()):
            sym = row.get('Symbol', '?')
            gain = row.get('Gain%', 0)
            gain_str = f"{gain:+.1f}%" if pd.notna(gain) else "N/A"
            with chart_cols[i]:
                if st.button(f"{sym}\n{gain_str}", key=f"sig_chart_{i}",
                             use_container_width=True):
                    st.session_state['chart_symbol'] = sym
                    st.session_state['_next_page'] = 'Chart'
                    st.rerun()

    # CSV download
    st.markdown("---")
    csv_data = filtered.to_csv(index=False)
    st.download_button("Download Report CSV", csv_data,
                       f"report_{selected_file_name.replace('signals_', '')}", "text/csv")
