#!/usr/bin/env python3
"""
export_to_sheets.py — Export breakout signal CSVs to a Google Sheet.

Each CSV in scanner_output/signals/ becomes one tab.
Adds a "Live Price" column with =GOOGLEFINANCE(A{row},"price").

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ONE-TIME SETUP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Install dependencies:
     pip install google-auth-oauthlib google-api-python-client

2. Create Google Cloud credentials:
   a. Go to https://console.cloud.google.com
   b. Create a new project (or use existing)
   c. Enable "Google Sheets API"
   d. Go to APIs & Services → Credentials
   e. Create OAuth 2.0 Client ID → Desktop app
   f. Download JSON → save as credentials.json in this project root

3. First run (opens browser for Google auth):
     python export_to_sheets.py
   Token saved to token.json — no browser needed after that.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USAGE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  # Create a brand new spreadsheet
  python export_to_sheets.py

  # Update an existing spreadsheet (adds/refreshes tabs)
  python export_to_sheets.py --sheet-id 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms

  # Filter by mode
  python export_to_sheets.py --mode swing
  python export_to_sheets.py --mode daytrade

  # Limit to newest N files
  python export_to_sheets.py --max-files 20

  # Custom signals directory
  python export_to_sheets.py --signals-dir scanner_output/signals
"""

import argparse
import csv
import os
import re
import sys
from pathlib import Path
from datetime import datetime

# ── Google API imports ────────────────────────────────────────────────────────
# Imported lazily so that --help works even without the libraries installed.
_GOOGLE_LIBS_OK = False
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    _GOOGLE_LIBS_OK = True
except ImportError:
    pass  # checked later in get_credentials(), after argparse runs

# ── Constants ─────────────────────────────────────────────────────────────────
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
SIGNALS_DIR = Path("scanner_output/signals")

# Symbol column index (0-based) — "Symbol" is the first column
SYMBOL_COL_INDEX = 0


def get_credentials() -> Credentials:
    """Load or refresh OAuth2 credentials, opening browser on first run."""
    if not _GOOGLE_LIBS_OK:
        print("ERROR: Google API libraries not installed.")
        print("Run: pip install google-auth-oauthlib google-api-python-client")
        sys.exit(1)
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                print(f"ERROR: {CREDENTIALS_FILE} not found.")
                print("See setup instructions at the top of this file.")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
        print(f"Credentials saved to {TOKEN_FILE}")

    return creds


def make_tab_name(filename: str) -> str:
    """
    Convert filename to a short, readable tab name.
    signals_swing_20260220_123019.csv → swing_0220_1230
    Tab names max 100 chars; we keep it short.
    """
    # Extract parts: signals_{mode}_{date}_{time}.csv
    match = re.match(r"signals_(\w+)_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})", filename)
    if match:
        mode, year, month, day, hour, minute = match.groups()
        return f"{mode}_{month}{day}_{hour}{minute}"
    # Fallback: strip extension, truncate
    return Path(filename).stem[-30:]


def read_csv_rows(filepath: Path) -> list[list[str]]:
    """Read CSV file, return list of rows (including header)."""
    rows = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if any(cell.strip() for cell in row):  # skip blank rows
                rows.append(row)
    return rows


