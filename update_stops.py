"""
update_stops.py — Compute smart stop-loss prices for manual portfolio positions.

Stop logic (takes the highest valid candidate to be as tight as possible):
  1. ATR stop   : last_close - ATR_MULT × ATR14 (Wilder's)
  2. Swing low  : lowest Low over SWING_LOOKBACK bars
  3. SMA support: SMA50 or SMA200 if price is within SMA_PROXIMITY_PCT of it
  4. S/R level  : nearest confirmed support level (2+ touches) minus ATR buffer
  5. Final stop = max(ATR stop, best support candidate) — never above entry price

Usage:
  python update_stops.py                  # update gil.hadas@gmail.com (default)
  python update_stops.py --user <user_id> # update any user by ID
  python update_stops.py --dry-run        # print results, do not save
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indicators import calculate_atr
from pattern_recognition import detect_sr_levels
from api.server import _load_portfolio_json, _save_portfolio_json

# ── Tunable constants ─────────────────────────────────────────────────────────
ATR_MULT        = 2.5   # stop = price - ATR_MULT × ATR14
SWING_LOOKBACK  = 20    # bars for swing-low
SMA_PROXIMITY   = 0.03  # 3% — treat SMA as support if price is within this band
SR_BUFFER_MULT  = 0.25  # stop = support_level - SR_BUFFER_MULT × ATR
DATA_PERIOD     = "200d"

DEFAULT_USER_ID = "cf699841-1d06-522a-9dfb-3f9619a33854"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_stop(sym: str, df_raw: pd.DataFrame) -> dict:
    """Return stop price and diagnostic note for one symbol."""
    df = df_raw.rename(columns=str.lower)
    if len(df) < 30:
        return {"stop": None, "stop_note": "insufficient data"}

    price = float(df["close"].iloc[-1])

    # 1. ATR (Wilder's)
    atr_series = calculate_atr(df)
    atr = float(atr_series.iloc[-1])
    atr_stop = price - ATR_MULT * atr

    # 2. Swing low
    swing_low = float(df["low"].iloc[-SWING_LOOKBACK:].min())

    # 3. SMA50 / SMA200 as dynamic support
    sma_candidates = []
    for period, label in [(50, "SMA50"), (200, "SMA200")]:
        if len(df) >= period:
            sma_val = float(df["close"].rolling(period).mean().iloc[-1])
            if sma_val < price and (price - sma_val) / price <= SMA_PROXIMITY:
                sma_candidates.append((sma_val, label))

    # 4. Structural S/R
    sr = detect_sr_levels(df, current_price=price, atr=atr)
    sr_support = sr.get("nearest_support")
    sr_stop = None
    if sr_support and sr_support < price:
        sr_stop = sr_support - SR_BUFFER_MULT * atr

    # 5. Best stop = highest of all candidates (tightest stop that has a basis)
    candidates = {
        "atr":      atr_stop,
        "swing20":  swing_low,
    }
    if sma_candidates:
        best_sma_val, best_sma_lbl = max(sma_candidates, key=lambda x: x[0])
        candidates[best_sma_lbl] = best_sma_val - SR_BUFFER_MULT * atr

    if sr_stop is not None:
        candidates["sr_support"] = sr_stop

    valid = {k: v for k, v in candidates.items() if v is not None and v > 0}
    if not valid:
        return {"stop": None, "stop_note": "no valid stop candidates"}

    best_key = max(valid, key=lambda k: valid[k])
    stop = round(valid[best_key], 2)

    parts = [f"ATR14={atr:.2f}", f"swing20={swing_low:.2f}"]
    if sr_support:
        strength = sr.get("support_strength", 0)
        parts.append(f"sr_support={sr_support:.2f}(×{strength})")
    for sma_val, sma_lbl in sma_candidates:
        parts.append(f"{sma_lbl}={sma_val:.2f}")
    parts.append(f"→basis={best_key}")

    return {"stop": stop, "stop_note": " | ".join(parts)}


def _download(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Download OHLCV data and split per symbol."""
    raw = yf.download(symbols, period=DATA_PERIOD, progress=False, auto_adjust=True)
    if raw.empty:
        return {}

    result: dict[str, pd.DataFrame] = {}
    if len(symbols) == 1:
        result[symbols[0]] = raw.dropna()
    else:
        for sym in symbols:
            if sym in raw.columns.get_level_values(1):
                df = raw.xs(sym, axis=1, level=1).dropna()
                result[sym] = df
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Update stop prices for manual portfolio")
    parser.add_argument("--user",    default=DEFAULT_USER_ID, help="User ID to update")
    parser.add_argument("--dry-run", action="store_true", help="Print results, do not save")
    args = parser.parse_args()

    data = _load_portfolio_json(args.user)
    if not data:
        print(f"ERROR: no portfolio found for user {args.user}")
        sys.exit(1)

    raw_positions = data.get("positions", {})
    pos_dict = (
        raw_positions if isinstance(raw_positions, dict)
        else {p["symbol"]: p for p in raw_positions}
    )
    symbols = list(pos_dict.keys())
    if not symbols:
        print("No positions in portfolio.")
        sys.exit(0)

    print(f"Fetching {DATA_PERIOD} data for {len(symbols)} symbols: {', '.join(symbols)}")
    frames = _download(symbols)

    updated: dict[str, dict] = {}
    rows = []
    for sym in symbols:
        pos = pos_dict[sym]
        if sym not in frames:
            result = {"stop": pos.get("stop", 0), "stop_note": "data unavailable"}
        else:
            result = _compute_stop(sym, frames[sym])

        new_stop = result["stop"] if result["stop"] is not None else pos.get("stop", 0)
        updated[sym] = {**pos, "stop": new_stop, "stop_note": result["stop_note"]}

        entry  = pos.get("entry_price", 0)
        dist   = round((entry - new_stop) / entry * 100, 1) if entry and new_stop else None
        rows.append((sym, entry, new_stop, dist, result["stop_note"]))

    # Print table
    print(f"\n{'Symbol':<7} {'Entry':>8} {'Stop':>8} {'Dist%':>7}  Basis")
    print("-" * 80)
    for sym, entry, stop, dist, note in sorted(rows):
        if dist is None:
            dist_str = "     —"
        elif dist < 0:
            dist_str = f"{dist:>6.1f}% ↑profit-lock"
        else:
            dist_str = f"{dist:>6.1f}%"
        print(f"{sym:<7} {entry:>8.2f} {stop:>8.2f} {dist_str}  {note}")

    if args.dry_run:
        print("\n[dry-run] No changes saved.")
        return

    data["positions"] = updated
    _save_portfolio_json(data, args.user)
    print(f"\nSaved updated stops for {len(updated)} positions.")


if __name__ == "__main__":
    main()
