"""
sheets_export.py — Export breakout signals to Google Sheets with Win/Loss analysis.

Creates two sheets:
  • "Signals"  — one row per ticker (latest signal), all parameters + Status
  • "Analysis" — win-rate breakdown by Quality, Mode, Sector, MinerviniScore,
                 Patterns, etc. to identify best predictors

SETUP (one-time):
  1. pip install gspread google-auth
  2. Google Cloud setup:
       a. Go to https://console.cloud.google.com
       b. Create a project (or reuse one)
       c. Enable "Google Sheets API" and "Google Drive API"
       d. Create a Service Account → download JSON key
       e. Save key as:  credentials/google_service_account.json
  3. Add to .env:
       GOOGLE_SERVICE_ACCOUNT_KEY=credentials/google_service_account.json
       GOOGLE_SHARE_EMAIL=your@gmail.com          # sheet will be shared with this address
  4. Run:  python sheets_export.py

Usage:
  python sheets_export.py                          # create or update sheet
  python sheets_export.py --sheet-id SPREADSHEET_ID  # update an existing sheet
  python sheets_export.py --output signals.csv        # just export CSV, no Google Sheets
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# ── Project setup ─────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
_env = PROJECT_ROOT / '.env'
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            if _line.startswith('export '): _line = _line[7:]
            k, _, v = _line.partition('=')
            os.environ.setdefault(k.strip(), v.split('#')[0].strip().strip('"').strip("'"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

REPORTS_DIR = PROJECT_ROOT / 'scanner_output' / 'signal_reports'
S3_SHEET_NAME = 'Breakout Signals Analysis'

# Column order for the Signals sheet
SIGNAL_COLS = [
    'Symbol', 'SignalDate', 'Mode', 'Type', 'Quality', 'MinerviniScore',
    'WinProb', 'RR_Grade', 'WinGrade', 'RSI',
    'Entry', 'Stop', 'Target', 'R:R', 'Gap%', 'Dist', 'SMA_Dist%', 'Vol',
    'Current', 'Gain%', 'DaysSince',
    'Status', 'HitTarget', 'HitStop',
    'Patterns', 'Near52wHigh', 'RSI_BullDiv', 'PatternVolConf',
    'Sector', 'Sentiment', 'Buzz',
]


# ── Data loading ──────────────────────────────────────────────────────────────

def _parse_file_date(fname: str) -> str:
    """Extract YYYYMMDD date string from report filename."""
    # report_swing_20260217_024340.csv
    parts = fname.replace('.csv', '').split('_')
    # parts: ['report', 'swing', '20260217', '024340']  (mode may be multi-word)
    for p in parts:
        if len(p) == 8 and p.isdigit():
            return p
    return '19700101'


def load_all_reports() -> pd.DataFrame:
    """Load every report CSV, tag with date, return concatenated DataFrame."""
    frames = []
    for f in sorted(REPORTS_DIR.glob('*.csv')):
        try:
            df = pd.read_csv(f)
            df['SignalDate'] = _parse_file_date(f.name)
            df['_source'] = f.name
            frames.append(df)
        except Exception as e:
            log.warning(f"Skipped {f.name}: {e}")

    if not frames:
        log.error(f"No report CSVs found in {REPORTS_DIR}")
        sys.exit(1)

    all_df = pd.concat(frames, ignore_index=True)
    log.info(f"Loaded {len(all_df):,} rows from {len(frames)} report files")
    return all_df


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """Keep the FIRST signal per Symbol (earliest SignalDate → first alert for that ticker).

    Using the first signal is correct for backtesting: it represents the original entry
    opportunity.  Later re-scans of the same ticker reflect a different price, so they
    would give a misleading R entry/stop/target.
    """
    df = df.sort_values(['Symbol', 'SignalDate', '_source'], ascending=[True, True, True])
    dedup = df.drop_duplicates(subset='Symbol', keep='first').copy()
    log.info(f"Deduplicated to {len(dedup):,} unique symbols (first signal per ticker)")
    return dedup


def assign_status(df: pd.DataFrame) -> pd.DataFrame:
    """Add Status column: WIN / LOSS / OPEN."""
    def _status(row):
        hit_t = str(row.get('HitTarget', '')).strip().lower() == 'true'
        hit_s = str(row.get('HitStop', '')).strip().lower() == 'true'
        if hit_t:
            return 'WIN'
        if hit_s:
            return 'LOSS'
        # Open position — classify direction
        try:
            gain = float(row.get('Gain%', 0) or 0)
        except (ValueError, TypeError):
            gain = 0.0
        return 'OPEN+' if gain >= 0 else 'OPEN-'

    df = df.copy()
    df['Status'] = df.apply(_status, axis=1)
    log.info(f"Status: {df['Status'].value_counts().to_dict()}")
    return df


def refresh_current_prices(df: pd.DataFrame) -> pd.DataFrame:
    """Fetch fresh current prices from yfinance for all symbols."""
    symbols = df['Symbol'].dropna().unique().tolist()
    log.info(f"Fetching current prices for {len(symbols)} symbols...")

    try:
        tickers = yf.download(symbols, period='1d', auto_adjust=True, progress=False)
        closes = tickers['Close'].iloc[-1] if 'Close' in tickers else tickers.iloc[-1]
        price_map = closes.dropna().to_dict()
    except Exception as e:
        log.warning(f"Batch price fetch failed ({e}), skipping price refresh")
        return df

    df = df.copy()
    refreshed = 0
    for i, row in df.iterrows():
        sym = row.get('Symbol', '')
        p = price_map.get(sym)
        if p and not pd.isna(p):
            df.at[i, 'Current'] = round(float(p), 2)
            try:
                entry = float(row.get('Price', 0) or 0)
                if entry > 0:
                    df.at[i, 'Gain%'] = round((float(p) - entry) / entry * 100, 2)
            except (ValueError, TypeError):
                pass
            refreshed += 1

    log.info(f"Refreshed prices for {refreshed} symbols")
    return df


def build_signals_sheet(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns and select final column set for the Signals sheet."""
    df = df.rename(columns={'Price': 'Entry'})

    for col in SIGNAL_COLS:
        if col not in df.columns:
            df[col] = ''

    return df[SIGNAL_COLS].copy()


