#!/usr/bin/env bash
# Deploys a reviewed commit to the Samvaad Saathi EC2 host and rolls back
# automatically if the service does not come back healthy.
#
# Written against the host as it actually is, confirmed from `systemctl cat`:
#
#   checkout   /home/ubuntu/apps/samvaad_sathi_backend   (in place, not releases/)
#   venv       /home/ubuntu/apps/samvaad_sathi_backend/venv
#   workdir    <checkout>/backend/backend
#   env file   <checkout>/backend/.env        (read by python-decouple, no EnvironmentFile=)
#   services   samvaad_sathi_backend.service, samvaad-worker.service   (User=ubuntu)
#   listens    127.0.0.1:8000 behind a reverse proxy
#
# An in-place checkout cannot swap atomically the way a releases/ symlink can,
# so there is a brief window during restart. Rollback is therefore by commit:
# the previous SHA is recorded first and restored if the health check fails.
#
#   sudo -u ubuntu ops/ec2-release.sh <40-char-sha>

set -Eeuo pipefail
umask 027

[[ ${1:-} =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected a full 40-character commit SHA'; exit 2; }
release_sha=$1

APP_ROOT=${APP_ROOT:-/home/ubuntu/apps/samvaad_sathi_backend}
VENV=${VENV:-$APP_ROOT/venv}
API_SERVICE=${API_SERVICE:-samvaad_sathi_backend.service}
WORKER_SERVICE=${WORKER_SERVICE:-samvaad-worker.service}
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:8000/api/health}

[[ $(id -un) == ubuntu ]] || { echo 'Run as the ubuntu user that owns the checkout'; exit 2; }
[[ -d $APP_ROOT/.git ]] || { echo "No git checkout at $APP_ROOT"; exit 2; }
[[ -x $VENV/bin/python ]] || { echo "No virtualenv at $VENV"; exit 2; }
[[ -f $APP_ROOT/backend/.env ]] || { echo 'Missing backend/.env'; exit 2; }

# Two overlapping releases would fight over the same checkout.
exec 9>/tmp/samvaad-release.lock
flock -n 9 || { echo 'Another release is in progress'; exit 2; }

cd "$APP_ROOT"
previous_sha=$(git rev-parse HEAD)
echo "Releasing $release_sha (current $previous_sha)"

# Refuse to deploy over uncommitted edits: they would be silently discarded,
# and on this host they are as likely to be an undocumented hotfix as noise.
[[ -z $(git status --porcelain --untracked-files=no) ]] \
  || { echo 'Checkout has uncommitted tracked changes; resolve them first'; exit 2; }

# Fetch the exact target first: the current commit may predate these helpers.
git fetch --quiet origin "$release_sha"

# Keep the target probe available even after restoring an older commit.
probe_dir=$(mktemp -d /tmp/samvaad-release-probe.XXXXXX)
trap 'rm -rf -- "$probe_dir"' EXIT
git show "$release_sha:ops/local_health.py" > "$probe_dir/local_health.py"
git show "$release_sha:ops/staging_smoke.py" > "$probe_dir/staging_smoke.py"
"$VENV/bin/python" "$probe_dir/local_health.py" "$HEALTH_URL" --validate-only

local_ready() {
  systemctl is-active --quiet "$API_SERVICE" \
    && systemctl is-active --quiet "$WORKER_SERVICE" \
    && "$VENV/bin/python" "$probe_dir/local_health.py" "$HEALTH_URL"
}

wait_until_ready() {
  local attempt
  for attempt in {1..12}; do
    if local_ready; then
      return 0
    fi
    if [[ $attempt -lt 12 ]]; then sleep 5; fi
  done
  return 1
}

restored=false
migration_started=false
rollback() {
  local failure_status=$?
  # A failed command substitution must propagate to the owning shell first.
  [[ $BASH_SUBSHELL -eq 0 ]] || return "$failure_status"
  trap - ERR
  [[ $restored == true ]] && return "$failure_status"
  restored=true
  echo "Release failed; restoring $previous_sha"
  if [[ $migration_started == true ]]; then
    echo 'Database downgrade is intentionally NOT automatic. Expand-only migrations'
    echo 'must remain compatible with the previous application commit.'
  fi
  if ! cd "$APP_ROOT" || ! git checkout --quiet --force "$previous_sha"; then
    echo 'URGENT: could not restore the previous commit; manual recovery required'
    return "$failure_status"
  fi
  local rollback_ok=true
  "$VENV/bin/pip" install --quiet -r backend/backend/requirements.txt \
    || { echo 'URGENT: rollback dependency install failed'; rollback_ok=false; }
  sudo systemctl restart "$API_SERVICE" "$WORKER_SERVICE" \
    || { echo 'URGENT: rollback restart failed'; rollback_ok=false; }
  if wait_until_ready && [[ $rollback_ok == true ]]; then
    echo 'Rolled back and healthy'
  else
    echo 'URGENT: rollback not verified - needs a human'
  fi
  return "$failure_status"
}
trap rollback ERR

git checkout --quiet --force "$release_sha"
"$VENV/bin/pip" install --quiet -r backend/backend/requirements.txt

# Migrations are mandatory and verified, even when a release contains no new
# revision. The expand-only CI gate guarantees the old application can keep
# running while additive DDL is applied and can be restored without downgrading
# the database if application health checks fail.
cd backend/backend
migration_heads_output=$("$VENV/bin/python" -m alembic heads)
mapfile -t migration_heads < <(printf '%s\n' "$migration_heads_output" | awk 'NF {print $1}')
[[ ${#migration_heads[@]} -eq 1 ]] \
  || { echo "Expected exactly one Alembic head, found ${#migration_heads[@]}"; false; }
expected_head=${migration_heads[0]}
echo "Target database revision: $expected_head"
echo 'Database revision before release:'
"$VENV/bin/python" -m alembic current
migration_started=true
"$VENV/bin/python" -m alembic upgrade head
current_revision=$("$VENV/bin/python" -m alembic current | awk 'NF {print $1}' | tail -1)
[[ $current_revision == "$expected_head" ]] \
  || { echo "Database revision mismatch: expected $expected_head, got $current_revision"; false; }
cd "$APP_ROOT"
"$VENV/bin/python" ops/check_schema_drift.py
echo "Database migration verified at $current_revision"

sudo systemctl restart "$API_SERVICE" "$WORKER_SERVICE"

wait_until_ready || { echo 'New release failed its health check'; false; }

trap - ERR
echo "Released $release_sha. Roll back with: ops/ec2-release.sh $previous_sha"
