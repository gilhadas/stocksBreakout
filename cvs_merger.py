import argparse
import os
import re
import sys

import pandas as pd


def _extract_symbols(content: str) -> list[str]:
    valid_segments = []
    for line in content.splitlines():
        clean_line = line.strip()
        # 1. Skip blank lines. Comment tokens (leading #) are filtered per-segment
        # below, not per-line — a single-line, comma-delimited file (no newlines)
        # whose first segment happens to be a "###SECTOR" label must not have its
        # entire content discarded as "one big comment line".
        if not clean_line:
            continue

        # Split by comma, tab, or semicolon
        segments = re.split(r'[,\t;]+', clean_line)
        valid_segments.extend(segments)

    clean_tickers = []
    for t in valid_segments:
        t = t.strip().upper()

        # 2. Handle exchange prefix (ignore everything before and including ':')
        if ":" in t:
            t = t.split(":")[-1].strip()

        # 3. Ignore purely numeric entries
        if t.isdigit():
            continue

        # 4. Final filter: ignore empty, ignore comments, ignore common headers
        if t and not t.startswith('#') and t not in {'SYMBOL', 'TICKER', 'STOCK'}:
            clean_tickers.append(t)

    return clean_tickers


def merge_watchlists(file_contents: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Merge {filename: raw_text} into (master_df, unique_df, duplicate_summary_df)."""
    all_data = []
    for filename, content in file_contents.items():
        clean_tickers = _extract_symbols(content)
        if clean_tickers:
            df = pd.DataFrame(clean_tickers, columns=['Stock Symbol'])
            df['Source File'] = filename
            all_data.append(df)

    if not all_data:
        empty = pd.DataFrame(columns=['Stock Symbol'])
        return empty, empty, pd.DataFrame(columns=['Stock Symbol', 'Found in these Files'])

    master_df = pd.concat(all_data, ignore_index=True)

    counts = master_df.groupby('Stock Symbol').size().reset_index(name='Total Occurrences')
    duplicate_symbols = counts[counts['Total Occurrences'] > 1]['Stock Symbol']

    unique_df = master_df.drop_duplicates(subset=['Stock Symbol']).copy()
    unique_df = unique_df[['Stock Symbol']].sort_values(by='Stock Symbol')

    if not duplicate_symbols.empty:
        dup_info = master_df[master_df['Stock Symbol'].isin(duplicate_symbols)]
        dup_summary = dup_info.groupby('Stock Symbol')['Source File'].unique().apply(lambda x: ", ".join(x)).reset_index()
        dup_summary.columns = ['Stock Symbol', 'Found in these Files']
    else:
        dup_summary = pd.DataFrame(columns=['Stock Symbol', 'Found in these Files'])

    return master_df, unique_df, dup_summary


def _cli_main() -> None:
    parser = argparse.ArgumentParser(
        prog='cvs_merger.py',
        description='Merge and deduplicate stock watchlist files (CSV/TXT). '
                    'Strips exchange prefixes (NASDAQ:AAPL -> AAPL), comment lines, '
                    'and purely numeric entries.',
    )
    parser.add_argument('files', nargs='+', help='Watchlist files to merge (.csv or .txt)')
    parser.add_argument('-o', '--output', help='Write the merged unique symbol list to this file '
                                                '(default: print to stdout)')
    parser.add_argument('--format', choices=['txt', 'csv'], default='txt',
                        help='Output format when --output is set (default: txt, one symbol per line)')
    args = parser.parse_args()

    file_contents = {}
    for path in args.files:
        with open(path, 'r', encoding='utf-8') as f:
            file_contents[os.path.basename(path)] = f.read()

    master_df, unique_df, dup_df = merge_watchlists(file_contents)

    contributed = master_df.get('Source File', pd.Series(dtype=str)).unique() if not master_df.empty else []
    for filename in file_contents:
        if filename not in contributed:
            print(f"WARNING: '{filename}' contributed 0 valid symbols", file=sys.stderr)

    if args.output:
        if args.format == 'csv':
            unique_df.to_csv(args.output, index=False)
        else:
            with open(args.output, 'w') as f:
                f.write('\n'.join(unique_df['Stock Symbol'].tolist()) + '\n')
        print(f"Wrote {len(unique_df)} unique symbols to {args.output}", file=sys.stderr)
    else:
        for sym in unique_df['Stock Symbol']:
            print(sym)

    if not dup_df.empty:
        print(f"\n{len(dup_df)} duplicate symbol(s) found across files:", file=sys.stderr)
        for _, row in dup_df.iterrows():
            print(f"  {row['Stock Symbol']}: {row['Found in these Files']}", file=sys.stderr)


def _streamlit_main() -> None:
    import streamlit as st

    st.set_page_config(page_title="Stock List Merger Pro", layout="wide")

    st.title("📈 Stock List Merger & Comparator")
    st.markdown("""
    ### Rules applied:
    - **Exchanges**: Removes prefixes (e.g., `NASDAQ:AAPL` → `AAPL`).
    - **Comments**: Ignores lines or segments starting with `#`.
    - **Numeric**: Ignores purely numeric entries (e.g., `1234`).
    - **Duplicates**: Shows you exactly which symbols appeared in multiple files.
    """)

    uploaded_files = st.file_uploader("Upload CSV or TXT files", type=["csv", "txt"], accept_multiple_files=True)

    if uploaded_files:
        file_contents = {}
        for file in uploaded_files:
            try:
                file_contents[file.name] = file.getvalue().decode("utf-8")
            except Exception as e:
                st.error(f"Error reading {file.name}: {e}")

        master_df, unique_df, dup_summary = merge_watchlists(file_contents)

        for filename in file_contents:
            if filename not in master_df.get('Source File', pd.Series(dtype=str)).unique():
                st.warning(f"File '{filename}' had no valid alphabetic symbols.")

        if not master_df.empty:
            tab1, tab2, tab3 = st.tabs(["📋 Merged Unique List", "👯 Duplicates Found", "📂 Raw Data Breakdown"])

            with tab1:
                st.subheader("Final Cleaned List")
                st.info(f"Total unique symbols: **{len(unique_df)}**")
                st.dataframe(unique_df, use_container_width=True, hide_index=True)

                st.write("### 📥 Download Results")
                col1, col2 = st.columns(2)

                csv_output = unique_df.to_csv(index=False).encode('utf-8')
                col1.download_button(
                    label="Download as CSV",
                    data=csv_output,
                    file_name="merged_stocks.csv",
                    mime="text/csv",
                    use_container_width=True
                )

                txt_content = "\n".join(unique_df['Stock Symbol'].tolist())
                col2.download_button(
                    label="Download as TXT (List)",
                    data=txt_content,
                    file_name="merged_stocks.txt",
                    mime="text/plain",
                    use_container_width=True
                )

            with tab2:
                st.subheader("Overlapping Symbols")
                if not dup_summary.empty:
                    st.warning(f"Found {len(dup_summary)} symbols occurring in multiple files.")
                    st.dataframe(dup_summary, use_container_width=True, hide_index=True)
                else:
                    st.success("No duplicates found! All symbols across all files are unique.")

            with tab3:
                st.subheader("Raw Extracted Entries")
                st.write("This shows every symbol found after cleaning (before deduplication).")
                st.dataframe(master_df, use_container_width=True, hide_index=True)
    else:
        st.info("Upload your stock lists (CSV or TXT) to get started.")


if len(sys.argv) > 1:
    _cli_main()
else:
    _streamlit_main()
