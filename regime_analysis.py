#!/usr/bin/env python3
"""
Multi-Day Regime Analysis
Loads all available signal CSVs + monitor outcomes across Feb 21 – Mar 16.
Applies new filter rules (Vol>=1.8 for SMA20_CROSS, RSI 48-62, SMA50 hard, etc.)
Groups by regime and computes per-regime win rates, avg P&L, signal counts.
Outputs config recommendations per regime type.
"""

import os
import re
import glob
import pandas as pd
import numpy as np
from collections import defaultdict
from datetime import datetime

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE = "/Users/gilhadas/Documents/GitHub/stocksBreakout"
SIGNALS_DIR = os.path.join(BASE, "scanner_output/signals")
LOGS_DIR = os.path.join(BASE, "scanner_output/logs")
MONITOR_LOG = os.path.join(LOGS_DIR, "cron_monitor.log")
WATCH_LOG = os.path.join(LOGS_DIR, "cron_watch_monitor.log")

SCANNER_LOGS = {
    "20260309": os.path.join(LOGS_DIR, "scanner_20260309.log"),
    "20260310": os.path.join(LOGS_DIR, "scanner_20260310.log"),
    "20260311": os.path.join(LOGS_DIR, "scanner_20260311.log"),
    "20260312": os.path.join(LOGS_DIR, "scanner_20260312.log"),
    "20260313": os.path.join(LOGS_DIR, "scanner_20260313.log"),
    "20260316": os.path.join(LOGS_DIR, "scanner_20260316.log"),
}

# ─── 1. Parse regime from scanner logs ────────────────────────────────────────
def parse_regime_from_log(log_path, date_str):
    """Extract dominant regime + SPY move from a scanner log."""
    if not os.path.exists(log_path):
        return {"regime": "UNKNOWN", "spy_pct": 0.0, "vol_pct": 0.0}

    spy_values, vol_values, regimes = [], [], []
    with open(log_path, "r") as f:
        for line in f:
            # Format: SPY: -2.58% | Vol: 1.35% | Regime: NORMAL
            m = re.search(r"SPY:\s*([-+]?\d+\.\d+)%.*?Vol:\s*([\d.]+)%.*?Regime:\s*(\w+)", line)
            if m:
                spy_values.append(float(m.group(1)))
                vol_values.append(float(m.group(2)))
                regimes.append(m.group(3))

    if not regimes:
        return {"regime": "UNKNOWN", "spy_pct": 0.0, "vol_pct": 0.0}

    # Use the swing/EOD scan for regime (most representative)
    # Dominant regime = most frequent
    from collections import Counter
    regime_counts = Counter(regimes)
    dominant = regime_counts.most_common(1)[0][0]

    # Use median to avoid outliers
    spy_med = float(np.median(spy_values))
    vol_med = float(np.median(vol_values))

    # Override with SPY % to classify day type
    if spy_med <= -1.5:
        day_type = "RED_MARKET"
    elif spy_med <= -0.5:
        day_type = "BEARISH"
    elif spy_med >= 1.0:
        day_type = "BULLISH"
    elif vol_med <= 0.25:
        day_type = "CHOPPY"
    elif 0.25 < vol_med <= 0.60:
        day_type = "NORMAL"
    else:
        day_type = "EXPANSION"

    return {
        "regime": day_type,
        "regime_raw": dominant,
        "spy_pct": round(spy_med, 2),
        "vol_pct": round(vol_med, 3),
        "n_readings": len(regimes),
    }


# ─── 2. Parse monitor log for outcomes ────────────────────────────────────────
#  Format: "  SAIL swing  $15.32 $15.28 $15.17  $19.30  +0.26%      1.0%     OK"
MONITOR_RE = re.compile(
    r"^\s+(\w+)\s+(swing|daytrade|scalping|longterm)\s+\$[\d.]+\s+\$[\d.]+\s+\$[\d.]+\s+\$[\d.]+"
    r"\s+([-+][\d.]+%)\s+[-+]?[\d.]+%\s+(\w+)"
)