# ── Analysis ──────────────────────────────────────────────────────────────────

def _winrate_table(df: pd.DataFrame, col: str, min_n: int = 3) -> pd.DataFrame:
    """Compute win/loss/open counts and win rate for each value of col."""
    closed = df[df['Status'].isin(['WIN', 'LOSS'])].copy()
    if closed.empty or col not in closed.columns:
        return pd.DataFrame()

    rows = []
    for val, grp in closed.groupby(col, observed=True):
        n = len(grp)
        if n < min_n:
            continue
        wins = (grp['Status'] == 'WIN').sum()
        losses = (grp['Status'] == 'LOSS').sum()
        open_pos = (df[col] == val).sum() - n   # all signals - closed
        win_rate = wins / n * 100
        avg_gain = grp['Gain%'].astype(float).mean()
        rows.append({
            col: val,
            'N (closed)': n,
            'Wins': wins,
            'Losses': losses,
            'Win Rate %': round(win_rate, 1),
            'Avg Gain% (closed)': round(avg_gain, 2),
            'Open Positions': max(open_pos, 0),
        })

    if not rows:
        return pd.DataFrame()
    result = pd.DataFrame(rows).sort_values('Win Rate %', ascending=False)
    return result


def _numeric_bands(df: pd.DataFrame, col: str, bins: list) -> pd.DataFrame:
    """Like _winrate_table but for a numeric column, grouped into bands."""
    df = df.copy()
    df[col] = pd.to_numeric(df[col], errors='coerce')
    df[f'_{col}_band'] = pd.cut(df[col], bins=bins)
    result = _winrate_table(df, f'_{col}_band')
    if not result.empty:
        result = result.rename(columns={f'_{col}_band': f'{col} band'})
    return result


