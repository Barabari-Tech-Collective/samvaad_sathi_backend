#!/usr/bin/env bash
# Install root-owned as /usr/local/sbin/samvaad-release only after validating
# /etc/samvaad-release.conf and the systemd WorkingDirectory/ExecStart paths.
set -Eeuo pipefail
umask 027
[[ ${1:-} =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected a full commit SHA'; exit 2; }
release_sha=$1
[[ $(id -u) == 0 ]] || { echo 'This runner requires root via SSM'; exit 2; }
[[ -f /etc/samvaad-release.conf ]] || { echo 'Release configuration missing'; exit 2; }
[[ $(stat -c '%u:%a' /etc/samvaad-release.conf) == '0:600' ]] || { echo 'Configuration must be root-owned mode 600'; exit 2; }
# shellcheck source=/dev/null
source /etc/samvaad-release.conf
: "${APP_USER:?}" "${SOURCE_REPO:?}" "${PYTHON_BIN:?}" "${ENV_FILE:?}" "${API_SERVICE:?}" "${WORKER_SERVICE:?}" "${LOCAL_HEALTH_URL:?}"
[[ $API_SERVICE =~ ^[a-zA-Z0-9_.@-]+$ && $WORKER_SERVICE =~ ^[a-zA-Z0-9_.@-]+$ ]] || exit 2
[[ $LOCAL_HEALTH_URL == http://127.0.0.1:* ]] || { echo 'Health URL must use the local service'; exit 2; }
[[ -f $ENV_FILE && -d $SOURCE_REPO/.git ]] || exit 2
exec 9>/var/lock/samvaad-release.lock
flock -n 9 || { echo 'Another release is in progress'; exit 2; }
release_root=/opt/samvaad/releases
release_dir="$release_root/$release_sha"
current_link=/opt/samvaad/current
previous_release=$(readlink -f "$current_link")
[[ $previous_release == "$release_root/"* && -d $previous_release ]] || { echo 'A verified rollback release must exist'; exit 2; }
[[ ! -e $release_dir ]] || { echo 'Release directory already exists; inspect it before retrying'; exit 2; }
switched=false
rollback() {
  if [[ $switched == true ]]; then
    echo 'Release failed; restoring previous verified release'
    ln -s "$previous_release" /opt/samvaad/rollback-link
    mv -Tf /opt/samvaad/rollback-link "$current_link"
    systemctl restart "$API_SERVICE" "$WORKER_SERVICE"
    curl --fail --silent --max-time 15 "$LOCAL_HEALTH_URL" >/dev/null || echo 'URGENT: rollback health check failed'
  fi
}
trap rollback ERR
runuser -u "$APP_USER" -- git -C "$SOURCE_REPO" fetch origin "$release_sha"
install -d -o "$APP_USER" -g "$APP_USER" "$release_dir"
runuser -u "$APP_USER" -- git -C "$SOURCE_REPO" archive "$release_sha" | runuser -u "$APP_USER" -- tar -x -C "$release_dir"
# This first feature has no migrations. Reject migration changes; they require a separate reviewed rollout.
if ! runuser -u "$APP_USER" -- git -C "$SOURCE_REPO" diff --quiet "$(basename "$previous_release")" "$release_sha" -- backend/backend/src/repository/migrations; then
  echo 'Migration changed: this release runner requires an approved migration procedure'; exit 2
fi
runuser -u "$APP_USER" -- "$PYTHON_BIN" -m venv "$release_dir/venv"
runuser -u "$APP_USER" -- "$release_dir/venv/bin/pip" install -r "$release_dir/backend/backend/requirements.txt"
ln -s "$ENV_FILE" "$release_dir/backend/.env"
chown -h "$APP_USER:$APP_USER" "$release_dir/backend/.env"
ln -s "$release_dir" /opt/samvaad/candidate-link
mv -Tf /opt/samvaad/candidate-link "$current_link"
switched=true
systemctl restart "$API_SERVICE" "$WORKER_SERVICE"
healthy=false
for attempt in {1..12}; do
  if systemctl is-active --quiet "$API_SERVICE" "$WORKER_SERVICE" && curl --fail --silent --max-time 5 "$LOCAL_HEALTH_URL" >/dev/null; then healthy=true; break; fi
  sleep 5
done
[[ $healthy == true ]] || { echo 'New release failed health checks'; false; }
trap - ERR
echo "Released $release_sha; rollback target $(basename "$previous_release")"
