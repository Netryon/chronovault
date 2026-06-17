#!/bin/bash
# ChronoVault Stack Guard — heal missing/unhealthy compose stacks
set -euo pipefail

LOG_FILE="/var/log/chronovault/stack-guard.log"
STATE_FAIL="/var/lib/chronovault/state/stack-guard-failed.json"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

container_running() {
  docker ps --format '{{.Names}}' | grep -qx "$1"
}

write_fail() {
  mkdir -p "$(dirname "${STATE_FAIL}")"
  printf '%s\n' "$1" > "${STATE_FAIL}"
}

clear_fail() {
  rm -f "${STATE_FAIL}"
}

heal_stack() {
  local stack="$1"
  local dir="/opt/chronovault/compose/${stack}"
  log "HEAL: attempting compose up for ${stack}"
  (cd "$dir" && docker compose up -d) || return 1
  return 0
}

check_nextcloud() {
  if container_running "nextcloud-postgres" && curl -sf http://127.0.0.1:8080/status.php >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

check_immich() {
  if container_running "immich-postgres" && \
     { curl -sf http://127.0.0.1:2283/api/server/ping >/dev/null 2>&1 || \
       curl -sf http://127.0.0.1:2283/api/server-info/ping >/dev/null 2>&1; }; then
    return 0
  fi
  return 1
}

mkdir -p /var/log/chronovault /var/lib/chronovault/state
log "Stack guard run started"
FAILURES=()

if ! check_nextcloud; then
  log "WARN: nextcloud unhealthy"
  heal_stack nextcloud || true
  sleep 20
  if ! check_nextcloud; then
    FAILURES+=("nextcloud")
    log "ERROR: nextcloud still unhealthy after heal"
  else
    log "OK: nextcloud healed"
  fi
fi

if ! check_immich; then
  log "WARN: immich unhealthy"
  heal_stack immich || true
  sleep 25
  if ! check_immich; then
    FAILURES+=("immich")
    log "ERROR: immich still unhealthy after heal"
  else
    log "OK: immich healed"
  fi
fi

if [ "${#FAILURES[@]}" -gt 0 ]; then
  write_fail "$(printf '{"failed":%s,"time":"%s"}' "$(printf '%s\n' "${FAILURES[@]}" | jq -R . | jq -s .)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)")"
  log "Stack guard finished WITH FAILURES: ${FAILURES[*]}"
  exit 1
fi

clear_fail
log "Stack guard finished OK"
