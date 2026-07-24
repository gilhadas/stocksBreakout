#!/bin/bash
# Install/uninstall the research agent schedules.
#   ./install.sh install    ./install.sh uninstall    ./install.sh status
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
LA="$HOME/Library/LaunchAgents"
JOBS=(com.stocksbreakout.research-runner com.stocksbreakout.research-lead)

case "${1:-status}" in
  install)
    mkdir -p "$LA"
    for j in "${JOBS[@]}"; do
      cp "$DIR/$j.plist" "$LA/$j.plist"
      launchctl bootout "gui/$(id -u)/$j" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$LA/$j.plist"
      echo "installed $j"
    done
    echo "NOTE: agents now run unattended. They may commit to research/auto-agents."
    echo "      Budget cap lives in research/ledger/budget.json."
    ;;
  uninstall)
    for j in "${JOBS[@]}"; do
      launchctl bootout "gui/$(id -u)/$j" 2>/dev/null || true
      rm -f "$LA/$j.plist"; echo "removed $j"
    done
    ;;
  status)
    for j in "${JOBS[@]}"; do
      printf '%-45s ' "$j"
      launchctl print "gui/$(id -u)/$j" >/dev/null 2>&1 && echo LOADED || echo "not loaded"
    done
    ;;
  *) echo "usage: $0 {install|uninstall|status}"; exit 1;;
esac
