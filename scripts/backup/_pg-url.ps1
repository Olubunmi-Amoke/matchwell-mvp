<#
.SYNOPSIS
    Parse a PostgreSQL connection URL into its components without ever
    printing the password.

.DESCRIPTION
    Shared helper dot-sourced by the backup/restore scripts. Accepts both
    "postgresql://" and SQLAlchemy-style "postgresql+psycopg://" URLs (the
    driver suffix is stripped). Returns a hashtable with HostName, Port,
    UserName, Password, and Database. Never writes any field to the
    console itself -- callers are responsible for only ever displaying
    HostName/Database, never Password (and treating UserName as
    operationally sensitive too).
#>
function ConvertFrom-PostgresUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url
    )

    # Normalize SQLAlchemy driver suffixes (postgresql+psycopg://, etc.) to
    # the plain scheme .NET's Uri parser understands.
    $normalized = $Url -replace '^postgresql\+[^:]+://', 'postgresql://'
    if ($normalized -notmatch '^postgresql://') {
        throw "Only postgresql:// (or postgresql+driver://) URLs are supported."
    }

    $uri = [System.Uri]$normalized
    if (-not $uri.UserInfo) {
        throw "The connection URL must include a username (and password)."
    }
    $userInfoParts = $uri.UserInfo.Split(':', 2)
    $userName = [System.Uri]::UnescapeDataString($userInfoParts[0])
    $password = if ($userInfoParts.Length -gt 1) {
        [System.Uri]::UnescapeDataString($userInfoParts[1])
    } else {
        ""
    }
    $database = $uri.AbsolutePath.TrimStart('/')
    if ([string]::IsNullOrWhiteSpace($database)) {
        throw "The connection URL must include a database name."
    }
    $port = if ($uri.Port -gt 0) { $uri.Port } else { 5432 }

    return @{
        HostName = $uri.Host
        Port     = $port
        UserName = $userName
        Password = $password
        Database = $database
    }
}
