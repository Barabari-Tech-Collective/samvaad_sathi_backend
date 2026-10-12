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
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:8000/docs}

[[ $(id -un) == ubuntu ]] || { echo 'Run as the ubuntu user that owns the checkout'; exit 2; }
[[ -d $APP_ROOT/.git ]] || { echo "No git checkout at $APP_ROOT"; exit 2; }
[[ -x $VENV/bin/python ]] || { echo "No virtualenv at $VENV"; exit 2; }
[[ -f $APP_ROOT/backend/.env ]] || { echo 'Missing backend/.env'; exit 2; }
[[ $HEALTH_URL == http://127.0.0.1:* ]] || { echo 'Health check must target the local service'; exit 2; }

# Two overlapping releases would fight over the same checkout.
exec 9>/tmp/samvaad-release.lock
flock -n 9 || { echo 'Another release is in progress'; exit 2; }

cd "$APP_ROOT"
previous_sha=$(git rev-parse HEAD)
[[ $previous_sha != "$release_sha" ]] || { echo 'Already at that commit'; exit 0; }
echo "Releasing $release_sha (current $previous_sha)"

# Refuse to deploy over uncommitted edits: they would be silently discarded,
# and on this host they are as likely to be an undocumented hotfix as noise.
git diff --quiet || { echo 'Checkout has uncommitted changes; resolve them first'; exit 2; }

git fetch --quiet origin "$release_sha"

restored=false
rollback() {
  [[ $restored == true ]] && return
  restored=true
  echo "Release failed; restoring $previous_sha"
  git checkout --quiet --force "$previous_sha" || echo 'URGENT: could not restore the previous commit'
  "$VENV/bin/pip" install --quiet -r backend/backend/requirements.txt || echo 'URGENT: rollback dependency install failed'
  sudo systemctl restart "$API_SERVICE" "$WORKER_SERVICE" || echo 'URGENT: rollback restart failed'
  sleep 5
  curl --fail --silent --max-time 15 "$HEALTH_URL" >/dev/null \
    && echo 'Rolled back and healthy' \
    || echo 'URGENT: still unhealthy after rollback - needs a human'
}
trap rollback ERR

git checkout --quiet --force "$release_sha"
"$VENV/bin/pip" install --quiet -r backend/backend/requirements.txt

# Migrations are applied before the restart so the new code never meets an old
# schema. Idempotent: the app also patches these columns at startup, and every
# migration involved uses IF NOT EXISTS.
( cd backend/backend && "$VENV/bin/python" -m alembic upgrade head )

sudo systemctl restart "$API_SERVICE" "$WORKER_SERVICE"

healthy=false
for _ in {1..12}; do
  if systemctl is-active --quiet "$API_SERVICE" "$WORKER_SERVICE" \
     && curl --fail --silent --max-time 5 "$HEALTH_URL" >/dev/null; then
    healthy=true
    break
  fi
  sleep 5
done
[[ $healthy == true ]] || { echo 'New release failed its health check'; false; }

trap - ERR
echo "Released $release_sha. Roll back with: ops/ec2-release.sh $previous_sha"