def _col_letter(zero_based_idx: int) -> str:
    """Convert 0-based column index to Google Sheets letter (A, B, … Z, AA, …)."""
    result = ""
    n = zero_based_idx + 1
    while n:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def build_sheet_data(rows: list[list[str]]) -> tuple[list[list[str]], int]:
    """
    Insert three new columns after the original Price column (B).

    Final layout:
      Col A: Symbol           (original)
      Col B: Entry Price      (original)
      Col C: Live Price       =GOOGLEFINANCE(A{row},"price")
      Col D: Total % Gain/Loss  =(C-B)/B  for every signal  [formatted as %]
      Col E: PREMIUM/Gold %   same formula, blank if Quality ≠ PREMIUM/GOLD
      Col F+: remaining original columns (Vol, Dist, …, Quality, …)

    Bottom row:
      D → AVERAGE of all signals (total portfolio avg)
      E → AVERAGE of PREMIUM/GOLD rows only (blank rows ignored by AVERAGE)

    Returns (data_rows, num_data_rows) — num_data_rows excludes header & total.
    """
    if not rows:
        return [], 0

    NUM_INSERTED = 3           # Live Price, Total %, PREM/Gold %
    num_data_rows = len(rows) - 1

    # Locate Quality column in the original CSV header
    orig_header = rows[0]
    quality_orig_idx = orig_header.index("Quality") if "Quality" in orig_header else -1
    # After inserting NUM_INSERTED cols starting at position 2,
    # original col k shifts to k + NUM_INSERTED for k >= 2
    if quality_orig_idx >= 0:
        quality_sheet_col = _col_letter(quality_orig_idx + NUM_INSERTED)
    else:
        quality_sheet_col = None  # fallback: PREM/Gold % column stays blank

    result = []
    for i, row in enumerate(rows):
        if i == 0:
            new_row = row[:2] + ["Live Price", "Total % Gain/Loss", "PREMIUM/Gold %"] + row[2:]
            result.append(new_row)
        else:
            sheet_row = i + 1   # 1-indexed; header is row 1
            live_price  = f'=GOOGLEFINANCE(A{sheet_row},"price")'
            total_pct   = f'=(C{sheet_row}-B{sheet_row})/B{sheet_row}'
            if quality_sheet_col:
                q = quality_sheet_col
                prem_pct = (
                    f'=IF(OR({q}{sheet_row}="PREMIUM",{q}{sheet_row}="GOLD"),'
                    f'(C{sheet_row}-B{sheet_row})/B{sheet_row},"")'
                )
            else:
                prem_pct = ""
            new_row = row[:2] + [live_price, total_pct, prem_pct] + row[2:]
            result.append(new_row)

    # Total row
    last_data_row = num_data_rows + 1      # sheet row number of the last data row
    num_cols = len(result[0])
    total_row = (
        ["Portfolio Total", "", "",
         f"=SUM(D2:D{last_data_row})",               # sum across all signals
         f'=IFERROR(SUM(E2:E{last_data_row}),"")']   # sum PREMIUM/GOLD only
        + [""] * (num_cols - 5)
    )
    result.append(total_row)

    return result, num_data_rows


def get_or_create_sheet_tab(service, spreadsheet_id: str, tab_name: str) -> int:
    """
    Return the sheetId of an existing tab, or create it and return its id.
    """
    spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing = {s["properties"]["title"]: s["properties"]["sheetId"]
                for s in spreadsheet["sheets"]}

    if tab_name in existing:
        return existing[tab_name]

    # Create new sheet tab
    body = {"requests": [{"addSheet": {"properties": {"title": tab_name}}}]}
    resp = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body=body
    ).execute()
    new_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
    print(f"  Created tab: {tab_name}")
    return new_id


def write_tab_data(service, spreadsheet_id: str, tab_name: str,
                   data: list[list[str]]) -> None:
    """Clear tab and write all rows with USER_ENTERED (formulas evaluated)."""
    range_name = f"'{tab_name}'!A1"

    # Clear existing content
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'",
        body={}
    ).execute()

    # Write new data
    body = {"values": data}
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption="USER_ENTERED",
        body=body,
    ).execute()


