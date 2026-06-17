#!/bin/bash
# ChronoVault Safe Container Updater — compose-stack pull + up (not per-container)
set -euo pipefail

LOG_FILE="/var/log/chronovault/container-update.log"
COMPOSE_ROOT="/opt/chronovault/compose"
STATE_FAIL="/var/lib/chronovault/state/container-update-failed.json"
STACK_ORDER=(nextcloud immich twingate control duckdns)
DRY_RUN=0
ONLY_STACK=""

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

write_fail() {
  local stack="$1" msg="$2"
  mkdir -p "$(dirname "${STATE_FAIL}")"
  printf '{"stack":"%s","time":"%s","message":"%s"}\n' "$stack" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$msg" > "${STATE_FAIL}"
}

clear_fail() {
  rm -f "${STATE_FAIL}"
}

usage() {
  echo "Usage: $0 [--dry-run] [--stack NAME]"
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --stack) ONLY_STACK="${2:-}"; [ -n "$ONLY_STACK" ] || usage; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1"; usage ;;
  esac
done

verify_nextcloud() {
  docker exec nextcloud-postgres pg_isready -U nextcloud -d nextcloud >/dev/null
  curl -sf http://127.0.0.1:8080/status.php >/dev/null
}

verify_immich() {
  curl -sf http://127.0.0.1:2283/api/server/ping >/dev/null 2>&1 || \
    curl -sf http://127.0.0.1:2283/api/server-info/ping >/dev/null
}

update_stack() {
  local name="$1"
  local dir="${COMPOSE_ROOT}/${name}"
  if [ ! -d "$dir" ] || [ ! -f "${dir}/docker-compose.yml" ]; then
    log "SKIP ${name}: no compose dir"
    return 0
  fi
  log "=== Stack: ${name} ==="
  if [ "$DRY_RUN" -eq 1 ]; then
    log "DRY-RUN: would run docker compose pull && docker compose up -d in ${dir}"
    return 0
  fi
  if ! (cd "$dir" && docker compose pull && docker compose up -d); then
    write_fail "$name" "compose pull/up failed"
    log "ERROR: stack ${name} update failed"
    return 1
  fi
  case "$name" in
    nextcloud)
      sleep 10
      verify_nextcloud || { write_fail "$name" "health check failed after update"; return 1; }
      ;;
    immich)
      sleep 15
      verify_immich || { write_fail "$name" "health check failed after update"; return 1; }
      ;;
    *)
      if ! (cd "$dir" && docker compose ps --status running | grep -q .); then
        write_fail "$name" "no running services after update"
        return 1
      fi
      ;;
  esac
  log "OK: ${name}"
  return 0
}

mkdir -p /var/log/chronovault /var/lib/chronovault/state
log "Container update started (dry_run=${DRY_RUN}, only_stack=${ONLY_STACK:-ALL})"

if [ -n "$ONLY_STACK" ]; then
  update_stack "$ONLY_STACK" || exit 1
else
  for s in "${STACK_ORDER[@]}"; do
    update_stack "$s" || exit 1
  done
fi

clear_fail
log "Container update completed successfully"
