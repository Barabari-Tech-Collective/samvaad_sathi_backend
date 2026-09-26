#!/usr/bin/env bash
#
# General-purpose full Postgres-to-Postgres migration for this app's database
# (pg_dump/pg_restore, provider-agnostic). Originally written for the production
# cutover (Aiven -> Neon); reused as-is for the staging cutover (old Neon account ->
# new Neon account) since the mechanics don't depend on either endpoint's provider.
#
# Why pg_dump/pg_restore instead of the app's own Alembic migrations: this is a full
# lift-and-shift of an existing, already-populated database (schema + data + the
# alembic_version table itself), not a from-scratch schema build. Using the native
# Postgres tools guarantees the destination ends up byte-for-byte equivalent to the
# source, including whatever migration state it's actually in - re-running Alembic
# against an empty target and then data-loading separately would only be correct if the
# deployed schema exactly matches what's in source control, which isn't a safe assumption
# to make sight-unseen for a live database.
#
# Usage:
#   export SOURCE_DB_URL="postgresql://user:PASSWORD@source-host:5432/dbname?sslmode=require"
#   export TARGET_DB_URL="postgresql://user:PASSWORD@target-host:5432/dbname?sslmode=require"
#
#   ./migrate_postgres_database.sh preflight   # check connectivity + Postgres versions, no writes
#   ./migrate_postgres_database.sh dump        # pg_dump source into a local .dump file
#   ./migrate_postgres_database.sh restore     # pg_restore that file into the target database
#   ./migrate_postgres_database.sh verify      # compare row counts per table, source vs target
#   ./migrate_postgres_database.sh all         # runs all of the above in order, stops on any failure
#
# Each step is independently runnable on purpose - inspect the dump file and verify
# output before moving to the next step, rather than running this unattended end-to-end.
#
# IMPORTANT before running `restore` for real:
#   1. Confirm the target database is actually empty (this script checks and refuses to
#      restore into a database that already has tables, to avoid silently merging into or
#      clobbering existing data).
#   2. The env var SOURCE_DB_URL/TARGET_DB_URL passwords will appear in `ps` output for the
#      duration of each pg_dump/pg_restore/psql call on a shared machine - run this from a
#      host you trust, or use a .pgpass file instead if that's a concern.
#   3. If either endpoint is a Neon "pooler" connection (hostname contains "-pooler"),
#      that's fine for this one-off dump/restore, but the *application's* long-lived
#      connection pool should likely use the non-pooled endpoint instead (toggle
#      "Connection pooling" off in Neon's connect dialog to get it) - PgBouncer-style
#      transaction pooling and asyncpg's server-side prepared statements don't always mix
#      well. Verify this separately after cutover.
#
# Migrations run with this script so far:
#   - Production: Aiven -> Neon (new production project)
#   - Staging: Neon (original account) -> Neon (new account)
#
set -euo pipefail

DUMP_FILE="${DUMP_FILE:-./samvaad_saathi_db_$(date +%Y%m%dT%H%M%S).dump}"
# Matches the 18 tables in this app's schema doc - used by `verify` to compare row
# counts. Update this list if the schema has grown since.
TABLES=(
  job_profile job_profile_question "user" interview interview_question
  question_attempt analytics_event ai_resume_analyses alembic_version
  pacing_practice_session pronunciation_practice question_supplement
  report session structure_practice structure_practice_answer
  summary_report user_resume_instances
)

require_env() {
  if [ -z "${SOURCE_DB_URL:-}" ] || [ -z "${TARGET_DB_URL:-}" ]; then
    echo "SOURCE_DB_URL and TARGET_DB_URL must both be set. See the usage comment at the top of this script." >&2
    exit 1
  fi
}

