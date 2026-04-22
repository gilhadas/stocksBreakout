#!/bin/bash
# restart_all.sh — Restart all long-running daemons
#
# What this restarts:
#   cron_agent        — job scheduler (always restart when cron_jobs.txt changes)
#   signal_surge_monitor — real-time surge/drop alert bot (restart when its code changes)
#   api/server.py     — FastAPI mobile server (launchd auto-restarts; kill triggers it)
#
# Usage:
#   ./restart_all.sh          # restart everything
#   ./restart_all.sh cron     # cron_agent only
#   ./restart_all.sh surge    # surge monitor only
#   ./restart_all.sh api      # API server only

set -e
cd "$(dirname "$0")"

PYTHON=venv/bin/python3
LOG_DIR=scanner_output/logs
DATE=$(TZ=America/New_York date +%Y%m%d)
NOW=$(TZ=America/New_York date '+%H:%M ET')

green()  { echo -e "\033[0;32m$*\033[0m"; }
yellow() { echo -e "\033[0;33m$*\033[0m"; }

restart_cron() {
    yellow "→ Restarting cron_agent..."
    pkill -f "cron_agent.py" 2>/dev/null && sleep 1 || true
    nohup caffeinate -i $PYTHON cron_agent.py --daemon \
        > $LOG_DIR/cron_agent.log 2>&1 &
    sleep 2
    if pgrep -f "cron_agent.py" > /dev/null; then
        green "  ✓ cron_agent running (PID $(pgrep -f 'cron_agent.py' | head -1))"
    else
        echo "  ✗ cron_agent failed to start — check $LOG_DIR/cron_agent.log"
    fi
}

restart_surge() {
    yellow "→ Restarting signal_surge_monitor..."
    pkill -f "signal_surge_monitor.py" 2>/dev/null && sleep 1 || true

    HOUR=$(TZ=America/New_York date +%H)
    MINUTE=$(TZ=America/New_York date +%M)
    TOTAL_MIN=$(( 10#$HOUR * 60 + 10#$MINUTE ))
    # 8:00 AM = 480 min, 4:15 PM = 975 min
    if [ "$TOTAL_MIN" -ge 480 ] && [ "$TOTAL_MIN" -lt 975 ]; then
        nohup caffeinate -i $PYTHON signal_surge_monitor.py --notify \
            >> $LOG_DIR/cron_surge_monitor_${DATE}.log 2>&1 &
        sleep 2
        if pgrep -f "signal_surge_monitor.py" > /dev/null; then
            green "  ✓ signal_surge_monitor running (PID $(pgrep -f 'signal_surge_monitor.py' | head -1))"
        else
            echo "  ✗ signal_surge_monitor failed — check $LOG_DIR/cron_surge_monitor_${DATE}.log"
        fi
    else
        yellow "  ⏭ Outside trading hours ($NOW) — surge monitor not started (cron starts it at 8 AM)"
    fi
}

restart_api() {
    yellow "→ Restarting API server (launchd)..."
    kill $(lsof -ti:8000) 2>/dev/null || true
    sleep 3
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8000/portfolio/execute-swap \
        -H "Content-Type: application/json" -d '{"close_symbol":"X","open_symbol":"Y"}' 2>/dev/null)
    if [ "$STATUS" = "401" ] || [ "$STATUS" = "422" ]; then
        green "  ✓ API server up (HTTP $STATUS)"
    else
        echo "  ✗ API server not responding (HTTP $STATUS) — check scanner_output/api_server.err"
    fi
}

TARGET=${1:-all}

echo "=== restart_all.sh @ $NOW ==="

case "$TARGET" in
    cron)  restart_cron ;;
    surge) restart_surge ;;
    api)   restart_api ;;
    all)
        restart_cron
        restart_surge
        restart_api
        ;;
    *)
        echo "Usage: $0 [cron|surge|api|all]"
        exit 1
        ;;
esac

green "Done."