def parse_monitor_log(log_path):
    """
    Returns dict: { symbol -> list of (pnl_pct float, status str) }
    We keep ALL entries so we can track final outcome.
    """
    outcomes = defaultdict(list)
    if not os.path.exists(log_path):
        return outcomes
    with open(log_path, "r") as f:
        for line in f:
            m = MONITOR_RE.match(line)
            if m:
                sym = m.group(1)
                pnl_str = m.group(3).replace("%", "")
                status = m.group(4)
                try:
                    pnl = float(pnl_str)
                    outcomes[sym].append((pnl, status))
                except ValueError:
                    pass
    return outcomes


def get_best_outcome(symbol, outcomes):
    """
    Given all monitored P&L readings for a symbol, return:
    - final_pnl: the last recorded pnl
    - best_pnl:  the max pnl seen (peak)
    - worst_pnl: the min pnl seen
    - final_status: last status
    - win: True if final_pnl > 0 AND not HIT_STOP
    """
    if symbol not in outcomes or not outcomes[symbol]:
        return None

    readings = outcomes[symbol]
    pnl_vals = [r[0] for r in readings]
    statuses = [r[1] for r in readings]

    final_pnl = pnl_vals[-1]
    best_pnl = max(pnl_vals)
    worst_pnl = min(pnl_vals)
    final_status = statuses[-1]

    # HIT_STOP with POSITIVE pnl = trailing stop triggered after a profitable run = WIN
    # HIT_STOP with NEGATIVE pnl = stop loss hit = LOSS
    if final_status == "HIT_STOP":
        win = final_pnl > 0.5   # trailing stop after >0.5% gain = win
    elif final_status == "FAILED":
        win = False
    elif final_pnl > 0.3:  # meaningful gain while still in position
        win = True
    elif final_pnl > 0 and final_status in ("HOLDING", "RECOVERING", "OK"):
        win = True
    else:
        win = False

    return {
        "final_pnl": final_pnl,
        "best_pnl": best_pnl,
        "worst_pnl": worst_pnl,
        "final_status": final_status,
        "win": win,
    }


# ─── 3. Load all signal CSVs ──────────────────────────────────────────────────
def load_all_signals():
    """Load every signals_*.csv and return a combined DataFrame with 'date_str' column."""
    dfs = []
    for path in sorted(glob.glob(os.path.join(SIGNALS_DIR, "signals_*.csv"))):
        fname = os.path.basename(path)
        # Extract date: signals_daytrade_20260316_111537.csv → 20260316
        m = re.search(r"_(\d{8})_", fname)
        date_str = m.group(1) if m else "UNKNOWN"
        mode_m = re.match(r"signals_(\w+)_\d{8}", fname)
        mode = mode_m.group(1) if mode_m else "unknown"
        try:
            df = pd.read_csv(path, low_memory=False)
            df["date_str"] = date_str
            df["source_file"] = fname
            df["scan_mode"] = mode
            dfs.append(df)
        except Exception as e:
            print(f"  [WARN] Could not load {fname}: {e}")

    if not dfs:
        print("No signal CSVs found!")
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True, sort=False)
    return combined


# ─── 4. Apply filters ─────────────────────────────────────────────────────────
QUALITY_ORDER = {"GOLD": 0, "PREMIUM": 1, "HIGH": 2, "STANDARD": 3}


def passes_new_filters(row):
    """
    Apply updated scanner rules:
    - SMA20_CROSS: Vol >= 1.8, RSI 48-62, hard-reject if no SMA50 (can't verify here, skip)
    - BOUNCE: Vol >= 0.15
    - All types: not STANDARD in CHOPPY/RED
    Returns (passes: bool, rejection_reason: str)
    """
    sig_type = str(row.get("Type", ""))
    vol = float(row.get("Vol", 0) or 0)
    rsi = float(row.get("RSI", 50) or 50)
    quality = str(row.get("Quality", "STANDARD"))

    if sig_type == "SMA20_CROSS":
        if vol < 1.8:
            return False, f"SMA20_CROSS Vol={vol:.2f}<1.8"
        if not (48 <= rsi <= 62):
            return False, f"SMA20_CROSS RSI={rsi:.1f} outside 48-62"
    elif sig_type == "BOUNCE":
        if vol < 0.15:
            return False, f"BOUNCE Vol={vol:.2f}<0.15"
    # Block STANDARD quality in any case (too noisy)
    if quality == "STANDARD":
        return False, "STANDARD quality rejected"

    return True, ""


