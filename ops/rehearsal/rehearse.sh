#!/usr/bin/env bash
# Release rehearsal for database migrations, on a disposable local Postgres only.
#   ops/rehearsal/rehearse.sh [FROM_REVISION] [DUMP.sql]
# Brings a fresh database to FROM_REVISION (default: the last release), loads data (a
# sanitised production dump if given, else the synthetic load seed), then times
# upgrade to head, downgrade back, and upgrade again. Fails on any error.
set -euo pipefail
FROM=${1:-d7e8f9a0b1c2}
DUMP=${2:-}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PG=${PG_BIN:-/usr/lib/postgresql/16/bin}
DATA=$(mktemp -d)
PORT=${REHEARSAL_PORT:-55432}
# Postgres refuses to run as root; use the postgres account when the shell is root.
AS=(); if [ "$(id -u)" = 0 ]; then AS=(runuser -u postgres --); chown postgres "$DATA"; fi
trap '"${AS[@]}" "$PG/pg_ctl" -D "$DATA" stop -m fast >/dev/null 2>&1 || true; rm -rf "$DATA"' EXIT

"${AS[@]}" "$PG/initdb" -D "$DATA" -U ci --auth=trust >/dev/null
"${AS[@]}" "$PG/pg_ctl" -D "$DATA" -o "-p $PORT -c max_connections=20 -k /tmp" -l "$DATA/log" start >/dev/null
"$PG/createdb" -h /tmp -p "$PORT" -U ci samvaad_rehearsal

export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=$PORT POSTGRES_DB=samvaad_rehearsal
export POSTGRES_USERNAME=ci POSTGRES_PASSWORD=unused
cd "$ROOT/backend/backend"
PY=${PYTHON:-python}
step() { local t0; t0=$(date +%s.%N); "$@"; printf '%-34s %6.1fs\n' "$LABEL" "$(echo "$(date +%s.%N) - $t0" | bc)"; }

LABEL="schema to $FROM" step "$PY" "$HERE/alembic_env.py" upgrade "$FROM"
if [ -n "$DUMP" ]; then
  LABEL="load sanitised dump" step "$PG/psql" -q -h /tmp -p "$PORT" -U ci -d samvaad_rehearsal -v ON_ERROR_STOP=1 -f "$DUMP"
else
  LABEL="load synthetic data" step "$PG/psql" -q -h /tmp -p "$PORT" -U ci -d samvaad_rehearsal -v ON_ERROR_STOP=1 -f "$ROOT/ops/load/seed.sql"
fi
ROWS=$("$PG/psql" -tA -h /tmp -p "$PORT" -U ci -d samvaad_rehearsal -c 'select count(*) from question_attempt')
echo "answers in database: $ROWS"
LABEL="upgrade $FROM -> head" step "$PY" "$HERE/alembic_env.py" upgrade head
LABEL="downgrade head -> $FROM" step "$PY" "$HERE/alembic_env.py" downgrade "$FROM"
LABEL="upgrade again -> head" step "$PY" "$HERE/alembic_env.py" upgrade head
AFTER=$("$PG/psql" -tA -h /tmp -p "$PORT" -U ci -d samvaad_rehearsal -c 'select count(*) from question_attempt')
[ "$ROWS" = "$AFTER" ] || { echo "row count changed: $ROWS -> $AFTER"; exit 1; }
echo "rehearsal passed: no rows lost"
