"""
Signal Performance Page — track how past signals performed.
Filters: date, trade type, quality tier → Performance by Scan summary table.
Drill down into individual signals per scan.
"""

import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime

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
    filtered = df[df['Quality'].isin(allowed_tiers)]
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
            'Signals': 0, 'Avg Gain': 0, 'Median Gain': 0, 'Win Rate': 0,
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
        'Median Gain': round(valid['Gain%'].median(), 2),
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
        selected_date = st.date_input(
            "Signal Date",
            value=available_dates[0],
            min_value=available_dates[-1],
            max_value=available_dates[0],
            key="sig_date",
        )

    with col_type:
        trade_type = st.selectbox("Trade Type", TRADE_TYPES, key="sig_trade_type")

    with col_quality:
        quality_filter = st.selectbox(
            "Quality Filter", list(QUALITY_FILTERS.keys()),
            index=2, key="sig_quality_filter",
        )

    allowed_tiers = QUALITY_FILTERS[quality_filter]

    # Find matching signal files for this date + type
    matching_files = _find_signal_files(selected_date, trade_type)

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
        st.warning(f"No signals found for **{selected_date}** / **{trade_type}**.")
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

    # ── Performance by Scan Table ──
    st.subheader("Performance by Scan")

    display_cols = ['Date', 'Time', 'Mode', 'Signals', 'Avg Gain', 'Median Gain',
                    'Win Rate', 'Winners', 'Losers', 'Best', 'Worst',
                    'Hit Target', 'Hit Stop']
    display_cols = [c for c in display_cols if c in summary_df.columns]

    gain_style_cols = [c for c in ['Avg Gain', 'Median Gain', 'Best', 'Worst']
                       if c in display_cols]

    styled = summary_df[display_cols].style.applymap(_color_gain, subset=gain_style_cols)
    st.dataframe(styled, use_container_width=True, hide_index=True, height=300)

    st.divider()

    # ── Signal Details (drill-down) ──
    st.subheader("Signal Details")

    # Build selectbox labels from summary
    scan_labels = []
    for _, row in summary_df.iterrows():
        label = f"{row['Mode']} | {row['Date']} {row['Time']} | {int(row['Signals'])} signals, {row['Avg Gain']:+.2f}%"
        scan_labels.append(label)

    selected_scan_idx = st.selectbox(
        "Select Scan", range(len(scan_labels)),
        format_func=lambda i: scan_labels[i],
        key="sig_detail_scan",
    )

    # Load the selected report
    selected_file_name = summary_df.iloc[selected_scan_idx]['_file']
    report_path = REPORTS_DIR / selected_file_name.replace('signals_', 'report_')

    try:
        detail_df = pd.read_csv(report_path)
    except Exception:
        st.error(f"Could not load report: {report_path.name}")
        return

    if detail_df.empty:
        st.warning("Empty report file.")
        return

    # Apply quality filter
    if 'Quality' in detail_df.columns:
        detail_df = detail_df[detail_df['Quality'].isin(allowed_tiers)].copy()

    if detail_df.empty:
        st.info("No signals match the selected quality filter in this scan.")
        return

    # Outcome filter
    outcome_filter = st.radio("Outcome", ["All", "Winners", "Losers", "Hit Target", "Hit Stop"],
                              horizontal=True, key="sig_outcome")

    filtered = detail_df.copy()
    if outcome_filter == 'Winners' and 'Gain%' in filtered.columns:
        filtered = filtered[filtered['Gain%'] > 0]
    elif outcome_filter == 'Losers' and 'Gain%' in filtered.columns:
        filtered = filtered[filtered['Gain%'] < 0]
    elif outcome_filter == 'Hit Target' and 'HitTarget' in filtered.columns:
        filtered = filtered[filtered['HitTarget'] == True]
    elif outcome_filter == 'Hit Stop' and 'HitStop' in filtered.columns:
        filtered = filtered[filtered['HitStop'] == True]

    # Signal table
    detail_cols = ['Symbol', 'Quality', 'Price', 'Current', 'Gain%',
                   'Stop', 'Target', 'R:R', 'HitTarget', 'HitStop',
                   'Patterns', 'Sector', 'DaysSince']
    detail_cols = [c for c in detail_cols if c in filtered.columns]

    if filtered.empty:
        st.info("No signals match the selected outcome filter.")
        return

    if 'Gain%' in filtered.columns:
        filtered = filtered.sort_values('Gain%', ascending=False)

    gain_cols = [c for c in ['Gain%'] if c in detail_cols]
    styled_detail = filtered[detail_cols].style.applymap(_color_gain, subset=gain_cols)
    st.dataframe(styled_detail, use_container_width=True, hide_index=True, height=500)

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
