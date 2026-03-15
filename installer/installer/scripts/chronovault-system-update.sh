#!/bin/bash
# Chronovault System Update Script
# Updates system packages and Docker

set -euo pipefail

LOG_FILE="/var/log/chronovault/system-update.log"
DATE=$(date '+%Y-%m-%d %H:%M:%S')

log() {
    echo "[$DATE] $*" | tee -a "${LOG_FILE}"
}

log "System update started"

# Update package lists
log "Updating package lists..."
apt-get update -qq

# Upgrade packages (non-interactive)
log "Upgrading packages..."
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq

# Update Docker if installed
if command -v docker &> /dev/null; then
    log "Updating Docker..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --only-upgrade docker.io docker-compose 2>/dev/null || true
fi

# Clean up old packages
log "Cleaning up old packages..."
apt-get autoremove -y -qq
apt-get autoclean -qq

log "System update completed successfully"
