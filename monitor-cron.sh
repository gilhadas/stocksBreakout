#!/bin/bash

# Monitor script for cron jobs
# Shows recent executions and any errors

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOG_DIR="$SCRIPT_DIR/scanner_output/logs"

echo "=================================="
echo "Breakout Scanner - Cron Monitor"
echo "=================================="
echo ""

# Check if log directory exists
if [ ! -d "$LOG_DIR" ]; then
    echo "❌ Log directory not found: $LOG_DIR"
    echo "Make sure scanner has run at least once."
    exit 1
fi

# Function to show last execution
show_last_execution() {
    local log_file=$1
    local job_name=$2
    
    if [ -f "$log_file" ]; then
        echo "📊 $job_name"
        echo "   Last run: $(date -r "$log_file" '+%Y-%m-%d %H:%M:%S')"
        
        # Check for errors
        error_count=$(grep -c "ERROR\|Failed\|Exception" "$log_file" 2>/dev/null || echo "0")
        if [ "$error_count" -gt 0 ]; then
            echo "   ❌ Errors found: $error_count"
            echo "   Last error:"
            grep "ERROR\|Failed\|Exception" "$log_file" | tail -1 | sed 's/^/      /'
        else
            echo "   ✓ No errors"
        fi
        
        # Show signals found
        signal_count=$(grep -c "SIGNALS FOUND:" "$log_file" 2>/dev/null || echo "0")
        if [ "$signal_count" -gt 0 ]; then
            signals=$(grep "SIGNALS FOUND:" "$log_file" | tail -1 | sed 's/.*FOUND: //')
            echo "   📈 Signals: $signals"
        fi
        
        echo ""
    else
        echo "⚪ $job_name: Never executed"
        echo ""
    fi
}

# Show status of common cron jobs
echo "Recent Cron Job Executions:"
echo "─────────────────────────────────────"
echo ""

show_last_execution "$LOG_DIR/cron_longterm.log" "Long-term Scan"
show_last_execution "$LOG_DIR/cron_longterm_exit.log" "Long-term Exit"
show_last_execution "$LOG_DIR/cron_swing.log" "Swing Scan"
show_last_execution "$LOG_DIR/cron_swing_exit.log" "Swing Exit"
show_last_execution "$LOG_DIR/cron_swing_combined.log" "Swing Combined"
show_last_execution "$LOG_DIR/cron_daytrade_morning.log" "Daytrade Morning"
show_last_execution "$LOG_DIR/cron_daytrade_exit.log" "Daytrade Exit"

# Show recent main scanner logs
echo "─────────────────────────────────────"
echo ""
echo "Recent Scanner Executions (non-cron):"
echo ""

scanner_logs=$(find "$LOG_DIR" -name "scanner_*.log" -type f -mtime -7 | sort -r | head -5)

if [ -n "$scanner_logs" ]; then
    for log in $scanner_logs; do
        filename=$(basename "$log")
        echo "📝 $filename ($(date -r "$log" '+%Y-%m-%d %H:%M'))"
        
        # Quick stats
        signals=$(grep -c "SIGNALS FOUND:" "$log" 2>/dev/null || echo "0")
        errors=$(grep -c "ERROR" "$log" 2>/dev/null || echo "0")
        
        if [ "$signals" -gt 0 ]; then
            echo "   Executions with signals: $signals"
        fi
        if [ "$errors" -gt 0 ]; then
            echo "   ❌ Errors: $errors"
        fi
        echo ""
    done
else
    echo "No recent scanner logs found."
fi

# Disk usage
echo "─────────────────────────────────────"
echo ""
echo "Storage Usage:"
du -sh "$SCRIPT_DIR/scanner_output"/* 2>/dev/null | sed 's/^/  /'

echo ""
echo "─────────────────────────────────────"
echo ""
echo "Commands:"
echo "  View specific log: tail -f $LOG_DIR/cron_swing.log"
echo "  View all errors:   grep -r ERROR $LOG_DIR/"
echo "  Clean old logs:    find $LOG_DIR -name '*.log' -mtime +30 -delete"
echo ""