def format_tab(service, spreadsheet_id: str, sheet_id: int,
               num_cols: int, num_data_rows: int) -> None:
    """
    Apply all formatting to a signal tab:
      - Dark header row (bold, white text, frozen)
      - % Change column (col D, index 3): percent format +0.00%/-0.00%
      - Conditional coloring on % Change data cells: green positive / red negative
      - Total row (last row): bold, light grey background
      - Auto-resize all columns
    """
    PCT_COL_START = 3                    # Column D (0-based) — Total % Gain/Loss
    PCT_COL_END   = 5                    # Column E+1 (exclusive) — covers D and E
    total_row_idx = num_data_rows + 1    # 0-based sheet row of Total row
    data_end_row  = num_data_rows + 1    # exclusive end for data range (= total_row_idx)

    requests = [
        # ── Header row formatting ─────────────────────────────────────────────
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0, "endRowIndex": 1,
                    "startColumnIndex": 0, "endColumnIndex": num_cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                        "backgroundColor": {"red": 0.13, "green": 0.13, "blue": 0.27},
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        },
        # ── Freeze header row ─────────────────────────────────────────────────
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        # ── D + E columns: percent number format ─────────────────────────────
        # Covers both "Total % Gain/Loss" (D) and "PREMIUM/Gold %" (E)
        # Applies to data rows + total row
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1, "endRowIndex": total_row_idx + 1,
                    "startColumnIndex": PCT_COL_START, "endColumnIndex": PCT_COL_END,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {
                            "type": "PERCENT",
                            "pattern": '+0.00%;-0.00%;0.00%',
                        }
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        },
        # ── Conditional format: green if gain (D and E data rows) ─────────────
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1, "endRowIndex": data_end_row,
                        "startColumnIndex": PCT_COL_START, "endColumnIndex": PCT_COL_END,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "NUMBER_GREATER",
                            "values": [{"userEnteredValue": "0"}],
                        },
                        "format": {
                            "textFormat": {"bold": True, "foregroundColor": {"red": 0.06, "green": 0.44, "blue": 0.13}},
                            "backgroundColor": {"red": 0.85, "green": 0.97, "blue": 0.87},
                        },
                    },
                },
                "index": 0,
            }
        },
        # ── Conditional format: red if loss (D and E data rows) ──────────────
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1, "endRowIndex": data_end_row,
                        "startColumnIndex": PCT_COL_START, "endColumnIndex": PCT_COL_END,
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "NUMBER_LESS",
                            "values": [{"userEnteredValue": "0"}],
                        },
                        "format": {
                            "textFormat": {"bold": True, "foregroundColor": {"red": 0.72, "green": 0.04, "blue": 0.04}},
                            "backgroundColor": {"red": 1.0, "green": 0.87, "blue": 0.87},
                        },
                    },
                },
                "index": 1,
            }
        },
        # ── Total row: bold, light grey background ────────────────────────────
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": total_row_idx, "endRowIndex": total_row_idx + 1,
                    "startColumnIndex": 0, "endColumnIndex": num_cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": {"red": 0.85, "green": 0.85, "blue": 0.85},
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        },
        # ── Auto-resize all columns ───────────────────────────────────────────
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0, "endIndex": num_cols,
                }
            }
        },
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}
    ).execute()


def format_tab_header_only(service, spreadsheet_id: str, sheet_id: int,
                           num_cols: int) -> None:
    """Simple header formatting for non-signal tabs (e.g. Index tab)."""
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0, "endRowIndex": 1,
                    "startColumnIndex": 0, "endColumnIndex": num_cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                        "backgroundColor": {"red": 0.13, "green": 0.13, "blue": 0.27},
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0, "endIndex": num_cols,
                }
            }
        },
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}
    ).execute()


def create_index_tab(service, spreadsheet_id: str,
                     file_entries: list[dict]) -> None:
    """
    Create or update an 'Index' tab listing all signal files with links.
    """
    tab_name = "Index"
    sheet_id = get_or_create_sheet_tab(service, spreadsheet_id, tab_name)

    header = ["Tab", "File", "Mode", "Date", "Time", "Rows"]
    rows = [header]
    for e in file_entries:
        rows.append([
            e["tab"],
            e["filename"],
            e["mode"],
            e["date"],
            e["time"],
            str(e["rows"]),
        ])

    write_tab_data(service, spreadsheet_id, tab_name, rows)
    format_tab_header_only(service, spreadsheet_id, sheet_id, len(header))
    print(f"  Updated Index tab ({len(file_entries)} entries)")


def parse_file_meta(filename: str) -> dict:
    """Extract mode/date/time from filename."""
    match = re.match(r"signals_(\w+)_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", filename)
    if match:
        mode, year, month, day, hour, minute, sec = match.groups()
        return {
            "mode": mode,
            "date": f"{year}-{month}-{day}",
            "time": f"{hour}:{minute}",
            "sort_key": f"{year}{month}{day}{hour}{minute}{sec}",
        }
    return {"mode": "unknown", "date": "", "time": "", "sort_key": "0"}


