#!/usr/bin/env bash
# Create a PostgreSQL logical backup (custom format) of the Matchwell pilot
# database, without ever printing a connection string or password.
#
# This script only ever reads the source database. It never mutates or
# drops anything.
#
# Usage:
#   DATABASE_URL="postgresql://user:pass@host:5432/matchwell" \
#     ./scripts/backup/pg-dump.sh ./backups/matchwell-2026-09-09.dump
#
# Pass --force as a second argument to allow overwriting an existing file.
set -euo pipefail

OUTPUT_PATH="${1:?Usage: pg-dump.sh <output-path> [--force]}"
FORCE="${2:-}"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is not set." >&2
  exit 1
fi

if [[ -e "$OUTPUT_PATH" && "$FORCE" != "--force" ]]; then
  echo "Refusing to overwrite existing backup file '$OUTPUT_PATH'. Pass --force to overwrite intentionally." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
eval "$(MATCHWELL_PG_URL="$DATABASE_URL" python3 "$SCRIPT_DIR/_pg_url.py")"

mkdir -p "$(dirname "$OUTPUT_PATH")"

echo "Backing up database '$PG_DATABASE' on host '$PG_HOST' (connection details redacted) ..."

pg_dump \
  -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DATABASE" \
  -Fc --no-owner --no-privileges \
  -f "$OUTPUT_PATH"

unset PGPASSWORD

SIZE_BYTES=$(stat -c%s "$OUTPUT_PATH" 2>/dev/null || stat -f%z "$OUTPUT_PATH")
echo "Backup complete: $OUTPUT_PATH ($SIZE_BYTES bytes)."
echo "Next: run scripts/backup/pg-restore.sh against a separate, explicitly named database to verify this backup."
