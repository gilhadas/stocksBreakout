#!/usr/bin/env bash
# Ablation experiment: Run A (cap=10) + Run B (cap=2) + Run D (selective+cap=2)
# All three emit in a single pass — one data fetch, one scan, three simulate() calls.
#
# Output: scanner_output/backtest_ablation_YYYYMMDD_HHMMSS.log
#
# Rows emitted:
#   NEW PREMIUM+ pooled-cap=10 ★       → Run A (champion baseline)
#   NEW PREMIUM+ pooled-cap=2  ★★      → Run B (cap only)
#   SELECTIVE NEW pooled-cap=2          → Run D (selective + cap=2)
#
# Hold-duration split (≤15d vs >15d WR%) is printed under each of the three rows.
# Multi-year Sharpe summary is printed at the end of the run.
#
# Uses --limit 200 --shuffle --seed 42 to match the documented champion runs
# (200 reproducible symbols from all.txt, ~20 min). Pass --limit 0 to use all
# 1375 symbols (~2-3 hrs). Pass --limit N to override.
#
# Add --full-compare to also run retired V9-H / V9-H2 / V9-H3 / V9-D sections.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="venv/bin/python3"
LOGDIR="scanner_output"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOGFILE="${LOGDIR}/backtest_ablation_${TIMESTAMP}.log"

mkdir -p "$LOGDIR"

echo "=== Ablation run: A + B + D ==="
echo "Log: $LOGFILE"
echo ""

caffeinate -i "$PYTHON" backtest_regime_compare.py \
  --watchlist input/all.txt \
  --no-tc \
  --bounce-bear-gate 15 \
  --shuffle --seed 42 \
  --limit 200 \
  --trades-log \
  "$@" \
  2>&1 | tee "$LOGFILE"

echo ""
echo "=== Done. Log saved to $LOGFILE ==="
