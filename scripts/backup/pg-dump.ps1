<#
.SYNOPSIS
    Create a PostgreSQL logical backup (custom format) of the Matchwell
    pilot database, without ever printing a connection string or password.

.DESCRIPTION
    Operator-run backup script. Reads the source connection from the
    DATABASE_URL environment variable (or -DatabaseUrl) and writes a
    pg_dump custom-format archive to -OutputPath. Refuses to overwrite an
    existing file unless -Force is passed. Never echoes the parsed
    password; pg_dump receives the password only via the PGPASSWORD
    environment variable for the lifetime of this process.

    This script only ever reads the source database. It never mutates or
    drops anything, and it never targets a "restore" database itself.

.PARAMETER DatabaseUrl
    A postgresql:// or postgresql+psycopg:// connection URL. Defaults to
    the DATABASE_URL environment variable.

.PARAMETER OutputPath
    Path to write the custom-format dump archive to (typically ending in
    .dump). Must not already exist unless -Force is passed.

.PARAMETER Force
    Allow overwriting an existing file at -OutputPath. Off by default.

.EXAMPLE
    $env:DATABASE_URL = "postgresql://user:pass@db-host:5432/matchwell"
    .\scripts\backup\pg-dump.ps1 -OutputPath .\backups\matchwell-2026-09-09.dump
#>
[CmdletBinding()]
param(
    [string]$DatabaseUrl = $env:DATABASE_URL,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

. "$PSScriptRoot\_pg-url.ps1"

if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    throw "DATABASE_URL is not set and -DatabaseUrl was not provided."
}

if ((Test-Path -LiteralPath $OutputPath) -and -not $Force) {
    throw "Refusing to overwrite existing backup file '$OutputPath'. Pass -Force to overwrite intentionally."
}

$parsed = ConvertFrom-PostgresUrl -Url $DatabaseUrl
Write-Host "Backing up database '$($parsed.Database)' on host '$($parsed.HostName)' (connection details redacted) ..."

$outputDir = Split-Path -Parent $OutputPath
if ($outputDir -and -not (Test-Path -LiteralPath $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}

$previousPgPassword = $env:PGPASSWORD
try {
    $env:PGPASSWORD = $parsed.Password
    $arguments = @(
        "-h", $parsed.HostName,
        "-p", $parsed.Port,
        "-U", $parsed.UserName,
        "-d", $parsed.Database,
        "-Fc",
        "--no-owner",
        "--no-privileges",
        "-f", $OutputPath
    )
    # Never log $arguments verbatim if it were to ever include a secret;
    # here it deliberately never does (password travels only via
    # PGPASSWORD), but we still avoid Write-Host'ing the raw argument list.
    & pg_dump @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump exited with code $LASTEXITCODE."
    }
}
finally {
    $env:PGPASSWORD = $previousPgPassword
}

$size = (Get-Item -LiteralPath $OutputPath).Length
Write-Host "Backup complete: $OutputPath ($size bytes)."
Write-Host "Next: run scripts\backup\pg-restore.ps1 against a separate, explicitly named database to verify this backup."
