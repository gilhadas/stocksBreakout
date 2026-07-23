#!/usr/bin/env bash
# disk-alert.sh — host-level disk usage check, reports to healthchecks.io.
#
# Why a host script, not a docker/crontab line: this checks the VM's root
# filesystem, not anything a container-scoped concern. Runs from the host even
# if every container is down (e.g. a repeat of the 2026-07-23 outage) or if
# what's filling the disk is Docker's OWN build cache, which lives outside any
# container's writable layer and isn't visible to app-level tooling.
#
# Reuses healthchecks.io rather than the CloudWatch Agent so no instance-role
# IAM changes are needed and it shares alert channels already configured
# there (see CLAUDE.md section 9, 2026-07-23 reliability pass).
#
# Install (crontab -e as the ubuntu user, runs hourly):
#   0 * * * * HC_UUID_DISK=<uuid> /home/ubuntu/stocksBreakout/deploy/disk-alert.sh >> /home/ubuntu/disk-alert.log 2>&1
set -euo pipefail

THRESHOLD="${DISK_ALERT_THRESHOLD:-80}"
UUID="${HC_UUID_DISK:-}"

USAGE=$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')
TIMESTAMP=$(date -Iseconds)

if [[ -z "$UUID" ]]; then
    echo "$TIMESTAMP disk=${USAGE}% — HC_UUID_DISK not set, skipping ping" >&2
    exit 0
fi

if (( USAGE >= THRESHOLD )); then
    echo "$TIMESTAMP disk=${USAGE}% >= ${THRESHOLD}% — reporting FAIL"
    curl -fsS -m 10 --retry 3 "https://hc-ping.com/${UUID}/fail" \
        --data-raw "disk usage ${USAGE}% (threshold ${THRESHOLD}%) — $(df -h / | tail -1)" \
        > /dev/null 2>&1 || true
else
    echo "$TIMESTAMP disk=${USAGE}% — OK"
    curl -fsS -m 10 --retry 3 "https://hc-ping.com/${UUID}" > /dev/null 2>&1 || true
fi