cmd_preflight() {
  require_env
  echo "== Checking pg_dump/pg_restore/psql are available =="
  command -v pg_dump >/dev/null || { echo "pg_dump not found"; exit 1; }
  command -v pg_restore >/dev/null || { echo "pg_restore not found"; exit 1; }
  command -v psql >/dev/null || { echo "psql not found"; exit 1; }
  pg_dump --version

  echo -e "\n== Source connectivity + version =="
  psql "$SOURCE_DB_URL" -c "SELECT version();"

  echo -e "\n== Target connectivity + version =="
  psql "$TARGET_DB_URL" -c "SELECT version();"

  echo -e "\n== Source table count (sanity check against the expected ${#TABLES[@]} tables) =="
  psql "$SOURCE_DB_URL" -t -c "SELECT count(*) FROM pg_tables WHERE schemaname = 'public';"

  echo -e "\n== Target table count (should be 0 before restore) =="
  psql "$TARGET_DB_URL" -t -c "SELECT count(*) FROM pg_tables WHERE schemaname = 'public';"

  echo -e "\nIf pg_dump's version above is older than either server's Postgres version, upgrade pg_dump before proceeding (backward-compat dumping from a newer server with an older client can silently miss newer features)."
}

cmd_dump() {
  require_env
  echo "Dumping source to $DUMP_FILE ..."
  # --format=custom: compressed, supports selective/parallel restore.
  # --no-owner --no-acl: source and target roles are very likely named differently
  # (confirmed true for both migrations this script has been used for so far), so
  # ownership/grant statements from the source would fail (or silently create a dangling
  # role reference) on restore - the restoring role should just own everything.
  pg_dump "$SOURCE_DB_URL" --format=custom --no-owner --no-acl --file="$DUMP_FILE"
  echo "Done: $DUMP_FILE ($(du -h "$DUMP_FILE" | cut -f1))"
}

cmd_restore() {
  require_env
  if [ ! -f "$DUMP_FILE" ]; then
    echo "Dump file not found: $DUMP_FILE (run 'dump' first, or set DUMP_FILE=path to an existing one)." >&2
    exit 1
  fi

  existing_tables=$(psql "$TARGET_DB_URL" -t -c "SELECT count(*) FROM pg_tables WHERE schemaname = 'public';" | tr -d '[:space:]')
  if [ "$existing_tables" != "0" ]; then
    echo "Target database already has $existing_tables table(s) in the public schema." >&2
    echo "Refusing to restore into a non-empty database - confirm this is really the intended target, then either drop/recreate its public schema or point TARGET_DB_URL at an empty database." >&2
    exit 1
  fi

  echo "Restoring $DUMP_FILE into target ..."
  pg_restore --dbname="$TARGET_DB_URL" --no-owner --no-acl --exit-on-error "$DUMP_FILE"
  echo "Restore complete."
}

cmd_verify() {
  require_env
  echo "Comparing row counts per table (source vs target):"
  printf "%-30s %15s %15s %s\n" "table" "source" "target" "status"
  mismatch=0
  for t in "${TABLES[@]}"; do
    src_count=$(psql "$SOURCE_DB_URL" -t -c "SELECT count(*) FROM \"$t\";" 2>/dev/null | tr -d '[:space:]' || echo "ERR")
    tgt_count=$(psql "$TARGET_DB_URL" -t -c "SELECT count(*) FROM \"$t\";" 2>/dev/null | tr -d '[:space:]' || echo "ERR")
    status="OK"
    # Compare as an explicit equality check first so "ERR" == "ERR" (query failed
    # identically on both sides - e.g. a typo'd table name) is never silently reported as
    # a matching row count instead of the query failure it actually is.
    if [ "$src_count" = "ERR" ] || [ "$tgt_count" = "ERR" ]; then
      status="ERROR"
      mismatch=1
    elif [ "$src_count" != "$tgt_count" ]; then
      status="MISMATCH"
      mismatch=1
    fi
    printf "%-30s %15s %15s %s\n" "$t" "$src_count" "$tgt_count" "$status"
  done

  if [ "$mismatch" -eq 1 ]; then
    echo -e "\nOne or more tables have mismatched row counts - do not cut the application over until this is understood/resolved."
    exit 1
  fi
  echo -e "\nAll table row counts match."
}

case "${1:-}" in
  preflight) cmd_preflight ;;
  dump) cmd_dump ;;
  restore) cmd_restore ;;
  verify) cmd_verify ;;
  all)
    cmd_preflight
    cmd_dump
    cmd_restore
    cmd_verify
    ;;
  *)
    echo "Usage: $0 {preflight|dump|restore|verify|all}" >&2
    exit 1
    ;;
esac