def _top_predictors(df: pd.DataFrame) -> pd.DataFrame:
    """Rank all categorical parameters by their win-rate spread (best vs worst group)."""
    closed = df[df['Status'].isin(['WIN', 'LOSS'])].copy()
    if closed.empty:
        return pd.DataFrame()

    cat_cols = ['Quality', 'Mode', 'Type', 'RR_Grade', 'WinGrade',
                'Near52wHigh', 'RSI_BullDiv', 'PatternVolConf', 'Sector', 'Sentiment']

    rows = []
    for col in cat_cols:
        if col not in closed.columns:
            continue
        tbl = _winrate_table(closed, col)
        if tbl.empty or len(tbl) < 2:
            continue
        spread = tbl['Win Rate %'].max() - tbl['Win Rate %'].min()
        best_val = tbl.iloc[0][col]
        best_wr = tbl.iloc[0]['Win Rate %']
        rows.append({
            'Parameter': col,
            'Best Value': best_val,
            'Best Win Rate %': best_wr,
            'Win Rate Spread %': round(spread, 1),
        })

    return pd.DataFrame(rows).sort_values('Win Rate Spread %', ascending=False)


def build_analysis(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Return dict of section_name → DataFrame for the Analysis sheet."""
    closed = df[df['Status'].isin(['WIN', 'LOSS'])]
    total_n = len(closed)
    total_wins = (closed['Status'] == 'WIN').sum()
    total_losses = (closed['Status'] == 'LOSS').sum()

    sections = {}

    # 1. Overall summary
    sections['Overall'] = pd.DataFrame([{
        'Total Signals': len(df),
        'Closed': total_n,
        'Wins': total_wins,
        'Losses': total_losses,
        'Win Rate %': round(total_wins / total_n * 100, 1) if total_n else 0,
        'Open+': (df['Status'] == 'OPEN+').sum(),
        'Open-': (df['Status'] == 'OPEN-').sum(),
        'Avg Gain% (closed)': round(closed['Gain%'].astype(float).mean(), 2) if total_n else 0,
    }])

    # 2. By Quality
    sections['By Quality'] = _winrate_table(df, 'Quality')

    # 3. By Mode
    sections['By Mode'] = _winrate_table(df, 'Mode')

    # 4. By Type
    sections['By Type'] = _winrate_table(df, 'Type')

    # 5. By MinerviniScore
    sections['By MinerviniScore'] = _winrate_table(df, 'MinerviniScore', min_n=2)

    # 6. By Sector
    sections['By Sector'] = _winrate_table(df, 'Sector')

    # 7. By RR_Grade
    sections['By RR Grade'] = _winrate_table(df, 'RR_Grade')

    # 8. By WinGrade
    sections['By WinGrade'] = _winrate_table(df, 'WinGrade')

    # 9. By Near52wHigh
    sections['By Near52wHigh'] = _winrate_table(df, 'Near52wHigh')

    # 10. By PatternVolConf
    sections['By PatternVolConf'] = _winrate_table(df, 'PatternVolConf')

    # 11. By RSI_BullDiv
    sections['By RSI_BullDiv'] = _winrate_table(df, 'RSI_BullDiv')

    # 12. By WinProb bands
    sections['By WinProb'] = _numeric_bands(df, 'WinProb',
                                            bins=[0, 0.6, 0.65, 0.70, 0.75, 0.80, 1.01])

    # 13. By R:R bands
    sections['By R:R'] = _numeric_bands(df, 'R:R', bins=[0, 1.5, 2.0, 2.5, 3.0, 10])

    # 14. Top predictors
    sections['Top Predictors'] = _top_predictors(df)

    return {k: v for k, v in sections.items() if v is not None and not v.empty}


# ── Google Sheets output ───────────────────────────────────────────────────────

def _gc():
    """Return an authenticated gspread client (gspread v6 API)."""
    import gspread

    key_path = os.environ.get('GOOGLE_SERVICE_ACCOUNT_KEY',
                              str(PROJECT_ROOT / 'credentials' / 'google_service_account.json'))

    if Path(key_path).exists():
        # Service account — best for automation (no browser needed)
        return gspread.service_account(filename=key_path)

    # Fallback: interactive OAuth — opens browser once, caches token
    oauth_path = PROJECT_ROOT / 'credentials' / 'oauth_credentials.json'
    log.warning(f"Service account key not found at {key_path} — trying OAuth...")
    return gspread.oauth(credentials_filename=str(oauth_path))


def _df_to_rows(df: pd.DataFrame) -> list:
    """Convert DataFrame to list-of-lists with header, NaN → empty string."""
    return [df.columns.tolist()] + df.fillna('').astype(str).values.tolist()


def _build_analysis_grid(analysis: dict) -> list:
    """Flatten all analysis sections into one 2D list with section titles + blank gaps."""
    grid = []
    max_cols = 0
    for name, df in analysis.items():
        if df.empty:
            continue
        grid.append([f'── {name} ──'])          # title row
        grid.extend(_df_to_rows(df))             # header + data
        grid.append([])                          # blank gap
        grid.append([])
        max_cols = max(max_cols, len(df.columns) + 1)
    return grid


def push_to_google_sheets(signals_df: pd.DataFrame, analysis: dict,
                           sheet_id: str = None) -> str:
    """Push data to Google Sheets in single batch calls. Returns spreadsheet URL."""
    import gspread

    gc = _gc()
    share_email = os.environ.get('GOOGLE_SHARE_EMAIL', '')

    if sheet_id:
        sh = gc.open_by_key(sheet_id)
        log.info(f"Updating existing sheet: {sh.title}")
    else:
        try:
            sh = gc.open(S3_SHEET_NAME)
            log.info(f"Found existing sheet: {S3_SHEET_NAME}")
        except gspread.SpreadsheetNotFound:
            sh = gc.create(S3_SHEET_NAME)
            log.info(f"Created new sheet: {S3_SHEET_NAME}")
            if share_email:
                sh.share(share_email, perm_type='user', role='writer')
                log.info(f"Shared with {share_email}")

    # ── Sheet 1: Signals — delete/recreate ensures correct dimensions ──
    n_rows = len(signals_df) + 5
    n_cols = len(signals_df.columns) + 2
    try:
        sh.del_worksheet(sh.worksheet('Signals'))
    except Exception:
        pass
    ws1 = sh.add_worksheet(title='Signals', rows=n_rows, cols=n_cols)

    log.info(f"Writing {len(signals_df)} signal rows (single batch)...")
    signal_grid = _df_to_rows(signals_df)
    try:
        ws1.update(values=signal_grid, range_name='A1')
        log.info(f"Signals sheet written: {len(signal_grid)} rows × {n_cols} cols")
    except Exception as e:
        log.error(f"Failed to write Signals sheet: {e}")
        raise

    ws1.freeze(rows=1)

    # Conditional formatting for Status column
    status_col = signals_df.columns.tolist().index('Status') + 1
    _apply_status_colors(sh, ws1.id, status_col, len(signals_df) + 1)

    # ── Sheet 2: Analysis — delete/recreate ──
    analysis_grid = _build_analysis_grid(analysis)
    try:
        sh.del_worksheet(sh.worksheet('Analysis'))
    except Exception:
        pass
    ws2 = sh.add_worksheet(title='Analysis', rows=len(analysis_grid) + 10, cols=15)

    log.info(f"Writing analysis ({len(analysis_grid)} rows, single batch)...")
    try:
        ws2.update(values=analysis_grid, range_name='A1')
        log.info("Analysis sheet written")
    except Exception as e:
        log.error(f"Failed to write Analysis sheet: {e}")
        raise

    # Delete the default empty Sheet1 if it exists
    try:
        sh.del_worksheet(sh.worksheet('Sheet1'))
    except Exception:
        pass

    url = f"https://docs.google.com/spreadsheets/d/{sh.id}"
    log.info(f"Done! Spreadsheet URL: {url}")
    return url


def _apply_status_colors(sh, worksheet_id: int, status_col: int, num_rows: int):
    """Color-code Status column: WIN=green, LOSS=red, OPEN+=blue, OPEN-=orange."""
    import gspread
    col_index = status_col - 1  # 0-indexed

    color_rules = [
        ('WIN',   {'red': 0.714, 'green': 0.843, 'blue': 0.659}),
        ('LOSS',  {'red': 0.918, 'green': 0.600, 'blue': 0.600}),
        ('OPEN+', {'red': 0.643, 'green': 0.761, 'blue': 0.957}),
        ('OPEN-', {'red': 0.988, 'green': 0.820, 'blue': 0.612}),
    ]

    requests = []
    for text, color in color_rules:
        requests.append({
            'addConditionalFormatRule': {
                'rule': {
                    'ranges': [{'sheetId': worksheet_id,
                                'startRowIndex': 1, 'endRowIndex': num_rows,
                                'startColumnIndex': col_index,
                                'endColumnIndex': col_index + 1}],
                    'booleanRule': {
                        'condition': {'type': 'TEXT_EQ', 'values': [{'userEnteredValue': text}]},
                        'format': {'backgroundColor': color},
                    }
                },
                'index': 0
            }
        })

    sh.batch_update({'requests': requests})


# ── CSV output ────────────────────────────────────────────────────────────────

def save_csv(signals_df: pd.DataFrame, analysis: dict, output_path: str):
    signals_df.to_csv(output_path, index=False)
    log.info(f"Signals CSV saved: {output_path}")

    analysis_path = output_path.replace('.csv', '_analysis.csv')
    parts = []
    for name, tbl in analysis.items():
        tbl2 = tbl.copy()
        tbl2.insert(0, 'Section', name)
        parts.append(tbl2)
        parts.append(pd.DataFrame([{}]))  # blank separator
    if parts:
        pd.concat(parts, ignore_index=True).to_csv(analysis_path, index=False)
        log.info(f"Analysis CSV saved: {analysis_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Export signals to Google Sheets')
    parser.add_argument('--sheet-id', help='Existing Google Sheets ID to update')
    parser.add_argument('--output', help='Also save a local CSV (e.g. signals.csv)')
    parser.add_argument('--no-price-refresh', action='store_true',
                        help='Skip yfinance price refresh (faster)')
    parser.add_argument('--csv-only', action='store_true',
                        help='Save CSV only, skip Google Sheets upload')
    args = parser.parse_args()

    # 1. Load
    raw = load_all_reports()

    # 2. Deduplicate
    dedup = deduplicate(raw)

    # 3. Refresh prices (unless skipped)
    if not args.no_price_refresh:
        dedup = refresh_current_prices(dedup)

    # 4. Status
    dedup = assign_status(dedup)

    # 5. Build Signals sheet
    signals_df = build_signals_sheet(dedup)

    # 6. Build Analysis
    analysis = build_analysis(dedup)

    # Print summary to terminal
    print("\n" + "=" * 60)
    print(f"  BREAKOUT SIGNALS SUMMARY")
    print("=" * 60)
    if 'Overall' in analysis:
        for k, v in analysis['Overall'].iloc[0].items():
            print(f"  {k:<30} {v}")
    print()
    if 'By Quality' in analysis:
        print("  Win Rate by Quality:")
        print(analysis['By Quality'].to_string(index=False))
    print()
    if 'Top Predictors' in analysis:
        print("  Top Predictors (by win-rate spread):")
        print(analysis['Top Predictors'].to_string(index=False))
    print("=" * 60 + "\n")

    # 7. Always save CSV (--output overrides path, default = scanner_output/analysis/)
    output_path = args.output or f"scanner_output/analysis/signals_analysis_{datetime.now().strftime('%Y%m%d')}.csv"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    save_csv(signals_df, analysis, output_path)

    # 8. Push to Google Sheets
    if not args.csv_only:
        try:
            url = push_to_google_sheets(signals_df, analysis, sheet_id=args.sheet_id)
            print(f"  Google Sheet: {url}\n")
        except ImportError:
            log.error("gspread not installed. Run: pip install gspread google-auth")
        except Exception as e:
            log.error(f"Google Sheets upload failed: {e}")
            log.info("Saving CSV fallback...")
            save_csv(signals_df, analysis, output_path)
            print(f"  CSV saved: {output_path}\n")


if __name__ == '__main__':
    main()
