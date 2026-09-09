#!/usr/bin/env bash
# Restore a Matchwell PostgreSQL backup into a separate, explicitly named
# database and verify it, without ever risking the production/source
# database. Mirrors scripts/backup/pg-restore.ps1 -- see that script's
# header comment for the full behavior description.
#
# Usage:
#   RESTORE_DATABASE_URL="postgresql://user:pass@host:5432/matchwell_restore_drill" \
#   DATABASE_URL="postgresql://user:pass@prod-host:5432/matchwell" \
#     ./scripts/backup/pg-restore.sh ./backups/matchwell-2026-09-09.dump [--force] [--compare-source]
set -euo pipefail

BACKUP_PATH="${1:?Usage: pg-restore.sh <backup-path> [--force] [--compare-source]}"
shift
FORCE=false
COMPARE_SOURCE=false
for option in "$@"; do
  case "$option" in
    --force) FORCE=true ;;
    --compare-source) COMPARE_SOURCE=true ;;
    *) echo "Unknown option: $option" >&2; exit 1 ;;
  esac
done

if [[ -z "${RESTORE_DATABASE_URL:-}" ]]; then
  echo "RESTORE_DATABASE_URL is not set." >&2
  exit 1
fi
if [[ ! -f "$BACKUP_PATH" ]]; then
  echo "Backup file not found: $BACKUP_PATH" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Parse the restore target first (this is what pg_restore/createdb use).
eval "$(MATCHWELL_PG_URL="$RESTORE_DATABASE_URL" python3 "$SCRIPT_DIR/_pg_url.py")"
RESTORE_HOST="$PG_HOST"; RESTORE_PORT="$PG_PORT"; RESTORE_USER="$PG_USER"
RESTORE_DB="$PG_DATABASE"; RESTORE_PASSWORD="$PGPASSWORD"

if [[ "$RESTORE_DB" != *restore* && "$RESTORE_DB" != *drill* ]]; then
  echo "RESTORE_DATABASE_URL's database name must contain 'restore' or 'drill' (got '$RESTORE_DB') to make accidental production targeting obvious. Rename the target database." >&2
  exit 1
fi

if [[ -n "${DATABASE_URL:-}" ]]; then
  eval "$(MATCHWELL_PG_URL="$DATABASE_URL" python3 "$SCRIPT_DIR/_pg_url.py")"
  if [[ "$PG_HOST" == "$RESTORE_HOST" && "$PG_DATABASE" == "$RESTORE_DB" ]]; then
    echo "RESTORE_DATABASE_URL must not be the same host+database as DATABASE_URL (source/production). Refusing to proceed." >&2
    exit 1
  fi
fi
if [[ "$COMPARE_SOURCE" == true && -z "${DATABASE_URL:-}" ]]; then
  echo "--compare-source requires DATABASE_URL." >&2
  exit 1
fi

echo "Restoring into database '$RESTORE_DB' on host '$RESTORE_HOST' (connection details redacted) ..."

export PGPASSWORD="$RESTORE_PASSWORD"

EXISTING_TABLES=$(psql -h "$RESTORE_HOST" -p "$RESTORE_PORT" -U "$RESTORE_USER" -d "$RESTORE_DB" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';" 2>/dev/null || echo "")
if [[ -n "$EXISTING_TABLES" && "$EXISTING_TABLES" -gt 0 && "$FORCE" != true ]]; then
  echo "Restore database '$RESTORE_DB' already has $EXISTING_TABLES table(s). Pass --force to drop and restore over it, or choose a fresh database name." >&2
  exit 1
fi

createdb -h "$RESTORE_HOST" -p "$RESTORE_PORT" -U "$RESTORE_USER" "$RESTORE_DB" 2>/dev/null || true

pg_restore \
  -h "$RESTORE_HOST" -p "$RESTORE_PORT" -U "$RESTORE_USER" -d "$RESTORE_DB" \
  --clean --if-exists --no-owner --no-privileges \
  "$BACKUP_PATH"

unset PGPASSWORD

echo "Running Alembic migrations against the restore database ..."
DATABASE_URL="$RESTORE_DATABASE_URL" uv run alembic upgrade head

echo "Verifying invariant row counts (privacy-safe: counts only, never content) ..."
export MATCHWELL_VERIFY_DATABASE_URL="$RESTORE_DATABASE_URL"
if [[ "$COMPARE_SOURCE" == true ]]; then
  export MATCHWELL_VERIFY_COMPARE_URL="$DATABASE_URL"
fi
uv run python scripts/backup/verify_invariants.py
unset MATCHWELL_VERIFY_DATABASE_URL MATCHWELL_VERIFY_COMPARE_URL

cat <<'EOF'

Restore drill complete. Record this drill in backup_drill_runs so the admin
operations dashboard reflects a fresh drill date, e.g. (run against the
PRODUCTION database, not the restore database, once you have confirmed the
restore was drawn from a real production backup):

  INSERT INTO backup_drill_runs (id, performed_at, performed_by, target_description, verification_passed, notes)
  VALUES (gen_random_uuid(), now(), '<your email>', '<host/provider description>', true, '<any non-sensitive notes>');
EOF