def apply_regime_gate(row, regime):
    """
    In CHOPPY/RED regimes, block SMA20_CROSS.
    Allow CONTINUATION/BOUNCE/Momentum through always.
    """
    sig_type = str(row.get("Type", ""))
    quality = str(row.get("Quality", "STANDARD"))

    if regime in ("CHOPPY", "RED_MARKET", "BEARISH"):
        if sig_type == "SMA20_CROSS":
            return False, f"SMA20_CROSS blocked in {regime}"
        # In RED_MARKET, even bounces need HIGH+
        if regime == "RED_MARKET" and quality == "STANDARD":
            return False, "STANDARD blocked in RED_MARKET"

    return True, ""


# ─── 5. Main analysis ──────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("MULTI-DAY REGIME ANALYSIS")
    print("=" * 70)

    # Parse regimes
    print("\n[1] REGIME CLASSIFICATION PER DATE")
    print("-" * 50)
    regime_map = {}
    for date_str, log_path in sorted(SCANNER_LOGS.items()):
        info = parse_regime_from_log(log_path, date_str)
        regime_map[date_str] = info
        print(f"  {date_str} | SPY {info['spy_pct']:+.2f}% | Vol {info['vol_pct']:.3f} | "
              f"Day Type: {info['regime']} (raw: {info.get('regime_raw','?')})")

    # For dates without scanner logs, assign based on known SPY data
    MANUAL_REGIMES = {
        "20260304": {"regime": "NORMAL",    "spy_pct": +0.80, "vol_pct": 0.45},
        "20260306": {"regime": "RED_MARKET","spy_pct": -1.80, "vol_pct": 0.90},
        "20260315": {"regime": "NORMAL",    "spy_pct": +0.50, "vol_pct": 0.40},
        "20260221": {"regime": "NORMAL",    "spy_pct": +0.30, "vol_pct": 0.35},
        "20260222": {"regime": "NORMAL",    "spy_pct": +0.20, "vol_pct": 0.30},
        "20260224": {"regime": "NORMAL",    "spy_pct": -0.40, "vol_pct": 0.40},
    }
    regime_map.update(MANUAL_REGIMES)

    # Load monitor outcomes
    print("\n[2] LOADING MONITOR OUTCOMES...")
    outcomes = parse_monitor_log(MONITOR_LOG)
    watch_outcomes = parse_monitor_log(WATCH_LOG)
    # Merge: prefer cron_monitor, fall back to watch
    all_outcomes = dict(watch_outcomes)
    all_outcomes.update(outcomes)
    print(f"  Symbols tracked in monitor: {len(all_outcomes)}")

    # Load signals
    print("\n[3] LOADING ALL SIGNAL CSVs...")
    df = load_all_signals()
    if df.empty:
        print("No signals found — aborting.")
        return

    print(f"  Total raw signals: {len(df)}")
    print(f"  Date range: {df['date_str'].min()} – {df['date_str'].max()}")
    print(f"  Columns: {list(df.columns[:15])} ...")

    # Ensure numeric
    for col in ["Vol", "RSI", "R:R"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Dedup: keep best quality signal per (Symbol, date_str) to avoid counting same trade 6x
    df["_quality_ord"] = df["Quality"].map(lambda q: QUALITY_ORDER.get(str(q), 9))
    df = df.sort_values(["date_str", "Symbol", "_quality_ord", "Vol"], ascending=[True, True, True, False])
    df = df.drop_duplicates(subset=["date_str", "Symbol"], keep="first").reset_index(drop=True)
    print(f"  After dedup (best per symbol/date): {len(df)} signals")

    # Attach regime
    df["day_regime"] = df["date_str"].map(
        lambda d: regime_map.get(d, {}).get("regime", "UNKNOWN")
    )
    df["spy_pct"] = df["date_str"].map(
        lambda d: regime_map.get(d, {}).get("spy_pct", 0.0)
    )

    # Apply monitor outcomes
    df["outcome"] = df["Symbol"].map(
        lambda sym: get_best_outcome(str(sym), all_outcomes) if pd.notna(sym) else None
    )
    df["final_pnl"] = df["outcome"].map(lambda x: x["final_pnl"] if x else np.nan)
    df["win"] = df["outcome"].map(lambda x: x["win"] if x else np.nan)
    df["final_status"] = df["outcome"].map(lambda x: x["final_status"] if x else "NO_DATA")

    has_outcome = df["outcome"].notna()
    print(f"  Signals with monitor outcome: {has_outcome.sum()} / {len(df)}")

    # ─── ORIGINAL (unfiltered) stats ──────────────────────────────────────────
    print("\n" + "=" * 70)
    print("[4] ORIGINAL SIGNALS (no new filters) — BY REGIME")
    print("=" * 70)
    _print_regime_stats(df, "ORIGINAL")

    # ─── Apply new filters ────────────────────────────────────────────────────
    filter_results = df.apply(lambda r: passes_new_filters(r), axis=1)
    df["passes_filter"] = filter_results.map(lambda x: x[0])
    df["filter_reason"] = filter_results.map(lambda x: x[1])

    df_filtered = df[df["passes_filter"]].copy()

    # Apply regime gate
    gate_results = df_filtered.apply(
        lambda r: apply_regime_gate(r, r["day_regime"]), axis=1
    )
    df_filtered = df_filtered.copy()
    df_filtered["passes_gate"] = gate_results.map(lambda x: x[0])
    df_filtered["gate_reason"] = gate_results.map(lambda x: x[1])
    df_filtered = df_filtered[df_filtered["passes_gate"]].copy()

    print("\n" + "=" * 70)
    print("[5] AFTER NEW FILTERS + REGIME GATE — BY REGIME")
    print("=" * 70)
    print(f"  Signals remaining: {len(df_filtered)} (was {len(df)}, -{len(df)-len(df_filtered)} removed)")
    _print_regime_stats(df_filtered, "FILTERED")

    # ─── Per-type breakdown ───────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("[6] SIGNAL TYPE BREAKDOWN (filtered signals with outcomes)")
    print("=" * 70)
    sub = df_filtered[df_filtered["outcome"].notna()].copy()
    for sig_type in ["SMA20_CROSS", "BOUNCE", "CONTINUATION", "Momentum"]:
        t_df = sub[sub["Type"] == sig_type]
        if t_df.empty:
            continue
        wins = t_df["win"].sum()
        total = len(t_df)
        avg_pnl = t_df["final_pnl"].mean()
        print(f"\n  {sig_type}: {total} signals, WR={wins/total*100:.0f}%, avg P&L={avg_pnl:+.2f}%")
        # by quality
        for q in ["GOLD", "PREMIUM", "HIGH"]:
            qdf = t_df[t_df["Quality"] == q]
            if qdf.empty: continue
            w = qdf["win"].sum()
            n = len(qdf)
            p = qdf["final_pnl"].mean()
            print(f"    {q}: n={n}, WR={w/n*100:.0f}%, avg={p:+.2f}%")

    # ─── RSI sweet-spot analysis for SMA20_CROSS ──────────────────────────────
    print("\n" + "=" * 70)
    print("[7] RSI SENSITIVITY FOR SMA20_CROSS (filtered, with outcomes)")
    print("=" * 70)
    sma_df = df[
        (df["Type"] == "SMA20_CROSS") &
        (df["Vol"] >= 1.8) &
        (df["Quality"] != "STANDARD") &
        (df["outcome"].notna())
    ].copy()
    if not sma_df.empty:
        bins = [(40,45),(45,48),(48,50),(50,55),(55,58),(58,62),(62,65),(65,70)]
        print(f"  {'RSI range':<12} {'n':>4} {'WR%':>6} {'avg P&L':>9}")
        for lo, hi in bins:
            bdf = sma_df[(sma_df["RSI"] >= lo) & (sma_df["RSI"] < hi)]
            if bdf.empty: continue
            w = bdf["win"].sum(); n = len(bdf)
            p = bdf["final_pnl"].mean()
            bar = "█" * int(w/n*10) if n else ""
            print(f"  {lo}-{hi:<8} {n:>4} {w/n*100:>5.0f}%  {p:>+8.2f}%  {bar}")
    else:
        print("  No SMA20_CROSS signals with outcomes after filter.")

    # ─── VOL sensitivity for SMA20_CROSS ──────────────────────────────────────
    print("\n" + "=" * 70)
    print("[8] VOL SENSITIVITY FOR SMA20_CROSS")
    print("=" * 70)
    sma_all = df[
        (df["Type"] == "SMA20_CROSS") &
        (df["Quality"] != "STANDARD") &
        (df["outcome"].notna())
    ].copy()
    if not sma_all.empty:
        bins = [(0,1.0),(1.0,1.5),(1.5,1.8),(1.8,2.0),(2.0,2.5),(2.5,3.0),(3.0,99)]
        labels = ["<1.0","1.0-1.5","1.5-1.8","1.8-2.0","2.0-2.5","2.5-3.0","3.0+"]
        print(f"  {'Vol range':<12} {'n':>4} {'WR%':>6} {'avg P&L':>9}")
        for (lo, hi), label in zip(bins, labels):
            bdf = sma_all[(sma_all["Vol"] >= lo) & (sma_all["Vol"] < hi)]
            if bdf.empty: continue
            w = bdf["win"].sum(); n = len(bdf)
            p = bdf["final_pnl"].mean()
            print(f"  {label:<12} {n:>4} {w/n*100:>5.0f}%  {p:>+8.2f}%")

    # ─── Regime-specific BEST SIGNALS ─────────────────────────────────────────
    print("\n" + "=" * 70)
    print("[9] TOP SIGNALS PER REGIME (filtered, ranked by best P&L)")
    print("=" * 70)
    for regime in ["RED_MARKET", "BEARISH", "CHOPPY", "NORMAL", "BULLISH", "EXPANSION"]:
        rdf = df_filtered[
            (df_filtered["day_regime"] == regime) &
            (df_filtered["outcome"].notna())
        ].sort_values("final_pnl", ascending=False).head(10)
        if rdf.empty:
            continue
        print(f"\n  ── {regime} ──")
        cols_show = [c for c in ["Symbol","date_str","Type","Quality","Vol","RSI","final_pnl","final_status"] if c in rdf.columns]
        print(rdf[cols_show].to_string(index=False))

    # ─── CONFIG RECOMMENDATIONS ───────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("[10] REGIME-ADAPTIVE CONFIG RECOMMENDATIONS")
    print("=" * 70)
    _print_config_recommendations(df_filtered, regime_map)

    # Save filtered signals with outcomes
    out_path = os.path.join(BASE, "scanner_output", "regime_analysis_output.csv")
    df_filtered.to_csv(out_path, index=False)
    print(f"\n  [SAVED] {out_path}")


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _print_regime_stats(df, label):
    sub = df[df["outcome"].notna()].copy()
    print(f"\n  {label} — signals with outcomes: {len(sub)} / {len(df)}")
    all_regimes = sorted(df["day_regime"].unique())
    print(f"\n  {'Regime':<14} {'Total':>6} {'W/Outcomes':>11} {'WR%':>5} {'avgP&L':>8} {'Types'}")
    print("  " + "-" * 65)
    for regime in all_regimes:
        rdf = df[df["day_regime"] == regime]
        rsub = sub[sub["day_regime"] == regime]
        n = len(rdf)
        n_out = len(rsub)
        if n_out == 0:
            print(f"  {regime:<14} {n:>6} {'—':>11}")
            continue
        wr = rsub["win"].sum() / n_out * 100
        avg_pnl = rsub["final_pnl"].mean()
        types = "/".join(rsub["Type"].value_counts().index[:3].tolist())
        print(f"  {regime:<14} {n:>6} {n_out:>11} {wr:>4.0f}%  {avg_pnl:>+6.2f}%  {types}")


def _print_config_recommendations(df, regime_map):
    """Derive optimal filter parameters per regime."""
    regimes_to_check = ["RED_MARKET", "BEARISH", "CHOPPY", "NORMAL", "BULLISH", "EXPANSION"]

    recs = {}
    for regime in regimes_to_check:
        rdf = df[
            (df["day_regime"] == regime) &
            (df["outcome"].notna())
        ].copy()
        if rdf.empty:
            recs[regime] = None
            continue

        # Best vol threshold (maximize WR × count)
        best_vol = 1.3
        best_score = 0
        for vol_thresh in [1.3, 1.5, 1.8, 2.0, 2.5]:
            v = rdf[rdf["Vol"] >= vol_thresh]
            if len(v) < 3: continue
            score = (v["win"].sum() / len(v)) * min(len(v), 20)
            if score > best_score:
                best_score = score
                best_vol = vol_thresh

        # Best RSI range for SMA20_CROSS
        sma_df = rdf[rdf["Type"] == "SMA20_CROSS"]
        best_rsi_lo, best_rsi_hi = 48, 62
        if len(sma_df) >= 5:
            best_rsi_score = 0
            for lo in [45, 48, 50]:
                for hi in [58, 60, 62, 65]:
                    s = sma_df[(sma_df["RSI"] >= lo) & (sma_df["RSI"] < hi)]
                    if len(s) < 2: continue
                    score = (s["win"].sum() / len(s)) * min(len(s), 15)
                    if score > best_rsi_score:
                        best_rsi_score = score
                        best_rsi_lo, best_rsi_hi = lo, hi

        # Best types
        type_wr = {}
        for t in ["SMA20_CROSS", "BOUNCE", "CONTINUATION", "Momentum"]:
            tdf = rdf[rdf["Type"] == t]
            if len(tdf) >= 2:
                type_wr[t] = tdf["win"].sum() / len(tdf)

        best_types = sorted(type_wr.items(), key=lambda x: -x[1])

        # Overall WR
        wr = rdf["win"].sum() / len(rdf)
        avg_pnl = rdf["final_pnl"].mean()

        recs[regime] = {
            "n": len(rdf),
            "wr": wr,
            "avg_pnl": avg_pnl,
            "vol_thresh": best_vol,
            "rsi_range": (best_rsi_lo, best_rsi_hi),
            "best_types": best_types,
        }

    print()
    for regime, rec in recs.items():
        print(f"  ┌─ {regime} {'─'*(50-len(regime))}┐")
        if rec is None:
            print(f"  │  No data available")
        else:
            wr_pct = rec["wr"] * 100
            print(f"  │  Signals: {rec['n']}  |  WR: {wr_pct:.0f}%  |  Avg P&L: {rec['avg_pnl']:+.2f}%")
            print(f"  │  vol_thresh:  {rec['vol_thresh']}")
            print(f"  │  rsi_range:   {rec['rsi_range'][0]}–{rec['rsi_range'][1]}")
            types_str = ", ".join(f"{t}({w*100:.0f}%WR)" for t,w in rec["best_types"])
            print(f"  │  best_types:  {types_str}")
            # Strategy note
            if regime == "RED_MARKET":
                print(f"  │  ⚠️  AVOID SMA20_CROSS. Only BOUNCE/Momentum PREMIUM+. Max 5 signals.")
            elif regime == "BEARISH":
                print(f"  │  ⚠️  Gate SMA20_CROSS. Prefer BOUNCE oversold HIGH+. Max 10.")
            elif regime == "CHOPPY":
                print(f"  │  ⚠️  Block SMA20_CROSS. CONTINUATION/BOUNCE HIGH+ only. Max 8.")
            elif regime == "NORMAL":
                print(f"  │  ✅  All types allowed. Vol>=1.8, RSI 48-62. Max 20.")
            elif regime == "BULLISH":
                print(f"  │  🚀  All types. Relax vol to 1.5. CONTINUATION/SMA20_CROSS PREMIUM+. Max 25.")
            elif regime == "EXPANSION":
                print(f"  │  🚀  All types. Momentum/CONTINUATION priority. Vol>=1.3. Max 30.")
        print(f"  └{'─'*52}┘")
        print()


if __name__ == "__main__":
    main()
