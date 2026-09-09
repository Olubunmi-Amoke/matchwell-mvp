<#
.SYNOPSIS
    Restore a Matchwell PostgreSQL backup into a separate, explicitly named
    database and verify it, without ever risking the production/source
    database.

.DESCRIPTION
    Operator-run restore-drill script. Refuses to run unless
    -RestoreDatabaseUrl points at a database that is clearly NOT the
    source/production database (different host+database name from
    -SourceDatabaseUrl / DATABASE_URL). Refuses to restore into an existing
    database that already has application tables unless -Force is passed.

    Steps performed:
      1. Safety checks (see above).
      2. Create the restore database if it does not already exist.
      3. pg_restore the backup archive into the restore database only.
      4. Run Alembic migrations against the restore database (proving the
         backup is compatible with the current migration history).
      5. Run scripts/backup/verify_invariants.py for safe, privacy-safe
         row-count verification (never prints row content).

    No secret ever appears on the command line or in this script's log
    output: passwords travel only through the PGPASSWORD environment
    variable for the lifetime of each external command.

.PARAMETER BackupPath
    Path to a pg_dump custom-format archive produced by pg-dump.ps1.

.PARAMETER RestoreDatabaseUrl
    Connection URL for the SEPARATE restore-verification database. Must
    not equal -SourceDatabaseUrl. Prefer the RESTORE_DATABASE_URL
    environment variable so credentials do not appear in process arguments.

.PARAMETER SourceDatabaseUrl
    The original/production connection URL, used only for the safety
    comparison and the optional invariant-count diff. Defaults to
    DATABASE_URL.

.PARAMETER Force
    Allow restoring into a restore database that already contains
    application tables (they will be dropped and recreated by pg_restore
    --clean). Off by default.

.EXAMPLE
    .\scripts\backup\pg-restore.ps1 `
        -BackupPath .\backups\matchwell-2026-09-09.dump `
        -RestoreDatabaseUrl "postgresql://user:pass@db-host:5432/matchwell_restore_drill"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [string]$RestoreDatabaseUrl = $env:RESTORE_DATABASE_URL,
    [string]$SourceDatabaseUrl = $env:DATABASE_URL,
    [switch]$CompareToSource,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

. "$PSScriptRoot\_pg-url.ps1"

if (-not (Test-Path -LiteralPath $BackupPath)) {
    throw "Backup file not found: $BackupPath"
}
if (-not $RestoreDatabaseUrl) {
    throw "RestoreDatabaseUrl or RESTORE_DATABASE_URL is required."
}

$restore = ConvertFrom-PostgresUrl -Url $RestoreDatabaseUrl

if ($restore.Database -notmatch '(restore|drill)') {
    throw "RestoreDatabaseUrl's database name must contain 'restore' or 'drill' (got '$($restore.Database)') to make accidental production targeting obvious. Rename the target database."
}

if ($SourceDatabaseUrl) {
    $source = ConvertFrom-PostgresUrl -Url $SourceDatabaseUrl
    if (($source.HostName -eq $restore.HostName) -and ($source.Database -eq $restore.Database)) {
        throw "RestoreDatabaseUrl must not be the same host+database as the source/production database. Refusing to proceed."
    }
}
if ($CompareToSource -and -not $SourceDatabaseUrl) {
    throw "-CompareToSource requires -SourceDatabaseUrl or DATABASE_URL."
}

Write-Host "Restoring into database '$($restore.Database)' on host '$($restore.HostName)' (connection details redacted) ..."

$previousPgPassword = $env:PGPASSWORD
try {
    $env:PGPASSWORD = $restore.Password

    # Does the restore database already exist and have application tables?
    $existingTableCount = & psql -h $restore.HostName -p $restore.Port -U $restore.UserName -d $restore.Database -tAc `
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';" 2>$null
    if ($LASTEXITCODE -eq 0 -and $existingTableCount -and ([int]$existingTableCount.Trim() -gt 0) -and -not $Force) {
        throw "Restore database '$($restore.Database)' already has $($existingTableCount.Trim()) table(s). Pass -Force to drop and restore over it, or choose a fresh database name."
    }

    # Create the database if it does not exist yet (createdb fails
    # harmlessly if it already does; that failure path is intentionally
    # ignored so this script is idempotent).
    & createdb -h $restore.HostName -p $restore.Port -U $restore.UserName $restore.Database 2>$null | Out-Null

    $restoreArgs = @(
        "-h", $restore.HostName,
        "-p", $restore.Port,
        "-U", $restore.UserName,
        "-d", $restore.Database,
        "--clean", "--if-exists",
        "--no-owner", "--no-privileges",
        $BackupPath
    )
    & pg_restore @restoreArgs
    if ($LASTEXITCODE -ne 0) {
        throw "pg_restore exited with code $LASTEXITCODE."
    }
}
finally {
    $env:PGPASSWORD = $previousPgPassword
}

Write-Host "Running Alembic migrations against the restore database ..."
$previousDatabaseUrl = $env:DATABASE_URL
try {
    $env:DATABASE_URL = $RestoreDatabaseUrl
    & uv run alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "alembic upgrade head failed against the restore database."
    }
}
finally {
    $env:DATABASE_URL = $previousDatabaseUrl
}

Write-Host "Verifying invariant row counts (privacy-safe: counts only, never content) ..."
$previousVerifyDatabaseUrl = $env:MATCHWELL_VERIFY_DATABASE_URL
$previousVerifyCompareUrl = $env:MATCHWELL_VERIFY_COMPARE_URL
try {
    $env:MATCHWELL_VERIFY_DATABASE_URL = $RestoreDatabaseUrl
    $env:MATCHWELL_VERIFY_COMPARE_URL = if ($CompareToSource) { $SourceDatabaseUrl } else { $null }
    & uv run python scripts/backup/verify_invariants.py
    if ($LASTEXITCODE -ne 0) {
        throw "Restore invariant verification failed."
    }
}
finally {
    $env:MATCHWELL_VERIFY_DATABASE_URL = $previousVerifyDatabaseUrl
    $env:MATCHWELL_VERIFY_COMPARE_URL = $previousVerifyCompareUrl
}

Write-Host ""
Write-Host "Restore drill complete. Record this drill in backup_drill_runs so the"
Write-Host "admin operations dashboard reflects a fresh drill date, e.g.:"
Write-Host "  INSERT INTO backup_drill_runs (id, performed_at, performed_by, target_description, verification_passed, notes)"
Write-Host "  VALUES (gen_random_uuid(), now(), '<your email>', '<host/provider description>', true, '<any non-sensitive notes>');"
Write-Host "Run this INSERT against the PRODUCTION database, not the restore database, once you have confirmed the restore was drawn from a real production backup."
