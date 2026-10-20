# Stops only the portable PostgreSQL installed by start_postgres.ps1 under
# ProgramData. Never touches an external/Docker PostgreSQL service.
[CmdletBinding()]
param(
    [string]$InstallDir = "C:\ProgramData\PropertyGuruPortablePg"
)
$ErrorActionPreference = "Stop"
$PgCtl = Join-Path $InstallDir "pgsql\bin\pg_ctl.exe"
$DataDir = Join-Path $InstallDir "data"
if (!(Test-Path $DataDir)) { Write-Host "No portable PostgreSQL data directory; nothing to stop."; return }
if (Test-Path $PgCtl) {
    $Previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $PgCtl -D $DataDir status *> $null
    $Running = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $Previous
    if ($Running) {
        & $PgCtl -D $DataDir stop -m fast
        if ($LASTEXITCODE -ne 0) { throw "Failed to stop portable PostgreSQL." }
        Write-Host "Portable PostgreSQL stopped. Data preserved in $DataDir."
    } else {
        Write-Host "Portable PostgreSQL is not running."
    }
} else {
    Write-Host "Portable PostgreSQL binaries are not installed; nothing to stop."
}