def main():
    parser = argparse.ArgumentParser(description="Export signal CSVs to Google Sheets")
    parser.add_argument("--sheet-id", help="Existing spreadsheet ID to update")
    parser.add_argument("--mode", choices=["swing", "daytrade", "longterm", "scalping"],
                        help="Filter by trading mode")
    parser.add_argument("--max-files", type=int, default=0,
                        help="Max number of files to export (0 = all, newest first)")
    parser.add_argument("--signals-dir", default=str(SIGNALS_DIR),
                        help=f"Signals directory (default: {SIGNALS_DIR})")
    args = parser.parse_args()

    signals_dir = Path(args.signals_dir)
    if not signals_dir.exists():
        print(f"ERROR: Signals directory not found: {signals_dir}")
        sys.exit(1)

    # ── Find CSV files ────────────────────────────────────────────────────────
    pattern = f"signals_{args.mode}_*.csv" if args.mode else "signals_*.csv"
    csv_files = sorted(signals_dir.glob(pattern))

    if not csv_files:
        print(f"No CSV files found in {signals_dir}")
        sys.exit(0)

    # Sort newest first
    def sort_key(p):
        m = re.search(r"(\d{8}_\d{6})", p.name)
        return m.group(1) if m else p.name

    csv_files = sorted(csv_files, key=sort_key, reverse=True)

    if args.max_files:
        csv_files = csv_files[: args.max_files]

    print(f"Found {len(csv_files)} signal file(s) to export")

    # ── Authenticate ──────────────────────────────────────────────────────────
    creds = get_credentials()
    service = build("sheets", "v4", credentials=creds)

    # ── Create or open spreadsheet ────────────────────────────────────────────
    if args.sheet_id:
        spreadsheet_id = args.sheet_id
        print(f"Using existing spreadsheet: {spreadsheet_id}")
    else:
        title = f"Breakout Signals {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        spreadsheet = service.spreadsheets().create(
            body={"properties": {"title": title}},
            fields="spreadsheetId",
        ).execute()
        spreadsheet_id = spreadsheet["spreadsheetId"]
        print(f"Created new spreadsheet: {title}")
        print(f"  ID: {spreadsheet_id}")
        print(f"  URL: https://docs.google.com/spreadsheets/d/{spreadsheet_id}")

    # ── Export each CSV ───────────────────────────────────────────────────────
    file_entries = []
    for csv_path in csv_files:
        filename = csv_path.name
        tab_name = make_tab_name(filename)
        meta = parse_file_meta(filename)

        print(f"\nProcessing: {filename} → tab '{tab_name}'")

        try:
            rows = read_csv_rows(csv_path)
        except Exception as e:
            print(f"  ERROR reading file: {e}")
            continue

        if len(rows) < 2:
            print(f"  Skipping: no data rows (only header or empty)")
            continue

        data, num_data_rows = build_sheet_data(rows)

        try:
            sheet_id = get_or_create_sheet_tab(service, spreadsheet_id, tab_name)
            write_tab_data(service, spreadsheet_id, tab_name, data)
            format_tab(service, spreadsheet_id, sheet_id, len(data[0]), num_data_rows)
            print(f"  Written: {num_data_rows} signal(s), {len(data[0])} columns")
        except HttpError as e:
            print(f"  ERROR writing tab: {e}")
            continue

        file_entries.append({
            "tab": tab_name,
            "filename": filename,
            "mode": meta["mode"],
            "date": meta["date"],
            "time": meta["time"],
            "rows": num_data_rows,
        })

    # ── Create Index tab ──────────────────────────────────────────────────────
    if file_entries:
        print("\nUpdating Index tab...")
        try:
            create_index_tab(service, spreadsheet_id, file_entries)
        except HttpError as e:
            print(f"  ERROR creating index: {e}")

    # ── Done ──────────────────────────────────────────────────────────────────
    print(f"\nDone! Open your spreadsheet:")
    print(f"  https://docs.google.com/spreadsheets/d/{spreadsheet_id}")


if __name__ == "__main__":
    main()
