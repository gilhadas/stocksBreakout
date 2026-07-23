#!/usr/bin/env bash
# setup-swap.sh — add a swapfile to the deployment VM. Idempotent; safe to re-run.
#
# Why this exists
# ---------------
# The t3.small has 2 GB of RAM and, by default, no swap at all. On 2026-07-22 the
# 16:30 ET swing scan (which loads FinBERT — ~440 MB of weights plus torch's
# runtime) pushed the box past physical RAM. With no swap and no per-container
# memory limits, the host OOM killer fired immediately and took out the network
# daemons: CPU kept burning at 12-50% while NetworkOut sat at exactly 0 bytes for
# seven hours, with Tailscale offline and the tunnel serving 530s. Recovery
# needed a console reboot because the security group has no inbound rules.
#
# Swap does not make the box fast — swapping to gp3 is slow — but it converts a
# hard OOM kill into a slow scan, which is a far better failure mode for a
# machine whose job is to manage live positions. Pair it with the per-container
# mem_limit values in compose.yaml: the limits decide *who* dies, the swap makes
# it much less likely that anyone has to.
#
# Usage (on the VM):
#   sudo bash deploy/setup-swap.sh          # default 4G
#   sudo bash deploy/setup-swap.sh 2G       # explicit size
#
# Verify afterwards:  free -h   &&   swapon --show
set -euo pipefail

SWAPSIZE="${1:-4G}"
SWAPFILE="/swapfile"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run with sudo — this changes system memory configuration." >&2
    exit 1
fi

if swapon --show --noheadings 2>/dev/null | grep -q .; then
    echo "Swap is already active:"
    swapon --show
    echo
    echo "Nothing to do. Delete ${SWAPFILE} and re-run if you want to resize."
    exit 0
fi

# gp3 root volume is 30 GB and has already hit "No space left on device" once
# (see CLAUDE.md section 9) — refuse rather than fill it.
AVAIL_MB=$(df --output=avail -m / | tail -1 | tr -d ' ')
NEED_MB=$(numfmt --from=iec "${SWAPSIZE}" | awk '{print int($1/1024/1024)}')
if (( AVAIL_MB < NEED_MB + 2048 )); then
    echo "ERROR: only ${AVAIL_MB} MB free on / — need ${NEED_MB} MB for swap plus" >&2
    echo "       a 2 GB safety margin. Free space first (docker system prune -af)." >&2
    exit 1
fi

echo "Creating ${SWAPSIZE} swapfile at ${SWAPFILE}..."
fallocate -l "${SWAPSIZE}" "${SWAPFILE}" || \
    dd if=/dev/zero of="${SWAPFILE}" bs=1M count="${NEED_MB}" status=progress
chmod 600 "${SWAPFILE}"
mkswap "${SWAPFILE}"
swapon "${SWAPFILE}"

# Survive reboots.
if ! grep -q "^${SWAPFILE}" /etc/fstab; then
    echo "${SWAPFILE} none swap sw 0 0" >> /etc/fstab
    echo "Added ${SWAPFILE} to /etc/fstab."
fi

# Default vm.swappiness=60 swaps too eagerly for a box that is mostly idle and
# then briefly very busy. 10 keeps things in RAM until there is real pressure.
sysctl -w vm.swappiness=10
if ! grep -q "^vm.swappiness" /etc/sysctl.conf; then
    echo "vm.swappiness=10" >> /etc/sysctl.conf
fi

echo
echo "Done. Current memory:"
free -h
swapon --show
