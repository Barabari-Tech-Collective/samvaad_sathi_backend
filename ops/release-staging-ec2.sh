#!/usr/bin/env bash
# Staging release for the Samvaad backend on its EC2 host (D-021: everything runs on AWS).
#
#   bash ops/release-staging-ec2.sh              release the tip of origin/staging
#   bash ops/release-staging-ec2.sh <git-sha>    release that commit
#   bash ops/release-staging-ec2.sh --rollback   go back to the last good release
#
# 1. checks backend/.env     2. backs up Postgres (pg_dump)   3. checks out the commit
# 4. installs requirements   5. alembic upgrade head          6. restarts the samvaad services
# 7. health check. On a failure after step 3 it checks out the previous commit, reinstalls and
# restarts. Migrations are not undone automatically; restore the step-2 backup if needed.
#
# Run it on the server as the app user (ubuntu). It needs sudo only for systemctl restart.
set -euo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/apps/samvaad_sathi_backend}"
VENV="${VENV:-$APP_DIR/venv}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/samvaad}"
STATE_DIR="${STATE_DIR:-$APP_DIR/.release}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/health}"
CODE_DIR="$APP_DIR/backend/backend"
ENV_FILE="$APP_DIR/backend/.env"
log() { echo "[samvaad-release $(date -u +%H:%M:%S)] $*"; }

cd "$APP_DIR"
mkdir -p "$STATE_DIR" "$BACKUP_DIR"

# Services are found by name so this works whatever the unit for the API is called.
mapfile -t SERVICES < <(systemctl list-unit-files --type=service --no-legend | awk '{print $1}' | grep -i samvaad || true)
[ "${#SERVICES[@]}" -gt 0 ] || { log "no samvaad systemd services found; set them up first"; exit 1; }

health() {
  local code=""
  for attempt in $(seq 1 24); do
    code=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" || true)
    [ "$code" = "200" ] && { log "healthy: $HEALTH_URL"; return 0; }
    sleep 5
  done
  log "UNHEALTHY: $HEALTH_URL (HTTP $code)"
  return 1
}

install_and_restart() {
  "$VENV/bin/pip" install --quiet -r "$CODE_DIR/requirements.txt"
  for s in "${SERVICES[@]}"; do sudo systemctl restart "$s"; log "restarted $s"; done
}

go_back() {
  local last
  last=$(cat "$STATE_DIR/last-good" 2>/dev/null || true)
  [ -n "$last" ] || { log "no previous good release recorded; nothing to roll back to"; return 1; }
  log "rolling back to $last"
  git checkout --quiet --detach "$last"
  install_and_restart
  health && log "rolled back to $last" || { log "rollback is also unhealthy; check journalctl -u <service>"; return 1; }
}

if [ "${1:-}" = "--rollback" ]; then go_back; exit $?; fi

log "1/7 environment file"
[ -f "$ENV_FILE" ] || { log "missing $ENV_FILE"; exit 1; }

# Record what runs now, so a failed release can come back to it.
CURRENT=$(git rev-parse HEAD)
[ -f "$STATE_DIR/last-good" ] || echo "$CURRENT" > "$STATE_DIR/last-good"

log "2/7 database backup"
# Read only the connection settings from .env, without printing them.
envval() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }
TAG_NOW=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="$BACKUP_DIR/$TAG_NOW-${CURRENT:0:12}.dump"
SSLMODE=require; [ "$(envval DB_SSL_ENABLED | tr '[:upper:]' '[:lower:]')" = "false" ] && SSLMODE=disable
PGPASSWORD="$(envval POSTGRES_PASSWORD)" PGSSLMODE="$SSLMODE" pg_dump --format=custom --no-owner \
  -h "$(envval POSTGRES_HOST)" -p "$(envval POSTGRES_PORT)" -U "$(envval POSTGRES_USERNAME)" -d "$(envval POSTGRES_DB)" \
  -f "$BACKUP"
[ -s "$BACKUP" ] || { log "backup is empty; stopping"; exit 1; }
log "backup: $BACKUP ($(du -h "$BACKUP" | cut -f1))"

log "3/7 checking out"
git fetch --quiet origin staging
SHA="${1:-$(git rev-parse origin/staging)}"
git checkout --quiet --detach "$SHA"
log "release ${SHA:0:12} (was ${CURRENT:0:12})"

fail() { log "release failed: $1"; go_back || true; log "database backup to restore if needed: $BACKUP"; exit 1; }

log "4/7 requirements"
"$VENV/bin/pip" install --quiet -r "$CODE_DIR/requirements.txt" || fail "pip install"

log "5/7 migrations"
(cd "$CODE_DIR" && "$VENV/bin/alembic" upgrade head) || fail "alembic upgrade head"

log "6/7 restarting ${SERVICES[*]}"
for s in "${SERVICES[@]}"; do sudo systemctl restart "$s" || fail "restart $s"; done

log "7/7 health"
health || fail "health check"
echo "$SHA" > "$STATE_DIR/last-good"
log "released ${SHA:0:12}. Now check the student profile's WhatsApp switch and a resume score on staging."
